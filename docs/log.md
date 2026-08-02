# Engineering Log

Use this append-only log to preserve implementation context across sessions and branches. Record decisions and evidence that another agent or developer would need to continue the work without reconstructing it from scratch.

## Logging Rules

- Add an entry when implementation begins, and update it before handoff or switching branches.
- Record the date, branch, status, scope, important decisions, meaningful changes, validation, and unfinished work.
- Keep entries concise. Do not paste raw command transcripts, routine exploration, generated output, credentials, tokens, or other secrets.
- Preserve unrelated entries. Correct an earlier entry with a dated follow-up instead of rewriting its history.
- Move entries between branches through normal Git commits, merges, or cherry-picks; an uncommitted entry is visible only in the current working tree.
- Keep durable system design in `docs/architecture.md`; this file records implementation history and handoff state.

## Entry Template

```markdown
### YYYY-MM-DD — `<branch>` — Work item

- **Status:** In progress | Blocked | Complete
- **Scope:** Files, packages, or product path being changed.
- **Decisions:** Important choices and why they were made.
- **Changes:** Concise summary of meaningful implementation work.
- **Validation:** Tests, checks, or runtime evidence completed.
- **Remaining:** Unfinished work, risks, or `None`.
- **Commits:** Commit hashes, or `Uncommitted`.
```

## Entries

### 2026-08-02 — `willem/fix-malformed-external-links-2026-08-02` — Ignore malformed extracted article links

- **Status:** Complete
- **Scope:** Interesting external-link URL normalization and focused service coverage for short-form news processing.
- **Decisions:** Treat URL-parser `ValueError` as candidate-local invalid input so defanged or malformed bracket URLs are skipped without failing or retrying the owning news item.
- **Changes:** Made candidate normalization fail closed on deterministic URL-validation errors and added a regression using the malformed Pipedream URL shape from news item 21750 alongside valid protocol-relative and absolute links.
- **Validation:** Focused Ruff passed; all 4 interesting-external-link service tests passed; `git diff --check` passed.
- **Remaining:** After deployment, safely enqueue news item 21750 once for reprocessing.
- **Commits:** Included in this commit.

### 2026-08-02 — `willem/reduce-x-api-cost` — Clean up X cost-reduction patch

- **Status:** Complete
- **Scope:** Current unstaged X cadence, usage-accounting, tests, and documentation diff.
- **Decisions:** Preserve the implemented cadence and billing behavior; leave the separate global and bookmark-channel cooldown controls because collapsing them would change the existing config contract.
- **Changes:** Simplified resource-ID normalization to one typed set operation, ignored malformed non-string IDs instead of stringifying them, removed a mirrored page-size assertion, and clarified that active queue deduplication is scoped by connection and trigger payload.
- **Validation:** Focused Ruff passed; 76 X integration/API, vendor-cost, handler, and queue tests passed; `git diff --check` passed.
- **Remaining:** None beyond the parent work item's normal review and rollout.
- **Commits:** Included in this commit.

### 2026-08-02 — `willem/reduce-x-api-cost` — Reduce X bookmark-sync cost

- **Status:** Complete
- **Scope:** X bookmark cadence, checkpoint pagination, vendor usage accounting, focused tests, and architecture/config documentation.
- **Decisions:** Keep bookmark sync enabled and incremental; use an hourly effective interval because production has averaged about one new bookmark per day and no non-initial hour exceeded three new items; retain the 15-minute scheduler fan-out and queue deduplication so failed or jittered work is retried promptly.
- **Changes:** Changed default X sync cooldowns from 15 to 60 minutes, reduced bookmark pages from 10 to 5 with checkpoint-driven pagination retained, documented the environment controls, attached returned X resource IDs to usage rows, and estimated X cost using its UTC-day resource deduplication while retaining raw request/resource counts.
- **Validation:** Read-only production DB/runtime/log evidence confirmed one active connection, 95 non-retried cron tasks, 94 ten-resource reads, and zero new bookmarks over the rolling 24 hours. Focused Ruff passed; 76 X integration/API, vendor-cost, handler, and queue tests passed; `git diff --check` passed.
- **Remaining:** Review and roll out through the normal deployment path; confirm live interval settings and compare post-deploy raw versus billable X resources. No production mutation, commit, push, or deployment was performed.
- **Commits:** Included in this commit.

### 2026-08-01 — `main` — Add durable implementation guidance

- **Status:** Complete
- **Scope:** Root agent guidance and the cross-session engineering log.
- **Decisions:** Require small working slices and durable architecture while allowing explicitly managed compatibility paths for staged migrations.
- **Changes:** Added the implementation principles to `AGENTS.md` and established this structured, append-only log.
- **Validation:** Reviewed the resulting Markdown and ran `git diff --check`.
- **Remaining:** None.
- **Commits:** Uncommitted.

### 2026-08-01 — `main` — Build, test, and deploy release

- **Status:** In progress
- **Scope:** Current backend, iOS, generated-contract, dependency, deployment, and documentation changes in the release worktree.
- **Decisions:** Preserve the existing `main` history, split the work into topical commits, and use the pushed SHA as the release identity through Python, native iOS, Maestro, and Docker deployment gates.
- **Changes:** Preparing account deletion and public policy routes, iOS release/privacy configuration, briefing figure alignment, and Langfuse/client secret-sync removal for release.
- **Validation:** Not started.
- **Remaining:** Complete all release gates, push the tested SHA, verify the commit-matched Docker deployment and live health.
- **Commits:** Uncommitted

### 2026-08-02 — `main` — Prevent transient Briefing Retry on reopen

