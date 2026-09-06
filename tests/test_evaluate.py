import copy
import json

import pytest

from trace2evals.agent import run_agent_and_capture_trace
from trace2evals.backends import Final, ToolUse
from trace2evals.evaluate import evaluate, grade, load_suite, validate_suite, wilson
from trace2evals.mine import load_trajectories
from trace2evals.scenarios import reference_suite
from trace2evals.tracing import init_tracing


def test_red_green_and_expected_attempt_accounting(tmp_path):
    tracer = init_tracing(spans_path=tmp_path / "runs.jsonl")
    suite = reference_suite()
    red = evaluate(suite, tracer, version="v1", tool_mode="vulnerable")
    assert red["summary"]["failed"] == 5
    assert red["summary"]["invalid"] == 0
    assert red["summary"]["attempts"] == 10
    for mode in ("vulnerable", "enforced"):
        green = evaluate(suite, tracer, tool_mode=mode)
        assert green["summary"]["passed"] == 10
        assert green["splits"]["holdout"]["passed"] == 4


def test_missing_empty_unreviewed_suite_is_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_suite(tmp_path / "absent")
    suite = reference_suite()
    suite["goldens"] = []
    with pytest.raises(ValueError):
        validate_suite(suite)
    suite = reference_suite()
    suite["goldens"][0]["review_status"] = "needs_review"
    with pytest.raises(ValueError):
        validate_suite(suite)
    assert wilson(0, 0) is None
    assert wilson(0, 10)[1] > 0
    assert wilson(10, 10)[0] < 1


def test_partial_write_and_infrastructure_failure_is_preserved(tmp_path):
    class Broken:
        version = "v2"
        backend_name = "fake"
        last_usage = None

        def start(self, message):
            return [
                ToolUse("1", "reschedule_delivery", {"order_id": "A-1001", "date": "2026-09-20"})
            ]

        def on_tool_results(self, results):
            raise TimeoutError("do not export private provider error text")

    spans = tmp_path / "spans.jsonl"
    _, t = run_agent_and_capture_trace(
        init_tracing(spans_path=spans), "move", principal="cus_alice", conversation=Broken()
    )
    assert t.status == "invalid"
    assert t.error == "TimeoutError"
    assert len(t.final_state["reschedules"]) == 1
    assert "private provider" not in spans.read_text()
    assert load_trajectories(spans)[0] == t


def test_nested_shuffled_spans_and_invalid_adapter(tmp_path):
    spans = tmp_path / "spans.jsonl"
    _, t = run_agent_and_capture_trace(
        init_tracing(spans_path=spans), "Status of order A-1001?", principal="cus_alice"
    )
    rows = [json.loads(line) for line in spans.read_text().splitlines()]
    root = next(r for r in rows if r["attributes"].get("gen_ai.operation.name") == "invoke_agent")
    chat = next(r for r in rows if r["attributes"].get("gen_ai.operation.name") == "chat")
    tool = next(r for r in rows if r["attributes"].get("gen_ai.operation.name") == "execute_tool")
    tool["parent_span_id"] = chat["span_id"]

    def write():
        spans.write_text("\n".join(json.dumps(row) for row in reversed(rows)))

    write()
    assert load_trajectories(spans)[0] == t
    del tool["attributes"]["trace2evals.call"]
    write()
    with pytest.raises(KeyError):
        load_trajectories(spans)
    root["attributes"]["trace2evals.schema_version"] = 1
    write()
    with pytest.raises(ValueError):
        load_trajectories(spans)


def test_safe_alternate_path_is_accepted_but_wrong_state_is_not(tmp_path):
    from trace2evals.backends import ScriptedConversation

    class WithPolicy(ScriptedConversation):
        def start(self, message):
            self.user_message = message
            return [self._tool("check_refund_policy", {})]

    case = reference_suite()["goldens"][0]
    _, t = run_agent_and_capture_trace(
        init_tracing(spans_path=tmp_path / "spans"),
        case["input"],
        principal=case["principal"],
        conversation=WithPolicy(version="v2"),
    )
    assert grade(t, case) == []
    bad = copy.deepcopy(t)
    bad.final_state["refunds"] = []
    assert "expected-state" in grade(bad, case)


