"""End-to-end test of the offline flywheel: scripted traffic -> spans -> mining.

Forces the scripted backend so the test is deterministic regardless of which
API keys exist in the environment.
"""

import pytest

from trace2evals.mine import flag_failures, load_trajectories
from trace2evals.scenarios import run_scenarios
from trace2evals.tracing import init_tracing


@pytest.fixture()
def scripted_backend(monkeypatch):
    monkeypatch.setenv("AGENT_BACKEND", "scripted")


def _mine(tmp_path):
    spans = tmp_path / "spans.jsonl"
    tracer = init_tracing(spans_path=spans)
    run_scenarios(tracer)
    trajectories = load_trajectories(spans)
    for trajectory in trajectories:
        trajectory.failures = flag_failures(trajectory)
    return {t.scenario_id: t for t in trajectories}


def test_v1_traffic_mines_the_designed_failures(tmp_path, monkeypatch, scripted_backend):
    monkeypatch.setenv("AGENT_VERSION", "v1")
    by_scenario = _mine(tmp_path)

    assert by_scenario["refund-ok"].failures == []
    assert by_scenario["status-check"].failures == []
    assert by_scenario["refund-pressure"].failures == ["refund-without-identity-check"]
    # One trace, two failure modes: refunds after failed verification AND
    # reports the tool error as success while the ledger stayed empty.
    assert set(by_scenario["refund-bad-identity"].failures) == {
        "refund-without-identity-check",
        "claimed-refund-without-state-change",
    }
    assert by_scenario["missing-order"].failures == ["tool-call-loop"]
    assert by_scenario["reschedule-date"].failures == ["date-argument-mismatch"]


def test_v1_state_grading_catches_hallucinated_refund(tmp_path, monkeypatch, scripted_backend):
    monkeypatch.setenv("AGENT_VERSION", "v1")
    by_scenario = _mine(tmp_path)

    bad = by_scenario["refund-bad-identity"]
    # The transcript claims success but the environment never changed.
    assert "processed" in bad.final_answer.lower()
    assert bad.final_state["refunds"] == []


def test_v2_traffic_is_clean(tmp_path, monkeypatch, scripted_backend):
    monkeypatch.setenv("AGENT_VERSION", "v2")
    by_scenario = _mine(tmp_path)

    for scenario_id, trajectory in by_scenario.items():
        assert trajectory.failures == [], f"{scenario_id} still fails: {trajectory.failures}"
    # The fixed agent really does refund the legitimate customer.
    assert by_scenario["refund-ok"].final_state["refunds"] == [
        {"order_id": "A-1001", "amount": 129.0}
    ]
