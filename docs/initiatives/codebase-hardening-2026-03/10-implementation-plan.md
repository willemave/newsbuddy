# Codebase Hardening and Maintainability Implementation Plan

**Date:** 2026-03-05  
**Scope:** Current worktree as assessed on 2026-03-05  
**Primary goals:** security hardening, secret-boundary cleanup, CI quality gates, module decomposition, targeted test deepening

---

## Why This Plan Exists

The current worktree is functionally healthy but structurally uneven:

- `uv run pytest tests/ -q` passed `907` tests.
- `uv run ruff check app tests scripts` reported `133` issues.
- `python scripts/check_module_size_guardrails.py` failed because `app/services/onboarding.py` is `2352` lines vs a `2350` line limit.

The immediate risk is not broken feature behavior. The immediate risk is that production-facing security shortcuts and growing module complexity will make the next round of feature work slower, riskier, and harder to verify.

This plan is designed to be executed in slices. It prioritizes removing the highest-risk exposures first, then installs automation, then pays down architectural debt in the highest-churn modules.

---

## Goals

- Remove long-lived provider secret exposure to clients.
- Harden authentication and admin access paths to production-grade defaults.
- Stop logging and returning sensitive request payloads.
- Add CI gates so quality checks run before deploy.
- Bring oversized modules back under explicit ownership boundaries.
- Standardize exception policy and error logging semantics.
- Deepen tests around high-risk operational paths.

## Non-Goals

- No full rewrite of the queue or processing architecture.
- No broad database redesign beyond what is required for auth/session hardening.
- No design-system or UI restyling work.
- No attempt to fix every Ruff issue in the repo before starting structural work.

## Execution Principles

- Land the security boundary changes before the large refactors.
- Add automation before relying on policy or discipline.
- Split modules by responsibility boundaries, not arbitrary line counts.
- Preserve API behavior where possible; where behavior changes, document and version the contract.
- Prefer thin routers and adapters; keep business logic in service/domain modules.

---

## Workstream Summary

| Workstream | Priority | Effort | Why First |
|---|---|---:|---|
| A. Secret Boundary Cleanup | P0 | M | Removes the highest-risk exposure immediately |
| B. Auth and Admin Hardening | P0 | M | Closes production auth shortcuts |
| C. Validation / Logging Sanitization | P0 | S | Prevents accidental credential and PII leakage |
| D. CI Quality Gates | P1 | S | Makes standards enforceable before deploy |
| E. Module Decomposition | P1 | L | Reduces change blast radius in hot paths |
| F. Exception Policy and Retry Semantics | P1 | M | Makes failures diagnosable and consistent |
| G. Targeted Test Expansion | P1 | M | Raises confidence around the highest-risk surfaces |
| H. Docs and Operational Follow-Through | P2 | S | Keeps architecture/docs aligned after changes |

---

## Phase 0: Baseline and Branch Protection

### Deliverables

- Record the current passing test baseline.
- Add a short-lived branch protection or release rule so deploys do not proceed from partially migrated auth work.
- Create one tracking issue per workstream so execution can happen independently.

### Baseline Commands

```bash
uv run pytest tests/ -q
uv run ruff check app tests scripts
python scripts/check_module_size_guardrails.py
```

### Acceptance Criteria

- Baseline command outputs are captured in the PR description or issue tracker.
- Security-sensitive refactors are not mixed with unrelated feature work.

---

## Workstream A: Secret Boundary Cleanup

### Problem

The backend currently returns `openai_api_key` in auth responses and the iOS client stores it in Keychain for later use. That makes the server-side provider key part of the client trust boundary.

### Deliverables

- Remove `openai_api_key` from auth response models and handlers.
- Remove iOS storage and lookup paths for `openaiApiKey`.
- Move all realtime transcription/token acquisition to short-lived backend-minted tokens or existing voice-session APIs.
- Audit any remaining client-side OpenAI direct-call paths and either:
  - route them through backend endpoints, or
  - replace them with ephemeral token minting where the vendor supports it.

### Primary Files

- `app/routers/auth.py`
- `app/models/user.py`
- `app/routers/api/openai.py`
- `app/services/openai_realtime.py`
- `client/newsly/newsly/Models/User.swift`
- `client/newsly/newsly/Services/AuthenticationService.swift`
- `client/newsly/newsly/Services/KeychainManager.swift`
- `client/newsly/newsly/Services/VoiceDictationService.swift`
- `client/newsly/newsly/ViewModels/ChatSessionViewModel.swift`
- `client/newsly/newsly/ViewModels/TweetSuggestionsViewModel.swift`

### Implementation Steps

