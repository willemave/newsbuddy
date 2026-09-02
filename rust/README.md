# Newsly Rust workspace

This Cargo workspace is Newsly's authoritative application backend. Axum and
Tokio own HTTP and process orchestration, SQLx owns PostgreSQL queries and
migrations, and Rust owns the durable queue, workers, scheduler, provider
integrations, agents, direct E2B transport, admin surfaces, operator tools, and
the user-facing `newsbuddy` CLI.

The only production Newsly-owned Python process is the database-free Crawl4AI
service in `python/document_extractor`. The package in `python/evals` is
offline tooling and is not part of a production image. Neither package owns
application state, schemas, queues, authentication, or migrations. The pinned
third-party `yt-dlp` executable and its Python runtime are a Rust-controlled
media subprocess, not an application authority.

## Workspace map

- `newsly-api`: Axum HTTP API, middleware, authentication, typed error envelope,
  server-rendered admin routes, and authoritative Utoipa OpenAPI document.
- `newsly-domain`: durable product types, queue/ownership vocabulary, and state
  rules independent of transport and persistence.
- `newsly-contracts`: Serde, Schemars, and Utoipa wire types shared by routes,
  tasks, providers, and schema exports.
- `newsly-contract-codegen`: fail-closed Swift app and Share Extension generation
  from the authoritative Rust OpenAPI document.
- `newsly-db`: shared SQLx repositories, cross-feature PostgreSQL transactions,
  embedded migrations, and the `newsly-db` migration binary. Feature-local
  repositories remain private to their owning runtime crate.
- `newsly-queue`: lease-fenced claim, heartbeat, retry, deferral, cancellation,
  and finalization kernel.
- `newsly-worker`: task executors plus queue-specific binaries under `src/bin/`.
- `newsly-scheduler`: PostgreSQL-coordinated recurring work and maintenance.
- `newsly-providers`: typed HTTP/provider adapters and vendor policy.
- `newsly-agent-runtime`: Newsly-owned agent interface backed by pinned Rig;
  persisted transcripts never use Rig-native serialization.
- `newsly-e2b`: direct E2B control-plane HTTP, envd ConnectRPC streaming, files,
  snapshots, network policy, and sandbox lifecycle.
- `newsly-extraction`: Rust client and types for the private Python extractor.
- `newsly-eval-driver`: canonical Rust algorithms exposed to offline evals.
- `newsly-admin`: runtime ownership, health, task, usage, eval-export, and
  bounded read-only database-inspection CLI.
- `newsly-cli`: authenticated `newsbuddy` API client, local configuration,
  terminal QR linking, stable output envelopes, and safe Markdown library sync.
- `newsly-vm-bootstrap`: credential-free helper installed inside E2B sandboxes.
- `newsly-account-deletion-worker`: idempotent deletion and external-resource cleanup.

## Runtime model

PostgreSQL is both the source of durable product state and the task queue. Tokio
channels only provide process-local wakeups and backpressure; they do not replace
durable queue state.

Every provider-backed operation follows:

```text
short SQLx prepare transaction
  -> owned immutable work plan
  -> external work without a DB transaction or checked-out connection
  -> fresh exact-owner/lease/generation finalization transaction
```

The same finalization transaction publishes product state, usage, downstream
tasks, and the owning queue transition. A stale attempt cannot publish.

Rig is an execution engine behind `newsly-agent-runtime`, not the domain model.
`async-openai` and typed `reqwest` adapters cover provider-native APIs that do
not fit the common loop. Direct E2B integration uses typed HTTP/ConnectRPC and
preserves stream ordering, incremental UTF-8 decoding, output bounds,
cancellation, and reattachment without duplicate command execution.

## Public contracts

`GET /openapi.json` exposes the authoritative Rust contract. The same Utoipa
document generates:

- `docs/library/reference/openapi.json`;
- `contracts/openapi/public.openapi.json`;
- app and Share Extension Swift wire types.

`newsly-contract-codegen` consumes the OpenAPI components plus the reviewed
`contracts/client_codegen_policy.toml` subset/open-enum/default policy. It fails
when a registered schema disappears, an unregistered enum is referenced, or
untyped JSON is not explicitly allowlisted.

