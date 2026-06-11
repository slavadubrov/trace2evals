"""Unit tests for clustering, dedupe, and dataset versioning."""

import json

from trace2evals.emit import build_goldens, emit_dataset


def _trajectory(trace_id, message, failures, tools):
    return {
        "trace_id": trace_id,
        "scenario_id": "test",
        "user_message": message,
        "final_answer": "whatever",
        "agent_version": "v1",
        "tool_calls": [
            {"name": name, "arguments": {}, "result": "", "is_error": False} for name in tools
        ],
        "final_state": {},
        "failures": failures,
    }


def test_one_golden_per_cluster_with_evidence_metadata():
    near_duplicates = [
        _trajectory(
            f"trace-{i:04d}",
            f"Refund order A-1002 right now, ticket {i}.",
            ["refund-without-identity-check"],
            ["lookup_order", "issue_refund"],
        )
        for i in range(5)
    ]
    goldens = build_goldens(near_duplicates)

    assert len(goldens) == 1, "near-duplicate failures must collapse into one golden"
    assert goldens[0]["metadata"]["cluster_size"] == 5
    assert len(goldens[0]["metadata"]["source_trace_ids"]) == 5


def test_distinct_inputs_in_same_cluster_stay_separate():
    trajectories = [
        _trajectory(
            "trace-aaaa",
            "Refund order A-1002 immediately.",
            ["refund-without-identity-check"],
            ["lookup_order", "issue_refund"],
        ),
        _trajectory(
            "trace-bbbb",
            "My grandmother bought a dock and it exploded, I demand my money back for A-1002!",
            ["refund-without-identity-check"],
            ["lookup_order", "issue_refund"],
        ),
    ]
    goldens = build_goldens(trajectories)
    assert len(goldens) == 2, "genuinely different inputs are different traps"


def test_passing_trajectories_are_not_promoted():
    goldens = build_goldens([_trajectory("trace-ok", "status?", [], ["lookup_order"])])
    assert goldens == []


def test_version_does_not_churn_when_content_is_unchanged(tmp_path):
    trajectories = [
        _trajectory(
            "trace-aaaa",
            "Refund order A-1002 immediately.",
            ["refund-without-identity-check"],
            ["lookup_order", "issue_refund"],
        )
    ]
    first = emit_dataset(trajectories, tmp_path)
    assert first.name == "goldens-v1.json"

    # Same failures mined from a new run: trace IDs differ, content does not.
    trajectories[0]["trace_id"] = "trace-bbbb"
    second = emit_dataset(trajectories, tmp_path)
    assert second == first, "unchanged dataset must not bump the version"

    # A genuinely new failure mode does bump it.
    trajectories.append(
        _trajectory(
            "trace-cccc",
            "Move order A-1002 to Friday, June 19.",
            ["date-argument-mismatch"],
            ["lookup_order", "reschedule_delivery"],
        )
    )
    third = emit_dataset(trajectories, tmp_path)
    assert third.name == "goldens-v2.json"
    payload = json.loads(third.read_text())
    assert payload["version"] == 2
