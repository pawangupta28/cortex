"""Finance golden set with answers taken from an independent source.

Cortex aligns quarters itself (end date - 40 days -> calendar quarter), resolves concepts
by recency and derives fiscal Q4. The gold answers here come instead from the SEC's own
`frame` labels in the raw companyfacts JSON (e.g. CY2026Q2), which the SEC assigns to each
reported 3-month fact. If Cortex mis-aligns a quarter, picks the wrong concept or double
counts a restatement, the answer disagrees with the SEC frame and the test fails.

Document questions are kept only if their key fact actually appears in that company's
filings, so the set adapts when filings are refreshed.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

NAMES = {"AAPL": "Apple", "MSFT": "Microsoft", "NVDA": "NVIDIA", "GOOGL": "Alphabet", "AMZN": "Amazon"}
REV = ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"]
COST = ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"]
FRAME = re.compile(r"^CY(\d{4})Q([1-4])$")


def _split(template_id: str) -> str:
    h = int(hashlib.md5(template_id.encode()).hexdigest(), 16) % 10
    return "dev" if h < 6 else "calib" if h < 8 else "holdout"


def _frames(gaap: dict, concepts: list[str]) -> dict[str, float]:
    """calendar quarter 'YYYY-Qn' -> value, from SEC-assigned quarterly frames."""
    out: dict[str, float] = {}
    for c in concepts:
        for f in gaap.get(c, {}).get("units", {}).get("USD", []):
            m = FRAME.match(f.get("frame") or "")
            if m:
                out.setdefault(f"{m.group(1)}-Q{m.group(2)}", float(f["val"]))
    return out


def build(con, sec_dir: Path) -> list[dict]:
    items: list[dict] = []

    def add(tid: str, typ: str, questions: list[str], gold: dict):
        for i, q in enumerate(questions):
            items.append({"id": f"{tid}#{i}", "template": tid, "type": typ, "question": q, "gold": gold,
                          "split": _split(tid)})

    data = {}
    for t in NAMES:
        p = sec_dir / t / "companyfacts.json"
        if not p.exists():
            continue
        g = json.loads(p.read_text(encoding="utf-8"))["facts"]["us-gaap"]
        rev, cost = _frames(g, REV), _frames(g, COST)
        gp = _frames(g, ["GrossProfit"])
        for q in rev:
            if q not in gp and q in cost:
                gp[q] = rev[q] - cost[q]
        data[t] = {"rev": rev, "gp": gp, "op": _frames(g, ["OperatingIncomeLoss"]), "ni": _frames(g, ["NetIncomeLoss"])}

    def qtxt(q: str) -> str:
        y, n = q.split("-Q")
        return f"Q{n} {y}"

    # 1) point values in the latest SEC-framed quarter, per company
    for t, d in data.items():
        name = NAMES[t]
        latest = max(d["rev"])
        add(f"rev_{t}", "metric_value", [f"What was {name}'s revenue in {qtxt(latest)}?",
                                         f"{name} revenue for calendar {qtxt(latest)}"], {"values": [d["rev"][latest]]})
        if latest in d["gp"]:
            add(f"gm_{t}", "metric_value", [f"What was {name}'s gross margin in {qtxt(latest)}?"],
                {"values": [round(d["gp"][latest] / d["rev"][latest], 4)]})
        if latest in d["op"]:
            add(f"om_{t}", "metric_value", [f"What was {name}'s operating margin in {qtxt(latest)}?",
                                            f"{name} operating margin {qtxt(latest)}"],
                {"values": [round(d["op"][latest] / d["rev"][latest], 4)]})
        if latest in d["ni"]:
            add(f"ni_{t}", "metric_value", [f"What was {name}'s net income in {qtxt(latest)}?"],
                {"values": [d["ni"][latest]]})
        # "last quarter" phrasing, only where the most recent quarter has a direct SEC frame
        rank1 = con.execute("SELECT cal_quarter, q4_derived FROM fin_view WHERE ticker = ? AND period_rank = 1", [t]).fetchone()
        if rank1 and rank1[0] == latest and not rank1[1]:
            add(f"last_rev_{t}", "metric_value", [f"{name} revenue last quarter", f"What was {name}'s latest quarterly revenue?"],
                {"values": [d["rev"][latest]]})
        # 8-quarter trend: first and last points must both have SEC frames
        y, n = map(int, latest.split("-Q"))
        k = y * 4 + n - 1 - 7
        first = f"{k // 4}-Q{k % 4 + 1}"
        if first in d["rev"]:
            add(f"trend_rev_{t}", "metric_trend", [f"{name} revenue over the last 8 quarters",
                                                   f"How has {name}'s quarterly revenue changed over the past 8 quarters?"],
                {"values": [d["rev"][first], d["rev"][latest]]})

    # 2) cross-company rankings in a quarter every company reported directly
    common = sorted(set.intersection(*[set(d["rev"]) for d in data.values()]))
    if common:
        q = common[-1]
        for metric, key, words in (("revenue", "rev", "revenue"), ("gm", "gp", "gross margin"),
                                   ("om", "op", "operating margin")):
            vals = {t: (d[key][q] if key == "rev" else d[key].get(q, 0) / d["rev"][q]) for t, d in data.items()
                    if key == "rev" or q in d[key]}
            top = max(vals, key=vals.get)
            v = vals[top] if key == "rev" else round(vals[top], 4)
            add(f"rank_{metric}_{q}", "metric_rank", [f"Which company had the highest {words} in {qtxt(q)}?",
                                                      f"Top company by {words} in {qtxt(q)}"],
                {"label": top, "values": [v]})

    # 3) document questions, kept only if the fact is in that company's filings
    docs = [
        ("NVDA", "export", ["What does NVIDIA say about export controls?", "How do export restrictions affect NVIDIA?"], "export control"),
        ("NVDA", "foundry", ["Where does NVIDIA manufacture its chips?", "Which foundries does NVIDIA rely on?"], "TSMC"),
        ("AAPL", "tariffs", ["What does Apple say about tariffs?", "How do tariffs affect Apple?"], "tariff"),
        ("MSFT", "openai", ["What does Microsoft say about its relationship with OpenAI?"], "OpenAI"),
        ("GOOGL", "antitrust", ["What antitrust risks does Alphabet describe?", "What does Alphabet say about antitrust?"], "antitrust"),
        ("AMZN", "aws", ["What does Amazon say about AWS?", "How does Amazon describe its AWS segment?"], "AWS"),
    ]
    for t, tid, qs, fact in docs:
        n = con.execute("SELECT COUNT(*) FROM chunks WHERE json_extract_string(metadata, '$.ticker') = ? AND text ILIKE ?",
                        [t, f"%{fact}%"]).fetchone()[0]
        if n:
            add(f"doc_{t}_{tid}", "docs", qs, {"ticker": t, "fact": fact})

    # 4) hybrid: a number plus management's explanation from MD&A
    hybrid = [
        ("NVDA", ["How did NVIDIA's gross margin change over the last 8 quarters and what drove it?",
                  "What drove NVIDIA's gross margin change?"], ["blackwell", "mix", "h20", "inventory"]),
        ("MSFT", ["Why did Microsoft's revenue grow over the last 4 quarters?",
                  "What drove Microsoft's revenue growth?"], ["azure", "cloud", "ai"]),
        ("AMZN", ["What drove Amazon's operating income over the last 4 quarters?"], ["aws", "north america", "international"]),
        ("GOOGL", ["Why did Alphabet's revenue change over the last 4 quarters?"], ["cloud", "search", "youtube", "advertising"]),
    ]
    for t, qs, words in hybrid:
        add(f"why_{t}", "hybrid", qs, {"any_of": words, "ticker": t})

    for i, q in enumerate(["What was Tesla's revenue last quarter?", "What was Meta's net income in Q2 2026?",
                           "What will NVIDIA's revenue be next year?", "What is the capital of France?",
                           "How many subscribers does Netflix have?"]):
        add(f"unanswerable_{i}", "unanswerable", [q], {"abstain": True})
    return items
