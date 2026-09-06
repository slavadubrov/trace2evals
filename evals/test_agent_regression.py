"""Required reference suite: all task contracts must pass, offline by default."""

import os

import pytest

from trace2evals.agent import run_agent_and_capture_trace
from trace2evals.evaluate import grade, load_suite, validate_suite
from trace2evals.scenarios import reference_suite

SUITE = (
    load_suite(os.environ["TRACE2EVALS_SUITE"])
    if os.environ.get("TRACE2EVALS_SUITE")
    else reference_suite()
)
validate_suite(SUITE)


@pytest.mark.parametrize("case", SUITE["goldens"], ids=lambda c: c["id"])
def test_agent_regression(case, tracer):
    _, trajectory = run_agent_and_capture_trace(
        tracer, case["input"], case["id"], principal=case["principal"]
    )
    assert not grade(trajectory, case)
