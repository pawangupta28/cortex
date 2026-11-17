"""Layer 1: build a domain's warehouse and document chunks.

The domain pack (domains/<name>/pack.py) knows its sources; this module does what every
domain needs: a fresh database, schema profiling for the semantic layer and text-to-SQL,
a chunks table with provenance, and a lineage record of every input file.
"""
from __future__ import annotations

import json

import duckdb

from cortex.config import settings
from cortex.domain import get_domain
from cortex.telemetry.tracer import span


def ingest(force_synthetic: bool = False) -> dict:
    domain = get_domain()
    with span("ingest.run", **{"cortex.domain": domain.name}) as sp:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        settings.artifacts_dir.mkdir(parents=True, exist_ok=True)
        if settings.db_path.exists():
            settings.db_path.unlink()
        con = duckdb.connect(str(settings.db_path))
        chunks, lineage = domain.ingest(con, force_synthetic=force_synthetic)
        profile = profile_schema(con)
        con.execute("CREATE TABLE chunks (id VARCHAR PRIMARY KEY, doc_id VARCHAR, text VARCHAR, section_path VARCHAR, metadata JSON)")
        con.executemany("INSERT INTO chunks VALUES (?, ?, ?, ?, ?)",
                        [(c.id, c.doc_id, c.text, c.section_path, json.dumps(c.metadata)) for c in chunks])
        con.close()
        (settings.artifacts_dir / "schema_profile.json").write_text(
            encoding="utf-8", data=json.dumps(profile, indent=2, default=str))
        (settings.artifacts_dir / "lineage.json").write_text(encoding="utf-8", data=json.dumps(lineage, indent=2))
        stats = {"domain": domain.name, "source": lineage.get("source"),
                 "tables": {t: profile[t]["rows"] for t in profile}, "chunks": len(chunks)}
        sp["attributes"].update({"cortex.ingest.chunks": len(chunks), "cortex.ingest.source": str(lineage.get("source"))})
        return stats


def profile_schema(con: duckdb.DuckDBPyConnection) -> dict:
    """Column types, null rates, cardinality and samples: the raw material for the
    semantic layer and for schema-linking in text-to-SQL."""
    out: dict = {}
    tables = [r[0] for r in con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='main'").fetchall()]
    for t in tables:
        rows = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        cols = con.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name=? "
                           "ORDER BY ordinal_position", [t]).fetchall()
        cinfo = {}
        for name, dtype in cols:
            nulls, distinct = con.execute(f'SELECT COUNT(*) - COUNT("{name}"), approx_count_distinct("{name}") FROM "{t}"').fetchone()
            samples = [r[0] for r in con.execute(f'SELECT DISTINCT "{name}" FROM "{t}" WHERE "{name}" IS NOT NULL LIMIT 3').fetchall()]
            cinfo[name] = {"type": dtype, "null_rate": round(nulls / rows, 4) if rows else 0,
                           "distinct": distinct, "samples": samples}
        out[t] = {"rows": rows, "columns": cinfo}
    return out


def connect(read_only: bool = True) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(settings.db_path), read_only=read_only)
