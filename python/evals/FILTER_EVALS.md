# News filter text ablation

The current inference run compares four equivalent filter structures with
`typesafe/jev-1.14` through OpenRouter's `https://openrouter.ai/api/alpha/decisions` endpoint.
Every response must identify `typesafe/jev-1.14-20261014` and provider `TypeSafe`.
The original `jev-1.13.0` manifests preserve the initial experiment plan and completed Opus
reference labels; direct TypeSafe inference on those schema-1 manifests is disabled.

This offline harness consumes public-news exports prepared by the Rust operator. It never
accesses a Newsly database. The full method is in the
[evaluation design](../../docs/initiatives/2026-10-17-news-filter-evaluation-design.md).

## Reuse the frozen reference run

The original `run-v1` has 2,000 primary labels and 200 repeat-audit labels. Its separate
`smoke-v1` intentionally has only one accepted judge batch, covering two of its four pairs.
Neither needs new judge calls for the transport migration.

```bash
uv run --project python/evals python python/evals/scripts/run_filter_eval.py migrate-openrouter \
  --source test-results/news-filter-eval-2026-10-17/smoke-v1 \
  --output test-results/news-filter-eval-2026-10-17/smoke-v2 --allocation smoke
uv run --project python/evals python python/evals/scripts/run_filter_eval.py migrate-openrouter \
  --source test-results/news-filter-eval-2026-10-17/run-v1 \
  --output test-results/news-filter-eval-2026-10-17/run-v2 --allocation main
```

Migration creates a new immutable schema-2 manifest. It preserves every pair, canonical
preference, filter variant, source input and job position. Requests change only their model and
provider-routing fields. New input hashes bind the request, choice order and exact transport.
The same canonical primary and repeat judge batches are frozen under the new manifest hash.

An inline reference descriptor binds the original manifest, checkpoint, prompts, exported
labels, optional repeat artifacts and the exact inventory of **all** raw judge files, including
rejected attempts. File digests cover their exact bytes. Scoring verifies those digests and
exact pair/batch equivalence, then validates the original saved judge responses against the
original manifest. Any later source-state mutation invalidates reference reuse. The original
source is never edited or copied into a replacement judge run.

Main migration requires complete primary and repeat coverage. Smoke migration preserves its
existing complete-batch subset and records absent or empty repeat artifacts explicitly.
`run-judge` and `inspect-judge-halts` refuse imported-reference manifests. `prepare-judge` remains
available to verify the already-equivalent frozen prompt set; it never invokes a model.

Human worksheets and labels retain one owner: the original reference directory. Migration does
not generate new worksheets, and v2 scoring reads `run-v1/human-labels.jsonl` with the **v1**
manifest hash. Human annotations remain separate from frozen model-reference artifacts.

## Execute the bounded OpenRouter smoke and main run

Set only `OPENROUTER_API_KEY` in the invoking process through the approved secret store. Never
put a key in arguments, checked-in files, or console output; do not source unrelated provider
keys from a general environment file.

CLI caps are required and must match the manifest allocation. Across the two directories,
the allowance is **8,400 attempts and $5**: smoke 16 attempts/$0.01; main 8,384/$4.99. Failed,
interrupted and retried requests consume that allowance. A failed smoke never authorizes
resetting its budget by creating another directory.

```bash
uv run --project python/evals python python/evals/scripts/run_filter_eval.py run-jev \
  --output test-results/news-filter-eval-2026-10-17/smoke-v2 \
  --request-cap 16 --dollar-cap 0.01 --concurrency 1
```

Inspect the smoke before the main run: exact dated response model, provider identity, unique
response ID, finite normalized choice probabilities, integer token usage and metered cost.
The request freezes TypeSafe-only routing, no fallback, required parameters, data collection
`deny`, and ZDR. The harness never weakens these settings automatically.

```bash
uv run --project python/evals python python/evals/scripts/run_filter_eval.py run-jev \
  --output test-results/news-filter-eval-2026-10-17/run-v2 \
  --request-cap 8384 --dollar-cap 4.99 --concurrency 4
```

`--limit N` bounds new jobs for one invocation; removing it resumes the same ledger. Every
attempt reserves a conservative UTF-8-byte bound plus overhead at $0.042/M input tokens before
network dispatch. Successful `usage.cost` replaces that reservation only after validation
against the frozen rate, zero output charge and non-BYOK billing. Missing cost, drift,
identity mismatch, malformed responses, insufficient credits or unsatisfied routing halt the
run. Unknown billing retains its reservation. Retrying 429/529 is limited to two retries within
the same budget. Numeric `Retry-After` is honored up to 30 seconds.

`jev-checkpoint.json` holds immutable limits and transport metadata. Fsynced reservation and
completion events append to `jev-ledger.jsonl`; raw responses are separate, hash-bound files
under `jev-raw/`. Resume reconstructs accounting and unique response IDs. Interrupted
reservations keep consuming their full amount. A partial journal tail refuses automatic
recovery. A process lock prevents two runners spending one ledger concurrently.

The runner reports jobs that exhaust three attempts without a valid result. Their presence
prevents selecting a winner; the retry cap is never silently expanded. Correcting failed inputs
requires a new explicit experiment allocation that accounts for all previous reservations.

## Reference protocol and recovery

Original dataset preparation uses `prepare --sample ... --profiles ... --output ...`, then
`prepare-judge --output ...`. These commands create artifacts only. Each article gets an
affinity profile selected by a frozen aggregator/topic heuristic and a different control
profile balanced within its aggregator. Selection heuristics are not gold labels. All four
structures evaluate the same pairs; inference sees title, summary and key points only.

