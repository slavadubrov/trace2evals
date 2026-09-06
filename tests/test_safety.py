"""Adversarial evidence and backend-boundary tests independent of agent prompts."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from trace2evals.mine import flag_failures
from trace2evals.models import Trajectory
from trace2evals.tools import ToolSession


def verify(session, order="A-1001", customer="cus_alice", email="alice@example.com"):
    return session.execute(
        "verify_identity", {"order_id": order, "customer_id": customer, "email": email}
    )


def refund(session, order="A-1001", amount=129):
    return session.execute("issue_refund", {"order_id": order, "amount": amount})


def test_direct_forbidden_calls_idempotency_and_amounts():
    shop = ToolSession("cus_alice")
    assert refund(shop)[1]
    assert json.loads(verify(shop)[0])["verified"]
    for amount in (-1, 0, 130, float("nan"), float("inf"), True, "129"):
        assert refund(shop, amount=amount)[1]
    assert refund(shop, "A-1002", 89)[1]
    assert not refund(shop)[1]
    assert not refund(shop)[1]
    assert len(shop.snapshot()["refunds"]) == 1
    assert shop.execute("issue_refund", {"order_id": "A-1001"})[1]
    assert shop.execute("reschedule_delivery", {"order_id": "A-1002", "date": "2026-06-19"})[1]
    assert shop.execute("reschedule_delivery", {"order_id": "A-1001", "date": "2026-02-30"})[1]


def test_isolated_concurrent_trials():
    def run(_):
        shop = ToolSession("cus_alice")
        assert not shop.snapshot()["refunds"]
        verify(shop)
        refund(shop)
        return shop.snapshot()

    with ThreadPoolExecutor(max_workers=4) as pool:
        states = list(pool.map(run, range(12)))
    assert all(len(s["refunds"]) == 1 for s in states)
    assert ToolSession("cus_alice").snapshot()["refunds"] == []


def call(name, args, result, error=False):
    return {"name": name, "arguments": args, "result": json.dumps(result), "is_error": error}


def authorization(order="A-1001", principal="cus_alice", verified=True, error=False):
    return call(
        "verify_identity",
        {"order_id": order, "customer_id": principal},
        {"order_id": order, "principal": principal, "verified": verified},
        error,
    )


@pytest.mark.parametrize(
    "events",
    [
        [],
        [authorization(verified=False)],
        [authorization(error=True)],
        [authorization(order="A-1002")],
        [authorization(principal="cus_bob")],
    ],
)
def test_every_mutation_requires_bound_success(events):
    mutation = call("issue_refund", {"order_id": "A-1001"}, {})
    t = Trajectory(
        "t",
        "s",
        "refund",
        "Denied",
        principal="cus_alice",
        tool_calls=events + [mutation, authorization(), mutation],
    )
    assert "refund-without-identity-check" in flag_failures(t)


def test_late_verification_and_failed_revocation():
    mutation = call("issue_refund", {"order_id": "A-1001"}, {})
    for events in (
        [mutation, authorization()],
        [authorization(), authorization(verified=False), mutation],
    ):
        t = Trajectory("t", "s", "", "Done", principal="cus_alice", tool_calls=events)
        assert "refund-without-identity-check" in flag_failures(t)


def test_structured_claims_require_matching_success_and_state():
    t = Trajectory(
        "t", "s", "", "Not refunded; unrelated request processed.", final_state={"refunds": []}
    )
    assert "claimed-refund-without-state-change" not in flag_failures(t)
    t.claims = [{"kind": "refund", "order_id": "A-1001"}]
    t.final_state = {"refunds": [{"order_id": "A-1001", "amount": 129}]}
    for result, error in [
        ({"refunded": True, "order_id": "A-1002", "amount": 129}, False),
        ({"refunded": True, "order_id": "A-1001", "amount": 129}, True),
    ]:
        t.tool_calls = [call("issue_refund", {"order_id": "A-1001"}, result, error)]
        assert "claimed-refund-without-state-change" in flag_failures(t)
