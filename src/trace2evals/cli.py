"""CLI for the trace-to-eval flywheel: traffic -> mine -> emit.

Each step reads/writes plain local files, so `mine` and `emit` are fully
offline. `traffic` uses the scripted backend by default and a real model when
ANTHROPIC_API_KEY is set (see AGENT_BACKEND / AGENT_VERSION in .env.example).
"""

from __future__ import annotations

import json
import os
from argparse import ArgumentParser
from pathlib import Path

from .emit import DEFAULT_DATASET_DIR, emit_dataset
from .mine import mine
from .tracing import DEFAULT_SPANS_PATH, init_tracing

DEFAULT_TRAJECTORIES_PATH = Path("data/traces/trajectories.json")


def build_parser() -> ArgumentParser:
    parser = ArgumentParser(prog="trace2evals")
    commands = parser.add_subparsers(dest="command", required=True)

    traffic = commands.add_parser("traffic", help="run the agent over the demo scenarios")
    traffic.add_argument("--spans", type=Path, default=DEFAULT_SPANS_PATH)
    traffic.add_argument("--agent-version", choices=["v1", "v2"], default=None)

    mine_cmd = commands.add_parser("mine", help="rebuild trajectories from spans, flag failures")
    mine_cmd.add_argument("--spans", type=Path, default=DEFAULT_SPANS_PATH)
    mine_cmd.add_argument("--out", type=Path, default=DEFAULT_TRAJECTORIES_PATH)

    emit_cmd = commands.add_parser("emit", help="cluster failures into a versioned golden dataset")
    emit_cmd.add_argument("--trajectories", type=Path, default=DEFAULT_TRAJECTORIES_PATH)
    emit_cmd.add_argument("--dataset-dir", type=Path, default=DEFAULT_DATASET_DIR)

    return parser


def main() -> None:
    args = build_parser().parse_args()

    if args.command == "traffic":
        if args.agent_version:
            os.environ["AGENT_VERSION"] = args.agent_version
        # Fresh trace store per traffic run — append-forever stores make every
        # later mining pass re-discover stale failures.
        if args.spans.exists():
            args.spans.unlink()
        tracer = init_tracing(spans_path=args.spans)
        from .scenarios import run_scenarios

        run_scenarios(tracer)
        print(f"\nspans written to {args.spans}")
        return

    if args.command == "mine":
        mine(args.spans, args.out)
        return

    if args.command == "emit":
        trajectories = json.loads(args.trajectories.read_text(encoding="utf-8"))
        emit_dataset(trajectories, args.dataset_dir)
        return

    raise AssertionError(args.command)


if __name__ == "__main__":
    main()