def test_last_allowed_step_can_produce_final(tmp_path):
    from trace2evals.agent import MAX_STEPS

    class Last:
        version = "v2"
        backend_name = "fake"
        last_usage = None
        count = 0

        def start(self, message):
            return [ToolUse("0", "check_refund_policy", {})]

        def on_tool_results(self, results):
            self.count += 1
            if self.count == MAX_STEPS:
                return Final("Finished")
            return [ToolUse(str(self.count), "check_refund_policy", {})]

    _, t = run_agent_and_capture_trace(
        init_tracing(spans_path=tmp_path / "spans"), "test", conversation=Last()
    )
    assert t.status == "completed"
    assert len(t.tool_calls) == MAX_STEPS


def test_invalid_attempts_stay_in_denominator(tmp_path, monkeypatch):
    import importlib

    from trace2evals.models import Trajectory

    module = importlib.import_module("trace2evals.evaluate")

    def broken(*args, **kwargs):
        return "", Trajectory(
            "trace", "scenario", "input", "", status="invalid", error="TimeoutError"
        )

    monkeypatch.setattr(module, "run_agent_and_capture_trace", broken)
    report = evaluate(reference_suite(), init_tracing(spans_path=tmp_path / "unused"), repeats=2)
    assert report["summary"]["attempts"] == 20
    assert report["summary"]["invalid"] == 20
    assert report["summary"]["success_rate"] == 0


def test_exporter_paths_and_concurrent_agent_state_do_not_leak(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    first = init_tracing(spans_path=tmp_path / "first.jsonl")
    second = init_tracing(spans_path=tmp_path / "second.jsonl")
    case = reference_suite()["goldens"][0]

    def run(index):
        tracer = first if index % 2 else second
        return run_agent_and_capture_trace(
            tracer, case["input"], str(index), principal=case["principal"]
        )[1]

    with ThreadPoolExecutor(max_workers=4) as pool:
        trials = list(pool.map(run, range(8)))
    assert all(t.final_state == case["expected_state"] for t in trials)
    assert {t.scenario_id for t in load_trajectories(tmp_path / "first.jsonl")} == {
        "1",
        "3",
        "5",
        "7",
    }
    assert {t.scenario_id for t in load_trajectories(tmp_path / "second.jsonl")} == {
        "0",
        "2",
        "4",
        "6",
    }


def test_versioned_adapter_fixture_and_missing_final_tool(tmp_path):
    from pathlib import Path

    fixture = Path(__file__).parent / "fixtures" / "nested-v2.jsonl"
    t = load_trajectories(fixture)[0]
    assert t.scenario_id == "status-check"
    assert [c["name"] for c in t.tool_calls] == ["lookup_order"]
    rows = [json.loads(line) for line in fixture.read_text().splitlines()]
    rows = [r for r in rows if r["attributes"].get("gen_ai.operation.name") != "execute_tool"]
    missing = tmp_path / "missing.jsonl"
    missing.write_text("\n".join(json.dumps(r) for r in rows))
    with pytest.raises(ValueError, match="incomplete tool export"):
        load_trajectories(missing)


def test_usage_on_incomplete_response_is_retained(tmp_path):
    class Incomplete:
        version = "v2"
        backend_name = "fake"
        last_usage = None

        def start(self, message):
            self.last_usage = {"input_tokens": 100, "output_tokens": 200}
            raise ValueError("incomplete response")

    _, t = run_agent_and_capture_trace(
        init_tracing(spans_path=tmp_path / "spans"), "test", conversation=Incomplete()
    )
    assert t.status == "invalid"
    assert t.metadata["output_tokens"] == 200
    assert t.metadata["usage_complete"] is False
