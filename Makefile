.PHONY: install traffic mine evals test unit flywheel demo fmt clean

install:
	uv sync

# 1. Run the agent over scripted scenarios; spans land in data/traces/spans.jsonl.
#    Scripted backend by default; real model when ANTHROPIC_API_KEY is set.
traffic:
	uv run trace2evals traffic

# 2. Reconstruct trajectories from raw spans and flag failures (fully offline)
mine:
	uv run trace2evals mine

# 3. Cluster failures, dedupe, emit a versioned golden dataset (fully offline)
evals:
	uv run trace2evals emit

# 4. The CI gate: re-run the agent on every golden and grade the fresh trajectory
test:
	uv run pytest evals -q

# Unit tests for the scorers, miner, and emitter (no agent run, no keys)
unit:
	uv run pytest tests -q

# The whole loop against the current agent version
flywheel: traffic mine evals test

# The full story: mine the buggy v1 agent, watch the gate catch it (RED),
# then run the same gate against the fixed v2 agent (GREEN).
demo: traffic mine evals
	@echo ""
	@echo "=== gate vs buggy agent (AGENT_VERSION=v1) — expected RED ==="
	-uv run pytest evals -q
	@echo ""
	@echo "=== gate vs fixed agent (AGENT_VERSION=v2) — expected GREEN ==="
	AGENT_VERSION=v2 uv run pytest evals -q

fmt:
	uv run ruff format src tests evals
	uv run ruff check --fix src tests evals

clean:
	rm -f data/traces/spans.jsonl data/traces/trajectories.json