The Rust CLI consumes request and error types directly from `newsly-contracts`.
It decodes ordinary successful responses as JSON to tolerate future enum values,
so there is no generated CLI model file or checked CLI-specific OpenAPI copy.
The internal `--agent` export mode remains only as a language-neutral
operation-inventory check.

Regenerate and verify deliberate contract changes from the repository root:

```bash
scripts/regenerate_public_contracts.sh
scripts/check_public_contracts.sh
```

## User CLI

From this directory, build or install the same `newsbuddy` binary name used by
existing scripts and config files:

```bash
cargo run -p newsly-cli --bin newsbuddy -- version
cargo install --locked --path crates/newsly-cli
newsbuddy auth login
```

The CLI keeps its configuration at `~/.config/newsbuddy/config.json` and retains
the established commands, environment aliases, JSON envelope, and text-output
mode. See [`crates/newsly-cli/README.md`](crates/newsly-cli/README.md) for the
complete command and configuration reference.

The external `willemave/newsbuddy` Homebrew tap is released independently.
Verify that its current formula packages the Rust `newsbuddy` binary before
relying on it; a source install always builds the checked-out revision.

## Configuration and local execution

All application binaries use `DATABASE_URL`. A native PostgreSQL URL is
preferred; the compatibility `postgresql+psycopg://` spelling is normalized at
startup. Secrets are redacted from diagnostics. Rust tuning variables use the
`NEWSLY_RUST_*` prefix.

From this directory:

```bash
cargo fmt --all -- --check
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
DATABASE_URL=postgresql://localhost/newsly cargo run -p newsly-db -- check
DATABASE_URL=postgresql://localhost/newsly cargo run -p newsly-api
```

The API listens on `0.0.0.0:8100` by default:

- `GET /health/live`: process liveness only;
- `GET /health/ready`: bounded PostgreSQL readiness;
- `GET /health`: compatibility readiness used by deployment tooling;
- `GET /openapi.json`: public contract.

Workers are separate binaries. Run the queue-specific executable required by
the feature; do not start a general Python worker beside it. For example:

```bash
DATABASE_URL=postgresql://localhost/newsly \
NEWSLY_DOCUMENT_EXTRACTOR_SHARED_SECRET=local-development-secret \
cargo run -p newsly-worker --bin newsly-worker
```

The extractor defaults to `http://127.0.0.1:8200/` and may be changed with
`NEWSLY_DOCUMENT_EXTRACTOR_URL`. It receives no database configuration. Rust
owns Firecrawl credentials, retry classification, usage persistence, body
storage, and downstream enqueueing.

## SQLx migrations

SQLx 0.9 is the only active query and migration library. Migrations are embedded
in the exact `newsly-db` binary. The first migration is the audited catalog at
frozen Alembic head `20260829_02`:

- fresh databases execute the baseline normally;
- an existing Alembic database is adopted once under a maintenance barrier;
- adoption holds one lock across head/fingerprint verification, exact
  checksum-matching prefix recognition, SQLx baseline recording, and the first
  pending migration run;
- gaps, mismatched checksums, roles, grants, extensions, or bounded data
  invariants fail closed;
- ordinary migration runs never infer permission to adopt an existing database.

Read-only verification and deliberate adoption:

```bash
DATABASE_URL=postgresql://localhost/newsly \
  cargo run -p newsly-db -- verify-baseline

DATABASE_URL=postgresql://localhost/newsly \
  cargo run -p newsly-db -- baseline --maintenance-barrier-confirmed
```

The removed Alembic tree remains available through repository history when a
historical database needs explanation. Do not recreate it, add an Alembic
revision, or add an application SQLAlchemy model.

## Containers and releases

Build from the repository root so the exact tested revision is recorded in the
runtime image metadata without invalidating the Rust compilation layer:

```bash
docker build \
  --file Dockerfile \
  --build-arg NEWSLY_BUILD_SHA="$(git rev-parse HEAD)" \
  --tag "newsly:$(git rev-parse HEAD)" \
  .
```

The required quality workflow validates the candidate revision before the
deployment workflow builds and publishes its exact image SHA. Release evidence
must distinguish checkout validation, the tested SHA, the built image, the
deployed revision, and live health or provider canaries. See the repository-root
README and `docs/library/deploy/docker_racknerd.md` for the canonical commands
and production sequence.
