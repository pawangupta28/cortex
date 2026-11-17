"""Finance domain pack: real SEC filings (10-K / 10-Q text) and XBRL financial facts.

Data: `python -m cortex.cli fetch-sec` downloads, per company, the SEC companyfacts JSON
(every XBRL fact the company reported) and its latest 10-K and 10-Q documents into
data/raw/sec/<TICKER>/. Nothing here is synthetic.

What this pack does that is not obvious:
  * Concept resolution: companies tag the same line item with different XBRL concepts
    (NVIDIA "Revenues", Apple "RevenueFromContractWithCustomerExcludingAssessedTax"), and
    switch concepts over the years. Per metric we rank candidate concepts by recency and
    take, for each period, the value from the best concept that reports it.
  * Restatements: the same period appears in many filings; the latest filing wins.
  * Fiscal Q4 is usually not reported as a quarter, only inside the annual total, so it
    is derived as FY - (Q1 + Q2 + Q3) and flagged `q4_derived`. EPS is not additive, so
    derived quarters have no EPS.
  * Calendar alignment: fiscal years differ (Apple ends in September, NVIDIA in January),
    so every quarter also gets a calendar label from its end date, comparable across firms.
  * Filings: 10-K / 10-Q HTML is converted to text, split into Items (Risk Factors, MD&A,
    Business) by heading structure, and chunked by paragraph with the item path kept.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import date, timedelta
from html.parser import HTMLParser
from pathlib import Path

from cortex.config import ROOT, settings
from cortex.core.models import Chunk
from cortex.ingest.chunker import chunk_markdown

HERE = Path(__file__).resolve().parent

CONCEPTS = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet",
                "RevenueFromContractWithCustomerIncludingAssessedTax"],
    "cost_of_revenue": ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss"],
    "rd_expense": ["ResearchAndDevelopmentExpense", "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost"],
    "eps_diluted": ["EarningsPerShareDiluted"],
}
ADDITIVE = {"revenue", "cost_of_revenue", "gross_profit", "operating_income", "net_income", "rd_expense"}
ITEMS_10K = {"1": "Item 1 Business", "1a": "Item 1A Risk Factors", "7": "Item 7 MD&A"}
ITEMS_10Q = {"2": "Item 2 MD&A", "1a": "Item 1A Risk Factors"}


def _d(s: str) -> date:
    return date.fromisoformat(s)


# ------------------------------------------------------------------ XBRL -> quarterly table
def _metric_series(gaap: dict, metric: str) -> dict[tuple[str, str], dict]:
    """(start, end) -> fact, choosing among candidate concepts by recency, latest filing wins."""
    cands = []
    for concept in CONCEPTS[metric]:
        if concept in gaap:
            unit = "USD/shares" if metric == "eps_diluted" else "USD"
            facts = gaap[concept]["units"].get(unit, [])
            if facts:
                cands.append((max(f["end"] for f in facts), concept, facts))
    cands.sort(key=lambda c: c[0], reverse=True)          # most recently reported concept first
    out: dict[tuple[str, str], dict] = {}
    for _, concept, facts in cands:
        best: dict[tuple[str, str], dict] = {}
        for f in facts:
            if "start" not in f or f.get("form") not in ("10-K", "10-Q", "10-K/A", "10-Q/A"):
                continue
            key = (f["start"], f["end"])
            if key not in best or f["filed"] > best[key]["filed"]:
                best[key] = {**f, "concept": concept}
        for key, f in best.items():
            out.setdefault(key, f)                         # earlier (more recent) concept has priority
    return out


def cal_quarter(end: date) -> tuple[int, int]:
    """Calendar quarter a fiscal quarter mostly falls in (SEC 'frame' convention):
    the quarter containing end - 40 days. NVIDIA's quarter ending 2026-07-26 -> 2026-Q2."""
    mid = end - timedelta(days=40)
    return mid.year, (mid.month - 1) // 3 + 1


