"""Semantic layer: questions become a typed QuerySpec, and the spec compiles to SQL.

Design choice (ADR-006): the language model is allowed to *choose* a metric, dimensions
and filters, but metric arithmetic lives here, defined once in the domain's YAML. This
removes the most common text-to-SQL failure (plausible but wrong aggregation) and makes
every number reproducible from the spec.

Everything domain-specific (metrics, dimensions, entity filters, time handling) comes
from domains/<name>/semantic_layer.yaml and glossary.yaml.
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
WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
            "ten": 10, "twelve": 12}


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
        self.entities: dict = cfg.get("entities", {})
        t = cfg.get("time", {})
        col = t.get("column")
        self.time = {
            "year": t.get("year_expr", f"year({col})" if col else None),
            "quarter": t.get("quarter_expr",
                             f"CAST(year({col}) AS VARCHAR) || '-Q' || CAST(quarter({col}) AS VARCHAR)" if col else None),
            "month": t.get("month_expr", f"strftime({col}, '%Y-%m')" if col else None),
        }
        self.recency = t.get("recency")             # {column, unit}
        self.default_recent = t.get("default_recent")
        self.label_expr = t.get("label_expr")       # adds a `period` column to answers
        self.glossary: dict[str, dict[str, list[str]]] = {e["glossary"]: gl.get(e["glossary"], {})
                                                         for e in self.entities.values()}
        self.unavailable: dict[str, list[str]] = gl.get("unavailable", {})

    # ------------------------------------------------------------- compile
    def compile(self, spec: QuerySpec) -> str:
        if spec.metric not in self.metrics:
            raise ValueError(f"unknown metric {spec.metric!r}")
        for d in spec.dimensions:
            if d not in self.dimensions:
                raise ValueError(f"unknown dimension {d!r}")
        m = self.metrics[spec.metric]
        filters = dict(spec.filters)
        time_dims = [d for d in spec.dimensions if d in ("month", "quarter", "year")]
        if (self.default_recent and self.recency and not time_dims
                and not any(k in filters for k in ("year", "quarter", "month", "recent"))):
            filters["recent"] = self.default_recent
        where = self._where(filters)
        if m.get("where"):
            where.append(m["where"])
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        dims = [(d, self.dimensions[d]["sql"]) for d in spec.dimensions]
        label = [] if (time_dims or not self.label_expr) else [f"{self.label_expr} AS period"]

        if m.get("grain"):
            key = m.get("grain_key", "id")
            inner_dims = "".join(f", {expr} AS {name}" for name, expr in dims)
            src = (f"(SELECT {key}{inner_dims}, COUNT(*) AS customer_orders "
                   f"FROM {self.base} {where_sql} GROUP BY ALL) AS g")
            select_dims = [name for name, _ in dims]
            sql = f"SELECT {', '.join(select_dims + [m['sql'] + ' AS ' + spec.metric])} FROM {src}"
            if select_dims:
                sql += f" GROUP BY {', '.join(select_dims)}"
        else:
            select_dims = [f"{expr} AS {name}" for name, expr in dims]
            sql = f"SELECT {', '.join(select_dims + label + [m['sql'] + ' AS ' + spec.metric])} FROM {self.base} {where_sql}"
            if dims:
                # group by the expression, not the alias: an alias can collide with a real column name
                sql += f" GROUP BY {', '.join(expr for _, expr in dims)}"
                mins = [self.dimensions[d].get("min_group_size") for d in spec.dimensions]
                if any(mins):
                    sql += f" HAVING COUNT(*) >= {max(x for x in mins if x)}"

        if spec.order and spec.dimensions:
            sql += f" ORDER BY {spec.metric} {spec.order.upper()} NULLS LAST"
        elif time_dims:
            others = [d for d in spec.dimensions if d not in time_dims]
            sql += f" ORDER BY {', '.join(others + [time_dims[0]])}"
        elif spec.dimensions:
            sql += f" ORDER BY {spec.metric} DESC NULLS LAST"
        if spec.limit:
            sql += f" LIMIT {int(spec.limit)}"
        return " ".join(sql.split())

    def _where(self, f: dict) -> list[str]:
        out = []
        if "year" in f and self.time["year"]:
            out.append(f"{self.time['year']} = {int(f['year'])}")
        if "quarter" in f and self.time["quarter"]:
            q = str(f["quarter"]).replace("'", "")
            out.append(f"{self.time['quarter']} = '{q}'")
        if "month" in f and self.time["month"]:
            out.append(f"{self.time['month']} = '{str(f['month'])[:7]}'")
        if "recent" in f and self.recency:
            out.append(f"{self.recency['column']} <= {int(f['recent'])}")
        for name, ent in self.entities.items():
            if name in f:
                vals = f[name] if isinstance(f[name], list) else [f[name]]
                ok = [v for v in vals if v in self.glossary[ent["glossary"]]]
                if ok:
                    out.append(f"{ent['column']} IN (" + ", ".join(f"'{v}'" for v in ok) + ")")
        return out

    # ------------------------------------------------------------- entity extraction
    def find_entities(self, question: str) -> dict[str, list[str]]:
        """Glossary matching with longest-match-wins: in 'office furniture', the shorter
        synonym 'furniture' (another category) is dropped because its span is covered."""
        q = question.lower()
        hits: list[tuple[int, int, str, str]] = []           # start, end, entity, value
        for name, ent in self.entities.items():
            for value, syns in self.glossary[ent["glossary"]].items():
                for s in syns:
                    cs = ent.get("codes_case_sensitive") and len(s) <= 3 and s.isupper()
                    hay, needle = (question, s) if cs else (q, s.lower())
                    for m in re.finditer(r"(?<![\w])" + re.escape(needle) + r"(?![\w])", hay):
                        hits.append((m.start(), m.end(), name, value))
        hits.sort(key=lambda h: -(h[1] - h[0]))
        kept: list[tuple[int, int, str, str]] = []
        for h in hits:
            if not any(k[0] <= h[0] and h[1] <= k[1] and k[3] != h[3] for k in kept):
                kept.append(h)
        out: dict[str, list[str]] = {}
        for _, _, name, value in sorted(kept):
            out.setdefault(name, [])
            if value not in out[name]:
                out[name].append(value)
        return out

    def unavailable_mentions(self, question: str) -> list[str]:
        q = question.lower()
        return [k for k, syns in self.unavailable.items()
                if any(re.search(r"(?<![\w])" + re.escape(s.lower()) + r"(?![\w])", q) for s in syns)]

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
        qd = re.sub(r"\b(year[ -]over[ -]year|quarter[ -]over[ -]quarter|yoy|qoq)\b", " ", q)   # not dimensions
        for name, d in self.dimensions.items():
            for syn in d["synonyms"]:
                s = re.escape(syn.lower())
                if re.search(rf"\b(by|per|each|which|what|across|every|top \d+|for each|over|of each|by each|compare)\s+(the\s+)?{s}\b", qd) \
                        or re.search(rf"\b{s}\s+(breakdown|wise|split|by)\b", qd) \
                        or (name == "month" and re.search(r"\b(monthly|per month)\b", qd)) \
                        or (name == "quarter" and re.search(r"\b(quarterly|trend|over time|over the (last|past)|quarter by quarter)\b", qd)):
                    if name not in dims:
                        dims.append(name)
                    break
        if "seller_state" in dims and "state" in dims:
            dims.remove("state")
        if "month" in dims and "quarter" in dims:
            dims.remove("quarter")

        filters: dict[str, object] = {}
        m = re.search(r"\b(q[1-4])\s*(?:of\s*|fy\s*|cy\s*)?(20\d\d)\b|\b(20\d\d)\s*-?\s*(q[1-4])\b", q)
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
        if self.recency:
            r = re.search(r"\b(last|past|latest|most recent|previous|trailing|recent)\s+(\d+|"
                          + "|".join(WORD_NUM) + r")?\s*(quarters?|qtrs?|years?)\b", q)
            if r:
                n = r.group(2)
                n = int(n) if n and n.isdigit() else WORD_NUM.get(n or "", 1)
                if r.group(3).startswith("year"):
                    n *= 4
                filters["recent"] = n
                if n > 1 and "quarter" not in dims and not re.search(r"\b(total|combined|sum|overall)\b", q):
                    dims.append("quarter")        # "last 8 quarters" asks for the series

        ents = self.find_entities(question)
        for name, vals in ents.items():
            if name not in dims:
                filters[name] = vals if len(vals) > 1 else vals[0]
        # "compare A and B" across an entity -> group by that entity
        for name, vals in ents.items():
            if len(vals) > 1 and name in self.dimensions and name not in dims:
                dims.insert(0, name)

        order, limit = None, None
        n = re.search(r"\b(top|bottom|best|worst)\s+(\d+)\b", q)
        if n:
            limit = int(n.group(2))
        if re.search(r"\b(highest|most|top|largest|biggest|best|maximum|max|greatest|worst performing|leads?|leading|fastest|strongest)\b", q):
            order = "desc"
        if re.search(r"\b(lowest|least|fewest|smallest|bottom|minimum|min|weakest|slowest)\b", q):
            order = "asc"
        if re.search(r"\bworst\b", q) and self.metrics[best].get("higher_is_better", True):
            order = "asc"
        if re.search(r"\bworst\b", q) and not self.metrics[best].get("higher_is_better", True):
            order = "desc"
        if order and dims and limit is None and re.search(r"\b(which|what|who)\b", q):
            limit = 5
        conf = 0.55 + 0.15 * bool(dims or filters) + 0.1 * (best_len > 4) + 0.1 * bool(order or not dims)
        return QuerySpec(metric=best, dimensions=dims, filters=filters, order=order, limit=limit,
                         confidence=round(min(conf, 0.95), 2))

    def describe(self) -> str:
        lines = ["METRICS:"]
        lines += [f"- {k}: {v['description']}" for k, v in self.metrics.items()]
        lines.append("DIMENSIONS: " + ", ".join(self.dimensions))
        ents = "; ".join(f"{n} (one of {', '.join(self.glossary[e['glossary']])})" for n, e in self.entities.items())
        lines.append("FILTERS: year (int), quarter ('YYYY-Qn'), month ('YYYY-MM')"
                     + (", recent (int: last N periods)" if self.recency else "") + (f", {ents}" if ents else ""))
        return "\n".join(lines)

    def metric_label(self, metric: str) -> str:
        m = self.metrics.get(metric, {})
        if m.get("label"):
            return m["label"]
        return metric.replace("_", " ").replace("avg", "average").capitalize()

    def metric_format(self, metric: str | None) -> str:
        if not metric or metric not in self.metrics:
            return "number"
        fmt = self.metrics[metric].get("format")
        if fmt:
            return fmt
        return "percent" if metric.endswith(("_rate", "_share")) else "number"
