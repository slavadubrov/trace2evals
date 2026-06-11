"""Mine trajectories out of raw spans and flag failures.

A trajectory is the ordered list of tool calls under one invoke_agent span,
plus the final answer and the final environment state. Failure detection is
deterministic — rules derived from error analysis of this agent's actual
failure modes, with specific names (`refund-without-identity-check`, not
`tool_problem`), because specific labels make better tests.

The same flag_failures() runs here over mined traces and inside the CI gate
over fresh re-runs, so a mined failure mode and a regressed one are detected
by the identical rule.
"""

from __future__ import annotations

import json
from pathlib import Path

from .agent import Trajectory
from .dates import parse_explicit_date
from .scorers import redundant_call_count
from .tracing import DEFAULT_SPANS_PATH


def load_trajectories(spans_path: Path | str = DEFAULT_SPANS_PATH) -> list[Trajectory]:
    """Rebuild trajectories from the JSONL span store (offline, no API needed)."""
    spans_by_trace: dict[str, list[dict]] = {}
    for line in Path(spans_path).read_text(encoding="utf-8").splitlines():
        span = json.loads(line)
        spans_by_trace.setdefault(span["trace_id"], []).append(span)

    trajectories = []
    for trace_id, spans in spans_by_trace.items():
        spans.sort(key=lambda s: s["start_ns"])
        agent_span = next(
            (s for s in spans if s["attributes"].get("gen_ai.operation.name") == "invoke_agent"),
            None,
        )
        if agent_span is None:
            continue
        attrs = agent_span["attributes"]
        in_msgs = json.loads(attrs.get("gen_ai.input.messages", "[]"))
        out_msgs = json.loads(attrs.get("gen_ai.output.messages", "[]"))
        trajectory = Trajectory(
            trace_id=trace_id,
            scenario_id=attrs.get("trace2evals.scenario_id", "unknown"),
            user_message=in_msgs[0]["content"] if in_msgs else "",
            final_answer=out_msgs[0]["content"] if out_msgs else "",
            agent_version=attrs.get("trace2evals.agent_version", "unknown"),
            final_state=json.loads(attrs.get("trace2evals.final_state", "{}")),
        )
        for span in spans:
            if span["attributes"].get("gen_ai.operation.name") == "execute_tool":
                trajectory.tool_calls.append(
                    {
                        "name": span["attributes"]["gen_ai.tool.name"],
                        "arguments": json.loads(
                            span["attributes"].get("gen_ai.tool.call.arguments", "{}")
                        ),
                        "result": span["attributes"].get("gen_ai.tool.call.result", ""),
                        "is_error": span["status"] == "ERROR",
                    }
                )
        trajectories.append(trajectory)
    return trajectories


def flag_failures(trajectory: Trajectory) -> list[str]:
    """Deterministic trajectory + state rules. Each rule name is a taxonomy label.

    A trajectory can carry several labels at once — a run that loops AND skips
    verification is two failure modes, not one.
    """
    failures = []
    calls = trajectory.tool_calls
    answer = trajectory.final_answer.lower()

    # Rule 1: refund attempted without a *successful* identity verification before it.
    for i, call in enumerate(calls):
        if call["name"] != "issue_refund":
            continue
        verified_before = any(
            c["name"] == "verify_identity" and '"verified": true' in c["result"] for c in calls[:i]
        )
        if not verified_before:
            failures.append("refund-without-identity-check")
            break

    # Rule 2: loop — the same tool called with identical arguments more than twice.
    if redundant_call_count(calls) > 2:
        failures.append("tool-call-loop")

    # Rule 3 (environment-state grading): the answer claims a refund happened,
    # but the refund ledger is empty. "Done" can be a hallucinated state.
    claims_refund = "refunded" in answer or "processed" in answer
    if claims_refund and not trajectory.final_state.get("refunds"):
        failures.append("claimed-refund-without-state-change")

    # Rule 4: argument mismatch — the reschedule date differs from the date the
    # customer explicitly asked for. A tool-name metric cannot catch this.
    stated_date = parse_explicit_date(trajectory.user_message)
    if stated_date:
        for call in calls:
            if (
                call["name"] == "reschedule_delivery"
                and call["arguments"].get("date") != stated_date
            ):
                failures.append("date-argument-mismatch")
                break

    # Rule 5: no final answer at all (dead end / max steps).
    if not trajectory.final_answer:
        failures.append("no-final-answer")

    # Rule 6: gross inefficiency — more than 6 tool calls for a single request.
    if len(calls) > 6:
        failures.append("inefficient-trajectory")

    return failures


def mine(spans_path: Path | str, out_path: Path | str) -> list[Trajectory]:
    trajectories = load_trajectories(spans_path)
    failed = 0
    for trajectory in trajectories:
        trajectory.failures = flag_failures(trajectory)
        if trajectory.failures:
            failed += 1
        status = "FAIL " + ",".join(trajectory.failures) if trajectory.failures else "ok"
        tools = [c["name"] for c in trajectory.tool_calls]
        print(f"{trajectory.scenario_id:24s} tools={tools} -> {status}")

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps([t.__dict__ for t in trajectories], indent=2), encoding="utf-8")
    print(f"\n{failed}/{len(trajectories)} trajectories failed. Wrote {out}")
    return trajectories
