# Learning Decks Design

## Goal

Add a Learning Decks feature that turns one article, podcast transcript, PDF/arXiv paper, Fast Read-enriched article, or public GitHub repository into a hosted Reveal.js learning deck. The deck should teach the topic with polished presentation quality and real depth: architecture, how it is built, tradeoffs, related approaches, and useful context. Users can add one freeform interests prompt, but generation is one-shot and asynchronous.

## Product Decisions

- Name: `Learning Decks`.
- First surface: a section inside the Knowledge tab.
- Secondary entry point: `Create learning deck` on eligible content detail surfaces.
- Viewer: open the hosted raw Reveal.js deck in an in-app Safari view by default, with an option to open in the external browser.
- Input sources:
  - existing Newsly content with already-processed body/transcript/PDF text
  - pasted non-GitHub URLs, ingested through normal Newsly content processing first
  - public GitHub repository URLs stored directly on the Learning Deck, not as `contents` rows
  - Fast Reads, by converting/enriching into long-form content first
- Scope: one primary source per deck.
- Customization: one freeform interests text field.
- Generation: one-shot background job, no outline approval.
- Deck length target: medium, about 20-30 slides.
- Deck style: presentation-polished and visually coherent, while still teaching deeply.
- Search: list/filter only in MVP; no full-text or metadata search.
- Rate limit: one active generation per user, no daily cap.
- Access: all authenticated users.
- Replacement: one current deck per source. Rerunning replaces the current deck only after the new artifact validates and publishes successfully.
- Failure: failed reruns keep the old successful deck and share link live.
- Sharing: simple share toggle with one stable public token URL per deck. When enabled, the token serves the latest successful deck.
- Deletion: deletes the deck artifact bundle from object storage and invalidates access. Internal raw logs are retained under normal retention policy.

## Source Identity

Learning Deck replacement uses source identity, not the interests prompt.

- Existing content: `content:{content_id}`.
- Pasted non-GitHub URL: ingest first, then use the resulting `content:{content_id}`.
- Fast Read: enrich/convert first, then use the resulting `content:{content_id}`.
- GitHub repository: normalized `github:{owner}/{repo}` using the latest default branch at generation time. Store resolved default branch and commit SHA in run metadata and source notes.

The latest interests prompt is stored on the deck/run for traceability, but it does not create a separate deck.

## Backend Data Model

Add stable product state plus per-attempt run state.

### `learning_decks`

Fields:

- `id`
- `user_id`
- `source_kind`: `content`, `github_repo`
- `source_identity`: normalized replacement key
- `source_url`
- `source_content_id`
- `source_title`
- `source_metadata`: JSONB for repo branch/SHA, platform, content type, etc.
- `title`
- `latest_successful_run_id`
- `latest_run_id`
- `artifact_storage_prefix`
- `deck_object_key`
- `source_notes_object_key`
- `source_notes_html_object_key`
- `share_enabled`
- `share_token_hash`
- `created_at`
- `updated_at`
- `deleted_at`

Indexes:

- unique active deck per `(user_id, source_identity)` where `deleted_at is null`
- `user_id, updated_at`
- `share_token_hash`

### `learning_deck_runs`

Fields:

- `id`
- `deck_id`
- `user_id`
- `status`: `queued`, `preparing`, `generating`, `validating`, `publishing`, `completed`, `failed`, `cancelled`
- `interests_prompt`
- `source_snapshot`: JSONB
- `timeline`: JSONB array of coarse notes
- `artifact_storage_prefix`
- `deck_object_key`
- `source_notes_object_key`
- `source_notes_html_object_key`
- `model_provider`
- `model_name`
- `sandbox_provider`: `e2b`
- `sandbox_id`
- `error_message`
- `started_at`
- `completed_at`
- `created_at`
- `updated_at`

Raw agent transcript and command logs should be stored internally outside user-facing artifact access, keyed by run id. Do not expose raw logs to normal users.

## API Design

Add a new authenticated API module:

- `GET /api/learning/decks`
  - list current user decks and latest run status
- `POST /api/learning/decks`
  - create or rerun a deck from one source
- `GET /api/learning/decks/{deck_id}`
  - deck detail, timeline, source metadata, latest success, latest run
