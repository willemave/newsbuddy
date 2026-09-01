# Local Staging Live Smoke Design

**Status:** Complete locally  
**Branch:** `main`  
**Scope:** Learning Decks, chat, deck-grounded chat, and Share Extension outcomes that create a deck or chat

## 1. Goal

Provide one repeatable command that builds and starts a production-shaped Newsly stack on the
developer Mac, drives it only through real HTTP APIs, lets the Rust workers call the configured
live LLM and E2B services, and emits enough evidence to tell whether the complete product paths
worked.

The smoke run is not a replacement for focused Rust tests, contract drift checks, native iOS
tests, Maestro, or production health checks. It is the opt-in live canary between those deterministic
gates and a deployment.

## 2. Selected approach

Use the checked-in production application image and worker supervisor through Docker Compose,
with a smoke-specific override and a unique Compose project name per run. The stack contains:

- PostgreSQL 15 with a fresh project-scoped volume;
- the SQLx migration job;
- the database-free document extractor;
- the API container;
- the production worker container, including chat and `run_llm_task` workers;
- the scheduler and existing media helper, matching the normal container topology;
- one project-scoped host directory for local artifacts and diagnostic output.

The API runs with `ENVIRONMENT=test` so the existing local-only debug authentication endpoint can
mint a disposable user token. All binaries, queue ownership, migrations, worker dispatch,
supervision, storage paths, and network calls otherwise use the same application image and code
paths as deployment.

The harness uses a unique run identifier in its Compose project, user, prompts, and output report.
It never connects to the developer database or production database.

### Alternatives considered

1. **Native Rust processes plus local PostgreSQL:** faster, but it does not validate the production
   image, entrypoint, supervisor, container networking, or extractor isolation.
2. **An already-running local stack:** useful for exploratory debugging, but it cannot guarantee a
   clean schema, queue, storage directory, or reproducible cleanup.
3. **Testcontainers for every service:** capable, but duplicates the canonical Compose topology and
   adds a second container specification. Compose remains the owner; Testcontainers may be useful
   later if CI needs programmatic service reuse.

## 3. Harness shape

Add a small Rust `newsly-smoke` crate and a shell entrypoint:

```text
scripts/smoke_local_staging.sh
  -> validate Docker and required credential names
  -> allocate unique ports, Compose project, data root, and report directory
  -> build the exact local Docker image
  -> start PostgreSQL, migrate, extractor, API, workers, scheduler, and helper
  -> wait for container and API readiness
  -> run newsly-smoke against the loopback API origin
  -> collect API results, usage summary, task failures, Compose state, and bounded logs
  -> tear down containers, networks, volumes, and temporary data
```

`newsly-smoke` owns typed requests, authentication, polling, scenario assertions, and a versioned
JSON report. It uses the existing `newsly-contracts`, `reqwest`, Tokio, Serde, and URL-safe public
contracts rather than maintaining duplicate wire structs.

The shell layer owns Docker Compose because Compose is already the deployment-topology authority.
It traps normal exit, error, and interruption so cleanup runs exactly once. `--keep-on-failure`
preserves the isolated stack only when explicitly requested.

## 4. Live scenarios

All scenarios use one configurable public source URL and the same disposable authenticated user.
Assertions are structural and behavioral; they do not require exact model prose.

### A. Stack and authentication

1. `/health/live` and `/health/ready` succeed.
2. Debug auth creates the disposable user and returns a bearer token.
3. An unauthenticated protected request is rejected with the typed error envelope.

### B. Direct chat

1. Create an ad-hoc chat with an initial message.
2. Poll the accepted message until its assistant row is terminal.
3. Fetch the durable transcript and verify one user/assistant pair with stable identities.
4. Send a follow-up in the same session, poll it, and verify the two-turn transcript is ordered and
   contains no visible tool/system chatter.
5. Verify an unrelated user cannot read the session.

### C. Share Extension to chat

1. POST a `chat` Share Action with the live source URL and a distinctive initial question.
2. Poll the Share Action through queued/running/applying to completed.
3. Verify the resulting content and chat are discoverable through public APIs.
4. Poll the initial assistant response and verify it is durably grounded in the submitted source.
5. Submit the same action again and assert that canonical content is reused rather than duplicated.

### D. Share Extension to Learning Deck

1. POST a `presentation` Share Action with a distinctive deck instruction.
2. Poll the Share Action to completion and resolve the resulting Learning Deck through public APIs.
3. Poll the deck through preparation, generation, validation, and publication.
4. Require a latest successful run, viewer availability, source-note availability, and a preserved
   instruction/source identity.
5. Fetch a signed viewer URL and source-notes URL and require successful, nonempty responses.
6. Enable public sharing, fetch the public viewer, disable sharing, and verify the old public URL is
   revoked.
7. Recreate the same deck source and verify the canonical deck identity is reused.

### E. Learning Deck-grounded chat

1. Send an Assistant Turn with `screen_type=learning_deck`, the deck source content identity, title,
   source URL, and a bounded slide-context note, matching the native reader.
2. Poll the assistant response and verify the session remains durable and source-grounded.
3. Send a second turn that does not request current/web information.
4. Verify the turn completes without exposing tool chatter and without requiring a web-search result.

