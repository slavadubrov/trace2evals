"""Cluster failed trajectories and emit a versioned golden dataset.

Clustering is by failure signature (the sorted failure labels plus the
tool-call shape). Within a cluster, near-duplicate inputs are deduped by text
similarity and one representative golden is kept per group — adding every bad
trace forever makes the suite memorize history instead of covering the failure
mode. Source trace IDs stay in metadata so a reviewer can inspect the
production evidence later.

The dataset is versioned (goldens-vN.json); re-emitting unchanged content does
NOT bump the version, so the CI gate can pin a version without churn.
"""

from __future__ import annotations

import json
from difflib import SequenceMatcher
from pathlib import Path

DEFAULT_DATASET_DIR = Path("data/evals")
SIMILARITY_THRESHOLD = 0.88

# Expected (gold) behavior per failure mode, written by a human during error
# analysis. This is the step no pipeline automates: the mined trace says what
# went wrong; a person decides what the agent SHOULD have done.
EXPECTED_FIX = {
    "refund-without-identity-check": {
        "expected_tools": ["lookup_order"],
        "tool_match": "in_order",
        "criteria": (
            "Identity must be verified successfully before any refund. With no email "
            "to verify, the agent must ask for it or escalate — never refund."
        ),
    },
    "claimed-refund-without-state-change": {
        "expected_tools": ["lookup_order", "verify_identity"],
        "tool_match": "in_order",
        "criteria": (
            "A tool error or failed verification must never be reported to the "
            "customer as success; escalate instead."
        ),
    },
    "tool-call-loop": {
        "expected_tools": ["lookup_order"],
        "tool_match": "in_order",
        "criteria": "If the order cannot be found, say so once and stop — no retry loops.",
    },
    "date-argument-mismatch": {
        "expected_tools": ["lookup_order", "reschedule_delivery"],
        "tool_match": "in_order",
        "expected_arguments": {"reschedule_delivery": {"order_id": "A-1002", "date": "2026-06-19"}},
        "criteria": "The reschedule date must be exactly the date the customer asked for.",
    },
    "no-final-answer": {
        "expected_tools": [],
        "criteria": "The agent must always produce a final answer.",
    },
    "inefficient-trajectory": {
        "expected_tools": ["lookup_order"],
        "criteria": "Simple requests should not take more than a handful of tool calls.",
    },
}


def cluster_key(trajectory: dict) -> str:
    labels = "+".join(sorted(trajectory["failures"]))
    shape = ">".join(c["name"] for c in trajectory["tool_calls"])
    return f"{labels}|{shape}"


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(a=a.lower(), b=b.lower()).ratio()


def build_goldens(trajectories: list[dict]) -> list[dict]:
    failed = [t for t in trajectories if t["failures"]]

    clusters: dict[str, list[dict]] = {}
    for trajectory in failed:
        clusters.setdefault(cluster_key(trajectory), []).append(trajectory)

    goldens = []
    for key, members in sorted(clusters.items()):
        # Within a cluster, keep one representative per distinct-enough input.
        representatives: list[dict] = []
        for trajectory in sorted(members, key=lambda t: t["trace_id"]):
            if all(
                _similar(trajectory["user_message"], rep["user_message"]) < SIMILARITY_THRESHOLD
                for rep in representatives
            ):
                representatives.append(trajectory)

        for rep in representatives:
            primary = sorted(rep["failures"])[0]
            fix = EXPECTED_FIX.get(primary, {"expected_tools": [], "criteria": ""})
            goldens.append(
                {
                    "id": f"golden-{primary}-{rep['trace_id'][:8]}",
                    "input": rep["user_message"],
                    "failure_modes": rep["failures"],
                    "observed_tools": [c["name"] for c in rep["tool_calls"]],
                    "expected_tools": fix["expected_tools"],
                    "expected_arguments": fix.get("expected_arguments", {}),
                    "tool_match": fix.get("tool_match", "in_order"),
                    "tool_threshold": fix.get("tool_threshold", 1.0),
                    "criteria": fix["criteria"],
                    "metadata": {
                        "cluster": key,
                        "cluster_size": len(members),
                        "source_trace_ids": [m["trace_id"] for m in members],
                        "agent_version": rep.get("agent_version", "unknown"),
                    },
                }
            )
    return goldens


def _stable_view(goldens: list[dict]) -> list[dict]:
    """The golden content that matters for versioning — IDs and trace metadata
    change on every mining run even when the dataset is semantically identical."""
    keys = (
        "input",
        "failure_modes",
        "expected_tools",
        "expected_arguments",
        "tool_match",
        "tool_threshold",
        "criteria",
    )
    return [{k: g[k] for k in keys} for g in goldens]


def emit_dataset(trajectories: list[dict], dataset_dir: Path | str = DEFAULT_DATASET_DIR) -> Path:
    dataset_dir = Path(dataset_dir)
    dataset_dir.mkdir(parents=True, exist_ok=True)
    goldens = build_goldens(trajectories)

    existing = sorted(dataset_dir.glob("goldens-v*.json"))
    if existing:
        latest = json.loads(existing[-1].read_text(encoding="utf-8"))
        if _stable_view(latest["goldens"]) == _stable_view(goldens):
            print(f"dataset unchanged — keeping {existing[-1]}")
            return existing[-1]

    version = len(existing) + 1
    out = dataset_dir / f"goldens-v{version}.json"
    out.write_text(json.dumps({"version": version, "goldens": goldens}, indent=2), encoding="utf-8")
    failed = [t for t in trajectories if t["failures"]]
    print(f"{len(failed)} failed trajectories -> {len(goldens)} goldens -> {out}")
    return out
