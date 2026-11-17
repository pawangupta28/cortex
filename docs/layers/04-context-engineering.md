# 4. Context engineering

**Status: Built · measured** (unit-tested; cache-hit and context-rot studies Designed)

## Problem
Most agent errors are context errors: the right evidence was retrieved but crowded out, cut mid-passage, or buried in the middle of a long prompt. Stuffing the top-k chunks is not a policy.

## Design
Every model call is assembled by one `ContextBuilder` from typed slots:

| Slot | Content | Rule |
| --- | --- | --- |
| system | rules, output contract | fixed, never evicted |
| tool_result | computed facts from the analyst | high weight |
| evidence | SQL results, retrieved passages (spotlighted) | ranked by retrieval score |
| task / memory / history / examples | multi-step state | lower weights, evicted first |

Selection under the token budget B is a 0/1 knapsack:

```
max Σ v_i·x_i   subject to   Σ c_i·x_i ≤ B,   x_i ∈ {0,1}
v_i = slot_weight × item_value,  c_i = tokens
```

Greedy by value per token, O(n log n). This is near-optimal when items are small relative to the budget. Fixed items always stay. The kept items are then reordered so stable parts (system rules, examples) come first, which lets provider prompt caching hit. A manifest of what was kept and what was dropped, with token counts, goes onto the trace.

Untrusted text is **spotlighted**: wrapped in `<untrusted source=…>` with each line datamarked, and the system rules say such text is data, never instructions.

## Results
- Unit test: with a 250-token budget, the builder keeps exactly the highest value-per-token evidence item and stays within the budget.
- Every trace records context tokens used and how many items were evicted.

## Next (Designed)
1. **Context-rot curve:** accuracy as irrelevant passages are padded in at 2k / 8k / 32k tokens. This sets the budget from data.
2. **Position study:** best-first ordering vs best at both ends.
3. **Prompt-cache hit rate and cost:** before and after cache-stable ordering, once live providers are on.
