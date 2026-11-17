"""Command line:  python -m cortex.cli <command>

  init        generate/load data, build the warehouse view, chunks and indexes
  ask "..."   ask one question (add --role regional_manager_sp to see row-level security)
  task "..."  run a multi-step investigation; --approve to approve its action
  eval        run the evaluation lab and write evals/REPORT.md  (--gate to fail on regression)
  serve       start the API + UI on http://localhost:8000
  mcp         run the MCP server over stdio (for Claude Code / IDEs)
"""
from __future__ import annotations

import argparse
import json
import sys


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):        # Windows consoles default to cp1252
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(prog="cortex")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("init")
    s.add_argument("--synthetic", action="store_true", help="force synthetic data even if Olist CSVs exist")
    s = sub.add_parser("ask")
    s.add_argument("question")
    s.add_argument("--role", default="analyst")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("task")
    s.add_argument("goal")
    s.add_argument("--approve", action="store_true")
    s = sub.add_parser("eval")
    s.add_argument("--gate", action="store_true")
    s.add_argument("--save-baseline", action="store_true")
    s = sub.add_parser("serve")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--host", default="127.0.0.1")
    sub.add_parser("mcp")
    a = p.parse_args(argv)

    if a.cmd == "init":
        from cortex.ingest.loader import ingest
        from cortex.retrieval.index import build_index
        stats = ingest(force_synthetic=a.synthetic)
        corpus = build_index()
        print(json.dumps({**stats, "collections": {k: len(v.chunks) for k, v in corpus.collections.items()}}, indent=2))
        return 0
    if a.cmd == "ask":
        from cortex.agents.pipeline import Cortex
        ans = Cortex().ask(a.question, role=a.role)
        if a.json:
            print(ans.model_dump_json(indent=2))
        else:
            print(ans.text)
            print(f"\n[{ans.intent.value} · confidence {ans.confidence} · {'abstained' if ans.abstained else 'answered'} · "
                  f"{ans.model} · {ans.latency_ms:.0f} ms · trace {ans.trace_id}]")
            for i, e in enumerate(ans.evidence, start=1):
                print(f"  E{i}: {e.kind} {e.source or e.id}")
        return 0
    if a.cmd == "task":
        from cortex.agents.orchestrator import Orchestrator
        o = Orchestrator()
        t = o.run(o.create(a.goal)["id"])
        print(t["report"] or "")
        print(f"\nstate: {t['state']}  {t['error'] or ''}")
        if a.approve and t["state"] == "awaiting_approval":
            t = o.decide(t["id"], "cli-user", True)
            print(f"approved -> {t['state']}; CRM tickets: {len(o.tickets())}")
        return 0
    if a.cmd == "eval":
        from cortex.config import ROOT
        from cortex.evals.harness import gate, run
        r = run()
        print((ROOT / "evals" / "REPORT.md").read_text(encoding="utf-8"))
        if a.save_baseline:
            (ROOT / "evals" / "baseline.json").write_text(encoding="utf-8", data=json.dumps(
                {"accuracy_overall": r["accuracy_overall"][0], "asr_defences_on": r["redteam"]["defences_on"]["asr"]}, indent=2))
            print("baseline saved")
        if a.gate:
            problems = gate(r)
            for x in problems:
                print("GATE FAIL:", x)
            return 1 if problems else 0
        return 0
    if a.cmd == "serve":
        import uvicorn
        uvicorn.run("cortex.api.app:app", host=a.host, port=a.port)
        return 0
    if a.cmd == "mcp":
        from cortex.api.mcp_server import mcp
        mcp.run("stdio")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
