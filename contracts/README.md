# Migration contract corpus

This directory is the language-neutral contract corpus for Newsly's production
runtime. The Rust `newsly-api` Utoipa document is the sole public route and
OpenAPI authority. Task, queue, E2B, extraction, and evaluation fixtures are
versioned compatibility inputs that Rust tests consume directly. No Python
application can generate, approve, or override them.

The corpus contains:

- `openapi/`: the complete checked-in public OpenAPI document and representative
  request, success-response, and error-response fixtures;
- `tasks/`: one JSON Schema per registered queue task, the hash-pinned task
  catalog, and queue transition fixtures. `TaskType::ALL` and `TaskSpec` are
  the canonical task inventory and routing policy; Rust tests require the
  catalog and ownership manifest to cover every enum value exactly once and
  to match their concrete handler, queue, deduplication, user-ownership,
  schema-path, and schema-hash declarations;
- `metadata/`: absent/null/default wire-presence, typed UTC timestamp, legacy
  JSONB, and database schema fingerprints;
- `llm/`: provider/history and E2B lifecycle, streaming, path, and recovery
  recordings;
- `extraction/`: the DB-less Crawl4AI service boundary and golden cases;
- `evals/`: language-neutral inputs for Python-built embedding bundles consumed
  by Rust production algorithms;
- `fixtures/index.json`: a frozen hash-indexed inventory of compatibility inputs;
- `policy-manifest.toml`: audited route, task, state-writer, and E2B namespace
  ownership plus deletion conditions. PostgreSQL is the live ownership control
  plane; the manifest is its checked desired-state input.

Regenerate Rust-owned public schemas after an intentional HTTP-contract change:

```bash
scripts/regenerate_public_contracts.sh
scripts/check_public_contracts.sh
```

Update a frozen compatibility fixture deliberately beside the Rust test that
consumes it, including its entry in `fixtures/index.json`. There is no Python
migration-corpus generator or checker.

The checked-in Swift app and Share Extension sources are generated from the
Rust OpenAPI document through the reviewed client policy. The Rust `newsbuddy`
CLI consumes request and error types from `newsly-contracts` directly and keeps
ordinary successful responses forward-compatible as JSON; it has no generated
wire-model file or checked CLI-specific OpenAPI copy. Every public change must
keep the Rust OpenAPI snapshot, typed error envelope, generated native-client
artifacts, and affected Rust CLI/native-client tests aligned.

The Python islands consume only their narrow corpus sections:

- `python/document_extractor` implements the `extraction/` request/result schema
  without database or queue access;
- `python/evals` produces versioned artifacts under `evals/`, while
  `newsly-eval-driver` executes the canonical production algorithm.

Fixtures must remain synthetic and privacy-safe. Never check in real tokens,
credentials, prompts, user content, URLs containing private identifiers, or
production provider responses.