Original judge execution uses local `claude --model claude-opus-5-5 --effort high` with tools,
skills, customizations and session persistence disabled. The child receives a sanitized
environment without `ANTHROPIC_*`/`CLAUDE*`; an exported `ANTHROPIC_API_KEY` is refused before
reserving any invocation. There is no SDK or model fallback. Two processes may run concurrently,
with explicit persisted call caps. Every response must report only Opus 5.5 model usage.
Each 40-pair batch contains at most one preference for an article. Judge inputs include the
canonical profile, never a filter variant or Jev output.

The deterministic 10% repeat audit uses fresh calls, no first-pass answers, and the same
invocation ledger. It reports disagreements without silently replacing primary labels.
For the completed source runs, further judging would change the frozen source descriptor;
do not resume them as part of the OpenRouter inference run.

The `literal-quote-typography-v2` validator normalizes only curly/straight quote characters and
whitespace. It never removes words, punctuation or ellipses or accepts paraphrases. It is
applied uniformly during scoring and recorded in provenance. Explicit original-run recovery
uses `--acknowledge-halt <batch-id>` to authorize a bounded retry, or
`--revalidate-halted <batch-id> --revalidate-only` to validate one stored full batch without
calling a model. Promotions require the exact attempt, raw hash and validator version; failed
attempts and raw files remain unchanged. Only whole valid batches can be promoted. Future
audit actions carry UTC timestamps; existing history is not rewritten.

## Score and review

```bash
uv run --project python/evals python python/evals/scripts/run_filter_eval.py score \
  --output test-results/news-filter-eval-2026-10-17/run-v2
```

A winner requires every Jev output and primary reference label. `--allow-partial` reports
only development diagnostics and coverage: no selected winner, test curves, test baselines
or test human-sensitivity metrics. Test metrics remain withheld until the completed run freezes
its development selection record.

Every accepted reference must bind to one completed or explicitly promoted attempt. Scoring
loads that saved raw response, validates its labels and evidence, and checks its recorded hash
when available. The migration descriptor additionally binds old completed raw files that lacked
a contemporaneous hash. Metrics and `selection.json` record the source manifest and descriptor
hashes alongside the validator version.

Selection uses recent development items only: at least 95% relevance recall overall and in
each profile having at least 20 positive references, then maximum macro profile admission
precision. Every present profile participates; no admissions contributes zero precision. Ties
prefer prose, bullets, structured, checklist, then the higher threshold. Development inputs
and the choice are frozen before any held-out metrics are calculated.

`metrics.json` includes raw three-class confusion, uncertainty, operational fail-open and
completed-only curves, an accept-all baseline, fixed-0.9 profile/aggregator breakdowns for all
variants, and paired story-cluster bootstrap intervals. Recent and stale-science slices are
separate. Unknown reference labels are counted but excluded from binary denominators. These
are enriched challenge-set metrics, not production prevalence or production precision.
Bootstrap intervals do not account for systematic judge error.

Human annotations in the original reference directory must retain `pair_id`, its original
`manifest_hash`, `human_label`, and `review_cohort`. Valid labels are `match`, `no_match`, and
`insufficient_evidence`. Random, coverage and targeted cohorts receive separate human-only
and reference-override sensitivity reports; they are never pooled as an accuracy estimate.
Missing cohort provenance is `unspecified`. Headline Opus gold and development selection are
unchanged by human sensitivity reports. Generic worksheets do not reveal affinity/control roles.

## Validation

```bash
uv run --project python/evals ruff check python/evals/src/newsly_evals/filtering \
  python/evals/scripts/run_filter_eval.py python/evals/tests/test_filter_*.py
uv run --project python/evals mypy --config-file python/evals/pyproject.toml \
  python/evals/src/newsly_evals/filtering python/evals/scripts/run_filter_eval.py \
  python/evals/tests/test_filter_*.py
uv run --project python/evals pytest python/evals/tests/test_filter_*.py -q
```

For the observed OpenRouter response deviation where all three probabilities are on the
0.01 grid and their sum is 0.99 or 1.01, validator `choice-cent-rounding-v2` accepts the
unchanged values. This is a bounded parser extension based on observed responses, not
a documented provider rounding guarantee. Other distributions retain the original sum
tolerance. All other model, choice, routing, usage, and billing checks still apply.

An already saved HTTP 200 rejection caused only by this deviation can be recovered with
`revalidate-jev --output <run-directory> --attempt-index <index>`. It makes no provider
call. Under the run lock, it verifies the exact attempt, input and raw hash, current full
response contract, original reservation, and unique generation ID; it appends a versioned,
timestamped recovery event. The original rejection and raw response remain untouched.
Repeated recovery, unrelated failures, or altered evidence fail closed. Existing immutable
checkpoint files remain unchanged and missing validator provenance is marked legacy;
new checkpoints and future reservations record the validator version. Selection and
metrics also record it, and metrics hash any supplied human-label file at scoring time.

A response whose selected choice is below the maximum reported probability remains a
protocol failure. `acknowledge-jev --output <run-directory> --attempt-index <index>`
permits a bounded retry of an existing halted instance only after verifying that exact
violation and the entire routing, model, raw/input hash, usage and billing envelope.
The acknowledgment retains the rejection and actual charge, accepts no model result,
and does not reset the job's three-attempt limit or shared request/dollar caps. Future
instances use the same typed failure contract and bounded retry policy. Reports should
show protocol-failure and retry rates alongside completed-result quality and retry
sensitivity; a later successful attempt does not erase an earlier failure.
