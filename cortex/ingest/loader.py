"""Layer 1: load raw sources into DuckDB, build the analytic view, profile the schema,
and turn documents + review text into chunks with provenance."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import duckdb

from cortex.config import ROOT, settings
from cortex.ingest.chunker import chunk_markdown
from cortex.ingest.synthetic import generate
from cortex.telemetry.tracer import span

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


def raw_dir() -> Path:
    real = settings.data_dir / "raw" / "olist"
    if all((real / f).exists() for f in OLIST_FILES.values()):
        return real
    return settings.data_dir / "raw" / "synthetic"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def ingest(force_synthetic: bool = False) -> dict:
    with span("ingest.run", **{"cortex.domain": settings.domain}) as sp:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        src = raw_dir()
        if force_synthetic or src.name == "synthetic":
            src = settings.data_dir / "raw" / "synthetic"
            if force_synthetic or not (src / OLIST_FILES["orders"]).exists():
                generate(src)
        if settings.db_path.exists():
            settings.db_path.unlink()
        con = duckdb.connect(str(settings.db_path))
        lineage = {}
        for table, fname in OLIST_FILES.items():
            path = src / fname
            con.execute(f"CREATE TABLE {table} AS SELECT * FROM read_csv_auto(?, header=true, all_varchar=false)",
                        [str(path)])
            lineage[table] = {"file": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
                              "sha256": _sha(path)}
        con.execute(ORDER_FACTS_SQL)

        profile = profile_schema(con)
        chunks = build_chunks(con)
        con.execute("CREATE TABLE chunks (id VARCHAR PRIMARY KEY, doc_id VARCHAR, text VARCHAR, section_path VARCHAR, metadata JSON)")
        con.executemany("INSERT INTO chunks VALUES (?, ?, ?, ?, ?)",
                        [(c.id, c.doc_id, c.text, c.section_path, json.dumps(c.metadata)) for c in chunks])
        con.close()

        settings.artifacts_dir.mkdir(parents=True, exist_ok=True)
        (settings.artifacts_dir / "schema_profile.json").write_text(encoding="utf-8", data=json.dumps(profile, indent=2, default=str))
        (settings.artifacts_dir / "lineage.json").write_text(encoding="utf-8", data=json.dumps({"source": src.name, "tables": lineage}, indent=2))
        stats = {"source": src.name, "tables": {t: profile[t]["rows"] for t in profile}, "chunks": len(chunks)}
        sp["attributes"].update({"cortex.ingest.chunks": len(chunks), "cortex.ingest.source": src.name})
        return stats


def profile_schema(con: duckdb.DuckDBPyConnection) -> dict:
    """Column types, null rates, cardinality and samples: the raw material for the
    semantic layer and for schema-linking in text-to-SQL."""
    out: dict = {}
    tables = [r[0] for r in con.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='main'").fetchall()]
    for t in tables:
        rows = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        cols = con.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name=? ORDER BY ordinal_position", [t]).fetchall()
        cinfo = {}
        for name, dtype in cols:
            nulls, distinct = con.execute(f'SELECT COUNT(*) - COUNT("{name}"), approx_count_distinct("{name}") FROM {t}').fetchone()
            samples = [r[0] for r in con.execute(f'SELECT DISTINCT "{name}" FROM {t} WHERE "{name}" IS NOT NULL LIMIT 3').fetchall()]
            cinfo[name] = {"type": dtype, "null_rate": round(nulls / rows, 4) if rows else 0,
                           "distinct": distinct, "samples": samples}
        out[t] = {"rows": rows, "columns": cinfo}
    return out


def build_chunks(con: duckdb.DuckDBPyConnection):
    chunks = []
    docs_dir = settings.domain_dir / "docs"
    for md in sorted(docs_dir.glob("*.md")):
        chunks += chunk_markdown(md.stem, md.read_text(encoding="utf-8"), metadata={"type": "policy", "source": f"docs/{md.name}"})
    # Each non-empty review becomes a chunk with structured metadata for filtered retrieval.
    rows = con.execute("""
        SELECT r.review_id, r.review_comment_message, r.review_score, f.category, f.customer_state,
               strftime(f.purchased_at, '%Y-%m') AS month, f.is_late
        FROM reviews r JOIN order_facts f USING (order_id)
        WHERE r.review_comment_message IS NOT NULL AND length(trim(r.review_comment_message)) > 0
    """).fetchall()
    from cortex.core.models import Chunk
    for rid, msg, score, cat, st, month, late in rows:
        chunks.append(Chunk(id=f"rv_{rid[:12]}", doc_id=f"review:{rid}", text=msg, section_path="review",
                            metadata={"type": "review", "score": int(score), "category": cat, "state": st,
                                      "month": month, "late": bool(late)}))
    return chunks


def connect(read_only: bool = True) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(settings.db_path), read_only=read_only)
