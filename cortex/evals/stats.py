"""Statistics for comparing system versions honestly.

Old and new versions answer the *same* questions, so comparisons are paired: we resample
questions, not answers, which removes between-question variance and gives much tighter
intervals than comparing two independent means.
"""
from __future__ import annotations

import math

import numpy as np


def bootstrap_ci(x, n_boot: int = 10000, alpha: float = 0.05, seed: int = 0) -> tuple[float, float, float]:
    x = np.asarray(x, dtype=float)
    if len(x) == 0:
        return (float("nan"),) * 3
    rng = np.random.default_rng(seed)
    means = x[rng.integers(0, len(x), (n_boot, len(x)))].mean(axis=1)
    lo, hi = np.quantile(means, [alpha / 2, 1 - alpha / 2])
    return round(float(x.mean()), 4), round(float(lo), 4), round(float(hi), 4)


def paired_bootstrap(a, b, n_boot: int = 10000, alpha: float = 0.05, seed: int = 0) -> dict:
    """CI for mean(b - a) over the same items. 'significant' if the interval excludes 0."""
    d = np.asarray(b, dtype=float) - np.asarray(a, dtype=float)
    mean, lo, hi = bootstrap_ci(d, n_boot, alpha, seed)
    return {"mean_diff": mean, "ci": [lo, hi], "significant": bool(lo > 0 or hi < 0)}


def permutation_test(a, b, n_perm: int = 10000, seed: int = 0) -> float:
    """Two-sided paired permutation (sign-flip) test p-value for mean(b - a) = 0."""
    d = np.asarray(b, dtype=float) - np.asarray(a, dtype=float)
    if not d.any():
        return 1.0
    rng = np.random.default_rng(seed)
    obs = abs(d.mean())
    signs = rng.choice([-1.0, 1.0], size=(n_perm, len(d)))
    null = np.abs((signs * d).mean(axis=1))
    return float((np.sum(null >= obs) + 1) / (n_perm + 1))


def mcnemar(a_correct, b_correct) -> dict:
    """Exact McNemar test on discordant pairs for paired pass/fail outcomes."""
    a, b = np.asarray(a_correct, bool), np.asarray(b_correct, bool)
    n01 = int(np.sum(~a & b))     # only b right
    n10 = int(np.sum(a & ~b))     # only a right
    n = n01 + n10
    if n == 0:
        return {"b_only": 0, "a_only": 0, "p_value": 1.0}
    k = min(n01, n10)
    p = min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)
    return {"b_only": n01, "a_only": n10, "p_value": round(p, 5)}


def cohen_kappa(r1, r2) -> float:
    """Agreement beyond chance between two raters (e.g. LLM judge vs human): (p_o - p_e) / (1 - p_e)."""
    r1, r2 = list(r1), list(r2)
    labels = sorted(set(r1) | set(r2))
    n = len(r1)
    po = sum(x == y for x, y in zip(r1, r2)) / n
    pe = sum((r1.count(lab) / n) * (r2.count(lab) / n) for lab in labels)
    return round((po - pe) / (1 - pe), 4) if pe < 1 else 1.0


def min_detectable_effect(p: float, n: int, alpha: float = 0.05, power: float = 0.8) -> float:
    """Approximate smallest change in a pass rate a paired test with n items can detect.
    Conservative: assumes discordance ~ 2p(1-p)."""
    from statistics import NormalDist
    z = NormalDist().inv_cdf(1 - alpha / 2) + NormalDist().inv_cdf(power)
    return round(z * math.sqrt(2 * p * (1 - p) / n), 4)


def benjamini_hochberg(pvals: dict[str, float], q: float = 0.05) -> dict[str, bool]:
    items = sorted(pvals.items(), key=lambda kv: kv[1])
    m = len(items)
    cutoff = 0
    for i, (_, p) in enumerate(items, start=1):
        if p <= q * i / m:
            cutoff = i
    return {k: (i < cutoff) for i, (k, _) in enumerate(items)}
