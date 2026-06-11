"""Deterministic scorers — cheap, fast, and they do not drift.

tool_correctness is the exact function from the article. The argument and loop
checks back the "argument correctness" and "efficiency, loops, and dead ends"
sections: right tool plus wrong arguments is still broken, and a loop detector
is a few lines over the trace — it does not need a model.
"""

from __future__ import annotations

import json


def tool_correctness(called: list[str], expected: list[str], mode: str = "in_order") -> float:
    """Score the called tools against the expected tools.

    - exact: the sequence must match exactly (order is policy).
    - in_order: required tools must appear in relative order; extras are allowed.
    - any_order: required tools must appear; order does not matter.
    """
    if not expected:
        return 1.0
    if mode == "exact":
        return float(called == expected)
    if mode == "any_order":
        return len(set(expected) & set(called)) / len(set(expected))

    # in_order: fraction of expected tools that appear in the correct relative
    # order (longest common subsequence), so one missing tool does not zero out
    # the credit for everything called after it.
    rows = [[0] * (len(expected) + 1) for _ in range(len(called) + 1)]
    for i, tool in enumerate(called):
        for j, wanted in enumerate(expected):
            if tool == wanted:
                rows[i + 1][j + 1] = rows[i][j] + 1
            else:
                rows[i + 1][j + 1] = max(rows[i][j + 1], rows[i + 1][j])
    return rows[-1][-1] / len(expected)


def argument_mismatches(tool_calls: list[dict], expected_arguments: dict[str, dict]) -> list[str]:
    """Compare observed tool arguments with the expected ones stored in the golden.

    For every tool with expected arguments, the last observed call must match on
    each expected key. Returns human-readable mismatch descriptions (empty = pass).
    """
    mismatches = []
    for tool_name, expected in expected_arguments.items():
        observed = [c for c in tool_calls if c["name"] == tool_name]
        if not observed:
            mismatches.append(f"{tool_name}: expected a call, but none was observed")
            continue
        arguments = observed[-1]["arguments"]
        for key, value in expected.items():
            if arguments.get(key) != value:
                mismatches.append(
                    f"{tool_name}.{key}: expected {value!r}, got {arguments.get(key)!r}"
                )
    return mismatches


def redundant_call_count(tool_calls: list[dict]) -> int:
    """Max number of times the same tool was called with identical arguments."""
    seen: dict[str, int] = {}
    for call in tool_calls:
        key = call["name"] + json.dumps(call["arguments"], sort_keys=True)
        seen[key] = seen.get(key, 0) + 1
    return max(seen.values(), default=0)
