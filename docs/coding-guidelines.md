# Coding Guidelines

For SwiftUI app and Share Extension conventions, use
[`docs/coding-guidelines-ios.md`](coding-guidelines-ios.md).

## Rust boundaries

The production backend is the Cargo workspace under `rust/`:

- `newsly-api` owns Axum routes, middleware, and the Utoipa public contract;
- `newsly-domain` owns product types and state rules;
- `newsly-db` owns shared SQLx repositories, cross-feature transactions, and
  migrations;
- feature crates may keep private SQLx repository modules beside their owning
  workflow when the query is not a shared persistence contract;
- `newsly-queue` owns lease, retry, deferral, and transition semantics;
- `newsly-worker` owns task executors and process entrypoints;
- `newsly-providers` owns external provider adapters;
- `newsly-agent-runtime` wraps Rig and keeps SDK types out of durable state;
- `newsly-e2b` owns direct E2B control-plane and ConnectRPC transport.

Routes parse and authorize requests, invoke one application operation, and map
the result into the typed response/error envelope. They do not contain SQL,
provider calls, queue policy, or domain state machines.

SQL belongs in a repository module. Shared persistence contracts and
cross-feature transactions belong in `newsly-db`; a feature-local query may
remain private to its owning runtime crate. Prefer `query!` and `query_as!` when
practical and always bind values. Complex PostgreSQL features such as
`SKIP LOCKED`, advisory locks, partial indexes, FTS, and compare-and-set updates
should remain explicit SQL rather than being hidden behind a second ORM.

Provider and agent crates do not depend on `newsly-db`. A durable record uses a
Newsly-owned type and versioned encoding; Rig, `async-openai`, ConnectRPC, and
E2B representations are transient adapter details.

## Long-running work

Every worker and provider-backed command follows the same shape:

```text
short prepare transaction
  -> owned, immutable work plan
  -> external work with no DB transaction or checked-out connection
  -> fresh short finalize transaction
  -> revalidate owner, lease/generation, and product lifecycle
```

Lease loss or cancellation stops progress publication and prevents final state
from committing. Do not solve long transaction ownership with a larger pool,
detached ORM state, or a timeout increase.

External retries must follow the operation's idempotency contract. An E2B
command whose delivery is ambiguous is reattached by durable sandbox/process
identity; it is never blindly started again.

## Contracts and errors

Use Serde for encoding, Utoipa for public OpenAPI, Schemars for provider/tool
schemas, and explicit validators or `TryFrom` conversions for constrained
domain types.

Audit these details for every changed wire type:

- absent versus explicit `null`;
- defaulted versus required fields;
- tagged unions and open versus closed enums;
- aliases and request-only or response-only fields;
- RFC 3339 UTC timestamps serialized with `Z`;
- legacy JSONB lenience versus strict new inputs.

Public failures use the shared typed error envelope. Log the internal source
with structured `tracing` fields, but return only stable codes, bounded safe
details, and the request identifier.

The Rust Utoipa document is the public route/schema authority. Regenerate and
check the checked-in corpus after an intentional HTTP change:

```bash
scripts/regenerate_public_contracts.sh
scripts/check_public_contracts.sh
```

Do not hand-edit generated client artifacts.

## Python islands

Newsly-owned Python is allowed only in:

- `python/document_extractor`: production Crawl4AI extraction behind a
  database-free, authenticated, versioned contract;
- `python/evals`: offline datasets, embedding/model experiments, judges, and
  reports that exercise the real Rust algorithms through `newsly-eval-driver`.

Neither package may read Newsly's database, claim queue rows, run migrations,
issue user auth tokens, or own product state. The extractor returns typed usage
events; Rust decides retries and persists them. Evals exchange versioned
JSON/NDJSON artifacts and must not grow a second production matcher.

The application image may include the pinned third-party `yt-dlp` executable
and its private Python runtime. Treat it as a Rust-controlled media tool, not a
place for Newsly application code or workflow ownership.

