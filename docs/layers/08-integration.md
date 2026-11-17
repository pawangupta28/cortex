# 8. Integration: MCP, A2A, live APIs, business systems

**Status: Built · measured** (A2A conformance testing and OIDC Designed)

## MCP server
Built on the official MCP Python SDK 2.x, which negotiated protocol version **2026-07-28** in testing. It is served **stateless over Streamable HTTP** (`/mcp/`, so it scales horizontally) and over stdio for Claude Code. Roots, Sampling and Logging are not used because they are deprecated in this revision.

| Tool | Purpose |
| --- | --- |
| `ask` | Cited, verified answer with a confidence score |
| `run_metric` | Governed SQL through the semantic layer, row-level security applied |
| `search_documents` | Search the domain's reference documents |
| `sec_live_quarters` | **Live external API**: fetch a company's latest quarters from data.sec.gov and normalise them with the same pipeline as ingestion |

```bash
claude mcp add cortex -- python -m cortex.cli --domain finance mcp
```

Verified: a client over HTTP lists the four tools, calls `ask`, and calls `run_metric` with a scoped role, getting only rows within that scope.

## A2A
- An Agent Card at `/.well-known/agent-card.json` with two skills: grounded Q&A and multi-step investigation.
- A JSON-RPC `message/send` endpoint that returns a task with a text artifact.

It is a minimal subset; it needs to be validated against the official A2A SDK before claiming conformance.

## Business systems (mock enterprise)
- A CRM ticket table and a notification channel, written only by the actor agent after human approval, with idempotency keys and saga rollback ([agents](06-agents-orchestration.md)).
- Roles come from an `X-Role` header validated against `policies.yaml`.

## Next (Designed)
- Keycloak OIDC: the JWT role claim feeds the same policy engine.
- A SOAP adapter wrapped as an MCP server, showing how a legacy system is brought in.
- Slack delivery of answers and approvals.
- FRED macro series as a second live API, with a free key.
