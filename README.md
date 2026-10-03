# trace2evals

A small, installable teaching repository for [AI Agent Evaluation in Production:
Traces to Test Suites](https://slavadubrov.com/blog/2026/06/10/agent-evals-traces-to-test-suites/).

A plausible answer does not prove that an agent performed the right action.
This demo runs a support agent, records its tool calls and shop state, mines
failures, and checks fresh runs against explicit task contracts.

```text
synthetic requests → agent + isolated tools → OTel JSONL → failure candidates
                                                           ↓ review expectations
                       report ← fresh agent runs ← pinned task suite
```

The scripted backend reproduces planted mistakes without a model or an API key.
The optional OpenAI backend runs the same loop with `gpt-5.6-luna`. **A key never
selects live mode automatically.** Nothing here touches a real order or payment.

## Start here

Python 3.11+ and [uv](https://docs.astral.sh/uv/) are required.

```bash
uv sync --locked
make demo
make check
```

`make demo` regenerates its scratch trace file and generates six teaching traces, mines four failed requests, and emits
review candidates. It then runs ten explicit contracts three ways:

| Agent policy | Tool enforcement | Expected result |
| --- | --- | --- |
| v1, planted bugs | vulnerable | 5 failures; exit 1 |
| v2, corrected policy | vulnerable | 10 passes |
| v2, corrected policy | enforced | 10 passes |

Four failures reproduce the source report; the fifth is a wrong date on a
separate control task. An infrastructure error returns exit 2 and **does not**
count as the expected red result. The demo requires precisely exit 1 for red.
The following green commands must also pass.

Inspect `data/demo/red.json`, `prompt-only.json`, and `green.json`. Each report
contains all attempts, findings, traces, state, suite and code hashes, model
metadata, token counts, and execution limits. The `regression` and `holdout`
splits are reported separately.

The four synthetic holdout cases are excluded from mining. They are visible in
this educational repository and were exercised during development; they are
not a secret benchmark or evidence of generalization to production traffic.

## Run each stage

```bash
uv run trace2evals traffic --spans data/my-run/spans.jsonl
uv run trace2evals mine --spans data/my-run/spans.jsonl --out data/my-run/trajectories.json
uv run trace2evals emit --trajectories data/my-run/trajectories.json --dataset-dir data/my-run/candidates
uv run trace2evals evaluate --report data/my-run/evaluation.json
```

Tracing appends to preserve evidence. Use a new path for a separate batch.
Emitting identical candidates preserves their numeric version; new content uses
`max(version) + 1`. Changed resource IDs, dates and principals remain distinct.
All failure labels survive promotion. The emitter never copies failed behavior
into an approved expectation.

**Candidates are not release tests yet.** Read the evidence, write expected
state and allowed tool behavior, and record reviewer provenance. See
[the evaluation guide](docs/evaluation.md). The bundled reference suite already
contains explicit synthetic task contracts; it is identified as
`reference_contract`, not falsely attributed to a human reviewer.

## Run OpenAI Luna

```bash
uv sync --locked --extra live
# Put OPENAI_API_KEY in .env, which is ignored by Git.
uv run --extra live --env-file .env trace2evals evaluate \
  --backend openai --case status-check --report data/runs/luna-smoke.json

# Ten tasks, one attempt each; costs real API usage.
uv run --extra live --env-file .env trace2evals evaluate \
  --backend openai --report data/runs/luna-suite.json
```

`--case` is a smoke test, not a full gate. Set `AGENT_MODEL=gpt-5.6-terra` to
compare Terra explicitly. The default model is `gpt-5.6-luna`, with low reasoning
effort, 2,048 output tokens per request, no SDK retries, and a 30-second request
timeout. A trial allows 10 tool rounds, at most 12 tool calls, and at most 11
model requests including the final response. These are execution bounds, not
a guaranteed currency budget or an overall wall-clock timeout.

The adapter uses [Responses function calling](https://developers.openai.com/api/docs/guides/function-calling)
and [structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs).
It preserves reasoning items during continuation, validates final claims, and
records incomplete/refused/malformed responses as invalid trials. It sets
`store=False`; consult provider data policies for retention semantics.

Only `OPENAI_API_KEY` and optional `AGENT_MODEL` are needed for the CLI. Backend,
agent version, and tool mode are explicit command options. For the Python API
and pytest wrapper, `AGENT_BACKEND` and `AGENT_VERSION` can set defaults.
`uv --env-file` loads configuration; editing `.env` takes effect on the next
command. There is no background server to restart.

## Install and reuse

The package installs and runs outside the checkout, including the bundled
suite. It is not published to PyPI.

```bash
uv build
uv tool install ./dist/trace2evals-0.2.0-py3-none-any.whl
trace2evals evaluate --report ./evaluation.json

# Inside another uv project:
uv add /absolute/path/to/trace2evals
```

The public imports are small and independent of a model provider:

```python
from trace2evals import Trajectory, argument_mismatches, tool_correctness

assert tool_correctness(["lookup", "verify", "refund"], ["verify", "refund"]) == 1.0
assert tool_correctness(["refund"], [], mode="exact") == 0.0
assert argument_mismatches(
    [{"name": "reschedule", "arguments": {"date": "2026-09-20"}}],
    {"reschedule": {"date": "2026-09-20"}},
) == []
```

Use `trace2evals.scorers` for other domains. `evaluate`, `mine`, and the mock
shop are intentionally domain-specific examples: replace their contracts and
policy checks for a different application. No agent framework, database,
service, or vendor evaluation platform is required.

## Read the code

| File | Responsibility |
| --- | --- |
| `models.py` | Run evidence without provider or tracing dependencies |
| `backends.py` | Scripted decisions and OpenAI Responses conversation |
| `agent.py` | Bounded execution and preservation of failed attempts |
| `tools.py` | Per-trial shop, input checks, authorization and idempotency |
| `tracing.py` | Instance-owned local OTel exporter; no global provider mutation |
| `mine.py` | Versioned trace adapter and retrospective domain invariants |
| `scorers.py` | Tool sequence, every-invocation arguments, repeat counting |
| `emit.py` | Exact dedupe and versioned review candidates |
| `evaluate.py` | Required suite validation, fresh runs and attempt reports |
| `data/suite-v1.json` in the package | Explicit regression and control contracts |

Read [architecture and boundaries](docs/architecture.md) and
[evaluation semantics](docs/evaluation.md).

## Deliberate limits

- This is synthetic onboarding material. Scripted success is a software check,
  not an estimate of LLM quality. Ten live tasks are a smoke-sized sample.
- The mock email ceremony is not authentication. The host supplies a trusted
  principal. Enforced tools check it before writes; prompt-only v2 is shown as
  a separate comparison. The vulnerable mode is explicitly named.
- Full refunds use an in-memory, per-session operation identity. Production
  idempotency requires durable transactional storage across processes and retries.
- Completion claims are structured and checked against tool results and state.
  Free-text truthfulness, refusal helpfulness and tone are not measured by this
  deterministic gate. No LLM judge ships with the repository; the evaluation
  guide explains the calibration needed to add one.
- The JSONL adapter supports this repository's **schema v2**, not arbitrary OTel
  exports or a claim of complete GenAI semantic-convention compliance.
- Traces contain full synthetic inputs and tool data. Redact real data before
  importing it, and apply retention/access controls. Traces are written to
  local JSONL only; nothing is exported over the network.
