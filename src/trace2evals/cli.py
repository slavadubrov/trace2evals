"""File-based pipeline. Paid model execution requires --backend openai."""

from __future__ import annotations

import json
from argparse import ArgumentParser
from pathlib import Path

from .emit import emit_dataset
from .evaluate import evaluate, load_suite
from .mine import mine
from .scenarios import reference_suite, run_scenarios
from .tracing import init_tracing


def build_parser():
    parser = ArgumentParser(prog="trace2evals")
    commands = parser.add_subparsers(dest="command", required=True)
    traffic = commands.add_parser("traffic", help="generate synthetic regression traffic")
    traffic.add_argument("--spans", type=Path, default=Path("data/traces/spans.jsonl"))
    traffic.add_argument("--agent-version", choices=["v1", "v2"], default="v1")
    traffic.add_argument("--backend", choices=["scripted", "openai"], default="scripted")
    traffic.add_argument("--tool-mode", choices=["vulnerable", "enforced"], default="vulnerable")
    miner = commands.add_parser("mine", help="validate trace schema and flag failures")
    miner.add_argument("--spans", type=Path, default=Path("data/traces/spans.jsonl"))
    miner.add_argument("--out", type=Path, default=Path("data/traces/trajectories.json"))
    emit = commands.add_parser("emit", help="emit versioned review candidates")
    emit.add_argument("--trajectories", type=Path, default=Path("data/traces/trajectories.json"))
    emit.add_argument("--dataset-dir", type=Path, default=Path("data/evals"))
    gate = commands.add_parser(
        "evaluate", help="run a pinned required suite and write all attempts"
    )
    gate.add_argument("--suite", type=Path, help="default: bundled reference suite v1")
    gate.add_argument("--report", type=Path, default=Path("data/runs/report.json"))
    gate.add_argument("--spans", type=Path, default=Path("data/runs/spans.jsonl"))
    gate.add_argument("--agent-version", choices=["v1", "v2"], default="v2")
    gate.add_argument("--backend", choices=["scripted", "openai"], default="scripted")
    gate.add_argument("--tool-mode", choices=["vulnerable", "enforced"], default="enforced")
    gate.add_argument("--repeats", type=int, default=1)
    gate.add_argument("--case", help="run one case by ID (smoke test, not a full suite)")
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.command == "traffic":
            # Append preserves previous evidence. Use a fresh path for a separate batch.
            trajectories = run_scenarios(
                init_tracing(spans_path=args.spans),
                version=args.agent_version,
                backend=args.backend,
                tool_mode=args.tool_mode,
            )
            raise SystemExit(2 if any(t.status == "invalid" for t in trajectories) else 0)
        if args.command == "mine":
            mine(args.spans, args.out)
        elif args.command == "emit":
            print(emit_dataset(json.loads(args.trajectories.read_text()), args.dataset_dir))
        else:
            suite = load_suite(args.suite) if args.suite else reference_suite()
            if args.case:
                suite["goldens"] = [c for c in suite["goldens"] if c["id"] == args.case]
                suite["selection"] = f"single-case smoke test: {args.case}"
            report = evaluate(
                suite,
                init_tracing(spans_path=args.spans),
                version=args.agent_version,
                backend=args.backend,
                tool_mode=args.tool_mode,
                repeats=args.repeats,
            )
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(report["summary"]))
            summary = report["summary"]
            raise SystemExit(2 if summary["invalid"] else 1 if summary["failed"] else 0)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        parser.error(f"invalid configuration or artifact ({type(exc).__name__})")


if __name__ == "__main__":
    main()