def quarterly_rows(ticker: str, company: str, facts_json: dict) -> list[dict]:
    gaap = facts_json["facts"].get("us-gaap", {})
    series = {m: _metric_series(gaap, m) for m in CONCEPTS}
    quarters: dict[tuple[str, str], dict] = {}
    annuals: dict[tuple[str, str], dict] = {}
    for m, s in series.items():
        for (st, en), f in s.items():
            days = (_d(en) - _d(st)).days + 1
            # annual = a fiscal year from a 10-K; 10-Qs also carry trailing-twelve-month figures, which are not fiscal years
            is_fy = 350 <= days <= 380 and f.get("form", "").startswith("10-K") and f.get("fp") == "FY"
            bucket = quarters if 80 <= days <= 100 else annuals if is_fy else None
            if bucket is not None:
                bucket.setdefault((st, en), {})[m] = f

    # derive fiscal Q4 = FY - (Q1 + Q2 + Q3) where Q4 is not reported directly
    for (ast, aen), afacts in annuals.items():
        inside = sorted(k for k in quarters if _d(ast) <= _d(k[0]) and _d(k[1]) <= _d(aen))
        if len(inside) == 3 and not any(k[1] == aen for k in quarters):
            q4_start = (_d(inside[-1][1]) + timedelta(days=1)).isoformat()
            row = {}
            for m in ADDITIVE:
                if m in afacts and all(m in quarters[k] for k in inside):
                    val = afacts[m]["val"] - sum(quarters[k][m]["val"] for k in inside)
                    row[m] = {"val": val, "concept": afacts[m]["concept"], "accn": afacts[m]["accn"],
                              "filed": afacts[m]["filed"], "derived": True}
            if row:
                quarters[(q4_start, aen)] = row

    # one quarter per end date (rare duplicates with a different start: keep the shorter, i.e. later start)
    by_end: dict[str, tuple[str, str]] = {}
    for k in quarters:
        if k[1] not in by_end or k[0] > by_end[k[1]][0]:
            by_end[k[1]] = k
    quarters = {k: quarters[k] for k in by_end.values()}

    # fiscal labels: quarters inside each fiscal year are Q1..Q4, FY named by its end year
    fy_ends = sorted(_d(k[1]) for k in annuals)
    rows = []
    for (st, en), fs in sorted(quarters.items(), key=lambda kv: kv[0][1]):
        end = _d(en)
        fy_end = next((e for e in fy_ends if e >= end - timedelta(days=5)), None)
        if fy_end is None:                                 # current fiscal year, not closed yet
            last = fy_ends[-1] if fy_ends else end
            fy_year = last.year + 1
            q_index = sum(1 for e2 in {k[1] for k in quarters} if last < _d(e2) <= end)
        else:
            fy_year = fy_end.year
            prev = max((e for e in fy_ends if e < fy_end - timedelta(days=5)), default=fy_end - timedelta(days=365))
            q_index = sum(1 for e2 in {k[1] for k in quarters} if prev < _d(e2) <= end)
        cy, cq = cal_quarter(end)
        val = {m: (fs[m]["val"] if m in fs else None) for m in CONCEPTS}
        if val["gross_profit"] is None and val["revenue"] is not None and val["cost_of_revenue"] is not None:
            val["gross_profit"] = val["revenue"] - val["cost_of_revenue"]
        if val["revenue"] is None:
            continue
        rows.append({"ticker": ticker, "company": company, "period_start": st, "period_end": en,
                     "cal_quarter": f"{cy}-Q{cq}", "cal_year": cy,
                     "fiscal_label": f"FY{fy_year} Q{min(max(q_index, 1), 4)}",
                     **val, "q4_derived": any(f.get("derived") for f in fs.values()),
                     "concepts": ",".join(sorted({f["concept"] for f in fs.values()})),
                     "accn": max((f.get("accn", "") for f in fs.values()), default="")})
    return rows


