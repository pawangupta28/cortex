import numpy as np

from cortex.agents.analyst import distinctive_terms
from cortex.agents.verifier import verify
from cortex.context.builder import ContextBuilder
from cortex.core.models import ContextItem, Evidence
from cortex.evals.stats import bootstrap_ci, cohen_kappa, mcnemar, paired_bootstrap
from cortex.gateway.gateway import CircuitBreaker
from cortex.gateway.router import simulate
from cortex.retrieval.index import mmr, rrf
from cortex.uncertainty.conformal import ConfidenceModel, ece


def test_rrf_prefers_consensus():
    s = rrf([[1, 2, 3], [2, 1, 4]])
    assert max(s, key=s.get) in (1, 2) and s[4] < s[1]


def test_mmr_diversifies():
    v = np.array([[1, 0], [1, 0], [0, 1.0]])
    assert mmr(v[0], v, np.array([1.0, 0.99, 0.5]), k=2, lam=0.5) == [0, 2]


def test_router_cuts_cost_within_budget():
    r = simulate(n=1500)
    assert r["cost_reduction_pct"] > 30 and r["quality_gap_pts"] < 8


def test_circuit_breaker_opens():
    cb = CircuitBreaker(threshold=2, cooldown_s=999)
    cb.failure(); cb.failure()
    assert not cb.available


def test_context_knapsack_respects_budget():
    items = [ContextItem(slot="system", content="x" * 400, fixed=True)] + \
            [ContextItem(slot="evidence", content="y" * 400, value=v, ref=str(v)) for v in (0.1, 0.9, 0.5)]
    kept, m = ContextBuilder(budget_tokens=250).build(items)
    assert m.used <= 250 and [i.ref for i in kept if i.slot == "evidence"] == ["0.9"]


def test_verifier_catches_invented_number():
    ev = [Evidence(id="sql", kind="sql_result", content="", data={"rows": [["SP", 0.2999]]})]
    ok = verify("SP has a late rate of 30.0% [E1].", ev)
    bad = verify("SP has a late rate of 45.0% [E1].", ev)
    assert ok[0].verified and not bad[0].verified


def test_distinctive_terms_finds_planted_theme():
    target = ["box arrived crushed and broken"] * 20 + ["fine product"] * 5
    background = ["fine product arrived on time"] * 200
    assert distinctive_terms(target, background)[0][0] in ("crushed", "broken", "box", "box arrived", "arrived crushed")


def test_conformal_threshold_quantile():
    q = ConfidenceModel.conformal_threshold([0.9] * 9 + [0.5], alpha=0.1)
    assert abs(q - 0.5) < 1e-9


def test_stats_helpers():
    assert bootstrap_ci([1, 1, 1])[0] == 1.0
    assert paired_bootstrap([0] * 30, [1] * 30)["significant"]
    assert mcnemar([0] * 10, [1] * 10)["p_value"] < 0.01
    assert cohen_kappa([1, 0, 1, 0], [1, 0, 1, 0]) == 1.0
    assert ece([0.9, 0.9], [1, 1]) < 0.11
