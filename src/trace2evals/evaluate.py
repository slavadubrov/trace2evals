"""Deterministic gate and attempt accounting, usable without pytest or a checkout."""

from __future__ import annotations

import hashlib
import json
import math
import platform
from collections import Counter
from dataclasses import asdict
from importlib.metadata import version as distribution_version
from pathlib import Path

from .agent import run_agent_and_capture_trace
from .mine import flag_failures
from .scorers import argument_mismatches, tool_correctness

GRADER_REVISION = "deterministic-v2"


def load_suite(path: Path | str) -> dict:
    suite = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_suite(suite)
    return suite


def validate_suite(suite: dict) -> None:
    if not isinstance(suite, dict):
        raise ValueError("suite must be an object")
    if (
        suite.get("schema_version") != 2
        or type(suite.get("version")) is not int
        or suite["version"] < 1
        or not suite.get("task_revision")
        or suite.get("grader_revision") != GRADER_REVISION
        or not suite.get("goldens")
    ):
        raise ValueError("missing, empty, or incompatible required suite")
    if not isinstance(suite["goldens"], list):
        raise ValueError("goldens must be an array")
    ids = set()
    for case in suite["goldens"]:
        if not isinstance(case, dict):
            raise ValueError("case must be an object")
        if case.get("review_status") not in {"reference_contract", "approved"}:
            raise ValueError("unreviewed mining candidates cannot gate releases")
        if case.get("review_status") == "approved" and not case.get("reviewed_by"):
            raise ValueError("approved cases need reviewer provenance")
        for key in ("id", "input", "criteria", "split"):
            if not isinstance(case.get(key), str) or not case[key]:
                raise ValueError(f"missing case {key}")
        if case["id"] in ids:
            raise ValueError("duplicate case ID")
        ids.add(case["id"])
        if not isinstance(case.get("principal"), (str, type(None))):
            raise ValueError("invalid principal")
        for key in ("expected_tools",):
            if not isinstance(case.get(key), list) or not all(
                isinstance(t, str) for t in case[key]
            ):
                raise ValueError(f"invalid {key}")
        tool_correctness([], case["expected_tools"], case["tool_match"])
        threshold = case.get("tool_threshold")
        if type(threshold) not in (float, int) or not 0 <= threshold <= 1:
            raise ValueError("invalid tool threshold")
        if not isinstance(case.get("expected_arguments"), dict) or not all(
            isinstance(k, str) and isinstance(v, dict)
            for k, v in case["expected_arguments"].items()
        ):
            raise ValueError("missing expected arguments")
        state = case.get("expected_state")
        if not isinstance(state, dict) or set(state) != {"refunds", "reschedules"}:
            raise ValueError("explicit expected state required")
        if not all(isinstance(v, list) for v in state.values()):
            raise ValueError("invalid expected state")
        for key, entries in state.items():
            keys = {"order_id", "amount" if key == "refunds" else "date"}
            for entry in entries:
                if (
                    not isinstance(entry, dict)
                    or set(entry) != keys
                    or not isinstance(entry["order_id"], str)
                    or not entry["order_id"]
                ):
                    raise ValueError("invalid expected state entry")
                if key == "refunds" and (
                    type(entry["amount"]) not in (int, float)
                    or not math.isfinite(entry["amount"])
                    or entry["amount"] <= 0
                ):
                    raise ValueError("invalid expected refund amount")
                if key == "reschedules" and not isinstance(entry["date"], str):
                    raise ValueError("invalid expected reschedule date")


def grade(trajectory, case: dict) -> list[str]:
    findings = flag_failures(trajectory)  # All invariants, not just the mined label.
    score = tool_correctness(
        [c["name"] for c in trajectory.tool_calls], case["expected_tools"], case["tool_match"]
    )
    if score < case["tool_threshold"]:
        findings.append("required-tools")
    findings.extend(argument_mismatches(trajectory.tool_calls, case["expected_arguments"]))
    if trajectory.final_state != case["expected_state"]:
        findings.append("expected-state")
    required_claims = [
        {"kind": kind, "order_id": entry["order_id"]}
        for key, kind in (("refunds", "refund"), ("reschedules", "reschedule"))
        for entry in case["expected_state"][key]
    ]
    if sorted(trajectory.claims, key=str) != sorted(required_claims, key=str):
        findings.append("expected-completion-claims")
    return findings


def wilson(successes: int, total: int) -> list[float] | None:
    """95% Wilson interval for a binary proportion; no data is not zero risk."""
    if total == 0:
        return None
    p, z = successes / total, 1.959963984540054
    scale = 1 + z * z / total
    center = (p + z * z / (2 * total)) / scale
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / scale
    return [max(0.0, center - margin), min(1.0, center + margin)]


def summarize(rows: list[dict]) -> dict:
    counts = Counter(row["status"] for row in rows)
    total = len(rows)
    return {
        "attempts": total,
        "passed": counts["passed"],
        "failed": counts["failed"],
        "invalid": counts["invalid"],
        "success_rate": counts["passed"] / total if total else None,
        "wilson95": wilson(counts["passed"], total),
    }


def evaluate(
    suite: dict, tracer, *, version="v2", backend="scripted", tool_mode="enforced", repeats=1
) -> dict:
    validate_suite(suite)
    if type(repeats) is not int or not 1 <= repeats <= 100:
        raise ValueError("repeats must be between 1 and 100")
    rows = []
    for case in suite["goldens"]:
        for attempt in range(1, repeats + 1):
            _, trajectory = run_agent_and_capture_trace(
                tracer,
                case["input"],
                scenario_id=case["id"],
                principal=case.get("principal"),
                version=version,
                backend=backend,
                tool_mode=tool_mode,
            )
            findings = grade(trajectory, case)
            status = (
                "invalid"
                if trajectory.status != "completed"
                else "failed"
                if findings
                else "passed"
            )
            rows.append(
                {
                    "case_id": case["id"],
                    "split": case["split"],
                    "attempt": attempt,
                    "status": status,
                    "findings": findings,
                    "trajectory": asdict(trajectory),
                }
            )
    source = Path(__file__).parent
    digest = hashlib.sha256()
    for path in sorted(source.glob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return {
        "schema_version": 1,
        "suite_version": suite["version"],
        "suite_sha256": hashlib.sha256(json.dumps(suite, sort_keys=True).encode()).hexdigest(),
        "task_revision": suite["task_revision"],
        "grader_revision": GRADER_REVISION,
        "source_sha256": digest.hexdigest(),
        "runtime": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "trace2evals": distribution_version("trace2evals"),
            "opentelemetry-sdk": distribution_version("opentelemetry-sdk"),
        },
        "selection": suite.get("selection"),
        "agent_version": version,
        "backend": backend,
        "tool_mode": tool_mode,
        "repeats": repeats,
        "summary": summarize(rows),
        "splits": {
            split: summarize([r for r in rows if r["split"] == split])
            for split in sorted({r["split"] for r in rows})
        },
        "uncertainty_note": "Descriptive Wilson intervals assume independent Bernoulli trials. Synthetic task selection and repeated scripted trials do not establish population quality.",
        "judge": "disabled: no human calibration; free-text semantic truthfulness is not gated",
        "attempts": rows,
    }