# ------------------------------------------------------------------ filings -> sections -> chunks
class _Text(HTMLParser):
    BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "table"}

    def __init__(self):
        super().__init__()
        self.out: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style") or tag.startswith("ix:header"):
            self.skip += 1
        if tag in self.BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") or tag.startswith("ix:header"):
            self.skip = max(0, self.skip - 1)
        if tag in self.BLOCK:
            self.out.append("\n")
        elif tag == "td":
            self.out.append(" | ")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def html_to_text(raw: str) -> str:
    p = _Text()
    p.feed(raw)
    t = html.unescape("".join(p.out)).replace("’", "'").replace("“", '"').replace("”", '"')
    t = re.sub(r"[ \t\xa0​]+", " ", t)
    t = re.sub(r" *\| *(\| *)+", " | ", t)
    t = re.sub(r"\n\s*\n+", "\n\n", t)
    return t


ITEM_HEAD = re.compile(r"(?im)^[ |]*item\s+(\d{1,2}[a-c]?)\s*[\.:\-]")


def split_items(text: str) -> dict[str, str]:
    """Split a filing into Items. Tables of contents and repeated page headers also contain
    'Item N.' lines, so boundaries are only where the item number changes, and for each
    item the longest span wins (the body, not the TOC entry)."""
    marks = [(m.start(), m.group(1).lower()) for m in ITEM_HEAD.finditer(text)]
    merged: list[tuple[int, str]] = []
    for pos, item in marks:
        if not merged or merged[-1][1] != item:
            merged.append((pos, item))
    best: dict[str, str] = {}
    for i, (pos, item) in enumerate(merged):
        end = merged[i + 1][0] if i + 1 < len(merged) else len(text)
        body = text[pos:end]
        if len(body) > len(best.get(item, "")):
            best[item] = body
    return best


def _is_heading(line: str) -> bool:
    w = line.split()
    return 1 <= len(w) <= 10 and not line.rstrip().endswith((".", ":", ";", ",")) and "|" not in line \
        and line[:1].isupper() and not re.fullmatch(r"[\d\s$%(),.\-]+", line)


def filing_chunks(ticker: str, form: str, filed: str, url: str, raw_html: str) -> list[Chunk]:
    text = html_to_text(raw_html)
    items = split_items(text)
    wanted = ITEMS_10K if form == "10-K" else ITEMS_10Q
    chunks: list[Chunk] = []
    for key, title in wanted.items():
        body = items.get(key)
        if not body or len(body) < 2000:
            continue
        # turn short title-like lines into markdown headings, so the structure-aware chunker keeps the path
        md_lines = []
        for line in body.splitlines()[1:]:
            s = line.strip()
            if not s or s.lower() in ("table of contents",) or re.fullmatch(r"\d{1,3}", s):
                continue
            md_lines.append(f"## {s}" if _is_heading(s) else s)
            md_lines.append("")
        doc_id = f"{ticker}-{form}-{filed}"
        meta = {"type": "filing", "ticker": ticker, "form": form, "filed": filed, "item": title, "url": url}
        for c in chunk_markdown(doc_id, f"# {ticker} {form} ({filed}) {title}\n\n" + "\n".join(md_lines),
                                max_words=180, metadata=meta):
            if len(c.text.split()) >= 25:                  # drop orphan fragments
                chunks.append(c)
    return chunks


