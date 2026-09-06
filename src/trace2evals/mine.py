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

from .dates import parse_explicit_date
from .models import Trajectory
from .scorers import redundant_call_count
from .tracing import DEFAULT_SPANS_PATH


def load_trajectories(spans_path: Path | str = DEFAULT_SPANS_PATH) -> list[Trajectory]:
    """Read schema v2; reject partial/foreign exports rather than invent evidence.

    Tool spans may be nested under chat spans. A trace must contain exactly one
    invocation, and every tool must descend from it. Sequence numbers define
    execution order; exporter order and equal timestamps do not.
    """
    groups: dict[str, list[dict]] = {}
    for line in Path(spans_path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            span = json.loads(line)
            groups.setdefault(span["trace_id"], []).append(span)
    if not groups:
        raise ValueError("empty trace export")
    trajectories = []
    for trace_id, spans in groups.items():
        by_id = {s["span_id"]: s for s in spans}
        if len(by_id) != len(spans):
            raise ValueError("duplicate span ID")
        roots = [s for s in spans if s["attributes"].get("gen_ai.operation.name") == "invoke_agent"]
        if len(roots) != 1:
            raise ValueError("expected one invocation per trace")
        root = roots[0]
        if root["attributes"].get("trace2evals.schema_version") != 2:
            raise ValueError("unsupported trace schema; re-export using trace2evals v0.2")
        payload = json.loads(root["attributes"]["trace2evals.trajectory"])
        required = {
            "trace_id",
            "scenario_id",
            "user_message",
            "final_answer",
            "agent_version",
            "final_state",
            "failures",
            "claims",
            "principal",
            "status",
            "error",
            "metadata",
        }
        if not isinstance(payload, dict) or set(payload) != required:
            raise ValueError("missing trajectory evidence")
        if not all(
            isinstance(payload[k], str)
            for k in ("trace_id", "scenario_id", "user_message", "final_answer", "agent_version")
        ):
            raise ValueError("invalid trajectory strings")
        if payload["status"] not in {"completed", "invalid"} or not isinstance(
            payload["metadata"], dict
        ):
            raise ValueError("invalid run status or metadata")
        if (payload["status"] == "invalid") != (root["status"] == "ERROR"):
            raise ValueError("conflicting invocation status")
        if not isinstance(payload["final_state"], dict) or set(payload["final_state"]) != {
            "refunds",
            "reschedules",
        }:
            raise ValueError("missing final state")
        if not all(isinstance(v, list) for v in payload["final_state"].values()):
            raise ValueError("invalid state records")
        if not isinstance(payload["claims"], list):
            raise ValueError("invalid claims")
        for claim in payload["claims"]:
            if (
                not isinstance(claim, dict)
                or set(claim) != {"kind", "order_id"}
                or claim["kind"] not in {"refund", "reschedule"}
                or not isinstance(claim["order_id"], str)
            ):
                raise ValueError("invalid claim")
        trajectory = Trajectory(**payload)
        if trajectory.trace_id != trace_id:
            raise ValueError("trace ID mismatch")
        calls = []
        for span in spans:
            if span["attributes"].get("gen_ai.operation.name") != "execute_tool":
                continue
            parent = span["parent_span_id"]
            visited = {span["span_id"]}
            while parent != root["span_id"]:
                if parent not in by_id or parent in visited:
                    raise ValueError("orphan or cyclic tool span")
                visited.add(parent)
                parent = by_id[parent]["parent_span_id"]
            attrs = span["attributes"]
            call = json.loads(attrs["trace2evals.call"])
            if (
                not isinstance(call, dict)
                or set(call) != {"name", "arguments", "result", "is_error"}
                or not isinstance(call["name"], str)
                or not isinstance(call["arguments"], dict)
                or not isinstance(call["result"], str)
                or type(call["is_error"]) is not bool
            ):
                raise ValueError("invalid tool evidence")
            if call["is_error"] != (span["status"] == "ERROR"):
                raise ValueError("conflicting tool status")
            if type(attrs["trace2evals.sequence"]) is not int:
                raise ValueError("sequence must be an integer")
            result = json.loads(call["result"])
            if not isinstance(result, dict) or ("error" in result) != call["is_error"]:
                raise ValueError("invalid result evidence")
            calls.append((attrs["trace2evals.sequence"], call))
        calls.sort(key=lambda pair: pair[0])
        if [i for i, _ in calls] != list(range(len(calls))):
            raise ValueError("missing or duplicate tool sequence")
        if type(trajectory.metadata.get("tool_call_count")) is not int or trajectory.metadata[
            "tool_call_count"
        ] != len(calls):
            raise ValueError("incomplete tool export")
        trajectory.tool_calls = [c for _, c in calls]
        trajectories.append(trajectory)
    return trajectories


def result_object(call: dict) -> dict:
    if call.get("is_error"):
        return {}
    try:
        result = json.loads(call["result"])
        return result if isinstance(result, dict) and "error" not in result else {}
    except (ValueError, TypeError):
        return {}


def flag_failures(trajectory: Trajectory) -> list[str]:
    """Deterministic trajectory + state rules. Each rule name is a taxonomy label.

    A trajectory can carry several labels at once — a run that loops AND skips
    verification is two failure modes, not one.
    """
    failures = []
    calls = trajectory.tool_calls
    if trajectory.status != "completed":
        failures.append("invalid-run")
    authorized = set()
    for call in calls:
        result = result_object(call)
        args = call["arguments"]
        if call["name"] == "verify_identity":
            binding = (args.get("customer_id"), args.get("order_id"))
            if (
                result.get("verified") is True
                and trajectory.principal is not None
                and result.get("principal") == trajectory.principal == binding[0]
                and result.get("order_id") == binding[1]
            ):
                authorized.add(binding)
            else:
                authorized.discard(binding)
        if call["name"] == "issue_refund":
            if (trajectory.principal, args.get("order_id")) not in authorized:
                if "refund-without-identity-check" not in failures:
                    failures.append("refund-without-identity-check")

    # Rule 2: loop — the same tool called with identical arguments more than twice.
    if redundant_call_count(calls) > 2:
        failures.append("tool-call-loop")

    # Structured claims are checked against both successful tool results and state.
    # Free-text truthfulness remains a separate, explicitly uncalibrated judgment.
    for claim in trajectory.claims:
        order_id = claim["order_id"]
        if claim["kind"] == "refund":
            outcomes = [result_object(c) for c in calls if c["name"] == "issue_refund"]
            completed = any(
                r.get("refunded") is True
                and r.get("order_id") == order_id
                and {"order_id": order_id, "amount": r.get("amount")}
                in trajectory.final_state.get("refunds", [])
                for r in outcomes
            )
            if not completed and "claimed-refund-without-state-change" not in failures:
                failures.append("claimed-refund-without-state-change")
        elif claim["kind"] == "reschedule":
            outcomes = [result_object(c) for c in calls if c["name"] == "reschedule_delivery"]
            if not any(
                r.get("order_id") == order_id
                and "scheduled_for" in r
                and {"order_id": order_id, "date": r["scheduled_for"]}
                in trajectory.final_state.get("reschedules", [])
                for r in outcomes
            ):
                failures.append("claimed-reschedule-without-state-change")

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
    if not trajectory.final_answer.strip():
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
