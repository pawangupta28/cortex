"""Golden test set: questions with independently computed answers.

Gold SQL is written by hand against the *base tables*, not the order_facts view the
semantic layer uses, so a bug in the view or the compiler shows up as a failure instead
of being reproduced in the expected answer. Splits are assigned by hashing the template
id, so paraphrases of one template never straddle dev and holdout (no leakage).
"""
from __future__ import annotations

import hashlib


MONTH_NAMES = ["January", "February", "March", "April", "May", "June"]
CAT_WORDS = {"office_furniture": "office furniture", "auto": "auto parts", "health_beauty": "health and beauty",
             "watches_gifts": "watches", "bed_bath_table": "bed bath and table", "computers_accessories": "computers",
             "toys": "toys", "housewares": "housewares"}
STATE_NAMES = {"SP": "Sao Paulo", "RJ": "Rio de Janeiro", "BA": "Bahia", "PE": "Pernambuco", "MG": "Minas Gerais"}

ORDER_CAT = """oc AS (SELECT DISTINCT oi.order_id, t.product_category_name_english AS c FROM order_items oi
               JOIN products p USING (product_id) JOIN category_translation t USING (product_category_name))"""
TS = "CAST(o.order_purchase_timestamp AS TIMESTAMP)"
LATE = ("CASE WHEN TRY_CAST(o.order_delivered_customer_date AS TIMESTAMP) > "
        "TRY_CAST(o.order_estimated_delivery_date AS TIMESTAMP) THEN 1.0 ELSE 0.0 END")


def _split(template_id: str) -> str:
    h = int(hashlib.md5(template_id.encode()).hexdigest(), 16) % 10
    return "dev" if h < 6 else "calib" if h < 8 else "holdout"