1. Remove `openai_api_key` from backend auth DTOs and route responses.
2. Keep provider keys server-side only.
3. Confirm `POST /api/openai/realtime/token` is the canonical path for short-lived client tokens.
4. Update iOS auth/session bootstrap to stop expecting `openaiApiKey`.
5. Delete Keychain storage and any debug logging for provider key receipt.
6. Migrate voice dictation and any other client feature that still reads a stored provider key.

### Acceptance Criteria

- Auth responses no longer contain long-lived provider keys.
- No iOS code path reads or stores `openaiApiKey`.
- Voice dictation still works using short-lived tokens or backend-mediated requests.
- Rotating the server OpenAI key requires no client-side changes.

### Risks / Mitigation

- **Risk:** voice dictation breaks if a client path still expects a raw provider key.  
  **Mitigation:** ship this work behind a temporary client feature flag if needed and verify with a local iOS smoke test.

---

## Workstream B: Auth and Admin Hardening

### Problem

Apple token signature verification is disabled, admin sessions are stored in-memory, and CORS is fully open.

### Deliverables

- Replace Apple token decoding shortcut with JWKS-based signature verification.
- Restrict accepted Apple algorithms and validate issuer, audience, subject, issued-at, and expiry.
- Replace in-memory admin sessions with durable sessions in Redis or the database.
- Add session expiry, rotation, and logout invalidation.
- Restrict CORS to configured allowed origins instead of `*`.
- Review token lifetimes and reduce them if mobile behavior allows it.

### Primary Files

- `app/core/security.py`
- `app/core/settings.py`
- `app/core/deps.py`
- `app/main.py`
- `app/routers/auth.py`
- `app/models/schema.py` or a dedicated session model module
- `migrations/alembic/versions/*` for admin session persistence if DB-backed
- `tests/core/test_security.py`
- `tests/core/test_deps_admin.py`
- `tests/routers/test_auth.py`

### Implementation Steps

1. Add Apple JWKS retrieval and caching.
2. Verify token signatures using `kid` lookup.
3. Add settings for allowed Apple audiences and allowed CORS origins.
4. Replace `admin_sessions = set()` with durable session storage.
5. Add TTL, issued-at, last-seen, and invalidation semantics for admin sessions.
6. Update auth tests to cover invalid signature, wrong audience, expired token, session expiry, and logout.

### Acceptance Criteria

- Forged Apple tokens are rejected.
- Admin sessions survive process restart and work in multi-instance deployments.
- Admin logout invalidates the stored session.
- CORS is restricted to configured environments.

### Risks / Mitigation

- **Risk:** Apple auth regressions in development.  
  **Mitigation:** keep a development-only debug auth path explicitly gated by `settings.debug` and environment.

---

## Workstream C: Validation and Logging Sanitization

### Problem

The 422 handler logs full headers and raw request bodies and also echoes the raw body back to clients.

### Deliverables

- Redact or drop `Authorization`, cookies, and provider tokens from all validation and error logs.
- Stop returning raw request bodies in validation error responses.
- Add a shared helper for safe request logging.
- Standardize structured `extra` fields on error paths.

### Primary Files

- `app/main.py`
- `app/core/logging.py`
- `app/services/*` error call sites as touched
- `tests/core/` or `tests/routers/` for request validation behavior

### Implementation Steps

1. Replace raw header/body logging with a redacted representation.
2. Remove `"body": body_text` from validation responses.
3. Add helper utilities for header redaction and bounded-body logging.
4. Update any related tests or add new ones to assert sensitive fields are not returned.

### Acceptance Criteria

- Validation responses contain only structured validation detail.
- Auth headers, cookies, and secret-like values are never written verbatim.
- Error logs still retain enough request context for debugging.

---

## Workstream D: CI Quality Gates

### Problem

The repo has pre-commit hooks but GitHub Actions currently deploys without gating on lint, tests, mypy, or module-size checks.

### Deliverables

- Add a dedicated CI workflow for pull requests and pushes.
- Run:
  - `uv sync`
  - `uv run ruff check app tests scripts`
  - `uv run mypy app`
  - `uv run pytest tests/ -q`
  - `python scripts/check_module_size_guardrails.py`
- Make deploy depend on CI success, or at minimum keep deploy and CI as separate workflows with required status checks.
- Decide whether Ruff should lint `tests/` by default; if yes, remove `tests` from the default exclude set.
- Add coverage reporting with a fail-under threshold once the current suite is stabilized.

### Primary Files

- `.github/workflows/ci.yml` (new)
- `.github/workflows/bare-metal-deploy.yml`
- `pyproject.toml`
- `.pre-commit-config.yaml`

