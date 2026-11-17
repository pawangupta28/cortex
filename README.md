# Cortex — enterprise GenAI platform (MVP)

Cortex turns a company's warehouse data, documents and customer text into **cited, verified answers** and **governed multi-step agent workflows**. It ships with evaluation, security and tracing built in. This MVP runs the e-commerce domain end to end, **fully offline with no API keys**. It switches to free LLM APIs (Groq, Gemini, OpenRouter) when you add keys.

```
question ─► input guard ─► cache ─► planner ─┬─► text-to-SQL (semantic layer ► SQL guard ► RLS) ─► analyst (computed facts)
                                             └─► hybrid retrieval (BM25 + dense ► RRF ► MMR) ─► injection quarantine
         ─► context builder (token knapsack, cache-stable order) ─► model gateway (bandit router, fallback, breakers)
         ─► verifier (every number traced to evidence) ─► conformal confidence ─► answer + citations + trace
```

## Quickstart

```bash
pip install -r requirements.txt
python -m cortex.cli init --synthetic      # data, warehouse view, chunks, indexes (~15 s)
python -m cortex.cli eval                  # evaluation lab → evals/REPORT.md
python -m cortex.cli serve                 # UI + API on http://127.0.0.1:8000
python -m pytest -q                        # 31 tests, offline and deterministic
```

**Windows (PowerShell):** run every command from inside the unzipped `cortex` folder, the one that contains `requirements.txt`:

```powershell
cd $HOME\Downloads\cortex-mvp\cortex      # adjust to wherever requirements.txt is
python -m pip install -r requirements.txt
python -m cortex.cli init --synthetic
python -m cortex.cli serve
```

Try in the CLI:

```bash
python -m cortex.cli ask "Which product categories have the highest late delivery rate?"
python -m cortex.cli ask "Why do customers complain about auto parts?"
python -m cortex.cli ask "revenue by state" --role regional_manager_sp     # row-level security
python -m cortex.cli task "Investigate why office furniture customers are unhappy" --approve
```

Docker: `docker compose up --build`. CI: `.github/workflows/ci.yml` runs lint, tests and the eval gate.

## What is built

| Layer | What runs in this MVP | Where |
| --- | --- | --- |
| 1 Ingestion | Olist-schema loader (real CSVs drop in), deterministic synthetic generator with planted causes, schema profiler, lineage + checksums, heading-aware chunking | `cortex/ingest/` |
| 2 Retrieval | BM25 (own implementation), LSA dense vectors, weighted reciprocal rank fusion, MMR, metadata filters, one index per collection | `cortex/retrieval/index.py` |
| 2 Text-to-SQL | Semantic layer (metrics in YAML) → QuerySpec → compiled SQL; LLM spec path + raw-SQL self-correction loop when keys are set | `semantic_layer.py`, `text2sql.py` |
| 3 Context | Token-budget knapsack, slot priorities, cache-stable ordering, manifest of kept/evicted items | `cortex/context/builder.py` |
| 4 Gateway | OpenAI-compatible adapters, retries with jitter, circuit breakers, fallback chain, shadow-cost accounting, offline degradation | `cortex/gateway/gateway.py` |
| 4 Router | Linear Thompson-sampling contextual bandit with Lagrangian cost budget, plus a ground-truth simulation | `cortex/gateway/router.py` |
| 4 Cache | Semantic cache keyed by role and data version; exact spec keys for metrics; literals + Jaccard guard for text | `cortex/gateway/cache.py` |
| 5 Agents | Analyst (computed facts, weighted log-odds themes), verifier, durable state machine with checkpoints, crash-resume, budgets, loop detection, human approval, idempotent actor, saga rollback | `cortex/agents/` |
| 5 Protocols | MCP server (SDK 2.x, negotiates spec 2026-07-28, stateless Streamable HTTP + stdio); A2A-style Agent Card and `message/send` endpoint | `cortex/api/` |
| 6 Uncertainty | Signal combiner (logistic, fitted on dev), per-intent (Mondrian) split-conformal thresholds, ECE, risk–coverage | `cortex/uncertainty/` |
| 7 Evals | 102-question golden set with gold SQL on base tables, bootstrap CIs, paired tests, McNemar, kappa, power analysis, CI gate | `cortex/evals/` |
| 8 Security | SQL guard (SELECT-only, allow-list, no file/extension functions), row-level security via shadowing CTEs, injection detector + quarantine + spotlighting, PII redaction, per-agent/per-role tool policy with audit log | `cortex/security/` |
| 9 Tracing | Spans with OpenTelemetry GenAI attribute names in `artifacts/traces.jsonl`; optional OTLP export | `cortex/telemetry/` |

