"""Command line:  python -m cortex.cli <command>

  --domain X  pick a domain pack (ecommerce, finance, ...); default from CORTEX_DOMAIN
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


def doctor() -> int:
    """One tiny request per configured provider; prints the real reason when one fails. Never prints keys."""
    import os
    from cortex.config import _env, _key, settings
    from cortex.gateway.gateway import OpenAICompatProvider, ProviderError
    for name in ("GROQ", "GEMINI", "OPENROUTER"):
        raw = _env(f"{name}_API_KEY").strip()
        state = "set" if _key(f"{name}_API_KEY") else ("PLACEHOLDER (replace it)" if raw else "not set")
        print(f"{name}_API_KEY: {state}")
    provs = settings.providers()
    if not provs:
        print("No live provider configured: Cortex will run offline. Put a real key in .env.")
        return 1
    os.environ["CORTEX_MIN_CALL_INTERVAL"] = "0"
    bad = 0
    for cfg in provs:
        p = OpenAICompatProvider(cfg, timeout=30)
        try:
            res = p.chat([{"role": "user", "content": "Reply with the single word OK."}], max_tokens=5)
            print(f"  OK    {cfg.name:<11} {cfg.model}  ({res.latency_s * 1000:.0f} ms, reply {res.text.strip()[:20]!r})")
        except ProviderError as e:
            bad += 1
            print(f"  FAIL  {cfg.name:<11} {cfg.model}  -> {str(e)[:300]}")
            if "404" in str(e) or "model" in str(e).lower():
                try:   # the model id was retired or mistyped: show what this key can use
                    r = p.client.get(f"{cfg.base_url}/models", headers={"Authorization": f"Bearer {cfg.api_key}"})
                    ids = sorted(m.get("id", "") for m in r.json().get("data", []))
                    skip = ("whisper", "tts", "guard", "embed", "orpheus", "playai", "distil")
                    ids = [i for i in ids if not any(k in i.lower() for k in skip)]
                    if cfg.name == "openrouter":
                        ids = [i for i in ids if i.endswith(":free")]
                    print(f"        models available to this key ({len(ids)}): " + ", ".join(ids[:40]))
                    print(f"        set {cfg.name.upper()}_MODEL in .env to one of these")
                except Exception as e2:  # noqa: BLE001
                    print(f"        could not list models: {e2}")
    return 1 if bad == len(provs) else 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):        # Windows consoles default to cp1252
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    from cortex.config import available_domains, settings
    p = argparse.ArgumentParser(prog="cortex")
    p.add_argument("--domain", default=None, help=f"domain pack: {', '.join(available_domains())}")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("domains")
    s = sub.add_parser("fetch-sec", help="download SEC filings for the finance domain (needs internet)")
    s.add_argument("tickers", nargs="*")
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
    s.add_argument("--sample", type=int, default=None, help="stratified sample size (free-tier rate limits)")
    s.add_argument("--save-baseline", action="store_true")
    s = sub.add_parser("serve")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--host", default="127.0.0.1")
    sub.add_parser("mcp")
    sub.add_parser("doctor", help="check each configured LLM provider with one tiny request")
    a = p.parse_args(argv)
    if a.domain:
        settings.use_domain(a.domain)
        import os
        os.environ["CORTEX_DOMAIN"] = a.domain      # for the API server process

    if a.cmd == "domains":
        for d in available_domains():
            print(("* " if d == settings.domain else "  ") + d)
        return 0
    if a.cmd == "fetch-sec":
        from cortex.connectors.sec_fetch import DEFAULT
        from cortex.connectors.sec_fetch import main as fetch
        fetch(a.tickers or DEFAULT)
        return 0

    if a.cmd == "doctor":
        return doctor()

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
        from cortex.evals.harness import eval_dir, gate, run
        r = run(sample=a.sample)
        name = "REPORT.llm.md" if r["mode"] == "online" else "REPORT.md"
        print((eval_dir() / name).read_text(encoding="utf-8"))
        if r["mode"] == "online" and not r["llm"].get("fully_live_questions"):
            print("\nWARNING: live mode, but no question was answered by a live model (every call fell back offline).")
            for prov, err in r["llm"].get("provider_errors", {}).items():
                print(f"  {prov}: {err}")
            print("Run `python -m cortex.cli doctor` to test each provider. These numbers are NOT live-model numbers.")
            return 2
        if a.save_baseline:
            (eval_dir() / "baseline.json").write_text(encoding="utf-8", data=json.dumps(
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
