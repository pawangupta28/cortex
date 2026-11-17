"""E-commerce domain pack: Olist-schema warehouse, store policies and customer reviews.

Uses the real Olist CSVs when they are in data/raw/olist/, otherwise generates a
deterministic synthetic dataset in the same schema (see synthetic.py).
"""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

from cortex.config import ROOT, settings
from cortex.core.models import Chunk
from cortex.ingest.chunker import chunk_markdown

HERE = Path(__file__).resolve().parent

OLIST_FILES = {
    "customers": "olist_customers_dataset.csv",
    "orders": "olist_orders_dataset.csv",
    "order_items": "olist_order_items_dataset.csv",
    "products": "olist_products_dataset.csv",
    "sellers": "olist_sellers_dataset.csv",
    "reviews": "olist_order_reviews_dataset.csv",
    "category_translation": "product_category_name_translation.csv",
}

ORDER_FACTS_SQL = """
CREATE OR REPLACE VIEW order_facts AS
WITH items AS (
    SELECT oi.order_id,
           SUM(oi.price) AS revenue,
           SUM(oi.freight_value) AS freight,
           any_value(COALESCE(t.product_category_name_english, p.product_category_name)) AS category,
           any_value(s.seller_state) AS seller_state,
           any_value(oi.seller_id) AS seller_id
    FROM order_items oi
    LEFT JOIN products p USING (product_id)
    LEFT JOIN category_translation t USING (product_category_name)
    LEFT JOIN sellers s USING (seller_id)
    GROUP BY oi.order_id
), rv AS (
    SELECT order_id, AVG(review_score) AS review_score FROM reviews GROUP BY order_id
)
SELECT o.order_id, o.order_status, c.customer_unique_id, c.customer_state,
       CAST(o.order_purchase_timestamp AS TIMESTAMP) AS purchased_at,
       TRY_CAST(o.order_delivered_customer_date AS TIMESTAMP) AS delivered_at,
       TRY_CAST(o.order_estimated_delivery_date AS TIMESTAMP) AS estimated_at,
       TRY_CAST(o.order_delivered_customer_date AS TIMESTAMP) > TRY_CAST(o.order_estimated_delivery_date AS TIMESTAMP) AS is_late,
       date_diff('day', CAST(o.order_purchase_timestamp AS TIMESTAMP),
                 TRY_CAST(o.order_delivered_customer_date AS TIMESTAMP)) AS delivery_days,
       i.revenue, i.freight, i.category, i.seller_state, i.seller_id, rv.review_score
FROM orders o
JOIN customers c USING (customer_id)
LEFT JOIN items i USING (order_id)
LEFT JOIN rv USING (order_id)
"""


def _sibling(name: str):
    spec = importlib.util.spec_from_file_location(f"ecommerce_{name}", HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def ingest(con, domain, force_synthetic: bool = False):
    real = settings.data_dir / "raw" / "olist"
    synth = settings.data_dir / "raw" / "synthetic"
    use_real = not force_synthetic and all((real / f).exists() for f in OLIST_FILES.values())
    src = real if use_real else synth
    if not use_real and (force_synthetic or not (synth / OLIST_FILES["orders"]).exists()):
        _sibling("synthetic").generate(synth)
    lineage = {}
    for table, fname in OLIST_FILES.items():
        path = src / fname
        con.execute(f"CREATE TABLE {table} AS SELECT * FROM read_csv_auto(?, header=true)", [str(path)])
        rel = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
        lineage[table] = {"file": str(rel), "sha256": _sha(path)}
    con.execute(ORDER_FACTS_SQL)

    chunks: list[Chunk] = []
    for md in sorted((domain.dir / "docs").glob("*.md")):
        chunks += chunk_markdown(md.stem, md.read_text(encoding="utf-8"),
                                 metadata={"type": "policy", "source": f"docs/{md.name}"})
    rows = con.execute("""
        SELECT r.review_id, r.review_comment_message, r.review_score, f.category, f.customer_state,
               strftime(f.purchased_at, '%Y-%m') AS month, f.is_late
        FROM reviews r JOIN order_facts f USING (order_id)
        WHERE r.review_comment_message IS NOT NULL AND length(trim(r.review_comment_message)) > 0
    """).fetchall()
    for rid, msg, score, cat, st, month, late in rows:
        chunks.append(Chunk(id=f"rv_{rid[:12]}", doc_id=f"review:{rid}", text=msg, section_path="review",
                            metadata={"type": "review", "score": int(score), "category": cat, "state": st,
                                      "month": month, "late": bool(late)}))
    return chunks, {"source": "olist" if use_real else "synthetic", "tables": lineage}


def golden(con, domain) -> list[dict]:
    return _sibling("golden_build").build(con)
