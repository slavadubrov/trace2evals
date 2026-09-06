# Architecture and boundaries

The learning objective is to make an evaluation result inspectable: which task
ran, what the agent attempted, which effects happened, and why a check failed.
The implementation uses a few ordinary Python modules and JSON artifacts.

## Execution owns state

`run_agent_and_capture_trace` constructs one conversation and one `ToolSession`
per trial. The session receives a trusted principal from the task fixture, never
from model output. Model arguments select an order; they cannot select a new
principal. The enforced session requires ownership before mutations, and a
successful verification bound to that principal and order before refunds.

The email registry is deliberately fake. Knowing an email is not proof of
identity in a real system. Replace this ceremony with the host application's
authentication and authorization service before reusing the example for real
writes. Order reads are public in this synthetic shop; production read access
requires its own authorization policy.

Full refunds must equal the stored order amount and require delivered status.
The old unsupported “within 30 days” policy was removed: the fixture had no
purchase timestamp and could not enforce it. Amount validation rejects booleans,
strings, negative values, NaN, infinity and mismatched totals.

A full refund's operation identity is the order ID. Repeating it in one session
returns the same outcome without appending another effect. Rescheduling dedupes
the order/date pair. This is deliberately limited to per-trial memory, not a
durable payment ledger. A production system needs database uniqueness and
transactions, not another prompt instruction.

The vulnerable mode disables ownership/verification enforcement before writes
so the planted prompt failures remain observable. Input validation, amount
validation and isolated state still apply. v1 and v2 change both scripted
policy behavior and, for OpenAI, the system prompt. They do not change the tool
implementation. The demo compares prompt-only v2 and enforced v2 explicitly.

## Evidence outlives a failed request

The agent loop caps tool rounds and total tool calls. A model timeout, malformed
reply, empty action or exhausted budget creates an `invalid` trajectory with
the state left by earlier calls. The evaluator retains that attempt. It exports
only the exception class, because provider exception text can contain secrets
or private content. Ctrl-C and process termination are not swallowed.

Each `init_tracing` call creates a local provider with its own exporter path.
It does not mutate OTel's global provider or redirect another caller's exporter.
The exporter serializes writes within that instance; use one exporter per file
and a single writer process. Cross-process shared JSONL writes are unsupported.
There is no batch/network exporter to flush and no background model worker.

## Trace adapter contract v2

`tracing.py` emits OTel span envelopes with `trace_id`, `span_id`,
`parent_span_id`, `start_ns`, `end_ns`, `status`, and `attributes`.
Domain evidence uses explicitly custom `trace2evals.*` attributes:

- Exactly one `invoke_agent` span per trace contains `schema_version=2` and a
  serialized trajectory without its tool-call list.
- Each `execute_tool` descendant has a contiguous, zero-based `sequence` and
  a serialized `call` containing name, arguments, result JSON and error status.
- The root metadata records the expected tool-call count; dropping even the
  final tool span is an error.
- Tool spans can be nested under chat spans. Sequence numbers, not timestamps
  or JSONL line order, determine execution order.

The importer rejects unsupported versions, missing roots/attributes, duplicate
span IDs, orphaned/cyclic tool ancestry, sequence gaps, conflicting statuses,
and missing tool spans. It does not silently turn missing evidence into an
empty successful trajectory. This is a local adapter contract, not a generic
OTLP importer. For another exporter, write an adapter and fixtures against
`Trajectory`; do not claim compatibility based on matching a few attribute names.

These artifacts are observations, not signed attestations. A malicious trace
producer can forge evidence. Restrict who can write evaluation inputs in an
actual release pipeline.

## Reuse without a framework

The `src` layout and bundled resource allow the wheel to work outside a Git
checkout. Pure `scorers.py` and `models.py` imports have no provider dependency.
The base package needs OTel for the demonstration pipeline; OpenAI is a `live`
extra. There is no Anthropic or DeepEval dependency.

For another domain, reuse the sequence/argument scorers and run-evidence model.
Write domain invariants and explicit state contracts analogous to `grade` and
`flag_failures`. For another model backend, implement `start(message)` and
`on_tool_results(results)` returning `Final` or a list of `ToolUse`; the existing
`conversation=` hook runs it through the same loop. Python duck typing keeps
this seam visible without introducing a class hierarchy or plugin registry.