- `DELETE /api/learning/decks/{deck_id}`
  - delete deck and object-storage artifact bundle; retain internal logs
- `POST /api/learning/decks/{deck_id}/viewer-url`
  - return short-lived signed private deck URL for app/Safari opens
- `POST /api/learning/decks/{deck_id}/source-notes-url`
  - return short-lived signed private rendered source-notes URL
- `POST /api/learning/decks/{deck_id}/share`
  - enable sharing and return stable share URL
- `DELETE /api/learning/decks/{deck_id}/share`
  - disable sharing
- `GET /learning/share/{token}/`
  - public raw deck route when sharing is enabled
- `GET /learning/share/{token}/source-notes`
  - public rendered source-notes route when sharing is enabled
- `GET /learning/signed/{token}/`
  - short-lived private raw deck route
- `GET /learning/signed/{token}/source-notes`
  - short-lived private rendered source-notes route

Routes should stream from existing object storage/media tooling. Private signed URLs should be short-lived and distinct from durable share tokens.

## Source Preparation

Existing content:

1. Verify ownership/visibility for the current user.
2. Require already-processed body/transcript/PDF text.
3. Build a source snapshot with title, URL, content type, summary metadata, and source text location.

Pasted non-GitHub URL:

1. Submit through existing Newsly ingestion.
2. If processing is incomplete, return deck/run status showing preparation is waiting on ingestion.
3. Start deck generation after content body is available.

Fast Reads:

1. Trigger existing conversion/enrichment path.
2. Use the resulting long-form content id as the source identity.

GitHub repository:

1. Accept public GitHub repository URLs only.
2. Normalize owner/repo.
3. Resolve latest default branch and commit SHA during the run.
4. Store repo metadata directly on Learning Deck rows.

## Worker And Agent Loop

Use separate Learning Deck product tables plus the database queue.

Add:

- `TaskType.GENERATE_LEARNING_DECK`
- `TaskQueue.LEARNING` so 30-minute jobs do not block content processing
- a handler under `app/pipeline/handlers/generate_learning_deck.py`

The backend workflow is intentionally coarse:

1. **Prepare source**
   - validate source, ingest/enrich when needed, assemble source snapshot
2. **Run learning agent**
   - create isolated sandbox
   - provide source snapshot, interests prompt, output contract, and quality bar
   - let the agent research, inspect, write, and revise in one bounded loop
3. **Validate artifact**
   - deterministic checks before publication
4. **Publish**
   - upload bundle through existing object storage/media tools
   - atomically promote latest successful run on the deck

The agent loop should do the real investigation and deck construction. The backend should only enforce phase boundaries, timeout, status notes, access control, and validation.

## Sandbox Provider

MVP provider: E2B Sandboxes.

Configuration:

- add `E2B_API_KEY` to settings/secrets
- add learning sandbox timeout setting, default 30 minutes
- add admin-configured learning model/provider settings

E2B is used because it matches the MVP requirements: on-demand isolated Linux sandboxes, command execution, bash/code execution, filesystem operations, and SDK auth through `E2B_API_KEY`.

MVP sandbox policy:

- full outbound network
- full bash/code execution inside the sandbox
- no Newsly service secrets passed into the sandbox
- ephemeral workspace per run
- hard wall-clock timeout: 30 minutes
- provider-level CPU/memory/resource limits configured when E2B exposes them; if a limit is not configurable, log that fact on the run and rely on the hard wall-clock timeout

Wrap this behind a small provider interface:

```python
class LearningSandbox:
    def run_command(self, command: str, *, timeout_seconds: int | None = None) -> CommandResult: ...
    def write_file(self, path: str, text: str) -> None: ...
    def read_file(self, path: str) -> str: ...
    def list_files(self, path: str) -> list[str]: ...
    def close(self) -> None: ...
```

The first implementation can be synchronous inside the queue handler if it respects the queue lease/timeout model.

## Artifact Contract

Each successful run must produce:

- `index.html`
- `source-notes.md`
- rendered source-notes HTML
- optional local assets

Reveal.js should use CDN scripts for MVP. External images and styles are allowed. Arbitrary third-party scripts are restricted; validation should allow only the expected Reveal.js CDN script sources plus local assets.

