"""Regression eval suite generated from mined failures — the CI gate.

Each golden RE-RUNS the live agent on the input that failed, then grades the
fresh trajectory. It does not replay the old failed trace: a regression
dataset is not an archive of old mistakes, it is a set of traps for the next
version of the agent.

Checks per golden (deterministic first — cheap, fast, no drift):
  1. The failure rule that created this golden must not re-fire.
  2. tool_correctness(called, expected) must clear the golden's threshold.
  3. Expected tool arguments must match (right tool + wrong arguments is broken).
  4. Optional: DeepEval ToolCorrectnessMetric, and TaskCompletionMetric as an
     LLM judge when OPENAI_API_KEY is set (different model family than the
     agent under test, to avoid self-preference bias).

Run:  uv run pytest evals            (AGENT_VERSION=v1 -> red, v2 -> green)
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from trace2evals.agent import run_agent_and_capture_trace
from trace2evals.mine import flag_failures
from trace2evals.scorers import argument_mismatches, tool_correctness

DATASET_DIR = Path(__file__).resolve().parents[1] / "data" / "evals"

_dataset_files = sorted(DATASET_DIR.glob("goldens-v*.json"))
if not _dataset_files:
    pytest.skip(
        "no golden dataset yet — run `make traffic mine evals` first",
        allow_module_level=True,
    )

# Pin the dataset version under test. CI should also pin app version, prompt
# version, judge model, and judge prompt — change any of those and the
# before/after comparison is confounded.
DATASET = json.loads(_dataset_files[-1].read_text(encoding="utf-8"))
GOLDENS = DATASET["goldens"]

try:
    from deepeval import assert_test
    from deepeval.metrics import TaskCompletionMetric, ToolCorrectnessMetric
    from deepeval.test_case import LLMTestCase, ToolCall

    HAS_DEEPEVAL = True
except ImportError:
    HAS_DEEPEVAL = False


@pytest.mark.parametrize("golden", GOLDENS, ids=[g["id"] for g in GOLDENS])
def test_agent_regression(golden: dict, tracer) -> None:
    answer, fresh_trace = run_agent_and_capture_trace(
        tracer, golden["input"], scenario_id=f"regression:{golden['id']}"
    )

    # Gate 1: the mined failure mode must not re-fire on the fresh run.
    refired = set(flag_failures(fresh_trace)) & set(golden["failure_modes"])
    assert not refired, f"failure mode regressed: {sorted(refired)}"

    # Gate 2: tool correctness against the gold trajectory.
    called = [c["name"] for c in fresh_trace.tool_calls]
    score = tool_correctness(called, golden["expected_tools"], golden.get("tool_match", "in_order"))
    assert score >= golden.get("tool_threshold", 1.0), (
        f"tool correctness {score:.2f} below threshold: "
        f"called {called}, expected {golden['expected_tools']}"
    )

    # Gate 3: argument correctness — the dataset stores expected arguments
    # because a tool-name metric cannot catch 2026-06-17 where policy says 2026-06-19.
    mismatches = argument_mismatches(fresh_trace.tool_calls, golden.get("expected_arguments", {}))
    assert not mismatches, f"argument mismatches: {mismatches}"

    # Optional extra metrics via DeepEval (uv sync --extra deepeval). DeepEval
    # initializes a judge model even for its deterministic metrics, so the whole
    # block needs OPENAI_API_KEY — keep the judge in a different model family
    # than the agent under test, and calibrate it against human labels before
    # letting it block a deploy.
    if HAS_DEEPEVAL and os.environ.get("OPENAI_API_KEY"):
        metrics = [TaskCompletionMetric(threshold=0.7)]
        if golden["expected_tools"]:
            metrics.append(ToolCorrectnessMetric(threshold=golden.get("tool_threshold", 1.0)))
        if metrics:
            assert_test(
                LLMTestCase(
                    input=golden["input"],
                    actual_output=answer,
                    tools_called=[ToolCall(name=name) for name in called],
                    expected_tools=[ToolCall(name=n) for n in golden["expected_tools"]],
                ),
                metrics,
            )
