# 6. Agents and orchestration

**Status: Built · measured** (native LLM function-calling loop Designed)

## Problem
Free-running agent loops are hard to bound, impossible to resume after a crash, and dangerous when they can write to business systems.

## Design
Specialised roles, each with only the tools it needs (`policies.yaml`):

| Agent | Job | Tools |
| --- | --- | --- |
| Planner | Decompose the goal (domain templates, or the LLM when a key is set) | none |
| Researcher | Gather evidence per step | `run_sql`, `search_docs` |
| Analyst | Compute every number in code | `compute`, `run_sql` |
| Writer | Draft from evidence only | none |
| Verifier | Check every number against evidence | `get_evidence` |
| Actor | Write to business systems, only after approval | `create_crm_ticket` |

**State machine, not a loop** (diagram in [architecture](../architecture.md#3-multi-step-agent-task)):
- **Checkpoint after every step** to SQLite, so `run(task_id)` resumes from the last completed step.
- **Budgets:** at most 8 steps, 180 s and $0.05 of shadow cost per task; going over moves the task to `needs_human`.
- **Loop detection:** a repeated identical step is skipped.
- **Verification gate:** any unverified step sends the task to `needs_human` instead of producing a report.
- **Human approval:** the proposed action, its evidence and the approver's identity are recorded.
- **Idempotent actions:** every write carries `sha1(task_id:action)` as its idempotency key, so a retry never duplicates a ticket.
- **Saga:** a CRM ticket followed by a notification. If the notification fails, the ticket is deleted again, undoing the earlier write.

**Analyst functions:**
- Trend, spread, growth and multi-series (one series per company) facts, computed from SQL rows.
- Distinctive themes in a slice of reviews, using the weighted log-odds ratio with an informative Dirichlet prior (Monroe et al. 2008):

```
δ_w = log((y_w^i + α_w)/(n^i + α_0 − y_w^i − α_w)) − log((y_w^j + α_w)/(n^j + α_0 − y_w^j − α_w))
z_w = δ_w / sqrt(1/(y_w^i + α_w) + 1/(y_w^j + α_w))
```

The z-score down-weights rare words that look distinctive only by chance.

## Results (tests)
- **Crash recovery:** a crash injected after step 2 leaves 2 checkpointed steps; resuming completes the task with 5 steps and no repeats.
- **Approval:** the approved action creates exactly 1 CRM ticket.
- **Saga:** with the notification forced to fail, the task ends `failed` and 0 tickets remain.
- **Finance investigation** ("Investigate NVIDIA's margins and risks"): 6 steps covering revenue, gross margin, operating margin and R&D intensity trends, management's explanation (MD&A) and risk factors, with every figure verified.

## Next (Designed)
A native function-calling loop in which the model chooses tools through MCP within the same budgets, policy checks and checkpoints, compared with the fixed decomposition on task success and steps per task.
