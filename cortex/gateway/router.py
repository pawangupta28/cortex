"""Layer 4: contextual-bandit model router with a cost constraint.

Each model is an arm. For a call with context x (task type, prompt size, ...), linear
Thompson sampling draws theta_a ~ N(theta_hat_a, v^2 A_a^-1) per arm and picks the arm
with the largest x . theta_a. The reward is

    r = quality - lambda * cost - mu * latency

and lambda is a Lagrange multiplier updated online by projected sub-gradient ascent on
the budget constraint E[cost] <= budget:  lambda <- max(0, lambda + eta * (cost - budget)).
So when spend runs above budget, cheaper arms become more attractive, automatically.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

TASKS = ["plan", "sql", "write", "judge", "other"]


def features(task: str, prompt_tokens: int) -> np.ndarray:
    onehot = [1.0 if task == t else 0.0 for t in TASKS]
    return np.array([1.0, *onehot, np.log1p(prompt_tokens) / 10.0])


@dataclass
class Arm:
    name: str
    d: int
    A: np.ndarray = field(init=False)
    b: np.ndarray = field(init=False)

    def __post_init__(self):
        self.A = np.eye(self.d)
        self.b = np.zeros(self.d)


class BanditRouter:
    def __init__(self, arms: list[str], budget_per_call: float = 0.002, latency_weight: float = 0.05,
                 v: float = 0.3, eta: float = 50.0, seed: int = 0, state_path: Path | None = None):
        self.d = len(features("other", 1))
        self.arms = {a: Arm(a, self.d) for a in arms}
        self.budget = budget_per_call
        self.mu = latency_weight
        self.v = v
        self.eta = eta
        self.lam = 1.0
        self.rng = np.random.default_rng(seed)
        self.pending: dict[str, tuple[str, np.ndarray]] = {}
        self.state_path = state_path
        self.history: list[dict] = []

    def choose(self, task: str, prompt_tokens: int, available: list[str] | None = None) -> tuple[str, str]:
        x = features(task, prompt_tokens)
        names = [a for a in self.arms if available is None or a in available]
        best, best_val = names[0], -np.inf
        for a in names:
            arm = self.arms[a]
            A_inv = np.linalg.inv(arm.A)
            theta_hat = A_inv @ arm.b
            theta = self.rng.multivariate_normal(theta_hat, self.v ** 2 * A_inv)
            val = float(x @ theta)
            if val > best_val:
                best, best_val = a, val
        did = uuid.uuid4().hex[:12]
        self.pending[did] = (best, x)
        return best, did

    def update(self, decision_id: str, quality: float, cost: float, latency_s: float) -> float:
        if decision_id not in self.pending:
            return 0.0
        arm_name, x = self.pending.pop(decision_id)
        reward = quality - self.lam * cost - self.mu * latency_s
        arm = self.arms[arm_name]
        arm.A += np.outer(x, x)
        arm.b += reward * x
        self.lam = max(0.0, self.lam + self.eta * (cost - self.budget))     # dual ascent
        self.history.append({"arm": arm_name, "quality": quality, "cost": cost, "latency": latency_s,
                             "reward": reward, "lambda": self.lam})
        return reward

    def save(self) -> None:
        if not self.state_path:
            return
        self.state_path.write_text(encoding="utf-8", data=json.dumps({
            "lambda": self.lam,
            "arms": {a: {"A": arm.A.tolist(), "b": arm.b.tolist()} for a, arm in self.arms.items()},
        }))

    def load(self) -> None:
        if self.state_path and self.state_path.exists():
            s = json.loads(self.state_path.read_text(encoding="utf-8"))
            self.lam = s["lambda"]
            for a, st in s["arms"].items():
                if a in self.arms:
                    self.arms[a].A, self.arms[a].b = np.array(st["A"]), np.array(st["b"])


def simulate(n: int = 3000, seed: int = 1) -> dict:
    """Offline simulation with known ground truth, used in tests and the eval report.

    Three arms: a strong expensive model, a mid model, and a cheap small model that is
    as good as the strong one on 'plan' (classification) but weak on 'write'.
    The optimal policy is task-dependent; a fixed 'always strong' policy overspends.
    """
    rng = np.random.default_rng(seed)
    truth = {   # task -> arm -> mean quality
        "plan": {"frontier": 0.92, "mid": 0.88, "small": 0.90},
        "sql": {"frontier": 0.90, "mid": 0.84, "small": 0.70},
        "write": {"frontier": 0.93, "mid": 0.86, "small": 0.60},
    }
    cost = {"frontier": 0.006, "mid": 0.0015, "small": 0.0002}
    lat = {"frontier": 2.0, "mid": 1.0, "small": 0.3}
    router = BanditRouter(list(cost), budget_per_call=0.0025, seed=seed)
    tasks = list(truth)
    got_q, got_c, base_q, base_c, oracle_q = [], [], [], [], []
    for _ in range(n):
        t = tasks[int(rng.integers(0, 3))]
        arm, did = router.choose(t, int(rng.integers(200, 2000)))
        q = float(np.clip(rng.normal(truth[t][arm], 0.05), 0, 1))
        router.update(did, q, cost[arm], lat[arm])
        got_q.append(q)
        got_c.append(cost[arm])
        base_q.append(truth[t]["frontier"])
        base_c.append(cost["frontier"])
        oracle_q.append(max(truth[t].values()))
    last = slice(n // 2, None)
    return {
        "router_quality": round(float(np.mean(got_q[last])), 4),
        "router_cost_per_call": round(float(np.mean(got_c[last])), 6),
        "always_frontier_quality": round(float(np.mean(base_q[last])), 4),
        "always_frontier_cost_per_call": round(float(np.mean(base_c[last])), 6),
        "cost_reduction_pct": round(float(100 * (1 - np.mean(got_c[last]) / np.mean(base_c[last]))), 1),
        "quality_gap_pts": round(float(100 * (np.mean(base_q[last]) - np.mean(got_q[last]))), 2),
        "final_lambda": round(router.lam, 3),
        "arm_share_second_half": {a: round(sum(1 for h in router.history[n // 2:] if h["arm"] == a) / (n - n // 2), 3)
                                  for a in cost},
    }
