# Security

Security is enforced where data and instructions cross a trust boundary: user to API, corpus to context, model output to SQL or tools, and tools to business systems. Each control has a test, and the red-team suite runs in CI.

## Threat model

| Asset | Threat | Boundary |
| --- | --- | --- |
| Data outside a role's scope (other states, other tickers) | Over-broad SQL, prompt tricks, cache reuse across roles | API → SQL, cache |
| Business systems (CRM, notifications) | Agent acting on injected instructions; duplicate or partial writes | Model → tools |
| Warehouse integrity | Destructive or exfiltrating SQL from a confused or compromised model | Model → SQL |
| System prompt and credentials | Extraction through direct or indirect injection | Corpus → context |
| Cost budget | Runaway loops, abusive request patterns | API, orchestrator |

## OWASP Top 10 for LLM Applications (2025) → controls → tests

| Risk | Control in Cortex | Test | Status |
| --- | --- | --- | --- |
| LLM01 Prompt injection | Input scoring (noisy-OR over patterns, block ≥ 0.7); retrieved chunks scored and **quarantined**; remaining untrusted text **spotlighted**; the writer has no tools | 3 direct + 3 indirect attacks per domain (planted in a supplier bulletin and a fake broker note) | Built · measured |
| LLM02 Sensitive information disclosure | Row-level security in SQL and on document metadata; cache keyed by role scope and data version; PII redaction on output | RLS probe per domain; PII redaction unit test | Built · measured |
| LLM03 Supply chain | Pinned requirements; no model downloads in the default path | — | Designed: SBOM, dependency and container scans in CI |
| LLM04 Data and model poisoning | Lineage with checksums for every input file; ingest is a separate, auditable step | — | Designed: anomaly checks on ingest |
| LLM05 Improper output handling | SQL guard: single SELECT, allow-listed tables, no schema-qualified names, blocked file/extension functions, forced LIMIT, timeout; no model output is executed or rendered as HTML | 12 malicious statements per domain | Built · measured |
| LLM06 Excessive agency | Least-privilege tools per agent and per role; human approval before writes; idempotency keys; saga rollback; kill switch in the policy engine | Policy denial test; saga and idempotency tests | Built · measured |
| LLM07 System prompt leakage | No secrets in prompts; direct extraction attempts blocked at input | Direct injection cases | Built · measured |
| LLM08 Vector and embedding weaknesses | Scope filters applied inside retrieval (before ranking); separate indexes per collection | RLS probe checks returned passages too | Built · measured |
| LLM09 Misinformation | Verifier on every number; citations; conformal and hard abstention; known-absent entities declined | Seeded wrong-number test; unanswerable questions | Built · measured |
| LLM10 Unbounded consumption | Per-task step, time and cost budgets; loop detection; SQL timeout and row limit | Budget logic in the orchestrator | Built; load test Designed |

## Results (both domains)

| Measure | Finance | E-commerce |
| --- | --- | --- |
| Attack success rate, defences off → on (8 cases) | 38% → 0% | 38% → 0% |
| Malicious SQL blocked | 12 / 12 | 12 / 12 |
| Row-level security: rows or passages outside the role scope | 0 | 0 |

What "defences off" shows: with quarantine disabled, the planted instruction in the test document reaches the answer in all three indirect cases. With it enabled, the passage is removed and the event is logged in the audit trail.

## How row-level security works
A role's scope (`policies.yaml`) becomes filtered CTEs that shadow the base tables, defined per domain in `domain.yaml`:

```sql
WITH fin_view AS (SELECT * FROM main.fin_view WHERE ticker IN ('NVDA')), ...
SELECT * FROM (<any generated query>) AS rls_q
```

Because the shadowing wraps the final SQL, the restriction holds for compiled queries and for LLM-written SQL alike. Schema-qualified names such as `main.fin_view` are rejected by the guard, so the shadowing can't be bypassed.

## Known limits
- Pattern-based injection detection misses novel phrasings. Next: a small trained classifier, with its false-positive rate on normal questions measured.
- Roles come from a header in this build. Next: an OIDC JWT claim.
- Containers aren't signed and dependencies aren't scanned yet (see [deployment](deployment.md)).
