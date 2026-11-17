# 9. Observability and LLMOps

**Status: Built** (tracing, audit, eval gate) · **Designed** (dashboards, staged rollout)

## Tracing
Every request produces a span tree: `cortex.ask` → `cortex.plan` → `retrieval.text2sql` → `gen_ai.chat` → `cortex.write`; agent tasks add `agent.task` and `agent.step` spans.

Spans use the attribute names from the OpenTelemetry GenAI semantic conventions:
- `gen_ai.operation.name`, `gen_ai.system`, `gen_ai.request.model`, `gen_ai.usage.input_tokens` and `output_tokens`;
- Cortex's own attributes for cost, verification rate, context tokens, evicted items, confidence and cache hits.

Spans are written to `artifacts/<domain>/traces.jsonl` and served at `/v1/traces/{id}`; every UI answer links to its trace. If `OTEL_EXPORTER_OTLP_ENDPOINT` is set, the same spans go to Langfuse, Grafana or Jaeger. The GenAI conventions are still marked Development, so the attribute set is pinned in one module (`cortex/telemetry/tracer.py`).

## Audit log
Append-only JSONL with every tool-policy decision (allow/deny + reason), every quarantined passage and every approval.

## LLMOps
- **Eval gate in CI** on every push: lint, 37 tests, ingest, the full e-commerce eval. The build fails if overall accuracy's upper confidence bound falls below the baseline minus 3 points, if attack success rate rises, or if row-level security leaks.
- **Baselines per domain** in `evals/<domain>/baseline.json`.

## Next (Designed)
- **Release bundles:** prompts, model IDs, tool definitions and domain config versioned as one artifact.
- **Staged rollout:** shadow → 5–10% canary → full, with a sequential test for promotion and one-command rollback.
- **Dashboards** for operations, cost, quality and safety (Grafana).
- **Online evaluation:** score 5–10% of live traffic and detect drift in query embeddings with MMD.
