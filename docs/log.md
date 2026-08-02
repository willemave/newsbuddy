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
