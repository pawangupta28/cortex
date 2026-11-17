"""Deterministic synthetic e-commerce data in the exact Olist schema.

Why synthetic: it lets the repo run end-to-end with no downloads, and we *know* the
causal structure we planted (which categories ship late, why customers complain),
so evals can check the system recovers it. Drop the real Olist CSVs into
data/raw/olist/ and the loader uses them instead, unchanged.
"""
from __future__ import annotations

import csv
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

STATES = ["SP", "RJ", "MG", "RS", "PR", "SC", "BA", "DF", "GO", "PE"]
STATE_WEIGHTS = [0.42, 0.13, 0.12, 0.06, 0.05, 0.04, 0.04, 0.03, 0.03, 0.08]
CITIES = {"SP": "sao paulo", "RJ": "rio de janeiro", "MG": "belo horizonte", "RS": "porto alegre",
          "PR": "curitiba", "SC": "florianopolis", "BA": "salvador", "DF": "brasilia",
          "GO": "goiania", "PE": "recife"}

# category -> (pt name, base price, late-delivery probability, main complaint theme)
CATEGORIES = {
    "bed_bath_table": ("cama_mesa_banho", 90, 0.08, "size"),
    "health_beauty": ("beleza_saude", 120, 0.06, "expiry"),
    "sports_leisure": ("esporte_lazer", 110, 0.07, "quality"),
    "furniture_decor": ("moveis_decoracao", 160, 0.22, "damaged"),
    "computers_accessories": ("informatica_acessorios", 180, 0.09, "defect"),
    "housewares": ("utilidades_domesticas", 80, 0.07, "quality"),
    "watches_gifts": ("relogios_presentes", 200, 0.05, "fake"),
    "telephony": ("telefonia", 140, 0.10, "defect"),
    "garden_tools": ("ferramentas_jardim", 100, 0.12, "missing_parts"),
    "auto": ("automotivo", 130, 0.18, "wrong_item"),
    "toys": ("brinquedos", 95, 0.06, "quality"),
    "office_furniture": ("moveis_escritorio", 260, 0.30, "damaged"),
}

POSITIVE = [
    "Arrived before the expected date, very happy with the purchase.",
    "Great product, exactly as described. Would buy again.",
    "Fast delivery and good packaging. Recommend this seller.",
    "Good quality for the price, delivered on time.",
    "Everything perfect, product works well.",
]
NEGATIVE_BY_THEME = {
    "damaged": ["The box arrived crushed and the piece was broken.",
                "Product came damaged, the packaging was very poor.",
                "Furniture arrived scratched and one leg was cracked."],
    "size": ["The sheets are smaller than described, wrong size.",
             "Size does not match the listing, towels are tiny."],
    "expiry": ["Product was close to the expiry date when it arrived.",
               "Received an expired item, very disappointed."],
    "quality": ["Poor quality, it broke after one week of use.",
                "Material feels cheap, not worth the price."],
    "defect": ["Device stopped working after two days, defective unit.",
               "Charger does not work, faulty product."],
    "fake": ["The watch looks fake, not the original brand.",
             "Not original, seems to be a copy."],
    "missing_parts": ["Missing parts in the kit, could not assemble it.",
                      "Screws were missing from the box."],
    "wrong_item": ["Received the wrong item, not what I ordered.",
                   "Part does not fit my car model, wrong item sent."],
}
LATE = ["Delivery was very late, waited weeks past the promised date.",
        "Still waiting, the order is late and nobody answers.",
        "Arrived much later than the estimated date."]


def _id(rng: np.random.Generator) -> str:
    return uuid.UUID(int=int(rng.integers(0, 2**63)) << 64 | int(rng.integers(0, 2**63))).hex


