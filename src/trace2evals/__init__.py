"""trace2evals: turn agent traces into a versioned regression eval suite.

Companion repo for "Evaluating AI Agents in Production: From Traces to Test
Suites". The flywheel: run agent -> capture OTel traces -> mine failures ->
cluster + dedupe -> versioned goldens -> CI gate that re-runs the agent.
"""

__version__ = "0.1.0"
