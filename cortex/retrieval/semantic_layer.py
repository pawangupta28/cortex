"""Semantic layer: questions become a typed QuerySpec, and the spec compiles to SQL.

Design choice (ADR-006): the language model is allowed to *choose* a metric, dimensions
and filters, but metric arithmetic lives here, defined once in YAML. This removes the
most common text-to-SQL failure (plausible but wrong aggregation) and makes every
number reproducible from the spec.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from cortex.config import settings

MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july",
                                      "august", "september", "october", "november", "december"], start=1)}
ORDINAL_Q = {"first": 1, "second": 2, "third": 3, "fourth": 4, "1st": 1, "2nd": 2, "3rd": 3, "4th": 4}


@dataclass
class QuerySpec:
    metric: str
    dimensions: list[str] = field(default_factory=list)
    filters: dict[str, object] = field(default_factory=dict)
    order: str | None = None          # "desc" | "asc" | None
    limit: int | None = None
    confidence: float = 1.0

    def to_dict(self) -> dict:
        return {"metric": self.metric, "dimensions": self.dimensions, "filters": self.filters,
                "order": self.order, "limit": self.limit}


class SemanticLayer:
    def __init__(self, domain_dir: Path | None = None):
        d = domain_dir or settings.domain_dir
        cfg = yaml.safe_load((d / "semantic_layer.yaml").read_text(encoding="utf-8"))
        gl = yaml.safe_load((d / "glossary.yaml").read_text(encoding="utf-8"))
        self.base = cfg["base_view"]
        self.metrics: dict = cfg["metrics"]
        self.dimensions: dict = cfg["dimensions"]
        self.categories: dict[str, list[str]] = gl["categories"]
        self.states: dict[str, list[str]] = gl["states"]

    # ------------------------------------------------------------- compile
    def compile(self, spec: QuerySpec) -> str:
        if spec.metric not in self.metrics:
            raise ValueError(f"unknown metric {spec.metric!r}")
        for d in spec.dimensions:
            if d not in self.dimensions:
                raise ValueError(f"unknown dimension {d!r}")
        m = self.metrics[spec.metric]
        where = self._where(spec.filters)
        if m.get("where"):
            where.append(m["where"])
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        dims = [(d, self.dimensions[d]["sql"]) for d in spec.dimensions]

        if m.get("grain") == "customer":
            inner_dims = "".join(f", {expr} AS {name}" for name, expr in dims)
            src = (f"(SELECT customer_unique_id{inner_dims}, COUNT(DISTINCT order_id) AS customer_orders "
                   f"FROM {self.base} {where_sql} GROUP BY ALL) AS cust")
            select_dims = [name for name, _ in dims]
            sql = f"SELECT {', '.join(select_dims + [m['sql'] + ' AS ' + spec.metric])} FROM {src}"
            if select_dims:
                sql += f" GROUP BY {', '.join(select_dims)}"
        else:
            select_dims = [f"{expr} AS {name}" for name, expr in dims]
            sql = f"SELECT {', '.join(select_dims + [m['sql'] + ' AS ' + spec.metric])} FROM {self.base} {where_sql}"
            if dims:
                sql += f" GROUP BY {', '.join(name for name, _ in dims)}"
                if "category" in spec.dimensions:
                    sql += " HAVING COUNT(DISTINCT order_id) >= 30"   # avoid ranking on tiny samples

        time_dims = [d for d in spec.dimensions if d in ("month", "quarter", "year")]
        if spec.order and spec.dimensions:
            sql += f" ORDER BY {spec.metric} {spec.order.upper()} NULLS LAST"
        elif time_dims:
            sql += f" ORDER BY {time_dims[0]}"
        elif spec.dimensions:
            sql += f" ORDER BY {spec.metric} DESC NULLS LAST"
        if spec.limit:
            sql += f" LIMIT {int(spec.limit)}"
        return " ".join(sql.split())

    def _where(self, f: dict) -> list[str]:
        out = []
        if "year" in f:
            out.append(f"year(purchased_at) = {int(f['year'])}")
        if "quarter" in f:
            y, q = str(f["quarter"]).split("-Q")
            out.append(f"year(purchased_at) = {int(y)} AND quarter(purchased_at) = {int(q)}")
        if "month" in f:
            out.append(f"strftime(purchased_at, '%Y-%m') = '{str(f['month'])[:7]}'")
        if "category" in f:
            cats = f["category"] if isinstance(f["category"], list) else [f["category"]]
            ok = [c for c in cats if c in self.categories]
            if ok:
                out.append("category IN (" + ", ".join(f"'{c}'" for c in ok) + ")")
        if "state" in f:
            sts = f["state"] if isinstance(f["state"], list) else [f["state"]]
            ok = [s for s in sts if s in self.states]
            if ok:
                out.append("customer_state IN (" + ", ".join(f"'{s}'" for s in ok) + ")")
        return out

    # ------------------------------------------------------------- offline NL parser
    def parse(self, question: str) -> QuerySpec | None:
        """Deterministic parser used when no LLM key is configured, and as the
        cross-check for LLM-produced specs. Returns None when no metric matches."""
        q = " " + question.lower().strip() + " "
        best, best_len = None, 0
        for name, m in self.metrics.items():
            for syn in m["synonyms"] + [name.replace("_", " ")]:
                if re.search(r"\b" + re.escape(syn.lower()) + r"\b", q) and len(syn) > best_len:
                    best, best_len = name, len(syn)
        if best is None:
            return None

        dims: list[str] = []
        for name, d in self.dimensions.items():
            for syn in d["synonyms"]:
                s = re.escape(syn.lower())
                if re.search(rf"\b(by|per|each|which|what|across|every|top \d+|for each|over|of each|by each)\s+(the\s+)?{s}\b", q) \
                        or re.search(rf"\b{s}\s+(breakdown|wise|split)\b", q) \
                        or (name == "month" and re.search(r"\b(monthly|trend|over time|per month)\b", q)) \
                        or (name == "quarter" and re.search(r"\bquarterly\b", q)):
                    if name not in dims:
                        dims.append(name)
                    break
        if "seller_state" in dims and "state" in dims:
            dims.remove("state")

        filters: dict[str, object] = {}
        m = re.search(r"\b(q[1-4])\s*(20\d\d)\b|\b(20\d\d)\s*(q[1-4])\b", q)
        mq = re.search(r"\b(first|second|third|fourth|1st|2nd|3rd|4th) quarter (of )?(20\d\d)\b", q)
        mm = re.search(r"\b(" + "|".join(MONTHS) + r")\s+(20\d\d)\b", q)
        if m:
            qq, yy = (m.group(1), m.group(2)) if m.group(1) else (m.group(4), m.group(3))
            filters["quarter"] = f"{yy}-Q{qq[1]}"
        elif mq:
            filters["quarter"] = f"{mq.group(3)}-Q{ORDINAL_Q[mq.group(1)]}"
        elif mm:
            filters["month"] = f"{mm.group(2)}-{MONTHS[mm.group(1)]:02d}"
        else:
            years = re.findall(r"\b((?:19|20)\d\d)\b", q)
            if len(set(years)) == 1:
                filters["year"] = int(years[0])
            elif len(set(years)) > 1 and "year" not in dims:
                dims.append("year")

        cats = [c for c, syns in self.categories.items()
                if any(re.search(r"\b" + re.escape(s.lower()) + r"\b", q) for s in syns)]
        # "office furniture" also contains "furniture"; keep the most specific match
        if "office_furniture" in cats and "furniture_decor" in cats and "decor" not in q:
            cats.remove("furniture_decor")
        if cats and "category" not in dims:
            filters["category"] = cats if len(cats) > 1 else cats[0]
        raw = " " + question + " "
        sts = []
        for code, syns in self.states.items():
            for s in syns:
                if len(s) == 2:
                    if re.search(rf"\b{s}\b", raw):          # codes are case-sensitive: avoid "go", "pr"
                        sts.append(code)
                        break
                elif re.search(r"\b" + re.escape(s.lower()) + r"\b", q):
                    sts.append(code)
                    break
        if sts and "state" not in dims:
            filters["state"] = sts if len(sts) > 1 else sts[0]

        order, limit = None, None
        n = re.search(r"\b(top|bottom|best|worst)\s+(\d+)\b", q)
        if n:
            limit = int(n.group(2))
        if re.search(r"\b(highest|most|top|largest|biggest|best|maximum|max|greatest|worst performing)\b", q):
            order = "desc"
        if re.search(r"\b(lowest|least|fewest|smallest|bottom|minimum|min)\b", q):
            order = "asc"
        if re.search(r"\bworst\b", q) and best in ("avg_review_score",):
            order = "asc"
        if re.search(r"\bworst\b", q) and best in ("late_delivery_rate", "one_star_rate", "avg_delivery_days"):
            order = "desc"
        if order and dims and limit is None and re.search(r"\b(which|what)\b", q):
            limit = 5
        conf = 0.55 + 0.15 * bool(dims or filters) + 0.1 * (best_len > 4) + 0.1 * bool(order or not dims)
        return QuerySpec(metric=best, dimensions=dims, filters=filters, order=order, limit=limit,
                         confidence=round(min(conf, 0.95), 2))

    def describe(self) -> str:
        lines = ["METRICS:"]
        lines += [f"- {k}: {v['description']}" for k, v in self.metrics.items()]
        lines.append("DIMENSIONS: " + ", ".join(self.dimensions))
        lines.append("FILTERS: year (int), quarter ('YYYY-Qn'), month ('YYYY-MM'), category (one of "
                     + ", ".join(self.categories) + "), state (one of " + ", ".join(self.states) + ")")
        return "\n".join(lines)
