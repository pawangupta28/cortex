# Cortex — domain-agnostic enterprise GenAI platform

Cortex turns an organisation's warehouse data and documents into **cited, number-verified answers** and **governed multi-step agent workflows**, with evaluation, security and tracing built in. Each domain is a pluggable **domain pack**. The retrieval, SQL, agent, security, evaluation and API layers are the same for all of them.

Two packs ship today:

| Domain | Data | What it shows |
| --- | --- | --- |
| **finance** (flagship) | **Real SEC data** for Apple, Microsoft, NVIDIA, Alphabet and Amazon: XBRL financial facts (8.7k), 344 company-quarters, latest 10-K and 10-Q text (1,375 passages) | Messy real-world data engineering, long-document RAG, analytics across companies, live external API tool |
| **ecommerce** | Olist schema: real CSVs if present, otherwise a deterministic synthetic set with planted causes | Text-to-SQL over a multi-table warehouse, review-theme mining, policy documents |

Example finance answer, fully offline, with every number checked against its source:

> **Q: How did NVIDIA's gross margin change over the last 8 quarters and what drove it?**
> Gross margin for NVDA went from 74.6% in 2024-Q3 to 75.0% in 2026-Q2 (+0.4 points) [E2]. The peak was 75.0% in 2025-Q4 [E1]. NVDA 10-Q (2026-08-26) Item 2 MD&A: Gross margin increased to 75.0% for the second quarter of fiscal year 2027 compared to 72.4% … due to improved mix from Blackwell Ultra. Gross margin for the first half of fiscal year 2026 was also impacted by a $4.5 billion charge associated with H20 excess inventory and purchase obligations. [E3]
> *hybrid · confidence 0.96 · all numbers verified*

## Documentation

| Start here | Deep dives | Cross-cutting |
| --- | --- | --- |
| [Architecture](docs/architecture.md): system, request and agent diagrams, design principles | [1 Ingestion & domain packs](docs/layers/01-ingestion-and-domain-packs.md) · [2 Retrieval](docs/layers/02-retrieval.md) · [3 Semantic layer & text-to-SQL](docs/layers/03-semantic-layer-text-to-sql.md) · [4 Context engineering](docs/layers/04-context-engineering.md) · [5 Model gateway](docs/layers/05-model-gateway.md) · [6 Agents](docs/layers/06-agents-orchestration.md) · [7 Verification & uncertainty](docs/layers/07-verification-uncertainty.md) · [8 Integration (MCP, A2A)](docs/layers/08-integration.md) · [9 Observability & LLMOps](docs/layers/09-observability-llmops.md) | [Security](docs/security.md) · [Evaluation](docs/evaluation.md) · [Deployment](docs/deployment.md) · [Roadmap](docs/roadmap.md) · [Design records](docs/adr/) |

Every page marks each component **Built · measured** (in this repo, tested, numbers from the eval reports) or **Designed** (specified, not yet implemented). All measured results so far come from the deterministic offline path; live-LLM runs are roadmap item 1.

## How a question flows

```
question ─► input guard ─► cache ─► planner ─┬─► semantic layer ► SQL guard ► row-level security ► warehouse ─► analyst (computed facts)
                                             └─► hybrid retrieval (BM25 + dense ► RRF ► MMR, section routing) ─► injection quarantine
         ─► context builder (token knapsack, cache-stable order) ─► model gateway (bandit router, fallback, breakers)
         ─► verifier (every number traced to evidence) ─► per-intent conformal confidence ─► answer + citations + trace
```

## Quickstart

