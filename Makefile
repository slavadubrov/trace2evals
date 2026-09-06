.PHONY: install traffic mine evals test unit demo check
install:
	uv sync --locked
traffic:
	uv run trace2evals traffic
mine:
	uv run trace2evals mine
evals:
	uv run trace2evals emit
test:
	uv run trace2evals evaluate
unit:
	uv run pytest tests -q
check:
	uv run ruff check src tests evals
	uv run ruff format --check src tests evals
	uv run pytest -q
demo:
	rm -f data/demo/spans.jsonl
	uv run trace2evals traffic --spans data/demo/spans.jsonl
	uv run trace2evals mine --spans data/demo/spans.jsonl --out data/demo/trajectories.json
	uv run trace2evals emit --trajectories data/demo/trajectories.json --dataset-dir data/demo/candidates
	@uv run trace2evals evaluate --agent-version v1 --tool-mode vulnerable --report data/demo/red.json; code=$$?; test $$code -eq 1
	uv run trace2evals evaluate --agent-version v2 --tool-mode vulnerable --report data/demo/prompt-only.json
	uv run trace2evals evaluate --agent-version v2 --tool-mode enforced --report data/demo/green.json