The agent writes the full `index.html`. Backend does not render from a structured slide schema in MVP.

Validation checks:

- required files exist
- `index.html` contains a Reveal-compatible slide structure
- deck size and asset count are within configured limits
- `source-notes.md` exists and has source sections
- rendered source notes pass Markdown sanitization
- external scripts are restricted
- no obvious secrets from Newsly settings are present
- no local filesystem paths from the backend host are exposed
- generated HTML passes the MVP host policy before raw serving: Reveal.js CDN scripts, local scripts from the artifact bundle, external images/styles, no inline event-handler attributes, and no arbitrary third-party scripts

Source notes should include:

- primary source metadata
- GitHub default branch and commit SHA when applicable
- important inspected files for repos
- web sources used, when any
- source-to-slide mapping for key claims
- limitations and anything the agent could not verify

Slides use hybrid citations: compact citations for key claims; source notes contain the full map.

## Artifact Hosting

Use the existing object storage/media tooling, not backend-local-only storage.

Storage shape:

```text
learning_decks/{user_id}/{deck_id}/runs/{run_id}/index.html
learning_decks/{user_id}/{deck_id}/runs/{run_id}/source-notes.md
learning_decks/{user_id}/{deck_id}/runs/{run_id}/source-notes.html
learning_decks/{user_id}/{deck_id}/runs/{run_id}/assets/...
```

Access shape:

- private app opens request short-lived signed viewer URLs
- shared opens use the stable deck share token
- shared source notes are public when sharing is enabled
- raw object storage URLs should not be exposed as the durable product URL

Promotion:

1. Upload new run artifacts to a run-specific prefix.
2. Validate object availability.
3. Set `learning_decks.latest_successful_run_id` and object keys in one DB transaction.
4. Existing share/private URLs now resolve to the new successful run.

## LLM And Usage

Use an admin-configured default learning model/provider. User-selectable model/provider is out of MVP scope.

Record model usage through existing vendor usage paths. Do not add explicit E2B/storage cost tracking in MVP beyond internal logs and infrastructure dashboards.

The learning prompt should instruct the agent to:

- start from the source
- use web research as needed, not mechanically
- prioritize teaching clarity and presentation quality
- explain architecture, construction, tradeoffs, alternatives, and implications
- produce roughly 20-30 slides
- include diagrams where useful
- write complete source notes
- avoid dense reference-dump decks

## iOS Design

Knowledge tab:

- Add a `Learning Decks` section.
- Show title, source type, latest status, last updated time, and share state.
- Show active generation timeline notes for in-progress/failed runs.
- Tapping a completed deck requests a private signed viewer URL and opens it in `SFSafariViewController`.
- Provide `Open in Browser`.
- Provide rendered source-notes open action.
- Provide share toggle.
- Provide delete.

Content detail:

- Add `Create learning deck` for eligible article/podcast/PDF/content sources.
- For already-generated source, button can show `Open learning deck` plus rerun option.
- Start generation with a small sheet containing only the freeform interests prompt.

Fast Reads:

- Add `Create learning deck` only through the conversion/enrichment path.
- UI should make clear that Newsly is preparing the full source before deck generation.

Pasted URL/GitHub:

- In the Knowledge tab Learning Decks section, add `Create deck`.
- User pastes URL and optional interests prompt.
- Backend decides GitHub repo vs normal content URL.

## Tests And Verification

Backend unit tests:

- source identity normalization for content and GitHub repos
- one active run per user guard
- create/rerun replaces by source identity
- failed rerun preserves previous latest successful run
- share token enable/disable behavior
- delete invalidates deck access and requests object deletion
- signed URL generation rejects wrong user
- public share access rejects disabled/deleted decks
- validation rejects missing `index.html`
- validation rejects missing source notes
- validation rejects arbitrary external scripts
- rendered source-notes sanitization

Backend integration tests:

- create deck from existing processed content enqueues generation
- pasted non-GitHub URL goes through content ingestion path
- GitHub URL creates deck-only external source
- Fast Read route triggers enrichment/conversion first
- queue handler promotes a successful mocked run atomically
- queue handler stores failure without replacing old artifact