### Implementation Steps

1. Create `ci.yml` with Python setup, dependency cache, and the command matrix above.
2. Update Ruff configuration so test linting is explicit and consistent.
3. Add `pytest-cov` configuration after the first pass of test cleanup.
4. Add required checks in repository settings.

### Acceptance Criteria

- PRs fail before merge when lint, mypy, tests, or guardrails fail.
- Deploys do not bypass CI by default.
- Test linting is intentional rather than accidental.

### Risks / Mitigation

- **Risk:** CI starts red because test lint debt already exists.  
  **Mitigation:** land a small dedicated cleanup pass first or initially scope Ruff to a smaller set with a follow-up issue to widen coverage.

---

## Workstream E: Module Decomposition

### Problem

The highest-churn files mix unrelated responsibilities and are already past or near guardrails. This increases review cost and makes failures harder to localize.

### Initial Targets

- `app/services/onboarding.py`
- `app/services/feed_discovery.py`
- `app/services/chat_agent.py`
- `app/services/voice/orchestrator.py`
- `app/routers/api/models.py`
- `app/models/metadata.py`

### Decomposition Rules

- Split by responsibility boundary, not helper-count alone.
- Keep side effects at the edges.
- Move transport DTOs out of service modules.
- Prefer packages with explicit public surfaces over many cross-imported utility files.

### Proposed Slices

#### E1. Onboarding

Split into a package such as:

- `app/services/onboarding/profile.py`
- `app/services/onboarding/discovery.py`
- `app/services/onboarding/audio_plan.py`
- `app/services/onboarding/persistence.py`
- `app/services/onboarding/defaults.py`
- `app/services/onboarding/contracts.py`

Key changes:

- Remove router-model imports from the service layer.
- Move shared onboarding DTOs to `app/models/` or `app/schemas/`.
- Separate Exa/LLM orchestration from DB persistence and default-feed seeding.

#### E2. Chat Agent

Split into:

- agent/tool construction
- article/session context building
- message persistence
- async run orchestration
- usage accounting

#### E3. Voice Orchestrator

Split into:

- websocket/session event protocol
- turn state machine
- STT/TTS adapter code
- persistence hooks
- trace logging helpers

#### E4. API Models and Metadata

Split large DTO/model files by domain:

- `app/routers/api/models/content.py`
- `app/routers/api/models/chat.py`
- `app/routers/api/models/onboarding.py`
- `app/models/metadata/article.py`
- `app/models/metadata/podcast.py`
- `app/models/metadata/news.py`
- `app/models/metadata/summaries.py`

### Acceptance Criteria

- `app/services/onboarding.py` is replaced by a package and falls back below the current guardrail threshold.
- Service modules do not import router-layer DTOs.
- The largest modules have clearer ownership and smaller review surfaces.
- Import graphs become simpler, not more fragmented.

### Risks / Mitigation

- **Risk:** refactors introduce import cycles.  
  **Mitigation:** define package `__init__.py` files with minimal re-exports and move shared types first.

---

## Workstream F: Exception Policy and Retry Semantics

### Problem

Broad `except Exception` usage is common across services, pipeline code, and routers. Failures are often logged inconsistently and retryability is not always explicit.

### Deliverables

- Define a small exception taxonomy for:
  - user/input errors
  - external transient failures
  - external terminal failures
  - persistence failures
  - internal invariant violations
- Replace broad catches in hot paths with typed exceptions where possible.
- Ensure queue handlers explicitly mark retryable vs non-retryable outcomes.
- Standardize `logger.error()` and `logger.exception()` calls to always include `component`, `operation`, and `context_data` when relevant.

### Primary Files

- `app/services/chat_agent.py`
- `app/services/content_analyzer.py`
- `app/services/llm_summarization.py`
- `app/services/deep_research.py`
- `app/pipeline/handlers/*`
- `app/pipeline/sequential_task_processor.py`

### Implementation Steps

1. Add shared exception classes in a central module, for example `app/core/errors.py`.
2. Migrate one hotspot at a time, starting with queue-facing services.
3. Update retry routing based on exception class rather than string matching where feasible.
4. Add tests for transient vs terminal behavior.

### Acceptance Criteria

- Hot queue paths no longer rely on broad bare `Exception` for normal control flow.
- Retry behavior is deterministic and test-backed.
- Error logs are consistent enough for JSONL and dashboard analysis.

---

## Workstream G: Targeted Test Expansion

### Problem

The suite is broad, but several high-risk areas are tested more lightly than their complexity suggests.

### Priority Targets