## Current evaluation (offline mode, synthetic data)

Full report: [`evals/REPORT.md`](evals/REPORT.md). **These are development numbers.** The data is synthetic, the question templates were used while fixing the parser, and the holdout is about 20 questions. Re-run on the real Olist data before quoting anything.

| Measure | Result |
| --- | --- |
| Golden-set accuracy (102 questions) | 98.0%, 95% CI [95.1%, 100%] |
| Hybrid "why" questions | 83.3% (2 of 12 miss the planted theme) |
| Unanswerable questions correctly declined | 6 / 6 |
| Prompt-injection attack success, defences off → on | 38% → 0% (8 cases) |
| Malicious SQL blocked by guard | 12 / 12 |
| Row-level security leaks | 0 |
| Cache precision on paraphrase pairs: embeddings only → production keys | 4% → 100% |
| Router cost per call vs always-frontier (simulation) | −58.7%, quality −3.5 pts |
| Conformal abstention on the holdout (~18 answers) | kept 88% of correct answers vs a 90% target; no error reduction at this sample size |
| Latency p50 / p95 (offline) | ~38 ms / ~56 ms |

## Turning on real LLMs (free tiers)

Copy `.env.example` to `.env` and set any of `GROQ_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY`. Model names change often, so set `*_MODEL` to a current free model. With keys set:

- the planner and the QuerySpec come from the model, and the deterministic parser cross-checks them;
- raw SQL is allowed outside the semantic layer, but still has to pass the guard;
- the writer is the model, and its numbers are verified, with one rewrite allowed;
- the bandit routes between providers, using verifier outcomes as the reward.

## Real Olist data

Download the [Olist Brazilian e-commerce dataset](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce). Put its CSVs in `data/raw/olist/`, then run `python -m cortex.cli init` (without `--synthetic`) and `eval`. Review text in the real data is Portuguese, so expect theme extraction and the review glossary to need Portuguese terms.

## Use from Claude Code (MCP)

```bash
claude mcp add cortex -- python -m cortex.cli mcp      # stdio, run from the repo root
# or HTTP: the API mounts MCP at http://127.0.0.1:8000/mcp/
```

Tools: `ask`, `run_metric`, `search_policies`.

## API

| Method and path | Purpose |
| --- | --- |
| `POST /v1/ask` (header `X-Role`) | Question → answer, citations, confidence, SQL, signals, trace id |
| `POST /v1/tasks` · `GET /v1/tasks/{id}` · `POST /v1/tasks/{id}/decision` | Agent task, report, approval |
| `GET /v1/traces/{trace_id}` | Span tree for one answer |
| `GET /v1/evals/latest` · `GET /v1/router/simulation` | Eval report, router study |
| `GET /.well-known/agent-card.json` · `POST /a2a` | A2A-style discovery and `message/send` |
| `/mcp/` | MCP Streamable HTTP (stateless) |

## Not built yet (blueprint weeks 2–4)

- **Finance and legal domains.** EDGAR/XBRL and CUAD packs.
- **Graph retrieval and better embeddings.** Graph retrieval with Leiden communities, and a real embedding model. `CORTEX_EMBEDDER=st` already works where Hugging Face is reachable.
- **Fine-tuned small model.** The LoRA training run (`training/README.md`).
- **Real login.** Keycloak OIDC in place of the `X-Role` header.
- **Deployment.** Postgres + pgvector, Terraform/k3s deploy, Langfuse/Grafana dashboards.
- **A2A conformance.** The A2A endpoint is a minimal subset; validate it against the official SDK before claiming conformance.
- **LLM judge.** An LLM-as-judge with kappa against human grades. `cohen_kappa` exists; the judge does not.

Design decisions: [`docs/adr/`](docs/adr/).
