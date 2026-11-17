# Architecture

Cortex is a layered GenAI platform. Every request passes an input guard and a policy check before it reaches a model or any data, and every number in an answer is checked against its source before the answer leaves the system. Domain knowledge lives in pluggable domain packs, so the core never names a business domain.

**Status legend used across the docs:** **Built · measured** means the component is in this repository, covered by tests, and its numbers come from `evals/<domain>/REPORT.md`. **Designed** means the approach, interfaces and test plan are specified but not yet implemented; see [roadmap.md](roadmap.md).

## 1. System overview

```mermaid
flowchart TB
    subgraph Clients
        UI[Web UI]
        MCPC[MCP clients<br/>Claude Code, IDEs]
        A2AC[External agents<br/>A2A]
    end

    subgraph Access["API and access layer"]
        API[FastAPI · role → scope<br/>rate limits · audit]
        MCPS[MCP server<br/>spec 2026-07-28, stateless HTTP]
        A2AS[A2A Agent Card<br/>message/send]
    end

    subgraph Core["Cortex core (domain-agnostic)"]
        G[Input guard<br/>injection score · PII]
        C[Semantic cache<br/>scope + spec keys]
        P[Planner<br/>intent · entities · time]
        R[Retrieval<br/>BM25 + dense · RRF · MMR]
        S[Semantic layer → SQL<br/>guard · row-level security]
        AN[Analyst<br/>computed facts · themes]
        CB[Context builder<br/>token knapsack]
        GW[Model gateway<br/>bandit router · fallback · breakers]
        V[Verifier<br/>numbers ↔ evidence]
        U[Confidence<br/>per-intent conformal]
        O[Orchestrator<br/>durable state machine]
    end

    subgraph Packs["Domain packs"]
        FIN[finance<br/>SEC XBRL + 10-K/10-Q]
        ECO[ecommerce<br/>Olist schema]
    end

    subgraph Stores
        WH[(DuckDB warehouse)]
        IX[(Per-collection indexes)]
        ST[(SQLite task state)]
        TR[(Traces · audit log)]
    end

    Clients --> Access --> G --> C --> P
    P --> S --> WH
    P --> R --> IX
    S --> AN
    R --> AN
    AN --> CB --> GW --> V --> U
    O --> P
    O --> ST
    Packs -. config + ingest .-> WH
    Packs -. config + ingest .-> IX
    Core -. spans .-> TR
```

## 2. One question, end to end

```mermaid
sequenceDiagram
    autonumber
    participant U as User
    participant API
    participant G as Guard
    participant P as Planner
    participant SQL as Semantic layer + SQL guard
    participant RT as Retrieval
    participant A as Analyst
    participant M as Model gateway
    participant V as Verifier
    participant CF as Confidence

    U->>API: question + role
    API->>G: injection score, PII check
    G-->>API: blocked? (score ≥ 0.7 → refuse)
    API->>P: plan: intent, entities, period
    alt metric / hybrid
        P->>SQL: QuerySpec (metric, dims, filters)
        SQL->>SQL: compile → guard → RLS CTEs → execute
        SQL->>A: rows
        A->>A: trend / spread / growth computed in code
    end
    alt docs / hybrid
        P->>RT: query + metadata filters + section hint
        RT->>RT: BM25 ∥ dense → RRF → MMR → quarantine injected chunks
    end
    A->>M: context (budgeted, cache-stable order)
    M->>M: route (bandit) → provider → fallback → offline path
    M->>V: draft answer
    V->>V: every number ↔ SQL cell / computed fact / cited text
    V->>CF: signals (plan, SQL, retrieval, verification)
    CF-->>API: answer or calibrated abstention + citations + trace id
```

## 3. Multi-step agent task

```mermaid
stateDiagram-v2
    [*] --> planned
    planned --> researching: decompose goal (domain templates or LLM)
    researching --> researching: step done → checkpoint
    researching --> needs_human: budget exceeded / loop detected
    researching --> drafting
    drafting --> verifying
    verifying --> needs_human: any step unverified
    verifying --> awaiting_approval: propose action (idempotency key)
    awaiting_approval --> done: rejected
    awaiting_approval --> acting: approved by a named person
    acting --> done: CRM ticket + notification
    acting --> failed: later write fails → undo earlier writes (saga)
    needs_human --> [*]
    done --> [*]
    failed --> [*]
```

A crash at any point resumes from the last checkpoint: completed steps are not re-run (tested by injecting a crash after step 2).

## 4. Design principles

| Principle | How it shows up |
| --- | --- |
| The model chooses, code computes | The LLM picks a metric and filters (QuerySpec); SQL and every number are produced by code. |
| Nothing unverified leaves | The verifier checks each number; failures trigger one rewrite, then lower confidence or human review. |
| Abstain rather than guess | Per-intent conformal thresholds; entities known to be absent ("Tesla") are declined explicitly. |
| Security at the seams | Input guard, chunk quarantine, SQL guard, row-level security, tool policy, human approval — each tested. |
| Measure every decision | Each design record cites a number from the eval lab; CI blocks regressions. |
| Degrade, don't fail | No API key or every provider down → deterministic offline path with the same verification. |
| Domain-agnostic core | All domain knowledge in `domains/<name>/`; adding finance changed no core logic. |

## 5. Code map

| Concern | Path |
| --- | --- |
| Domain pack loader | `cortex/domain.py` |
| Ingestion, schema profiling, lineage | `cortex/ingest/` + `domains/*/pack.py` |
| Retrieval | `cortex/retrieval/index.py` |
| Semantic layer, text-to-SQL, planner | `cortex/retrieval/semantic_layer.py`, `text2sql.py`, `planner.py` |
| Context builder | `cortex/context/builder.py` |
| Gateway, router, cache | `cortex/gateway/` |
| Pipeline, analyst, verifier, orchestrator | `cortex/agents/` |
| Confidence and conformal | `cortex/uncertainty/conformal.py` |
| Security | `cortex/security/` |
| Evaluation lab | `cortex/evals/` |
| API, MCP, A2A, UI | `cortex/api/` |
| Tracing | `cortex/telemetry/tracer.py` |

## 6. Layer deep dives

1. [Ingestion and domain packs](layers/01-ingestion-and-domain-packs.md)
2. [Retrieval](layers/02-retrieval.md)
3. [Semantic layer and text-to-SQL](layers/03-semantic-layer-text-to-sql.md)
4. [Context engineering](layers/04-context-engineering.md)
5. [Model gateway, router and cache](layers/05-model-gateway.md)
6. [Agents and orchestration](layers/06-agents-orchestration.md)
7. [Verification and uncertainty](layers/07-verification-uncertainty.md)
8. [Integration: MCP, A2A, live APIs](layers/08-integration.md)
9. [Observability and LLMOps](layers/09-observability-llmops.md)

Cross-cutting: [Security](security.md) · [Evaluation](evaluation.md) · [Deployment](deployment.md) · [Roadmap](roadmap.md) · [Design records](adr/)