- **Status:** Complete
- **Scope:** iOS Briefing lens task ownership, inactive lifecycle behavior, and focused view-model tests.
- **Decisions:** Treat replaced or cancelled lens completions as stale regardless of the URL error code, and allow the selected lens hydration to finish while cancelling only speculative neighbor loads.
- **Changes:** Added a generation/cancellation guard to lens error handling, preserved selected-lens hydration across deactivation, disabled inactive neighbor prefetch, and added regressions for late `-1005` errors and inactive hydration.
- **Validation:** Both new tests failed against the prior behavior; after the fix, 43 `BriefingViewModelTests` and `BriefingViewModelRefreshTests` passed, the `newsly` iOS 26.5 Simulator build succeeded, and `git diff --check` passed.
- **Remaining:** None.
- **Commits:** Included in this commit.

### 2026-08-02 — `main` — Bound the reusable Crawl4AI lifecycle

- **Status:** Complete
- **Scope:** HTML extraction, its dedicated reusable Crawl4AI manager, lifecycle tests, and processing architecture documentation.
- **Decisions:** Keep one warm, single-flight browser on its dedicated event-loop thread; use Crawl4AI 0.9.2's public per-page context recycling instead of private page cleanup; apply one deadline to lock admission and crawling; cancel and replace a crawler that exceeds it.
- **Changes:** Bounded crawl and cleanup waits, migrated crawler startup/shutdown to public lifecycle methods, enabled `max_pages_before_recycle=1`, and added regressions for timeout cancellation, recovery, lock contention, and stuck cleanup.
- **Validation:** Ruff and mypy passed for the crawler manager and HTML strategy; 37 focused HTML strategy tests; 139 processing-strategy tests; `git diff --check`.
- **Remaining:** Monitor production deadline and crawler-reset logs; move browser work behind process isolation only if Chromium or Playwright proves non-cooperative after cancellation.
- **Commits:** Included in this commit.
### 2026-08-02 — `willem/learning-deck-source-dependency` — Make Learning Deck source waits dependency-aware

- **Status:** Complete
- **Scope:** Learning Deck generation, focused backend tests, and queue architecture notes.
- **Decisions:** Replace the deck-age timeout with source-pipeline state: active ingestion remains resumable, while terminal or orphaned dependencies fail explicitly. Reuse the existing retry-preserving deferral loop rather than adding a parallel wakeup or delivery-deduplication path.
- **Changes:** Added source task inspection at the existing source-not-ready boundary and covered long delays, terminal/missing dependencies, terminal redelivery, and retry-preserving deferral.
- **Validation:** Ruff passed on the touched Python/test files; 51 focused Learning Deck, LLM handler, and queue-service tests passed; `git diff --check` passed.
- **Remaining:** Production remains unchanged. After review and deployment, Deck 12 requires an explicit rerun because its prior LLM task is terminal.
- **Commits:** Included in this commit.

### 2026-08-02 — `detached HEAD` — Repair PDF Gemini model routing

- **Status:** Complete
- **Scope:** PDF/arXiv Gemini extraction, model defaults and pricing, provider-error classification, focused tests, and environment guidance.
- **Decisions:** Replace the shut-down `gemini-3.1-flash-lite-preview` default with stable `gemini-3.1-flash-lite`; explicitly select the Gemini Developer API for API-key PDF extraction so the process-wide Vertex routing environment cannot redirect it through an incompatible region; retain local parsing as the fallback and classify deterministic model/location failures as warnings.
- **Changes:** Added a shared direct-client/error-classification helper, routed both PDF strategies through it, updated the default and pricing entry, documented the setting, and added unavailable-model/region regressions.
- **Validation:** Production worker inspection confirmed no `PDF_GEMINI_MODEL` override, `GOOGLE_GENAI_USE_VERTEXAI=true`, and the old preview default; a read-only production-key model lookup confirmed `gemini-3.1-flash-lite` supports `generateContent`; focused Ruff passed and 38 focused settings/strategy/helper/pricing tests passed.
- **Remaining:** Deploy through the normal release workflow for production to consume the new code default; no production environment edit is required unless operators prefer to pin `PDF_GEMINI_MODEL=gemini-3.1-flash-lite` explicitly.
- **Commits:** Uncommitted.

### 2026-08-02 — `willem/openai-luna-pdf-extraction` — Replace PDF extraction with native OpenAI Luna

- **Status:** Complete
- **Scope:** Generic PDF and arXiv extraction, OpenAI provider integration, settings, dependencies, tests, and service documentation.
- **Decisions:** Supersede the earlier Gemini routing repair after live comparison showed direct `gpt-5.6-luna` preserved page images and matched all 29 extraction checks; use Responses PDF input with Base64 bytes, high visual detail, and explicit `reasoning.effort=none`; remove local PDF parsing rather than retain an unrequested fallback.
- **Changes:** Added the shared native OpenAI PDF extractor, moved both strategies to `PDF_EXTRACTION_MODEL=gpt-5.6-luna`, removed the Google PDF helper and local `pypdf` extraction path, and removed the now-unused `pypdf` dependency.
- **Validation:** Focused Ruff and mypy passed; 38 focused settings/strategy/helper/pricing tests and all 140 processing-strategy tests passed; `uv lock --check`, `uv pip check`, and `git diff --check` passed.
- **Remaining:** Deploy normally for production to consume the new code default. Production needs no setting edit because neither the old nor new PDF model variable is explicitly set; operators may explicitly pin `PDF_EXTRACTION_MODEL=gpt-5.6-luna`.
- **Commits:** Included in this commit.