```bash
pip install -r requirements.txt

# Finance (real SEC data; free API, no key, ~60 MB):
#   PowerShell: $env:SEC_USER_AGENT="Your Name you@email.com"
export SEC_USER_AGENT="Your Name you@email.com"      # required by the SEC's fair-access policy
python -m cortex.cli --domain finance fetch-sec      # AAPL MSFT NVDA GOOGL AMZN by default
python -m cortex.cli --domain finance init
python -m cortex.cli --domain finance eval
python -m cortex.cli --domain finance serve          # UI + API on http://127.0.0.1:8000

# E-commerce (runs with zero downloads):
python -m cortex.cli --domain ecommerce init --synthetic
python -m cortex.cli --domain ecommerce serve

python -m pytest -q                                  # 37 tests; finance end-to-end skips without SEC data
```

Try:

```bash
python -m cortex.cli --domain finance ask "Which company had the highest operating margin in the latest quarter?"
python -m cortex.cli --domain finance ask "Compare Apple and Microsoft revenue over the last 4 quarters"
python -m cortex.cli --domain finance ask "What does NVIDIA say about export controls?"
python -m cortex.cli --domain finance ask "revenue by company" --role semis_analyst      # row-level security: NVDA only
python -m cortex.cli --domain finance ask "What was Tesla's revenue last quarter?"     # declined: not in the dataset
python -m cortex.cli --domain finance task "Investigate NVIDIA's margins and risks" --approve
```

## Data engineering in the finance pack

Real XBRL data is messy. The pack handles each of these problems explicitly (`domains/finance/pack.py`):

- **Concept drift.** The same line item is tagged differently across companies and years (NVIDIA uses `Revenues`, Apple uses `RevenueFromContractWithCustomerExcludingAssessedTax`). Candidate concepts are ranked by how recently they were reported, and each period takes its value from the best concept that has it.
- **Restatements.** The same period appears in many filings; the latest filing wins.
- **Fiscal Q4 is not reported as a quarter.** It is derived as the full year minus Q1–Q3 and flagged `q4_derived`. EPS isn't additive, so it is not derived for those quarters.
- **Trailing-twelve-month values in 10-Qs** look like annual figures. Only 10-K fiscal-year facts count as years.
- **Different fiscal calendars.** Apple's year ends in September and NVIDIA's in January. Every quarter gets a calendar label (end date minus 40 days, the SEC frame convention) so companies can be compared, and also keeps its fiscal label (`FY2027 Q2`).
- **Filings.** 10-K and 10-Q HTML is parsed into Items. Tables of contents and repeated page headers also contain "Item N." lines, so each item keeps its longest span. Items are chunked by sub-heading with the section path preserved.
- **Independent gold answers.** The test answers come from the SEC's own `frame` labels in the raw JSON, not from Cortex's quarter-alignment code, so an alignment bug fails the test instead of being copied into the expected answer.

## Current evaluation (offline mode)

Full reports: [`evals/finance/REPORT.md`](evals/finance/REPORT.md) and [`evals/ecommerce/REPORT.md`](evals/ecommerce/REPORT.md). These are regression baselines. The question templates were used during development and the holdout splits are small, so test with your own questions before quoting a number.

| Measure | Finance (real SEC data) | E-commerce (synthetic) |
| --- | --- | --- |
| Golden-set accuracy | 97.1% of 70, CI [92.9, 100] | 97.1% of 102, CI [93.1, 100] |
| Numeric answers (values, rankings, trends) | 100% | 100% |
| Document questions | 81.8% (fails: "Where does NVIDIA manufacture its chips?" — vocabulary mismatch with the TSMC passage) | 95.5% |
| Out-of-scope questions declined | 5 / 5 (Tesla, Meta, forecasts, off-topic) | 6 / 6 |
| Prompt injection success, defences off → on | 38% → 0% | 38% → 0% |
| Malicious SQL blocked | 12 / 12 | 12 / 12 |
| Row-level security leaks | 0 | 0 |
| Retrieval recall@3: BM25 / LSA dense / hybrid | 81.8% / 63.6% / 81.8% | 100% / 100% / 100% |
| Latency p50 / p95 (offline) | 28 ms / 59 ms | ~38 ms / ~56 ms |

Router (simulation with known ground truth): 58.7% lower cost per call than always using the frontier model, for 3.5 points less quality, within a fixed budget.