The Python files in `docs/brand-exploration-2026-08` are a narrow historical
exception for offline design-asset generation. They are not a package, may not
be imported by application code, and are excluded from production images. The
architecture guard rejects Python in every other repository path.

## Tests and checks

Add focused tests beside changed Rust code. Persistence and queue behavior need
isolated PostgreSQL coverage; provider behavior needs typed fakes plus live
canaries when SDK or protocol semantics matter. Contract changes require the
Rust drift check and affected native-client builds/tests.

iOS UI flows and reference images live with the client under
`client/newsly/Maestro/flows/` and `client/newsly/Maestro/baselines/`. Do not
recreate a repository-root Maestro tree or a backend `tests/ios_e2e` package.

Before handoff, run the smallest commands that prove the change and record any
broader gate that remains outstanding:

```bash
cd rust
cargo fmt --all -- --check
cargo clippy --workspace --all-targets --locked -- -D warnings
cargo test --workspace --locked
cd ..
scripts/check_public_contracts.sh
```

Before pushing a release commit to `main`, run the canonical local gate from a
clean checkout. It runs Rust, SQLx, contract, Python-island, native iOS, and AXe
checks and records ignored evidence under `test-results/release-gate/`:

```bash
scripts/release_gate.sh --env-file /absolute/path/to/local.env
```

For backend, deck, chat, Share Extension, provider, or sandbox changes, include
the production-shaped live smoke in the same invocation:

```bash
scripts/release_gate.sh \
  --env-file /absolute/path/to/local-staging.env \
  --with-live-smoke \
  --allow-live-provider-costs
```

The live phase makes paid provider and E2B calls. It builds each Docker image
once for the full run, then reuses the same isolated stack across every live
scenario. GitHub does not repeat these source-level release tests: a push to
`main` builds and smoke-tests the exact-SHA production images, refuses stale
deployments, and performs the blue/green rollout. Xcode Cloud independently
builds the pushed `main` revision.

For the opt-in production-shaped local smoke, build the application and
extractor images once, then run every live API scenario against that same
disposable Compose stack:

```bash
scripts/smoke_local_staging.sh \
  --allow-live-provider-costs \
  --env-file /absolute/path/to/local-staging.env
```

This command makes paid provider and E2B calls. It accepts only a loopback API
origin, creates an isolated PostgreSQL volume and artifact directory, and
writes redacted evidence under `test-results/local-staging-smoke/` before
tearing the stack down. Pass `--keep-on-failure` only when the isolated stack
must remain available for debugging.

Authenticated iOS UI tests default to the local API on port 8000. When another
checkout owns that port, point the test bundle at the smoke stack without
changing application defaults:

```bash
xcodebuild test \
  -project newsly.xcodeproj \
  -scheme newsly \
  -destination 'platform=iOS Simulator,OS=latest,name=iPhone 17' \
  NEWSLY_E2E_SERVER_PORT=28680 \
  -only-testing:newslyUITests
```

For a Python-island change:

```bash
uv run --project python/document_extractor ruff check \
  python/document_extractor/newsly_document_extractor \
  python/document_extractor/tests
uv run --project python/document_extractor mypy \
  python/document_extractor/newsly_document_extractor
uv run --project python/document_extractor pytest \
  python/document_extractor/tests -v

uv run --project python/evals ruff check \
  python/evals/src \
  python/evals/scripts \
  python/evals/tests
uv run --project python/evals mypy \
  --config-file python/evals/pyproject.toml \
  python/evals/src \
  python/evals/scripts \
  python/evals/tests
uv run --project python/evals pytest python/evals/tests -v
```

## Operations

Local development uses native Rust processes and a local PostgreSQL instance.
Docker is the staging/production runtime. SQLx is the only schema owner. The
retired Alembic source tree has been removed; its audited catalog survives in
the SQLx baseline and repository history and must not be recreated.

Use `newsly-admin` for ownership, health, task, usage, eval, and bounded
read-only database-inspection operations. Use the Rust admin HTTP surface or
container runtime for logs after narrowing the symptom. Do not create ad hoc
database-copy or production-mutation scripts.
