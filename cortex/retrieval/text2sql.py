"""Text-to-SQL: question -> QuerySpec (LLM or offline parser) -> compiled SQL -> guard -> execute.

Raw LLM-written SQL is used only when the question falls outside the semantic layer,
and even then it must pass the same guard (SELECT-only, allow-listed tables, LIMIT, RLS).
A failed execution is fed back to the model once with the error (self-correction loop).
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field

from cortex.config import settings
from cortex.gateway.gateway import Gateway, parse_json
from cortex.ingest.loader import connect
from cortex.retrieval.semantic_layer import QuerySpec, SemanticLayer
from cortex.security.sql_guard import SQLRejected, guard
from cortex.telemetry.tracer import span

SPEC_PROMPT = """You translate business questions into a JSON query spec for a semantic layer.
{layer}
Return JSON only: {{"metric": str|null, "dimensions": [str], "filters": {{}}, "order": "asc"|"desc"|null, "limit": int|null}}.
Use metric null if no metric fits. Never invent metric names."""

RAW_SQL_PROMPT = """You write one DuckDB SELECT statement. Tables and columns:
{schema}
Rules: SELECT only, no comments, at most {limit} rows. Return JSON {{"sql": "..."}}."""


@dataclass
class SQLResult:
    sql: str
    columns: list[str]
    rows: list[tuple]
    spec: dict | None = None
    path: str = "semantic_layer"          # semantic_layer | raw_llm
    error: str | None = None
    rls_applied: bool = False
    attempts: int = 1
    meta: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.error is None


def _schema_text() -> str:
    path = settings.artifacts_dir / "schema_profile.json"
    prof = json.loads(path.read_text(encoding="utf-8"))
    lines = []
    for t, info in prof.items():
        if t == "chunks":
            continue
        cols = ", ".join(f"{c} {v['type']}" for c, v in info["columns"].items())
        lines.append(f"{t}({cols})")
    return "\n".join(lines)


def execute(sql: str, timeout_s: float | None = None) -> tuple[list[str], list[tuple]]:
    con = connect()
    timer = threading.Timer(timeout_s or settings.sql_timeout_s, con.interrupt)
    timer.start()
    try:
        cur = con.execute(sql)
        cols = [d[0] for d in cur.description]
        return cols, cur.fetchall()
    finally:
        timer.cancel()
        con.close()


class Text2SQL:
    def __init__(self, gateway: Gateway, layer: SemanticLayer | None = None):
        self.gw = gateway
        self.layer = layer or SemanticLayer()

    def spec_for(self, question: str) -> QuerySpec | None:
        offline = self.layer.parse(question)
        if not self.gw.online:
            return offline
        res = self.gw.complete("sql", [
            {"role": "system", "content": SPEC_PROMPT.format(layer=self.layer.describe())},
            {"role": "user", "content": question}], json_mode=True,
            offline=lambda: json.dumps(offline.to_dict() if offline else {"metric": None}))
        data = parse_json(res.text) or {}
        if not data.get("metric"):
            return None
        try:
            spec = QuerySpec(metric=data["metric"], dimensions=list(data.get("dimensions") or []),
                             filters=dict(data.get("filters") or {}), order=data.get("order"),
                             limit=data.get("limit"), confidence=0.85)
            self.layer.compile(spec)      # validates names
            # agreement with the deterministic parser is a confidence signal
            if offline and offline.metric == spec.metric:
                spec.confidence = 0.95
            return spec
        except (ValueError, KeyError, TypeError):
            return offline

    def run(self, question: str, allowed_scope="*") -> SQLResult | None:
        with span("retrieval.text2sql") as sp:
            spec = self.spec_for(question)
            if spec is not None:
                sql = self.layer.compile(spec)
                try:
                    g = guard(sql, allowed_scope, settings.sql_row_limit)
                    cols, rows = execute(g.sql)
                    sp["attributes"].update({"cortex.sql.path": "semantic_layer", "cortex.sql.rows": len(rows)})
                    return SQLResult(sql=sql, columns=cols, rows=rows, spec=spec.to_dict(),
                                     rls_applied=g.rls_applied, meta={"spec_confidence": spec.confidence})
                except Exception as e:   # noqa: BLE001
                    return SQLResult(sql=sql, columns=[], rows=[], spec=spec.to_dict(), error=str(e))
            if not self.gw.online:
                return None
            return self._raw(question, allowed_scope)

    def _raw(self, question: str, allowed_scope) -> SQLResult:
        messages = [{"role": "system", "content": RAW_SQL_PROMPT.format(schema=_schema_text(), limit=settings.sql_row_limit)},
                    {"role": "user", "content": question}]
        last_err, sql = None, ""
        for attempt in (1, 2):
            res = self.gw.complete("sql", messages, json_mode=True, offline=lambda: '{"sql": ""}')
            sql = (parse_json(res.text) or {}).get("sql", "")
            try:
                g = guard(sql, allowed_scope, settings.sql_row_limit)
                cols, rows = execute(g.sql)
                self.gw.feedback(res, 1.0)
                return SQLResult(sql=sql, columns=cols, rows=rows, path="raw_llm", rls_applied=g.rls_applied,
                                 attempts=attempt, meta={"spec_confidence": 0.6})
            except (SQLRejected, Exception) as e:  # noqa: BLE001
                self.gw.feedback(res, 0.0)
                last_err = str(e)
                messages += [{"role": "assistant", "content": res.text},
                             {"role": "user", "content": f"That failed: {last_err[:300]}. Fix it."}]
        return SQLResult(sql=sql, columns=[], rows=[], path="raw_llm", error=last_err, attempts=2)
