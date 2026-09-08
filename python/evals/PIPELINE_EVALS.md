# Local summarization and Briefing evals

The Python harness bootstraps synthetic YAML state into a disposable `newsly_eval_*`
PostgreSQL database, starts the real Rust API and one worker, waits for published
HTTP output, stops the pipeline, and judges the output using the authenticated
Codex CLI. There is no CI model-eval gate.

The shared suite is `contracts/testing/pipeline/summarization.yaml`. Integration
checks use this same YAML, SQL generator, runtime and HTTP stub server.

## Stage boundaries

- `summary`: one raw article or podcast transcript, no summary in the initial
  state, and a pending summarization task. The real summarization worker produces
  the artifact; `GET /api/content/{id}` supplies the output. An unpublished row
  returns 404, so the runner waits for publication. A completed response must
  contain the canonical `longform_artifact` envelope. The judge and report receive
  the title plus that envelope once; `last-response.json` retains the unabridged
  HTTP payload. A completed response missing the artifact is an execution/contract
  error, not an alias fallback. Projection version is `pipeline-v5-canonical-artifact`
  and results/report version is 2. This does not test URL
  extraction, audio downloading or transcription.
- `briefing`: prepared news summaries or article/podcast summaries, assigned
  source sets, and no Briefing segments. `POST /api/briefing/refresh` triggers the
  real worker. The runner reads the index and lens pages until all fixture sources
  are covered. This tests synthesis and publication, not upstream summarization
  or semantic category assignment. Use `summary` cases for the upstream stage.

Ready artwork metadata is an initial-state precondition for long-form publication;
image generation is not run. News lens membership is explicitly seeded. The
embedding HTTP mock returns a fixed vector for each requested input. It exercises
real event-window planning, but does not evaluate embedding quality. All remaining
external calls are blocked by the shared proxy except `api.openai.com` for the
candidate. This is application proxy isolation, not an OS network sandbox.

Python only generates/applies initial-state SQL. It never queries the database to
score, claims a task, or implements the summarization algorithm. Rust owns schema,
queue execution, publication and the HTTP representation.

## Run locally

Use freshly built `newsly-api`, `newsly-worker`, and `newsly-db` binaries:

```sh
python/evals/.venv/bin/newsly-evals pipeline run \
  contracts/testing/pipeline/summarization.yaml \
  --binary-dir /path/to/rust/binaries \
  --env-file /path/to/local.env \
  --model openai:gpt-5.6-terra \
  --judge-model gpt-5.6-sol \
  --output test-results/pipeline-evals/run-1
```

Repeat `--case article_numbers --case news_conflicting_reports` to select cases.
Each case gets its own database and processes, and errors do not stop later cases.
Databases and processes are cleaned up on success, failure or interruption. Reports
and logs remain. Output directories must not already exist.

Candidate processing uses the normal Rust model settings. Current summarization
limits are three model requests with 6,000 output tokens per request. Briefing
composition allows four outer attempts, each with up to three model requests and
3,200 output tokens per request, per composition window. Queue retries are disabled.
The harness defaults to a 180-second publication deadline per case and a separate
180-second judge deadline; `--timeout` changes both (maximum 600 seconds). The
candidate and judge are real model calls. Usage and dollar cost are unavailable;
there is no claimed dollar ceiling. Other vendors, including embeddings, are mocked.

Generate inspectable SQL without calling a model:

```sh
python/evals/.venv/bin/newsly-evals pipeline generate \
  contracts/testing/pipeline/summarization.yaml \
  --case article_numbers --namespace inspect-article --output /tmp/article.sql
```

## Cases and evaluation

The fourteen cases cover:

- article numerical scope and confounding, corrections, and injected reader comments;
- podcast speaker attribution, sponsor claims, uncertain dates and inaudible text;
- news conflicting reports, duplicate-event synthesis and separate topic lenses;
- article and podcast Briefing summaries;
- news conciseness for simple updates, overlapping reports, background-heavy
  source material and compact summaries that must preserve uncertainty.