### F. Failure and operational evidence

1. Invalid Share Action and Learning Deck payloads return typed 4xx envelopes.
2. Unknown task, deck, chat, and cross-user identifiers do not leak another user's data.
3. At the end, require no smoke-owned task to remain queued/running past the scenario deadline.
4. Capture persisted provider usage grouped by feature and recent failed-task summaries through
   `newsly-admin`; direct database queries are not product assertions.

## 5. Live-call and cost policy

The command is opt-in and refuses to start unless `--allow-live-provider-costs` is present. It
requires nonempty `OPENAI_API_KEY`, `E2B_API_KEY` (or its supported alias), and `EXA_API_KEY`
without printing their values. Optional provider keys remain available for explicitly selected
models.

The default smoke makes one Share-to-Deck run, one Share-to-Chat run, one direct two-turn chat, and
one two-turn deck-grounded chat. The harness does not retry a failed scenario automatically because
that could repeat paid work. Product-owned bounded retries remain active. Every poll and scenario
has a deadline, while Learning Deck generation retains its product law: no artificial model-request
or output-token ceiling is inserted into the executor.

The report records request counts, persisted token/resource usage, elapsed time, model/provider
labels, and any persisted estimated cost that Newsly exposes. It never records credentials,
authorization headers, complete provider payloads, or full source bodies.

## 6. Isolation and cleanup

- A generated Compose project name prevents collision with `newsly-dev` and `newsly-prod`.
- Loopback API and PostgreSQL ports are dynamically selected.
- PostgreSQL data, application artifacts, and reports are run-scoped.
- Secrets come from a gitignored `.env.smoke.local` or an explicitly supplied env file.
- The default teardown uses `docker compose down --volumes --remove-orphans` and removes only the
  validated run-scoped temporary data directory.
- On failure, diagnostics are written before teardown. `--keep-on-failure` prints the exact scoped
  follow-up and cleanup commands.
- The harness never accepts a non-loopback API origin in local-staging mode and never accepts an
  external database URL.

## 7. Evidence and developer interface

Primary command:

```bash
scripts/smoke_local_staging.sh --allow-live-provider-costs
```

Useful options:

```text
--env-file PATH
--source-url URL
--agent-vm-template-id ID
--keep-on-failure
--report-dir PATH
--reuse-application-image IMAGE
--reuse-extractor-image IMAGE
```

The two reuse options are recovery controls for an interrupted local Docker build or stack startup;
the normal full-run command always builds fresh images. Reused images must already exist in the
local Docker daemon and remain fixed for every scenario in the recovery run.

The run produces `test-results/local-staging-smoke/<run-id>/report.json`, a concise Markdown
summary in the terminal, bounded container logs, Compose service state, the admin usage summary,
and recent task failures. The terminal prints one pass/fail line per scenario plus the final report
path.

The application image and extractor image are each built once at the beginning of the full run.
Every scenario uses those same images, containers, database, and authenticated users; scenarios
never rebuild Docker independently.

## 8. Validation and acceptance criteria

Implementation is accepted when:

- Rust formatting and warning-denied Clippy pass for the new crate and affected packages;
- deterministic unit tests cover polling deadlines, typed error handling, report redaction,
  terminal-state classification, and cross-scenario identity extraction;
- both Compose files pass `docker compose config` with dummy credential names;
- a credential-free preflight proves the command fails before building or starting containers;
- one authorized live run completes all scenarios against a freshly built image;
- the report demonstrates real API, PostgreSQL queue, Rust worker, OpenAI, E2B, artifact publication,
  and cleanup behavior;
- `docs/log.md` records the exact validation performed and any live lane not run.

## 9. Explicit non-goals

- No production mutation, deployment, push, commit, or Apple distribution.
- No model-quality grading based on exact wording.
- No replacement for Maestro Share Extension UI automation or Learning Deck visual baselines.
- No automatic scheduled execution until credentials, expected spend, and CI secret policy are
  approved separately.

## 10. Completed validation

The live run `20260901T032806Z-18accc` passed all six scenarios against one disposable local
Compose stack. It exercised 21 persisted provider calls and 34 provider requests, including real
OpenAI, Crawl4AI/document-extractor, Firecrawl, and E2B-backed Learning Deck work. The resulting
deck published nonempty viewer and source-note artifacts, public sharing was revoked correctly,
all chat transcripts were durable, and the final task-failure count was zero.

The full Rust workspace, SQLx offline/prepared-query checks, both Python islands, architecture and
contract guards, 629 native iOS unit tests, and all three authenticated iOS UI lifecycle tests also
passed locally. The UI test bundle now accepts `NEWSLY_E2E_SERVER_PORT` as an Xcode build setting so
an isolated staging API does not have to own the conventional port 8000.

One environment issue remains outside the harness: the shared E2B template alias `newsly-agent`
was stale and did not contain `/usr/local/bin/newsly-vm-bootstrap`. The successful run used a
uniquely named template built from the current `e2b.Dockerfile`; that template and its sandboxes
were deleted afterward. Until the shared alias is rebuilt through its owning release workflow,
local runs must supply a current template with `--agent-vm-template-id`.
