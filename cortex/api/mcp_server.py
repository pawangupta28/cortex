"""MCP server exposing Cortex to any MCP client (Claude Code, IDEs, other agents).

Built on the official MCP Python SDK 2.x (spec 2026-07-28 aware), served stateless over
Streamable HTTP so it scales horizontally. Roots, Sampling and Logging are not used: they
are deprecated in this spec revision (ADR-013).

Run standalone:   python -m cortex.api.mcp_server          (stdio, for Claude Code)
Mounted in API:   POST http://localhost:8000/mcp            (streamable HTTP)
"""
from __future__ import annotations

from functools import lru_cache

from mcp.server.mcpserver import MCPServer

from cortex.agents.pipeline import Cortex

mcp = MCPServer(name="cortex", title="Cortex enterprise GenAI platform",
                instructions="Ask business questions over the loaded domain (CORTEX_DOMAIN: ecommerce or finance). "
                             "Answers carry citations and a calibrated confidence; low-confidence questions are declined.")


@lru_cache(maxsize=1)
def _cx() -> Cortex:
    return Cortex()


@mcp.tool(description="Answer a business question with citations and a confidence score.")
def ask(question: str, role: str = "analyst") -> dict:
    a = _cx().ask(question, role=role)
    return {"answer": a.text, "confidence": a.confidence, "abstained": a.abstained, "intent": a.intent.value,
            "sql": a.sql, "citations": [{"label": f"E{i}", "source": e.source or e.id}
                                        for i, e in enumerate(a.evidence, start=1)], "trace_id": a.trace_id}


@mcp.tool(description="Compile a metric question to governed SQL and return rows (read-only, row-level security applied).")
def run_metric(question: str, role: str = "analyst") -> dict:
    cx = _cx()
    cx.policy.check(agent="researcher", tool="run_sql", role=role, args={"q": question})
    res = cx.t2s.run(question, cx.policy.allowed_scope(role))
    if res is None:
        return {"error": "question is outside the semantic layer"}
    return {"sql": res.sql, "columns": res.columns, "rows": [list(r) for r in res.rows[:100]], "spec": res.spec,
            "error": res.error}


@mcp.tool(description="Search the domain's reference documents (store policies, SEC filings, ...). "
                       "Returns the top passages with their source sections.")
def search_documents(query: str, k: int = 3) -> list[dict]:
    cx = _cx()
    hits = cx.corpus[cx.reference[0]].search(query, k=k)
    return [{"source": f"{h.chunk.doc_id}#{h.chunk.section_path}", "text": h.chunk.text, "score": round(h.score, 4)}
            for h in hits]


@mcp.tool(description="LIVE external API: fetch a company's latest quarterly figures straight from the SEC "
                       "(data.sec.gov XBRL companyfacts). Needs internet and SEC_USER_AGENT='Name email'. "
                       "Use for companies or quarters newer than the local warehouse.")
def sec_live_quarters(ticker: str, quarters: int = 4) -> dict:
    import importlib.util
    import json

    from cortex.config import ROOT
    from cortex.connectors import sec_fetch
    if not sec_fetch.UA or "@" not in sec_fetch.UA:
        return {"error": "set SEC_USER_AGENT='Your Name your@email' (SEC fair-access policy)"}
    t = ticker.upper()
    cik = sec_fetch.KNOWN_CIK.get(t)
    if cik is None:
        index = json.loads(sec_fetch.get("https://www.sec.gov/files/company_tickers.json"))
        cik = next((str(v["cik_str"]).zfill(10) for v in index.values() if v["ticker"].upper() == t), None)
    if cik is None:
        return {"error": f"unknown ticker {t}"}
    facts = json.loads(sec_fetch.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"))
    spec = importlib.util.spec_from_file_location("finance_pack", ROOT / "domains" / "finance" / "pack.py")
    pack = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pack)
    rows = pack.quarterly_rows(t, facts.get("entityName", t), facts)[-max(1, min(quarters, 12)):]
    keep = ("fiscal_label", "cal_quarter", "period_end", "revenue", "gross_profit", "operating_income", "net_income",
            "rd_expense", "eps_diluted", "q4_derived")
    return {"ticker": t, "source": f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
            "quarters": [{k: r[k] for k in keep} for r in rows]}


if __name__ == "__main__":
    mcp.run("stdio")