def build(con) -> list[dict]:
    one = lambda sql: con.execute(sql).fetchone()[0]          # noqa: E731
    items: list[dict] = []

    def add(tid: str, typ: str, questions: list[str], gold: dict):
        for i, q in enumerate(questions):
            items.append({"id": f"{tid}#{i}", "template": tid, "type": typ, "question": q, "gold": gold,
                          "split": _split(tid)})

    for y in (2017, 2018):
        v = one(f"SELECT ROUND(SUM(oi.price), 2) FROM order_items oi JOIN orders o USING (order_id) WHERE year({TS}) = {y}")
        add(f"revenue_{y}", "metric_value", [f"What was total revenue in {y}?", f"How much did we sell in {y}?",
                                              f"Total sales for {y}"], {"values": [v]})
    for m, name in enumerate(MONTH_NAMES, start=1):
        v = one(f"SELECT COUNT(*) FROM orders o WHERE strftime({TS}, '%Y-%m') = '2018-{m:02d}'")
        add(f"orders_2018_{m:02d}", "metric_value", [f"How many orders were placed in {name} 2018?",
                                                      f"Number of orders in {name} 2018"], {"values": [v]})
    for cat, words in CAT_WORDS.items():
        v = one(f"""WITH r AS (SELECT order_id, AVG(review_score) s FROM reviews GROUP BY 1), {ORDER_CAT}
                    SELECT ROUND(AVG(s), 3) FROM r JOIN oc USING (order_id) WHERE c = '{cat}'""")
        add(f"review_{cat}", "metric_value", [f"What is the average review score for {words}?",
                                               f"Average rating of {words}"], {"values": [v]})
    for st, name in STATE_NAMES.items():
        v = one(f"""SELECT ROUND(AVG({LATE}), 4) FROM orders o JOIN customers c USING (customer_id)
                    WHERE o.order_status = 'delivered' AND c.customer_state = '{st}'""")
        add(f"late_{st}", "metric_value", [f"What is the late delivery rate in {name}?",
                                            f"How often are deliveries late in {st}?"], {"values": [v]})
        d = one(f"""SELECT ROUND(AVG(date_diff('day', {TS}, TRY_CAST(o.order_delivered_customer_date AS TIMESTAMP))), 2)
                    FROM orders o JOIN customers c USING (customer_id)
                    WHERE o.order_status = 'delivered' AND c.customer_state = '{st}'""")
        add(f"days_{st}", "metric_value", [f"What is the average delivery time in {name}?",
                                            f"How long does shipping take to {st}?"], {"values": [d]})
    top_late = con.execute(f"""WITH {ORDER_CAT}
        SELECT c, AVG({LATE}) r FROM orders o JOIN oc USING (order_id) WHERE o.order_status = 'delivered'
        GROUP BY c HAVING COUNT(*) >= 30 ORDER BY r DESC LIMIT 1""").fetchone()
    add("top_late_cat", "metric_rank", ["Which product categories have the highest late delivery rate?",
                                        "Which category has the most late deliveries?"],
        {"label": top_late[0], "values": [round(top_late[1], 4)]})
    low_rev = con.execute(f"""WITH r AS (SELECT order_id, AVG(review_score) s FROM reviews GROUP BY 1), {ORDER_CAT}
        SELECT c, AVG(s) FROM r JOIN oc USING (order_id) GROUP BY c HAVING COUNT(*) >= 30 ORDER BY 2 LIMIT 1""").fetchone()
    add("low_review_cat", "metric_rank", ["Which categories have the lowest average review score?",
                                          "What product category has the worst rating?"],
        {"label": low_rev[0], "values": [round(low_rev[1], 3)]})
    top_state = con.execute(f"""SELECT c.customer_state, ROUND(SUM(oi.price), 2) FROM order_items oi
        JOIN orders o USING (order_id) JOIN customers c USING (customer_id) WHERE year({TS}) = 2018
        GROUP BY 1 ORDER BY 2 DESC LIMIT 1""").fetchone()
    add("top_state_rev_2018", "metric_rank", ["Top 3 states by revenue in 2018",
                                              "Which states generated the most revenue in 2018?"],
        {"label": top_state[0], "values": [top_state[1]]})
    trend = con.execute(f"""SELECT strftime({TS}, '%Y-%m') m, COUNT(*) FROM orders o WHERE year({TS}) = 2018
                            GROUP BY 1 ORDER BY 1""").fetchall()
    add("trend_orders_2018", "metric_trend", ["Monthly order count trend in 2018", "How did orders per month change over 2018?"],
        {"values": [trend[0][1], trend[-1][1]]})

    docs = [
        ("withdrawal", ["How many days do customers have to return a product?", "What is the return window?"],
         "returns_policy", "Right of withdrawal", "7 days"),
        ("damaged", ["What happens if a product arrives damaged?", "Process for items that arrive broken"],
         "returns_policy", "Damaged items", "photos"),
        ("refund_timing", ["How long do refunds take?", "When is an approved refund paid?"],
         "returns_policy", "Refund timing", "10 business days"),
        ("late_comp", ["What compensation is given for late deliveries?", "Do customers get a voucher when orders are late?"],
         "shipping_sla", "Compensation for late orders", "10%"),
        ("probation", ["When is a seller put on probation?", "What review score threshold triggers seller probation?"],
         "seller_quality_program", "Thresholds", "3.5"),
        ("remote_states", ["Which states have longer delivery estimates?", "Why do orders to Bahia take longer?"],
         "shipping_sla", "Estimated delivery date", "BA"),
        ("packaging", ["What are the packaging rules for fragile furniture?", "Packaging requirements for office furniture sellers"],
         "seller_quality_program", "Packaging requirements for fragile categories", "double-wall"),
        ("shelf_life", ["How much shelf life must health products have?", "Rules for products close to expiry"],
         "category_notes", "Health and beauty", "6 months"),
        ("warranty", ["What is the warranty period for defective items?", "What if a product stops working after a month?"],
         "returns_policy", "Defective items", "90 days"),
        ("carriers", ["What does the supplier bulletin say about carriers?", "Are there any carrier changes this quarter?"],
         "supplier_bulletin", "Carrier changes", "second carrier"),
        ("late_def", ["How is late delivery defined?", "When is an order considered late?"],
         "shipping_sla", "Late delivery definition", "estimated delivery date"),
    ]
    for tid, qs, doc, section, fact in docs:
        add(f"doc_{tid}", "docs", qs, {"doc_id": doc, "section": section, "fact": fact})

    themes = {"office_furniture": ["late", "damaged", "broken", "scratched", "crushed", "cracked"],
              "auto": ["wrong", "fit", "late"],
              "health_beauty": ["expir", "expiry"],
              "watches_gifts": ["fake", "original", "copy"],
              "bed_bath_table": ["size", "smaller", "tiny"],
              "computers_accessories": ["defect", "working", "faulty"]}
    for cat, words in themes.items():
        w = CAT_WORDS[cat]
        add(f"why_{cat}", "hybrid", [f"Why do customers complain about {w}?", f"What are the main complaints in {w} reviews?"],
            {"any_of": words})

    for i, q in enumerate(["What is the capital of France?", "Tell me a joke about databases",
                           "What is the weather in Sao Paulo today?", "How many employees does the company have?",
                           "What will revenue be in 2030?", "Who is the CEO of the store?"]):
        add(f"unanswerable_{i}", "unanswerable", [q], {"abstain": True})
    return items
