# Newsly evals

This is an offline-only Python package for constructing model-evaluation
datasets, running local or hosted embedding models, and reporting results. It
does not import the Newsly backend, connect to PostgreSQL, claim queue work, or
own production matching policy.

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

Run the isolated static and behavioral checks from the repository root:

```bash
uv run --project python/evals ruff check \
  python/evals/src python/evals/scripts python/evals/tests
uv run --project python/evals mypy --config-file python/evals/pyproject.toml \
  python/evals/src python/evals/scripts python/evals/tests
uv run --project python/evals pytest -q python/evals/tests
```
