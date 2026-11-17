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
                instructions="Ask business questions about orders, revenue, deliveries, reviews and store policies. "
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
    res = cx.t2s.run(question, cx.policy.allowed_states(role))
    if res is None:
        return {"error": "question is outside the semantic layer"}
    return {"sql": res.sql, "columns": res.columns, "rows": [list(r) for r in res.rows[:100]], "spec": res.spec,
            "error": res.error}


@mcp.tool(description="Search store policy documents. Returns the top passages with their source sections.")
def search_policies(query: str, k: int = 3) -> list[dict]:
    hits = _cx().corpus["policy"].search(query, k=k)
    return [{"source": f"{h.chunk.doc_id}#{h.chunk.section_path}", "text": h.chunk.text, "score": round(h.score, 4)}
            for h in hits]


if __name__ == "__main__":
    mcp.run("stdio")
