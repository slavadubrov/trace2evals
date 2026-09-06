"""Teaching traffic uses regression inputs only; holdout is excluded from mining."""

import json
from importlib.resources import files

from .agent import run_agent_and_capture_trace


def reference_suite() -> dict:
    return json.loads(files("trace2evals").joinpath("data/suite-v1.json").read_text())


SCENARIOS = [
    (g["id"], g["input"]) for g in reference_suite()["goldens"] if g["split"] == "regression"
]


def run_scenarios(tracer, *, version="v1", backend="scripted", tool_mode="vulnerable"):
    trajectories = []
    for case in reference_suite()["goldens"]:
        if case["split"] != "regression":
            continue
        answer, trajectory = run_agent_and_capture_trace(
            tracer,
            case["input"],
            scenario_id=case["id"],
            principal=case["principal"],
            version=version,
            backend=backend,
            tool_mode=tool_mode,
        )
        print(f"{case['id']}: {answer or trajectory.error}")
        trajectories.append(trajectory)
    return trajectories