def generate(out_dir: Path, n_customers: int = 6000, n_orders: int = 10000, seed: int = 7) -> dict[str, int]:
    rng = np.random.default_rng(seed)
    out_dir.mkdir(parents=True, exist_ok=True)
    cats = list(CATEGORIES)

    sellers = []
    for _ in range(150):
        st = rng.choice(STATES, p=STATE_WEIGHTS)
        sellers.append({"seller_id": _id(rng), "seller_zip_code_prefix": int(rng.integers(1000, 99999)),
                        "seller_city": CITIES[st], "seller_state": st})

    products = []
    for _ in range(600):
        en = cats[int(rng.integers(0, len(cats)))]
        products.append({"product_id": _id(rng), "product_category_name": CATEGORIES[en][0],
                         "product_name_lenght": int(rng.integers(20, 60)),
                         "product_description_lenght": int(rng.integers(100, 2000)),
                         "product_photos_qty": int(rng.integers(1, 6)),
                         "product_weight_g": int(rng.integers(100, 20000)),
                         "_en": en})

    def new_person() -> dict:
        return {"customer_unique_id": _id(rng), "state": rng.choice(STATES, p=STATE_WEIGHTS)}

    # A small loyal pool places repeat orders; everyone else buys once.
    # Gives a repeat-customer rate of a few percent, close to the real Olist data.
    loyal = [new_person() for _ in range(n_customers // 20)]

    customers, orders, items, reviews = [], [], [], []
    start = datetime(2017, 1, 1)
    span_days = (datetime(2018, 8, 31) - start).days
    for i in range(n_orders):
        cu = loyal[int(rng.integers(0, len(loyal)))] if rng.random() < 0.08 else new_person()
        cust_id = _id(rng)
        st = cu["state"]
        customers.append({"customer_id": cust_id, "customer_unique_id": cu["customer_unique_id"],
                          "customer_zip_code_prefix": int(rng.integers(1000, 99999)),
                          "customer_city": CITIES[st], "customer_state": st})
        # demand grows over time, like the real dataset
        day = int(span_days * np.sqrt(rng.random()))
        purchased = start + timedelta(days=day, hours=int(rng.integers(0, 24)), minutes=int(rng.integers(0, 60)))
        n_items = 1 if rng.random() < 0.88 else int(rng.integers(2, 4))
        prod = products[int(rng.integers(0, len(products)))]
        en = prod["_en"]
        _, base_price, p_late, theme = CATEGORIES[en]
        # remote states ship slower
        far = st in ("BA", "PE", "GO")
        est_days = int(rng.integers(15, 30)) + (5 if far else 0)
        late = rng.random() < p_late + (0.08 if far else 0)
        deliver_days = est_days + int(rng.integers(3, 25)) if late else max(3, est_days - int(rng.integers(2, 14)))
        status = "canceled" if rng.random() < 0.01 else "delivered"
        oid = _id(rng)
        orders.append({
            "order_id": oid, "customer_id": cust_id, "order_status": status,
            "order_purchase_timestamp": purchased.isoformat(sep=" "),
            "order_approved_at": (purchased + timedelta(hours=2)).isoformat(sep=" "),
            "order_delivered_carrier_date": (purchased + timedelta(days=2)).isoformat(sep=" ") if status == "delivered" else "",
            "order_delivered_customer_date": (purchased + timedelta(days=deliver_days)).isoformat(sep=" ") if status == "delivered" else "",
            "order_estimated_delivery_date": (purchased + timedelta(days=est_days)).date().isoformat() + " 00:00:00",
        })
        seller = sellers[int(rng.integers(0, len(sellers)))]
        for k in range(n_items):
            price = round(float(base_price * rng.lognormal(0, 0.45)), 2)
            items.append({"order_id": oid, "order_item_id": k + 1, "product_id": prod["product_id"],
                          "seller_id": seller["seller_id"],
                          "shipping_limit_date": (purchased + timedelta(days=3)).isoformat(sep=" "),
                          "price": price, "freight_value": round(float(10 + 0.08 * price * rng.random() + (12 if far else 0)), 2)})
        if status == "delivered" and rng.random() < 0.97:
            # score model: lateness and the category's defect theme drive dissatisfaction
            bad_product = rng.random() < (0.10 + (0.08 if en in ("furniture_decor", "office_furniture", "auto") else 0))
            if late:
                score = int(rng.choice([1, 2, 3], p=[0.5, 0.3, 0.2]))
                text = str(rng.choice(LATE))
            elif bad_product:
                score = int(rng.choice([1, 2], p=[0.6, 0.4]))
                text = str(rng.choice(NEGATIVE_BY_THEME[theme]))
            else:
                score = int(rng.choice([3, 4, 5], p=[0.1, 0.3, 0.6]))
                text = str(rng.choice(POSITIVE)) if rng.random() < 0.6 else ""
            created = purchased + timedelta(days=deliver_days + 1)
            reviews.append({"review_id": _id(rng), "order_id": oid, "review_score": score,
                            "review_comment_title": "", "review_comment_message": text,
                            "review_creation_date": created.isoformat(sep=" "),
                            "review_answer_timestamp": (created + timedelta(days=1)).isoformat(sep=" ")})

    translation = [{"product_category_name": v[0], "product_category_name_english": k} for k, v in CATEGORIES.items()]
    files = {
        "olist_customers_dataset.csv": customers,
        "olist_orders_dataset.csv": orders,
        "olist_order_items_dataset.csv": items,
        "olist_products_dataset.csv": [{k: v for k, v in p.items() if k != "_en"} for p in products],
        "olist_sellers_dataset.csv": sellers,
        "olist_order_reviews_dataset.csv": reviews,
        "product_category_name_translation.csv": translation,
    }
    for name, rows in files.items():
        with (out_dir / name).open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
    return {k: len(v) for k, v in files.items()}
