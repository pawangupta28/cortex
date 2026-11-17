"""Layer 6: confidence scoring, calibration and conformal abstention.

1. Signals (planner confidence, SQL success, retrieval strength, verifier pass rate, ...)
   are combined by a logistic model into s(x) in [0, 1].
2. Split conformal prediction picks the abstention threshold. On a calibration set of
   n correctly-answered questions with scores s_i, nonconformity is 1 - s_i and

       q_hat = Quantile({1 - s_i}, ceil((n + 1)(1 - alpha)) / n)

   Answer only when 1 - s(x) <= q_hat. Under exchangeability, a correct answer is kept
   with probability >= 1 - alpha (finite-sample guarantee); what we trade is how many
   wrong answers slip through, which the risk-coverage curve reports.
3. Thresholds are group-conditional (Mondrian conformal): one q_hat per intent. A single
   global q_hat let the plentiful metric questions set a bar that hybrid "why" questions,
   which score lower by nature, could never clear, so the system refused them all.
4. Calibration quality is measured with Expected Calibration Error (ECE).
"""
from __future__ import annotations

import json
import math

import numpy as np

from cortex.config import settings

SIGNALS = ["plan_conf", "sql_ok", "has_rows", "spec_conf", "doc_score", "verified_rate", "evidence_count",
           "quarantined", "unsupported"]
DEFAULT_WEIGHTS = {"bias": -2.0, "plan_conf": 1.5, "sql_ok": 1.0, "has_rows": 1.0, "spec_conf": 1.5,
                   "doc_score": 2.0, "verified_rate": 2.0, "evidence_count": 0.3, "quarantined": -0.5,
                   "unsupported": -4.0}


def _path():
    return settings.artifacts_dir / "confidence_model.json"


class ConfidenceModel:
    def __init__(self, weights: dict | None = None, threshold: float | None = None, alpha: float | None = None,
                 thresholds: dict[str, float] | None = None, load_saved: bool = True):
        saved = json.loads(_path().read_text(encoding="utf-8")) if load_saved and _path().exists() else {}
        self.weights = weights or saved.get("weights", DEFAULT_WEIGHTS)
        self.threshold = threshold if threshold is not None else saved.get("q_hat")
        self.thresholds: dict[str, float] = thresholds if thresholds is not None else saved.get("q_hat_by_intent", {})
        self.alpha = alpha if alpha is not None else saved.get("alpha", settings.conformal_alpha)

    def score(self, sig: dict) -> float:
        z = self.weights.get("bias", 0.0) + sum(self.weights.get(k, 0.0) * float(sig.get(k, 0.0)) for k in SIGNALS)
        return 1 / (1 + math.exp(-z))

    def abstain(self, s: float, intent: str | None = None) -> bool:
        q = self.thresholds.get(intent, self.threshold) if intent else self.threshold
        return q is not None and (1 - s) > q

    # ------------------------------------------------------------ fitting
    @staticmethod
    def fit_weights(X: list[dict], y: list[int]) -> dict:
        from sklearn.linear_model import LogisticRegression
        A = np.array([[float(x.get(k, 0.0)) for k in SIGNALS] for x in X])
        if len(set(y)) < 2:
            return dict(DEFAULT_WEIGHTS)
        lr = LogisticRegression(C=1.0, max_iter=1000).fit(A, y)
        w = {k: float(c) for k, c in zip(SIGNALS, lr.coef_[0])}
        w["bias"] = float(lr.intercept_[0])
        return w

    @staticmethod
    def conformal_threshold(scores_of_correct: list[float], alpha: float) -> float:
        n = len(scores_of_correct)
        if n == 0:
            return 1.0
        nonconf = np.sort(1 - np.array(scores_of_correct))
        k = min(n, math.ceil((n + 1) * (1 - alpha)))
        return float(nonconf[k - 1])

    def save(self) -> None:
        _path().write_text(encoding="utf-8", data=json.dumps({"weights": self.weights, "q_hat": self.threshold,
                                       "q_hat_by_intent": self.thresholds, "alpha": self.alpha}, indent=2))


def ece(confidences: list[float], correct: list[int], bins: int = 10) -> float:
    c, y = np.array(confidences), np.array(correct)
    total = 0.0
    for lo in np.linspace(0, 1, bins, endpoint=False):
        m = (c >= lo) & (c < lo + 1 / bins) if lo < 1 - 1 / bins else (c >= lo)
        if m.any():
            total += m.mean() * abs(y[m].mean() - c[m].mean())
    return round(float(total), 4)


def risk_coverage(confidences: list[float], correct: list[int]) -> list[dict]:
    order = np.argsort(-np.array(confidences))
    y = np.array(correct)[order]
    out = []
    for k in range(1, len(y) + 1):
        out.append({"coverage": round(k / len(y), 3), "risk": round(1 - y[:k].mean(), 4)})
    return out