# ------------------------------------------------------------------ pack hooks
def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def ingest(con, domain, force_synthetic: bool = False):
    src = settings.data_dir / "raw" / "sec"
    tickers = sorted(p.name for p in src.iterdir() if (p / "companyfacts.json").exists()) if src.exists() else []
    if not tickers:
        raise FileNotFoundError("No SEC data found. Run: python -m cortex.cli --domain finance fetch-sec")
    q_rows, fact_rows, filings, companies, chunks, lineage = [], [], [], [], [], {}
    for t in tickers:
        d = src / t
        cf = json.loads((d / "companyfacts.json").read_text(encoding="utf-8"))
        name = cf.get("entityName", t)
        companies.append((t, str(cf.get("cik", "")), name))
        q_rows += quarterly_rows(t, name, cf)
        for concept, body in cf["facts"].get("us-gaap", {}).items():
            if not any(concept in v for v in CONCEPTS.values()):
                continue
            for unit, facts in body["units"].items():
                for f in facts:
                    fact_rows.append((t, concept, unit, f.get("start"), f["end"], float(f["val"]), f.get("fy"),
                                      f.get("fp"), f.get("form"), f.get("filed"), f.get("accn"), f.get("frame")))
        lineage[f"{t}/companyfacts.json"] = _sha(d / "companyfacts.json")
        subs = json.loads((d / "submissions.json").read_text(encoding="utf-8")) if (d / "submissions.json").exists() else {}
        recent = subs.get("filings", {}).get("recent", {})
        for path in sorted(d.glob("10-[KQ]_*.htm")):
            form, filed = path.stem.split("_")
            url = ""
            if recent:
                for i, f in enumerate(recent.get("form", [])):
                    if f == form and recent["filingDate"][i] == filed:
                        acc = recent["accessionNumber"][i].replace("-", "")
                        url = f"https://www.sec.gov/Archives/edgar/data/{int(cf.get('cik', 0))}/{acc}/{recent['primaryDocument'][i]}"
                        break
            raw = path.read_text(encoding="utf-8", errors="ignore")
            fc = filing_chunks(t, form, filed, url, raw)
            chunks += fc
            filings.append((t, form, filed, url, len(fc)))
            lineage[f"{t}/{path.name}"] = _sha(path)

    con.execute("CREATE TABLE companies (ticker VARCHAR, cik VARCHAR, name VARCHAR)")
    con.executemany("INSERT INTO companies VALUES (?, ?, ?)", companies)
    con.execute("""CREATE TABLE facts (ticker VARCHAR, concept VARCHAR, unit VARCHAR, period_start DATE, period_end DATE,
                   val DOUBLE, fy INTEGER, fp VARCHAR, form VARCHAR, filed DATE, accn VARCHAR, frame VARCHAR)""")
    con.executemany("INSERT INTO facts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", fact_rows)
    cols = ["ticker", "company", "period_start", "period_end", "cal_quarter", "cal_year", "fiscal_label",
            *CONCEPTS.keys(), "q4_derived", "concepts", "accn"]
    con.execute("""CREATE TABLE fin_quarterly (ticker VARCHAR, company VARCHAR, period_start DATE, period_end DATE,
                   cal_quarter VARCHAR, cal_year INTEGER, fiscal_label VARCHAR, revenue DOUBLE, cost_of_revenue DOUBLE,
                   gross_profit DOUBLE, operating_income DOUBLE, net_income DOUBLE, rd_expense DOUBLE,
                   eps_diluted DOUBLE, q4_derived BOOLEAN, concepts VARCHAR, accn VARCHAR)""")
    con.executemany(f"INSERT INTO fin_quarterly VALUES ({', '.join('?' * len(cols))})",
                    [tuple(r[c] for c in cols) for r in q_rows])
    con.execute("""CREATE OR REPLACE VIEW fin_view AS
        SELECT *, row_number() OVER (PARTITION BY ticker ORDER BY period_end DESC) AS period_rank,
               revenue / NULLIF(lag(revenue, 4) OVER (PARTITION BY ticker ORDER BY period_end), 0) - 1 AS revenue_yoy
        FROM fin_quarterly""")
    con.execute("CREATE TABLE filings (ticker VARCHAR, form VARCHAR, filed DATE, url VARCHAR, chunks INTEGER)")
    con.executemany("INSERT INTO filings VALUES (?, ?, ?, ?, ?)", filings)

    for md in sorted((domain.dir / "docs").glob("*.md")):      # third-party notes (one has a planted injection)
        meta = {"type": "filing", "ticker": md.stem.split("_")[0].upper(), "form": "note", "item": "Analyst note",
                "source": f"docs/{md.name}"}
        chunks += chunk_markdown(md.stem, md.read_text(encoding="utf-8"), metadata=meta)
    return chunks, {"source": "sec-edgar", "tickers": tickers, "files": lineage}


def golden(con, domain) -> list[dict]:
    import importlib.util
    spec = importlib.util.spec_from_file_location("finance_golden", HERE / "golden_build.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.build(con, settings.data_dir / "raw" / "sec")


__all__ = ["ingest", "golden", "quarterly_rows", "filing_chunks", "split_items", "cal_quarter", "ROOT"]