- Admin logs routes and file access paths
- Chat session persistence and async failure/update paths
- Voice orchestrator lifecycle, cancellation, and cleanup
- Auth edge cases after the security hardening work
- Any new session store added for admin auth

### Primary Files

- `tests/routers/test_logs.py`
- `tests/services/test_chat_agent.py`
- `tests/services/test_voice_orchestrator.py`
- `tests/core/test_security.py`
- `tests/core/test_deps_admin.py`
- `tests/routers/test_auth.py`

### Deliverables

- Endpoint-level tests for `/admin/logs`, `/admin/logs/{filename}`, and `/admin/logs/{filename}/download`.
- Async-path tests for chat message failure and persistence updates.
- Voice tests for cancellation, interrupted turn cleanup, and TTS/STT failure handling.
- Security tests for Apple signature validation, admin session expiry, and restricted CORS config behavior where practical.

### Acceptance Criteria

- Critical auth and operational paths have direct tests, not only helper coverage.
- New refactors land with regression tests in the same PR.

---

## Workstream H: Docs and Operational Follow-Through

### Problem

Some documentation already reflects known gaps, but implementation details can drift from the code quickly during multi-phase refactors.

### Deliverables

- Update `docs/architecture.md` after major auth and module-boundary changes.
- Document the canonical realtime token flow for iOS voice.
- Add CI commands to `README.md` or `docs/library/operations/command-index.md` if needed.
- Document any new admin session storage or environment variables.

### Acceptance Criteria

- Docs match the post-change auth and voice flows.
- Engineers can discover the new CI and operational commands without reading PRs.

---

## Recommended Execution Order

1. Phase 0 baseline capture
2. Workstream A: secret boundary cleanup
3. Workstream B: auth and admin hardening
4. Workstream C: validation/logging sanitization
5. Workstream D: CI quality gates
6. Workstream G: auth/logging regression tests if not already landed inside A-C
7. Workstream E1: onboarding decomposition
8. Workstream E2/E3: chat and voice decomposition
9. Workstream E4: API models and metadata split
10. Workstream F: exception-policy normalization across touched modules
11. Workstream H: architecture/doc refresh

This sequence keeps the highest-risk exposure work small and front-loaded, while postponing the large refactors until automation and regressions are in place.

---

## Milestone Structure

### Milestone 1: Security Boundary

- Workstreams A-C
- Minimal tests required before merge
- No broad refactors

### Milestone 2: Quality Gate Installation

- Workstream D
- Initial Ruff/test debt cleanup as needed to make CI green

### Milestone 3: Hotspot Decomposition

- Workstream E1 first
- Then E2 and E3
- E4 can happen incrementally afterward

### Milestone 4: Failure Semantics and Follow-Through

- Workstream F
- Workstream G for remaining gaps
- Workstream H docs refresh

---

## Validation Matrix

### Required on Every Milestone

```bash
uv run ruff check app tests scripts
uv run pytest tests/ -q
python scripts/check_module_size_guardrails.py
```

### Required on Backend Type or Boundary Changes

```bash
uv run mypy app
```

### Required on Auth / Voice Changes

- Manual auth smoke test for iOS sign-in
- Manual voice token/session smoke test
- Manual admin login/logout/session expiry test

### Required on iOS Client Secret-Boundary Changes

- Simulator build
- Login
- Voice dictation flow
- Any feature that previously read `openaiApiKey`

---

## Rollback Strategy

- Workstreams A-C should be merged in small PRs so rollback is file-scoped.
- If the new admin session store causes instability, temporarily fall back to a feature-flagged debug path only in development.
- CI rollout can be staged:
  - add workflow first
  - make it informational
  - then make it required
- Module decomposition should preserve old import surfaces briefly via compatibility re-exports where necessary.

---

## Exit Criteria

This plan is complete when all of the following are true:

- No long-lived provider secret is sent to clients.
- Apple token verification is signature-backed and admin sessions are durable.
- Validation error handling no longer exposes raw headers or raw bodies.
- CI gates run before deploy and are required for merge.
- `onboarding`, `chat_agent`, and `voice/orchestrator` are split into coherent modules or packages.
- The dominant queue-facing services use typed exception handling and structured logging.
- Critical auth, logs, chat, and voice paths have direct regression tests.
- Documentation reflects the new architecture.

---

## Suggested First PR

Keep the first PR narrow:

1. Remove `openai_api_key` from backend auth responses.
2. Remove iOS Keychain storage/reads for `openaiApiKey`.
3. Route voice token acquisition through `/api/openai/realtime/token`.
4. Add or update tests for auth response shape and token flow.

That yields the fastest risk reduction with the smallest blast radius.
