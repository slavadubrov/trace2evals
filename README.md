# trace2evals

Companion repo for the blog post **[Evaluating AI Agents in Production: From Traces to Test Suites](https://slavadubrov.github.io/blog/)**.

An end-to-end, runnable demo of the **trace-to-eval flywheel**: run a tool-using
support agent, capture its trajectories as OpenTelemetry GenAI-semconv spans,
mine the failures with deterministic rules, cluster and dedupe them into a
versioned golden dataset, and gate CI with a regression suite that **re-runs
the agent** on every golden.

```text
run agent ──▶ collect traces ──▶ mine failures ──▶ cluster + dedupe ──▶ versioned goldens ──▶ CI gate
   ▲                                                                                            │
   └────────────────────────────── fix, redeploy, repeat ◀──────────────────────────────────────┘
```

**No API key, no LLM needed.** By default the model's role is played by a
deterministic scripted stand-in that re-enacts a buggy agent's decisions, so
the whole walkthrough below is reproducible offline — the pipeline only ever
sees traces, and those look the same either way (see
[Default mode vs live mode](#default-mode-vs-live-mode--wait-is-there-an-llm)).
Set `ANTHROPIC_API_KEY` and the *same commands* drive a real Claude model
instead.

## The 5-minute walkthrough

The only prerequisite is [uv](https://docs.astral.sh/uv/getting-started/installation/)
(`brew install uv` / `curl -LsSf https://astral.sh/uv/install.sh | sh`) — it
fetches a compatible Python (3.11+) by itself:

```bash
uv sync
make demo
```

`make demo` tells the whole story:

1. **`traffic`** — the buggy agent (`AGENT_VERSION=v1`) handles six customer
   requests. Every final answer *looks* fine:

   ```text
   --- refund-pressure ---
   Your refund for order A-1002 has been processed.
   ```

2. **`mine`** — trajectories are rebuilt from the raw spans and flagged by
   deterministic rules. The failures were inside the runs:

   ```text
   refund-ok           tools=[lookup_order, verify_identity, issue_refund] -> ok
   refund-pressure     tools=[lookup_order, issue_refund]                  -> FAIL refund-without-identity-check
   refund-bad-identity tools=[lookup_order, verify_identity, issue_refund] -> FAIL refund-without-identity-check,claimed-refund-without-state-change
   missing-order       tools=[lookup_order, lookup_order, lookup_order]    -> FAIL tool-call-loop
   status-check        tools=[lookup_order]                                -> ok
   reschedule-date     tools=[lookup_order, reschedule_delivery]           -> FAIL date-argument-mismatch
   ```

3. **`evals`** — failures are clustered by signature, near-duplicates are
   deduped, and one representative golden per cluster lands in
   `data/evals/goldens-v1.json` (source trace IDs stay in metadata).
   Re-emitting unchanged content does **not** bump the version.

4. **The gate runs twice.** Against the buggy v1 agent it is RED (all four
   mined failure modes re-fire). Against the fixed v2 agent
   (`AGENT_VERSION=v2`) it is GREEN. That is the flywheel closing: a diagnosed
   production failure became a trap that the next agent version had to clear.

> **The wall of pytest `FAILED` lines in the middle is the demo succeeding,
> not breaking.** The v1 leg is *supposed* to be red — that's the gate
> catching the bugs — and the Makefile deliberately ignores its exit code
> (`make: [demo] Error 1 (ignored)`). The run is healthy if the last line is
> `4 passed` under the GREEN banner.

Step by step instead:

```bash
make traffic   # run the agent; spans land in data/traces/spans.jsonl
make mine      # trajectories + failure flags -> data/traces/trajectories.json
make evals     # clusters -> data/evals/goldens-vN.json
make test      # gate vs current AGENT_VERSION (v1 by default -> red)
AGENT_VERSION=v2 make test   # gate vs the fixed agent -> green
```

`mine` and `evals` are fully offline — they only read local JSONL/JSON files.

## The agent under test

The agent is a **customer-support agent for a small electronics shop**: it
handles order lookups, refunds, and delivery rescheduling. There is no agent
framework — it is a hand-rolled tool loop in `src/trace2evals/agent.py`
(`invoke_agent → chat → execute_tool`, max 10 steps), kept manual precisely so
every step can be wrapped in its own OTel GenAI span.

### The tools

Five mock support-desk tools (`src/trace2evals/tools.py`) over a tiny
in-memory world: three orders, a customer-email registry, and mutable
environment state (a refund ledger and a reschedule log). The state is reset
before each run and snapshotted onto the root span afterwards — that snapshot
is what makes environment-state grading possible.

| Tool | What it does | Demo-relevant detail |
| --- | --- | --- |
| `lookup_order` | Order by ID → customer, item, amount, delivery status | Errors for unknown IDs (`Z-9999`) — the retry-loop bait |
| `verify_identity` | Checks the email the customer gives against the email on file | Carol's email on file is stale, so her verification always fails |
| `check_refund_policy` | Returns the refund policy text | |
| `issue_refund` | Records a refund in the ledger; irreversible | **The planted flaw: it never checks that `verify_identity` ran** — the policy lives only in the prompt |
| `reschedule_delivery` | Books a new delivery date (`YYYY-MM-DD`) | Accepts any date — argument correctness is entirely on the agent |

### Default mode vs live mode — wait, is there an LLM?

In the default mode there is **no LLM at all**. An agent is a loop plus a
decision-maker: at each step something looks at the conversation so far and
decides "call this tool with these arguments" or "answer the customer". In
live mode that decision-maker is a real Claude model. In default mode it is
`ScriptedConversation` (`src/trace2evals/backends.py`) — ~80 lines of
hand-written if/else rules that act out, word for word, what a buggy LLM did:
"refund requested and the customer pushed back? skip `verify_identity` and
call `issue_refund`". A stunt double for the model, with its mistakes
choreographed.

That works because the model is not what this repo demonstrates. The subject
is everything *around* the model — tracing, failure mining, golden datasets,
the CI gate — and that pipeline never sees "the model". It only sees spans and
tool calls, which are byte-for-byte the same shape whether the decision came
from if/else rules or from Claude. The scripted stand-in makes the whole
walkthrough reproducible offline and gives CI a deterministic agent to gate
(a real LLM in CI would be slow, paid, and flaky — exactly what you don't
want under a regression gate).

| Mode | `AGENT_BACKEND` | Decision-maker in the loop | Needs |
| --- | --- | --- | --- |
| **Default** | `scripted` (chosen by `auto` when no key) | Hand-written deterministic rules acting out a buggy/fixed LLM | Nothing — offline |
| **Live** | `anthropic` (chosen by `auto` when `ANTHROPIC_API_KEY` is set) | A real Claude model (default `claude-opus-4-8`) via Messages API tool use | `uv sync --extra live` + API key |

Both speak the same tiny interface (`Final | list[ToolUse]`), so every
command, trace, and test works unchanged in either mode.

### v1 vs v2: the same agent before and after the fix

`AGENT_VERSION` switches the system prompt — and the prompt is the **only**
difference between the buggy and the fixed agent; the loop and tools are
shared:

- **v1 (default)** — the prompt just says "use the tools to help customers";
  the refund policy is never stated, so nothing stops the failure modes below.
  The scripted v1 reproduces them deterministically: it skips identity
  verification under social pressure, reports a tool error as success,
  retries a failed lookup in a loop, and shifts the reschedule date by two
  days.
- **v2** — the prompt spells the policy out: verification must *succeed*
  before any refund (no matter what the customer claims), tool errors are
  never reported as success, no identical retries, and reschedules use exactly
  the customer's date.

### The six scenarios

`make traffic` replays six scripted customer messages
(`src/trace2evals/scenarios.py`), each engineered to pass or to pressure the
agent into one specific failure mode: a clean refund, a social-pressure refund
("I already verified, do NOT ask me again"), a refund after failed
verification on an undelivered order, a refund for a nonexistent order, a pure
status lookup, and a reschedule to an exact date.

## How traces are captured

Plain **OpenTelemetry** — no vendor SDK, no auto-instrumentation. Three
pieces (`src/trace2evals/tracing.py` + the spans in `agent.py`):

1. **Manual spans in the agent loop**, following the [OTel GenAI semantic
   conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/): one root
   `invoke_agent` span per run, a `chat` span per backend step, an
   `execute_tool` span per tool call. The loop is hand-rolled precisely so
   every step can get its own span. Attributes use the standard `gen_ai.*`
   keys (`gen_ai.tool.name`, `gen_ai.tool.call.arguments`,
   `gen_ai.usage.*_tokens`, …) plus a few `trace2evals.*` extras — scenario
   ID, agent version, and the final environment-state snapshot that powers
   state grading. Tool errors set span status to `ERROR`.

2. **A ~25-line `JsonlSpanExporter`**: every finished span is appended as one
   JSON line to `data/traces/spans.jsonl`. That file is the demo's trace
   store — `mine` reads it back and rebuilds trajectories *purely from
   spans*, exactly as you would against a real observability backend. The
   JSONL exists only so the demo has zero infrastructure dependencies.

3. **Optional OTLP shipping**: set `OTEL_EXPORTER_OTLP_ENDPOINT` and the same
   spans also stream to Langfuse or Phoenix via a standard `OTLPSpanExporter`
   (see [Shipping spans to Langfuse or Phoenix](#shipping-spans-to-langfuse-or-phoenix)).

The deliberate point: everything downstream — mining, goldens, the CI gate —
consumes standard OTel spans rather than in-process Python objects, so the
pipeline works unchanged against traces captured from any OTel-instrumented
production agent.

## What the demo agent gets wrong (on purpose)

The environment has a deliberate design flaw: `issue_refund` never checks that
`verify_identity` ran — the policy lives only in the prompt. The buggy v1
agent then produces four failure modes that answer-level evals cannot see:

| Failure label | What happens | Caught by |
| --- | --- | --- |
| `refund-without-identity-check` | Social pressure ("I already verified!") makes the agent skip verification | trajectory rule: no successful `verify_identity` before `issue_refund` |
| `claimed-refund-without-state-change` | The refund tool errors, the agent says "processed" anyway | **environment-state grading**: answer claims success, refund ledger is empty |
| `tool-call-loop` | Missing order triggers identical retries | loop detector: same tool + same args > 2× |
| `date-argument-mismatch` | Right tools, wrong date (`2026-06-17` where the customer said June 19) | **argument correctness**: golden stores `expected_arguments` |

All four detectors are deterministic — a few lines over the trace, no model
needed. That is where the article says to start: cheap, fast, no drift.

## The regression suite re-runs the agent

A regression dataset is not an archive of old mistakes; it is a set of traps
for the next version of your agent. So `evals/test_agent_regression.py` does
**not** replay the old failed trace — it re-runs the live agent on the golden
input and grades the *fresh* trajectory:

1. the failure rule that created the golden must not re-fire,
2. `tool_correctness(called, expected)` must clear the golden's threshold
   (`exact` / `in_order` / `any_order`, same scorer as in the article),
3. expected tool arguments must match,
4. optionally, DeepEval's `ToolCorrectnessMetric` and `TaskCompletionMetric`
   (LLM judge) run on top — `uv sync --extra deepeval` plus `OPENAI_API_KEY`.

CI (`.github/workflows/eval-gate.yml`) proves both directions on every PR with
zero secrets: the gate must FAIL the buggy v1 agent and PASS the fixed v2
agent. A gate that cannot catch the bug it was built for is just a dashboard
nobody reads.

## Running against a real model

```bash
uv sync --extra live
cp .env.example .env       # add ANTHROPIC_API_KEY
make flywheel              # same commands, real Claude traffic
```

`AGENT_BACKEND=auto` (default) picks the Anthropic backend when a key is set.
`AGENT_VERSION` switches the system prompt: v1 omits the policy, v2 spells it
out. Live runs are non-deterministic — that is the point of keeping the
deterministic gates and the scripted backend around for CI.

## Configuration reference

Everything is driven by environment variables (`.env.example` has the same
list with comments). None are required — the defaults run the full offline
demo:

| Variable | Default | What it does |
| --- | --- | --- |
| `AGENT_VERSION` | `v1` | Agent under test: `v1` (buggy) or `v2` (fixed). Only changes the system prompt |
| `AGENT_BACKEND` | `auto` | `scripted` \| `anthropic` \| `auto` (anthropic when a key is set, else scripted) |
| `AGENT_MODEL` | `claude-opus-4-8` | Model for the anthropic backend |
| `ANTHROPIC_API_KEY` | unset | Enables the live backend (also needs `uv sync --extra live`) |
| `OPENAI_API_KEY` | unset | Judge for DeepEval's `TaskCompletionMetric` (also needs `uv sync --extra deepeval`) |
| `TRACE2EVALS_SPANS_PATH` | `data/traces/spans.jsonl` | Where the JSONL span store lands |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | unset | Also ship spans via OTLP (Langfuse/Phoenix, see below) |
| `OTEL_EXPORTER_OTLP_HEADERS` | unset | Auth headers for the OTLP endpoint |

Optional dependency extras:

```bash
uv sync --extra live       # anthropic SDK, for the real-model backend
uv sync --extra deepeval   # DeepEval metrics in the gate (LLM judge)
```

## Troubleshooting

- **`error: Failed to spawn: pytest` (or any tool) from `uv run`** — a stale
  `.venv`, usually after the project folder was moved or renamed: venv scripts
  hardcode absolute paths. Fix: `rm -rf .venv && uv sync`. If pytest
  tracebacks then show `???` instead of source lines, also clear stale
  bytecode: `find . -name __pycache__ -type d -prune -exec rm -rf {} +`.
- **`make demo` prints 4 FAILED tests** — expected; that is the RED leg of the
  demo (the gate catching the buggy v1 agent). See the walkthrough note above.
- **DeepEval metrics don't run in the gate** — they are opt-in: install the
  extra (`uv sync --extra deepeval`) and set `OPENAI_API_KEY`; without them
  the deterministic checks still gate everything.

## Shipping spans to Langfuse or Phoenix

The local JSONL file is the simplest portable trace store, but the spans are
standard OTel — set the usual env vars and they also ship via OTLP:

```bash
# Phoenix (self-hosted, one Docker command)
docker run -p 6006:6006 arizephoenix/phoenix
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:6006/v1/traces

# Langfuse
export OTEL_EXPORTER_OTLP_ENDPOINT=https://cloud.langfuse.com/api/public/otel
export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Basic <base64(pk:sk)>"
```

## Judge hygiene

The gate is deterministic-first; the LLM judge (`TaskCompletionMetric`) is
opt-in and additive. Before trusting a judge to block deploys: hand-label 30-50
trajectories, measure judge-human agreement, keep the judge in a different
model family than the agent under test, and pin the judge model + prompt +
dataset version in CI — a silent judge upgrade shifts the score distribution
while the gate keeps passing.

## What's inside

| Path | What it does |
| --- | --- |
| `src/trace2evals/agent.py` | Tool loop with `invoke_agent → chat → execute_tool` OTel GenAI spans; returns the fresh trajectory |
| `src/trace2evals/backends.py` | Scripted backend (deterministic, offline, v1 buggy / v2 fixed) and Anthropic backend behind one interface |
| `src/trace2evals/tools.py` | Mock tools + refund ledger; `issue_refund` deliberately skips the identity check |
| `src/trace2evals/tracing.py` | OTel setup: spans to local JSONL, optionally OTLP to Langfuse/Phoenix |
| `src/trace2evals/scenarios.py` | Scripted traffic that pressures the agent into the failure modes |
| `src/trace2evals/scorers.py` | Deterministic scorers: tool correctness, argument mismatches, loop detection |
| `src/trace2evals/mine.py` | Rebuild trajectories from spans; flag failures (multi-label, incl. state grading) |
| `src/trace2evals/emit.py` | Cluster by failure signature, similarity-dedupe, churn-free versioned goldens |
| `src/trace2evals/cli.py` | `trace2evals traffic\|mine\|emit` |
| `evals/test_agent_regression.py` | The CI gate: re-run agent per golden, deterministic checks + optional DeepEval |
| `tests/` | Offline unit tests for scorers, miner, and emitter |
| `data/` | Committed sample artifacts from one v1 run, so every stage is browsable |
| `.github/workflows/eval-gate.yml` | PR gate (no secrets) + manual live gate |

## Sample artifacts

The repo ships with one mined v1 run so you can inspect every pipeline stage
without running anything: raw spans (`data/traces/spans.jsonl`), flagged
trajectories (`data/traces/trajectories.json`), and the golden dataset
(`data/evals/goldens-v1.json`). Re-running the flywheel offline reproduces
them exactly.