Conciseness is judged from substantive summary prose. Repeated source titles,
citation labels and appended title recaps are acceptable and excluded from prose
length and sentence limits. The judge checks repetitive explanation, filler and
overloaded sentences; it does not double-count narration/block serialization or
source cards. Citation accuracy remains part of destination/grounding evaluation. These expectations reach only the judge.

`expects` is passed only to the judge, never to the candidate. The judge evaluates
result type, relevance, grounding, completeness and source destinations. There are
no tool-choice, internal-call or exact-wording criteria. Readiness is a separate
execution condition; absence of published output is an error, not a passing summary.
Source cards alone do not satisfy a missing fact in generated text.

`report.md` contains the full final structured output and every judge reason.
`results.json` and per-case `result.json` are machine-readable. Each case also keeps
`seed.sql`, resolved IDs, `last-response.json`, `judge-input.txt`, and runtime logs.
`run.json` captures cases, model names, checkout revision/dirty state and binary
hashes. Exit codes: 0 all pass, 1 quality failure, 2 execution/judge error.

The saved judge input can be re-evaluated without another candidate call using the
shared judge command (`chat judge` accepts the same five-criterion schema):

```sh
python/evals/.venv/bin/newsly-evals chat judge \
  test-results/pipeline-evals/run-1/article_numbers/judge-input.txt \
  --model gpt-5.6-sol --output /tmp/rejudged.json
```

No-cost focused validation, including real API fixture bootstrap:

```sh
NEWSLY_EVAL_BIN_DIR=/path/to/rust/binaries python/evals/.venv/bin/pytest \
  python/evals/tests/test_pipeline_evals.py
```

## Production-derived tech-news snapshots

`contracts/testing/pipeline/tech_news_production.yaml` adds four conciseness cases
using eight public, ready production News records: a macOS release, AI policy,
developer tools, and evidence limits in AI claims. `tech_news_provenance.json`
records the snapshot date, public URLs and production source identifiers.

These are frozen production summaries plus their key points, not raw article
extracts or independently verified ground truth. The candidate receives both through
the normal Briefing source projection; the judge receives the same source evidence.
The fixture's lens groupings are local evaluation choices. No production Briefing
answer, user membership, private source or account data is used. Public URLs have
query strings and fragments removed. The local run never fetches those URLs.

Run it with the same command above, substituting
`contracts/testing/pipeline/tech_news_production.yaml` for the suite path. The original
synthetic cases remain useful controlled regressions and are not replaced. Both suites
are reused by the opt-in API integration checks.

Pipeline-v4 adds a news-only 25–45-word target and 60-word maximum per passage, excluding linked titles and permitted title recaps. Under-target prose is acceptable if complete; 46–60 words must serve material facts or qualifications. Over 60 fails relevance. Article/podcast grading is unchanged. Comparisons with pipeline-v3 combine candidate-prompt and news-rubric changes.

## Full production-source snapshots

Use `stage: summary` with the complete production-extracted article body or podcast
transcript in `sources[].text`. Preserve the public URL, content kind, source name,
`platform`, and `publication_date`; these are candidate inputs, and platform/URL
affect Rust's source hint and available artifact templates. Do not seed a production
summary or choose the candidate's artifact type in the fixture. The worker selects
and produces the real structured artifact, fetched through the content-detail API.
Grade the artifact payload, quotes, extras and feed preview against the complete
source; do not substitute a short Briefing passage for this output.

Export selected public source metadata with read-only Rust `newsly-admin db query`
and copy only the corresponding stored source-body files. Verify every body against
its production SHA-256. Keep copied bodies, generated YAML and full outputs under
ignored `test-results/`, with source IDs, URLs, hashes, and snapshot provenance.
Do not use a production database clone or include user/account/session metadata.
The evaluator still reads only its local fixture and the black-box HTTP output.

The September 7 full-source snapshot is under
`test-results/production-source-evals-20260907/`: two full articles, two complete
podcast transcripts, and two Briefing cases using three real News summaries and
key-point sets. All four bodies fit the 220,000-character production payload limit.
Run its `suite.yaml` with the normal `pipeline run` command; `--case` selects one
source. This remains a local reusable fixture, not a CI suite. Its provenance
records the exact bytes rather than claiming the stored extraction is identical
to every element of the publisher's original page or audio.
