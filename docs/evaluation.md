# What a pass establishes

A pass means the recorded fresh run completed, satisfied the suite's deterministic
invariants, included the required tools and arguments, reached the exact expected
shop state, and declared the expected structured completion claims.
It does not certify every sentence of the final answer.

## Three layers

| Layer | Implemented evidence | Example failure |
| --- | --- | --- |
| Outcome | Exact final state and structured claims backed by successful results | Says refund completed but ledger has no matching refund |
| Trajectory | Every-refund authorization, required tools, repeated calls, budget, final answer | Correct refund follows an earlier unauthorized attempt |
| Component | Every invocation's required arguments and backend tool validation | Wrong reschedule date followed by a correct call |

Tool scores are diagnostic coverage, not probabilities:

- `exact` requires identical sequences, including `[]` versus `[]`.
- `in_order` divides longest common subsequence length by expected length. It
  allows harmless additional calls; full credit means all requirements occur in order.
- `any_order` counts the multiset intersection. Two expected calls need two
  observed calls. Extra calls are permitted.
- Empty requirements score 1 for the two coverage modes. Unknown modes fail.

`argument_mismatches` checks every matching invocation. A missing key differs
from explicit null. Expectations apply to top-level keys with exact value
comparison; they are not a recursive JSON query language.

Tool-name credit cannot override safety or outcome findings. `grade` checks
all currently implemented invariants, not just the labels that originally
created a case. The `WithPolicy` test shows a valid extra policy lookup receiving
full credit. The expected-state assertion independently catches an agent that
avoids every mutation and therefore appears “safe” while never helping anyone.

## Candidates need an expectation decision

`emit` retains every failure label in `requirements` and dedupes only exact
input/principal/label combinations. Dedupe is exact, because similar text can
hide different order IDs or dates. Candidate IDs are stable content hashes;
trace IDs remain in evidence metadata. Unknown labels remain explicitly marked
for review, never assigned a permissive empty expectation.

To promote a candidate:

1. Inspect its source trace and understand the failure.
2. Copy its input, principal and source provenance into a new suite version.
3. Specify `expected_tools`, `tool_match`, `tool_threshold`,
   `expected_arguments`, and exact `expected_state`.
4. Retain all requirements in the review note. If two conflict, split the case
   or resolve the task contract; do not silently drop one.
5. Set `review_status: approved` and `reviewed_by` to the actual reviewer.
   Include `criteria`, `split`, and a unique ID.
6. Pin `schema_version: 2`, an integer suite `version`, `task_revision`, and
   `grader_revision: deterministic-v2`. Run with `--suite path/to/suite.json`.

The shipped suite is a source-authored synthetic reference contract, not a set
of human-adjudicated production observations. Mining output cannot be passed
directly to the gate. Required suites that are missing, empty, incompatible or
unreviewed fail configuration validation. Pytest does not skip the gate.

## Attempts, invalid runs and uncertainty

The report denominator includes every planned task/attempt. API timeouts and
invalid replies stay in `invalid`, never become exclusions. Report summaries
separate pass, task failure and invalid execution, both overall and by split.
Exit codes are 0 for all passing, 1 for task failures, and 2 for invalid runs or
configuration errors. A negative-control test must require exit 1 specifically.

`--repeats N` repeats every task and retains per-case attempt IDs. These are
ordinary repeated trials, not a claimed `pass^k` estimator. Repeating a scripted
backend provides no new stochastic evidence. Repeats of one task are correlated
through that task, and the task selection itself is synthetic.

The displayed 95% Wilson interval is a descriptive binary interval assuming
independent trials. With 10/10 it is approximately [0.722, 1.000], not a proof of
perfect quality. It does not account for biased selection or repeated-task
clustering. Use an independently sampled, larger dataset and task-level
uncertainty analysis before making population or release-risk claims.

The report pins suite version/hash, task/grader revision, a hash of all package
Python sources, agent policy, tool mode, model request/response name, prompt and
tool-schema hashes, limits, token usage and observed duration. `usage_complete=false`
marks invalid attempts whose observed token total may omit an unreported provider charge. Model aliases can
move; compare a pinned provider snapshot when one is available. The repository
also locks dependencies with `uv.lock`. Record machine and service configuration
when comparing runs on different infrastructure.

## Adding an LLM judge

To add a judge, restrict it to the semantic question deterministic checks
cannot answer—for example whether the final prose agrees with the structured
claims. Give it the task rubric, input, ordered calls with arguments/results,
final state and final text. Treat transcript instructions as untrusted data.
Require a structured binary verdict, criterion IDs and evidence references;
refusals, malformed JSON, unknown criteria, unsupported references and provider
errors belong in `invalid`, not pass or fail.

First collect actual human-adjudicated cases covering passes, failures and hard
negations, then freeze separate calibration and validation sets. Measure the
confusion matrix, false-pass rate among human failures, false-fail rate among
human passes, and uncertainty. Report undefined kappa explicitly. Choose
acceptable error limits from the consequences of a false decision. Pin model,
prompt, schema and dataset. Run in shadow mode until validation supports a gate.
Different model families alone do not establish independence or reliability.
