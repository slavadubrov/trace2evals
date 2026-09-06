"""Mine review candidates without pretending observed behavior is ground truth."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .dates import parse_explicit_date

DEFAULT_DATASET_DIR = Path("data/evals")

EXPECTED_FIX = {
    "refund-without-identity-check": "Every refund requires prior successful verification bound to principal and order.",
    "claimed-refund-without-state-change": "Claimed refunds require successful matching results and final state.",
    "claimed-reschedule-without-state-change": "Claimed reschedules require matching successful results and state.",
    "tool-call-loop": "Do not repeat an identical tool call more than twice.",
    "date-argument-mismatch": "Use the exact requested date for every reschedule.",
    "no-final-answer": "Produce a nonempty final answer.",
    "inefficient-trajectory": "Use at most six tool calls for these simple shop tasks.",
    "invalid-run": "Complete within the execution budget without infrastructure or protocol errors.",
}


def cluster_key(trajectory: dict) -> str:
    return "+".join(sorted(trajectory["failures"]))


def build_goldens(trajectories: list[dict]) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for t in trajectories:
        if t["failures"]:
            # Exact dedupe preserves changed order IDs, dates and permission contexts.
            key = json.dumps([t["user_message"], t.get("principal"), sorted(t["failures"])])
            groups.setdefault(key, []).append(t)
    candidates = []
    for key, members in sorted(groups.items()):
        rep = members[0]
        labels = sorted(set(rep["failures"]))
        requirements = [
            {
                "failure_mode": label,
                "criteria": EXPECTED_FIX.get(
                    label, "Unknown rule: reviewer must supply an executable expectation."
                ),
            }
            for label in labels
        ]
        arguments = {}
        if "date-argument-mismatch" in labels:
            date = parse_explicit_date(rep["user_message"])
            orders = re.findall(r"\b[A-Z]-\d{3,4}\b", rep["user_message"])
            if date and len(set(orders)) == 1:
                arguments = {"reschedule_delivery": {"order_id": orders[0], "date": date}}
        candidates.append(
            {
                "id": "candidate-" + hashlib.sha256(key.encode()).hexdigest()[:16],
                "input": rep["user_message"],
                "principal": rep.get("principal"),
                "failure_modes": labels,
                "requirements": requirements,
                "expected_arguments": arguments,
                "review_status": "needs_review",
                "metadata": {
                    "cluster": cluster_key(rep),
                    "cluster_size": len(members),
                    "source_trace_ids": sorted(t["trace_id"] for t in members),
                },
            }
        )
    return candidates


def dataset_files(directory: Path | str) -> list[Path]:
    return sorted(
        (
            p
            for p in Path(directory).glob("goldens-v*.json")
            if re.fullmatch(r"goldens-v[1-9]\d*\.json", p.name)
        ),
        key=lambda p: int(p.stem.split("-v")[-1]),
    )


def _stable_view(goldens: list[dict]) -> list[dict]:
    return [{k: v for k, v in g.items() if k != "metadata"} for g in goldens]


def emit_dataset(trajectories: list[dict], dataset_dir: Path | str = DEFAULT_DATASET_DIR) -> Path:
    directory = Path(dataset_dir)
    directory.mkdir(parents=True, exist_ok=True)
    goldens = build_goldens(trajectories)
    existing = dataset_files(directory)
    if existing:
        latest = json.loads(existing[-1].read_text())
        if latest.get("schema_version") == 2 and _stable_view(latest["goldens"]) == _stable_view(
            goldens
        ):
            return existing[-1]
    version = int(existing[-1].stem.split("-v")[-1]) + 1 if existing else 1
    out = directory / f"goldens-v{version}.json"
    # Exclusive creation refuses a concurrent writer instead of replacing its version.
    with out.open("x", encoding="utf-8") as stream:
        json.dump(
            {
                "schema_version": 2,
                "version": version,
                "selection": "failure-only, exact dedupe; review candidates",
                "goldens": goldens,
            },
            stream,
            indent=2,
        )
    return out
