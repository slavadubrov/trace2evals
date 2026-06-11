"""Unit tests for the deterministic scorers — these run offline in CI."""

import pytest

from trace2evals.scorers import argument_mismatches, redundant_call_count, tool_correctness

CALLED = ["lookup_order", "check_refund_policy", "issue_refund"]
EXPECTED = ["lookup_order", "verify_identity", "issue_refund"]


def test_tool_correctness_article_example():
    assert tool_correctness(CALLED, EXPECTED, "exact") == 0.0
    assert tool_correctness(CALLED, EXPECTED, "in_order") == pytest.approx(2 / 3)
    assert tool_correctness(CALLED, EXPECTED, "any_order") == pytest.approx(2 / 3)


def test_in_order_allows_harmless_extra_calls():
    called = ["lookup_order", "check_refund_policy", "verify_identity", "issue_refund"]
    assert tool_correctness(called, EXPECTED, "in_order") == 1.0
    assert tool_correctness(called, EXPECTED, "exact") == 0.0


def test_empty_expected_passes():
    assert tool_correctness(["anything"], [], "exact") == 1.0


def test_loop_is_not_full_credit():
    # The bug that broke trace2evals-2: a pure retry loop must not score 1.0
    # against an expected sequence it never completed.
    looped = ["lookup_order", "lookup_order", "lookup_order"]
    assert tool_correctness(looped, EXPECTED, "in_order") == pytest.approx(1 / 3)


def test_redundant_call_count():
    calls = [
        {"name": "lookup_order", "arguments": {"order_id": "Z-9999"}},
        {"name": "lookup_order", "arguments": {"order_id": "Z-9999"}},
        {"name": "lookup_order", "arguments": {"order_id": "Z-9999"}},
        {"name": "lookup_order", "arguments": {"order_id": "A-1001"}},
    ]
    assert redundant_call_count(calls) == 3
    assert redundant_call_count([]) == 0


def test_argument_mismatches():
    calls = [
        {"name": "reschedule_delivery", "arguments": {"order_id": "A-1002", "date": "2026-06-17"}}
    ]
    expected = {"reschedule_delivery": {"order_id": "A-1002", "date": "2026-06-19"}}
    mismatches = argument_mismatches(calls, expected)
    assert mismatches == ["reschedule_delivery.date: expected '2026-06-19', got '2026-06-17'"]

    calls[0]["arguments"]["date"] = "2026-06-19"
    assert argument_mismatches(calls, expected) == []

    assert argument_mismatches([], expected) == [
        "reschedule_delivery: expected a call, but none was observed"
    ]
