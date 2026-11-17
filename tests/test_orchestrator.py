import pytest

from cortex.agents.orchestrator import Orchestrator, SimulatedCrash


def test_resume_after_crash_and_approval_flow(cortex_app, tmp_path):
    o = Orchestrator(cortex_app, db_path=tmp_path / "s.sqlite")
    t = o.create("Investigate why office furniture customers are unhappy")
    with pytest.raises(SimulatedCrash):
        o.run(t["id"], crash_after_step=2)
    assert len(o.get(t["id"])["steps"]) == 2
    t = o.run(t["id"])
    assert t["state"] == "awaiting_approval" and len(t["steps"]) == 5
    assert o.decide(t["id"], "tester", True)["state"] == "done"
    assert len(o.tickets()) == 1


def test_saga_rolls_back_and_idempotent_retry(cortex_app, tmp_path):
    o = Orchestrator(cortex_app, db_path=tmp_path / "s.sqlite")
    t = o.run(o.create("Investigate auto parts complaints")["id"])
    o.fail_notification = True
    t = o.decide(t["id"], "tester", True)
    assert t["state"] == "failed" and o.tickets() == []
