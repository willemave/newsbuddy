# Local chat eval harness

Describe initial state in YAML, generate SQL, run queries through the normal Newsly HTTP API,
and grade the completed answers with an independent LLM judge. The harness and generator
are Python. Rust remains a black box after setup; there is no parallel agent loop or direct
tool invocation. No CI or release integration is added.

## Run one case

From the repository root, with local PostgreSQL running and `psql`, `uv`, and an authenticated
`codex` CLI available:

```bash
uv sync --project python/evals
SQLX_OFFLINE=true cargo build --manifest-path rust/Cargo.toml \
  -p newsly-api -p newsly-worker -p newsly-db --locked

uv run --project python/evals newsly-evals chat run \
  python/evals/datasets/chat/podcast_history.yaml \
  --env-file /absolute/path/to/local.env \
  --model openai:gpt-5.6-sol \
  --judge-model gpt-5.6-sol \
  --case episode_links
```

The env file supplies the candidate provider key. The judge uses the Codex CLI's existing
login. Model identifiers are explicit selections, not guaranteed account availability.
Use `--binary-dir /path/to/debug` when Cargo uses another target directory. Use
`--postgres-url postgresql://USER@localhost:PORT/postgres` for another local PostgreSQL instance.
The local role must be able to create/drop databases and install the normal migrations.
Ordinary `KEY=value` and quoted values are supported in the env file; shell expansion is not.

Omit `--case` to run all 12 cases; repeat it to select several. The current suite requires
13 candidate turns and 12 judge invocations. Each candidate turn uses the product defaults: eight model
requests, 32 tool calls and 8,000 output tokens per request; queue retries are disabled. `--max-turns`
defaults to 20 and `--timeout` to 180 seconds per candidate turn or judge call. This bounds
work, not dollar spend: the public API does not expose sufficient usage for a monetary cap.
The CLI prints routing and planned call counts before starting. Selecting a provider/model
and running this command makes real model calls through that provider, including paid routes.

The harness creates a fresh `newsly_eval_*` database, applies the Rust binary's migrations,
starts the Rust API and chat worker on an unused loopback port, and seeds each case with
separate users and URLs. It uses `/auth/debug/new-user` for tokens, the normal assistant-turn
API for requests, message-status polling for completion, and content/subscription APIs for
state checks. PostgreSQL itself is an existing local service; the harness does not install it.

On completion or failure it stops its processes and drops its database. `--keep-database`
retains only the database for operator inspection; processes are still stopped. The database name is
in `run.json`. No personal database is replaced, and no scheduler or ingestion worker starts.

## Author scenarios and cases

Shared initial state lives in `contracts/testing/chat/podcast_history.yaml`. This is synthetic
data, including the example guest claims. The strict schema supports users, followed feeds,
episodes with dated bodies and visibility/read/saved state, and external HTTP stubs.

```yaml
version: 1
name: small_podcast
users:
  reader: {name: Eval Reader}
feeds:
  show:
    title: Example Show
    url: https://feeds.example.test/{namespace}/show.xml
    users: [reader]
episodes:
  previous:
    title: A previous episode
    feed: show
    url: https://episodes.example.test/{namespace}/previous
    published_at: '2026-08-01T12:00:00Z'
    body: A synthetic discussion about measuring completed tasks.
    visible_to: [reader]
    read_by: []
    saved_by: [reader]
stubs: []
```

Use `{namespace}` in URLs so the same scenario can be seeded repeatedly without colliding.
Keep dates explicit with a timezone. Add a case to the suite YAML:

```yaml
- id: saved_episode
  user: reader
  turns: [Show me the episode I saved.]
  expects: Return the saved episode with its individual episode link.
  required_links: [previous]
  forbidden_links: [show]
  expected_result_kind: episodes
```

`turns` supports follow-up conversations. `context_episode` supplies a real content ID in
screen context. Optional checks include `minimum_episode_links`, `link_feed`, and
`forbidden_text`. Link checks parse Markdown, including reference-style links, and ignore
code examples. Required links refer to episode keys; forbidden links can refer to episode
or feed keys. A case's expectations never enter the candidate's context.

`expected_result_kind: episodes` rejects visible subscription cards even if the final
text contains episode links. The judge receives separate typed episode and subscription
evidence and grades result type independently of link correctness. A subscription label
with a borrowed episode URL must still fail.

Tool names/counts remain diagnostic information in the report. They are excluded from
the judge input and all pass/fail criteria. A correct episode list can pass regardless
of the retrieval tool used. The fixture includes episodes from other shows with shared
guests/topics and descriptions mentioning the requested show; show identity determines
membership, not keyword overlap.

For now the expected episode destination is the explicit URL in the scenario. No unsupported
Newsly deep link is invented. If the product adopts another supported destination, change
the fixture/link contract deliberately. The harness can report a product failure without
first changing product behavior.

Generate reviewable SQL without starting services or making model calls:

```bash
uv run --project python/evals newsly-evals chat generate \
  contracts/testing/chat/podcast_history.yaml \
  --namespace manual-eval --output test-results/chat-evals/seed.sql
```

The SQL is transactional, rejects databases outside `newsly_eval_*`, and prints a JSON
mapping of logical names to actual IDs. It does not create schema or perform migrations.
Local integration tests import this same generator and load the same YAML.

## External API mocks

`stubs` declares HTTP method/path, optional exact top-level JSON request fields via
`body_contains`, and response status/body. `text`, `content_type`, and `delay_seconds` support
feed documents and error/timeout variants. Unknown requests receive HTTP 502 and are recorded as runtime diagnostics; reset occurs
between completed cases.

The runtime points Exa's existing base-URL setting at the stub server and configures a
loopback HTTP proxy. HTTPS CONNECT is allowed only to the selected model provider's host.
Other proxy traffic must match a stub. No real E2B credentials are passed to the runtime.
This exercises proxy-aware HTTP adapters; it is not OS-level network isolation. Feed
validators still apply their normal DNS/URL validation, so a deliberately invalid synthetic
feed can be rejected before any HTTP request. Do not mistake that for a successful feed mock.

## Judge and artifacts

The judge receives the expected outcome, authorized scenario evidence, and actual conversation.
It returns validated pass/fail criteria for result type, relevance, grounding, completeness,
and destinations,
with reasons and excerpts. Exact checks for links, forbidden text, visible subscription
cards, and episode source membership cannot be overridden by the judge. Runtime state
changes and unmatched external requests are recorded separately as diagnostic observations. Invalid judge JSON and candidate
or API failures are reported as errors. Candidate failures stop the suite so an unfinished turn
cannot continue against the next case's stubs.

Results are saved under `test-results/chat-evals/<timestamp>/`:

- `report.md`: queries, explicit Final answer sections, observed tool summaries, visible
  subscription cards, checks, and judge explanations.
- `results.json` and per-case `result.json`: machine-readable outcomes, including partial evidence.
- `run.json`: model choices, source revision/dirty state, binary and fixture hashes, and run limits.
- Per-case `seed.sql`, `manifest.json`, and `judge-input.txt` for reproduction.
- `runtime/`: local process logs and migration output. Auth tokens and env-file contents are omitted.

Usage/cost fields are unavailable rather than estimated. Exit codes: 0 = all pass,
1 = quality/check failure, 2 = infrastructure or configuration error. Reports persist when a
later case fails. Existing output directories are never overwritten by a new run.

Rejudge a saved answer without another candidate call:

```bash
uv run --project python/evals newsly-evals chat judge \
  test-results/chat-evals/RUN/episode_links/judge-input.txt \
  --model gpt-5.6-sol --output test-results/chat-evals/rejudgment.json
```

Keep a few known good/bad answers as judge calibration controls. A no-link answer and an
answer linking episodes to subscriptions must fail; a correctly linked and scoped list should
pass. The first live run reproduced the missing-link regression and the judge failed it.

## Local validation

```bash
uv run --project python/evals pytest -q python/evals/tests
NEWSLY_EVAL_BIN_DIR=/absolute/path/to/debug \
  uv run --project python/evals pytest -q python/evals/tests/test_chat_local_integration.py
```

The opt-in local integration check creates a disposable migrated database and exercises real
Rust auth, content, subscription, and model-selection APIs using the shared scenario. It makes
no model calls. Full quality evaluation is the explicit `chat run` command above.

For raw article/podcast summarization and grouped Briefing composition, see
[PIPELINE_EVALS.md](PIPELINE_EVALS.md). Both harnesses share local runtime and HTTP mocks.

## Chat retrieval and diagnostics (September 7)

Chat searches support optional lexical queries, owning-source names, read/saved filters and
bounded pagination. `total_count` counts text matches; `scope_total_count` counts
the accessible collection after source/state filtering but before the keyword filter.
Saved items can be listed without a query. The shared fixture records
the production `feed_url` metadata and uses the episode URL for `source_url`.

Judge `chat-v5` includes mocked external search results and declared validation availability
as reference evidence. Unvalidated suggestions must be labeled honestly. Open-ended
recommendations may use library or search evidence without requiring a particular source or
card. An honest privacy refusal does not require prescribed wording. Content/link/privacy checks remain in place; tool names
are diagnostic only. These rubric changes must be distinguished from candidate improvements.

Failed public message status (including available partial output and tool progress) is saved
in `diagnostics.json` and `result.json`. Runtime logs retain worker task failures. Diagnostics
are excluded from judging, and Python does not read product/task tables after bootstrap.

The earlier reduced limits (6 requests, 12 tool calls, 2,000 output tokens) caused an observed
comparison failure: `agent request limit exceeded`. Chat-only runs now use the product's
configured-code defaults, including history and sandbox output limits, and persist all of
these in `run.json`. This is an environment correction, not evidence of a model-quality fix.
The shared pipeline runner retains its prior settings.