iOS checks:

- Xcode build
- Knowledge tab list renders ready/generating/failed states
- content detail create action opens prompt sheet
- successful deck opens signed URL in Safari view
- share toggle updates UI
- delete removes the row

Manual smoke:

1. Create a deck from existing article content.
2. Create a deck from a public GitHub repo.
3. Create a deck from a pasted arXiv/PDF URL after ingestion.
4. Rerun an existing deck and verify share URL swaps only after success.
5. Force a failed rerun and verify old deck remains live.
6. Enable sharing and verify deck plus source notes are public.
7. Disable sharing and verify token routes stop serving.

Suggested Python checks:

```bash
uv run ruff check app/models/api app/routers/api app/services app/pipeline tests/routers tests/services tests/pipeline
uv run pytest tests/services/test_learning_decks.py tests/routers/test_learning_decks.py tests/pipeline/test_generate_learning_deck.py -v
```

Suggested iOS build:

```bash
xcodebuild -project client/newsly/newsly.xcodeproj -scheme newsly -destination 'generic/platform=iOS Simulator' -derivedDataPath /tmp/newsly-codex-derived-data CODE_SIGNING_ALLOWED=NO build
```

## Implementation Plan

- [x] Add backend data model and migration for `learning_decks` and `learning_deck_runs`.
- [x] Add API DTOs and router skeleton for deck list/detail/create/delete/share/signed URLs.
- [x] Add source identity and source preparation services.
- [x] Add object-storage artifact service for upload, delete, private signed route resolution, and public share route resolution.
- [x] Serve local artifact assets through the same private signed and public share URL families.
- [x] Add artifact validation and source-notes rendering/sanitization.
- [x] Store internal agent/tool logs outside public artifacts and retain them on deck deletion.
- [x] Add queue task type/spec/handler with mocked sandbox boundary.
- [x] Add E2B sandbox adapter and settings.
- [x] Add learning agent prompt and run orchestration.
- [x] Add backend tests for API, services, validation, queue handler, replacement, and sharing.
- [x] Add iOS models/services/endpoints for Learning Decks.
- [x] Add Knowledge tab Learning Decks section.
- [x] Show Learning Deck row source type, updated time, status, and share state.
- [x] Add content detail and Fast Reads entry points.
- [x] Add pasted URL/GitHub create flow in Knowledge.
- [x] Run focused backend checks and Xcode build.
- [x] Smoke with one article, one GitHub repo, one PDF/arXiv URL, and one failed rerun using mocked agent output and local object storage.
- [x] Split Learning Deck backend ownership into source, token, hosting, viewer, and lifecycle modules.
- [x] Make run creation and generation-task enqueue happen in one transaction with a DB-level active-run guard.
- [x] Replace substring-based presentation runtime script validation with parsed CDN/package allowlists.
- [x] Move Learning Deck list and row UI out of `KnowledgeView`.
- [x] Move content-detail Learning Deck creation side effects into a dedicated sheet component.
- [x] Use typed Learning Deck source/status enums across backend API models and Swift client models.

## Risks

- Full outbound network plus unrestricted bash is powerful. MVP relies on E2B isolation, no Newsly secrets in sandbox, timeouts, and artifact validation. Network and command restrictions can come later.
- Agent-written HTML is flexible but harder to sanitize than backend-rendered slides. Validation must be conservative before raw hosting.
- CDN-based Reveal.js is simpler but less reliable than self-contained bundles.
- Public source notes can expose more context than slide citations. Sharing should clearly expose both deck and notes.
- One active run per user avoids concurrency spikes, but long 30-minute runs can still create perceived slowness.
- Pasted URL ingestion can fail before deck generation starts; the UI needs a clear `preparing source` state.
- E2B API key must be treated as a secret and stored only in environment/config. Any key pasted into chat should be rotated before production use.

## Non-Goals For MVP

- Private GitHub repositories.
- Multi-source decks.
- Outline approval or deck editing.
- Full-text deck search.
- Native SwiftUI slide renderer.
- Self-contained offline deck export.
- User-selectable model/provider.
- Daily generation quota.
- E2B/storage cost accounting beyond existing logs/infra.
- Exposing raw agent command logs to users.