## Layers

| Layer | In this repo | Where |
| --- | --- | --- |
| Domain packs | `domain.yaml` + `semantic_layer.yaml` + `glossary.yaml` + `policies.yaml` + `pack.py` per domain; adding a domain doesn't change core code | `domains/`, `cortex/domain.py` |
| 1 Ingestion | Pack loaders, schema profiler, lineage with checksums, heading-aware chunking | `cortex/ingest/`, `domains/*/pack.py` |
| 2 Retrieval | BM25 (own implementation), LSA dense, weighted RRF, MMR, metadata filters, per-collection indexes, section routing, de-duplication | `cortex/retrieval/index.py`, `agents/pipeline.py` |
| 2 Text-to-SQL | Semantic layer (metrics, entities, time and recency in YAML) → QuerySpec → SQL; LLM spec path; guarded raw-SQL self-correction | `semantic_layer.py`, `text2sql.py` |
| 3 Context | Token-budget knapsack, slot priorities, cache-stable order, manifest | `cortex/context/builder.py` |
| 4 Gateway | OpenAI-compatible adapters, retries, circuit breakers, fallback, shadow cost, offline degradation, bandit router, scoped semantic cache | `cortex/gateway/` |
| 5 Agents | Analyst (computed facts, multi-series trends, log-odds themes), verifier ($/B/M aware), durable state machine with checkpoints, human approval, idempotent saga actions | `cortex/agents/` |
| 5 Protocols | MCP server (SDK 2.x, spec 2026-07-28, stateless HTTP + stdio) including a **live SEC API tool**; A2A-style Agent Card | `cortex/api/` |
| 6 Uncertainty | Logistic confidence, **per-intent (Mondrian) conformal** thresholds, ECE, risk–coverage | `cortex/uncertainty/` |
| 7 Evals | Per-domain golden sets with independent gold answers, bootstrap CIs, paired tests, power analysis, CI gate | `cortex/evals/` |
| 8 Security | SQL guard, row-level security from domain config, injection quarantine + spotlighting, PII redaction, per-role/per-agent tool policy with audit | `cortex/security/` |
| 9 Tracing | OpenTelemetry GenAI attribute names, JSONL traces, optional OTLP export | `cortex/telemetry/` |

## Turning on real LLMs (free tiers)

Copy `.env.example` to `.env` and set any of `GROQ_API_KEY`, `GEMINI_API_KEY` or `OPENROUTER_API_KEY`. Model names change often, so also set the `*_MODEL` variables. With keys set, the planner and query spec come from the model and the deterministic parser cross-checks them. The writer becomes the model, and its numbers are still verified. The bandit routes between providers, using verifier outcomes as its reward.

## Use from Claude Code (MCP)

```bash
claude mcp add cortex -- python -m cortex.cli --domain finance mcp
```

Tools: `ask`, `run_metric`, `search_documents`, `sec_live_quarters`. The last one calls data.sec.gov live and needs `SEC_USER_AGENT`.

## Adding a domain

Copy `domains/finance/`, then:

1. Describe your tables in `domain.yaml` and their row-level security CTEs.
2. Define your metrics, entities and time handling in `semantic_layer.yaml`.
3. List entity names in `glossary.yaml`.
4. Set roles and data scopes in `policies.yaml`.
5. Write `ingest()` and `golden()` in `pack.py`.

Then run `python -m cortex.cli --domain <name> init` and `eval`.

## Not built yet

- Graph retrieval and a real embedding model. `CORTEX_EMBEDDER=st` works where Hugging Face is reachable, and is the likely fix for the TSMC-style miss.
- The LoRA training run (`training/README.md`).
- Keycloak OIDC in place of the `X-Role` header.
- Postgres + pgvector, a Terraform/k3s deployment, and dashboards.
- A2A conformance testing against the official SDK.
- An LLM-as-judge with kappa against human grades.

Design decisions: [`docs/adr/`](docs/adr/).
