# Newsly evals

This is a local Python package for constructing model-evaluation datasets,
running model evaluations, and reporting results. It does not import the Newsly
backend, claim queue work, or own production matching policy.

The [chat eval harness](CHAT_EVALS.md) generates synthetic initial state from YAML
as SQL, bootstraps a disposable local database, and exercises Rust through HTTP.
This fixture-only PostgreSQL access is separate from production-data exports.

Every durable dataset produced here is a versioned JSON or JSONL artifact. Any
snapshot of Newsly rows must be exported read-only by Rust/operator tooling
before Python sees it. `python/evals/scripts/export_title_clustering_dataset.py` normalizes
that snapshot and records source/output SHA-256 digests; it never accepts a
database URL.

The relation workflow is deliberately two phase:

1. `newsly-eval-driver prepare-relations` asks the production Rust policy for
   every canonical string and SHA-256 required by a case set.
2. Python encodes those strings and creates a versioned embedding bundle.
3. `newsly-eval-driver score-relations` executes production Rust clustering and
   returns decision traces and pairwise metrics.

Use `NEWSLY_EVAL_DRIVER` to point at an already-built driver binary. Otherwise
the package invokes the workspace binary through Cargo.

```bash
uv run --project python/evals newsly-evals relations \
  --cases relation-cases.json \
  --model sentence-transformers/all-MiniLM-L6-v2 \
  --output results.json
```

Prepare title-only judge batches without installing a judge SDK:

```bash
uv run --project python/evals python python/evals/scripts/run_title_clustering_opus.py \
  --input-jsonl outputs/title_clustering/content_rows_last_10000.jsonl \
  --prepare-only
```

Use the `local`, `hosted`, or `judge` extras only for the model pipeline being
evaluated. Provider calls and judge outputs are offline experiment artifacts;
they never persist application state.

The package is intentionally absent from production images.

## Cached-vector news category replay

`prepare_news_lens_replay.py` accepts a bounded public-news export from the Rust
operator, checks the encoder-v1 input hash and cached vector dimensions, and
creates versioned inputs for the Rust `replay-news-lenses` command. Python never
connects to the source database. The preparation script defines the dates and
cohorts of the September 2026 experiment; adjust those explicitly for a new study.

```bash
PYTHONPATH=python/evals/src uv run --project python/evals python \
  python/evals/scripts/prepare_news_lens_replay.py \
  --operator-export test-results/news-lens-replay-20260927/production-sample.operator-json \
  --output-dir test-results/news-lens-replay-20260927 \
  --cohort all --cadence weekly
cargo run --release --manifest-path rust/Cargo.toml -p newsly-eval-driver -- \
  replay-news-lenses \
  --input test-results/news-lens-replay-20260927/all-weekly.input.json \
  --output test-results/news-lens-replay-20260927/all-weekly.output.json
uv run --project python/evals python python/evals/scripts/summarize_news_lens_replay.py \
  --directory test-results/news-lens-replay-20260927
```

Repeat with `--cohort technology` or `general`, and `--cadence nightly
--compact-matrix`. The Rust driver owns the experimental clustering algorithms;
these are geometric counterfactuals, not production routing policy. In particular,
`incremental_surrogate` is not an exact production baseline. Current backfilled
vectors do not reconstruct historical deployed state. Raw samples and outputs
stay under ignored `test-results/`; check in the aggregate report and method only.

The workflow makes no embedding, category-naming, or model-judge calls. See the
[September replay report](../../docs/initiatives/2026-09-27-news-category-replay-results.md)
for the sample provenance, limitations, and results.

Run the isolated static and behavioral checks from the repository root:

```bash
uv run --project python/evals ruff check \
  python/evals/src python/evals/scripts python/evals/tests
uv run --project python/evals mypy --config-file python/evals/pyproject.toml \
  python/evals/src python/evals/scripts python/evals/tests
uv run --project python/evals pytest -q python/evals/tests
```
