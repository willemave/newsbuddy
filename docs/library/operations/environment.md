# Newsly Development Environment

Newsly's application backend is the Rust workspace under `rust/`.
Newsly-owned Python has two isolated projects: `python/document_extractor` for
the database-free Crawl4AI service and `python/evals` for offline model and
embedding work. The application image's pinned third-party `yt-dlp` executable
may bring its own Python runtime, but it is a Rust-controlled media tool rather
than an application package.

## Native local runtime

Install Rust 1.94.1, PostgreSQL 15 or newer, and `uv`. On macOS, the setup
helper installs and starts Homebrew PostgreSQL, creates the local database and
role, and writes a native SQLx URL to `.env`:

```bash
cp .env.example .env
./scripts/setup_local_postgres.sh
./scripts/start_services.sh all --env-file .env
```

The unified launcher accepts `server`, `workers`, `scheduler`, `extractor`, and
`migrate` in addition to `all`. Scraper scheduling and queue recovery belong to
the Rust scheduler; there are no separate scraper or watchdog processes.

For a complete local end-to-end run, choose an unoccupied API port and use the
bounded pool profile:

```bash
./scripts/start_services.sh all --env-file .env --local-e2e --port 8010
./scripts/codex_run_ios.sh --api-base-url http://127.0.0.1:8010
# or: ./scripts/axe_simulator_smoke.sh --api-base-url http://127.0.0.1:8010
```

The profile forces one pooled PostgreSQL connection per background process and
two for the API, in addition to the queue listeners. The launcher supervises
the extractor and Rust processes as direct children and terminates the rest of
the stack if one exits unexpectedly.

## Local release gate

The complete source-level release gate runs locally, not in GitHub Actions:

```bash
scripts/release_gate.sh --env-file /absolute/path/to/local.env
```

It requires a clean commit, starts one local Rust API for authenticated iOS and
AXe validation, and records the exact tested SHA. Add `--with-live-smoke
--allow-live-provider-costs` when the release needs the real API/LLM/E2B,
Learning Deck, chat, and Share Extension matrix. That phase builds the two
Docker images once and shares them across the full run.

On push to `main`, GitHub only builds and smoke-tests immutable production
images and deploys them after an exact current-main check. Xcode Cloud provides
the independent clean iOS build for each pushed main revision.

## Rust dependencies and checks

```bash
cd rust
cargo fetch --locked
cargo fmt --all -- --check
cargo clippy --workspace --all-targets --locked -- -D warnings
cargo test --workspace --locked
```

Use `cargo add -p <crate> <dependency>` for Rust dependencies. Do not add an
application dependency to a root Python project.

## Python islands

Each Python island has its own project metadata, lock, environment, and checks:

```bash
uv sync --project python/document_extractor --frozen --group dev
uv run --project python/document_extractor pytest -q python/document_extractor/tests

uv sync --project python/evals --frozen --group dev
uv run --project python/evals mypy --config-file python/evals/pyproject.toml \
  python/evals/src python/evals/scripts python/evals/tests
uv run --project python/evals pytest -q python/evals/tests
```

Neither island may own PostgreSQL, queue, auth, migration, or product state.

## SQLx migrations

Add timestamped reversible pairs under
`rust/crates/newsly-db/migrations/` and apply them with:

```bash
scripts/run_sqlx_migrations.sh
```

An existing database at the frozen pre-SQLx head is adopted once, with every
writer stopped and the maintenance barrier explicitly attested:

```bash
NEWSLY_SQLX_BASELINE_ADOPTION=true \
NEWSLY_MAINTENANCE_BARRIER_CONFIRMED=true \
scripts/run_sqlx_migrations.sh
```

Remove both one-shot flags after successful adoption. Fresh and already
adopted databases use the ordinary migration command.

## Public contracts and admin CSS

Rust Utoipa types own public OpenAPI:

```bash
scripts/regenerate_public_contracts.sh
scripts/check_public_contracts.sh
```

Tailwind input and output live under `rust/assets/admin-static/css/`:

```bash
npm ci
npm run build:css
```

## Required local configuration

Start from [`.env.example`](../../../.env.example). The minimum application
values are a native `DATABASE_URL`, `JWT_SECRET_KEY`, `ADMIN_PASSWORD`, and a
separate `DOCUMENT_EXTRACTOR_SHARED_SECRET`. Provider and E2B credentials are
required only for the paths that call those services. Never commit an env file
or print secret values in diagnostics.
