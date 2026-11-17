"""Layer 3: context engineering. Every model call's context is assembled here, nowhere else.

Selection under a token budget is a 0/1 knapsack:  max sum v_i x_i  s.t.  sum c_i x_i <= B.
We use the greedy value-per-token rule, O(n log n), which is near-optimal when each item
is small relative to the budget (the usual case here; the textbook 2-approximation adds a
best-single-item check). Fixed items are kept unconditionally, stable parts go first so
provider prompt caching can hit, and record a manifest of what was kept and evicted.
"""
from __future__ import annotations

from cortex.core.models import ContextItem, ContextManifest
from cortex.gateway.gateway import estimate_tokens

SLOT_ORDER = ["system", "examples", "memory", "task", "evidence", "tool_result", "history"]
SLOT_WEIGHT = {"system": 10.0, "task": 3.0, "evidence": 2.0, "tool_result": 1.5, "memory": 1.0,
               "history": 0.7, "examples": 0.5}


class ContextBuilder:
    def __init__(self, budget_tokens: int = 3000):
        self.budget = budget_tokens

    def build(self, items: list[ContextItem]) -> tuple[list[ContextItem], ContextManifest]:
        fixed = [i for i in items if i.fixed]
        used = sum(estimate_tokens(i.content) for i in fixed)
        candidates = [i for i in items if not i.fixed]
        # value density = slot weight * item value / tokens
        ranked = sorted(candidates, key=lambda i: -(SLOT_WEIGHT[i.slot] * i.value) / estimate_tokens(i.content))
        kept, evicted = list(fixed), []
        for it in ranked:
            c = estimate_tokens(it.content)
            if used + c <= self.budget:
                kept.append(it)
                used += c
            else:
                evicted.append(it)
        # cache-stable ordering: fixed/stable slots first, volatile last; evidence best-first
        kept.sort(key=lambda i: (SLOT_ORDER.index(i.slot), -i.value))
        manifest = ContextManifest(
            budget=self.budget, used=used,
            included=[{"slot": i.slot, "ref": i.ref, "tokens": estimate_tokens(i.content)} for i in kept],
            evicted=[{"slot": i.slot, "ref": i.ref, "tokens": estimate_tokens(i.content)} for i in evicted])
        return kept, manifest

    @staticmethod
    def to_messages(items: list[ContextItem], question: str) -> list[dict]:
        system = "\n\n".join(i.content for i in items if i.slot == "system")
        rest = "\n\n".join(i.content for i in items if i.slot != "system")
        return [{"role": "system", "content": system},
                {"role": "user", "content": f"{rest}\n\nQUESTION: {question}"}]
