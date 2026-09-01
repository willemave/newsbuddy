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

### 2026-09-01 — `main` — Warm-clay Buddy icon and onboarding guide

- **Status:** Complete locally.
- **Scope:** iOS app icon and mark assets, onboarding presentation, onboarding and Briefing loading states, and a debug-only visual-state screenshot seam.
- **Decisions:** Keep the approved slate-and-ink icon composition while recoloring Buddy warm clay with gold glasses; let one flow-level Buddy own onboarding continuity; expand Buddy at the welcome and dock it upper-right so it avoids the back affordance and primary controls; use Buddy instead of a generic spinner; settle motion when Reduce Motion is enabled.
- **Changes:** Replaced light, dark, and tinted app-icon variants plus app and Buddy marks; added a breathing Buddy loading indicator; applied it to onboarding preparation and initial Briefing loading; added the expanding and floating upper-right onboarding guide; added DEBUG-only backend-free visual states for deterministic screenshot capture.
- **Validation:** Built and launched the current checkout on iPhone 17 Pro Simulator running iOS 26.4; all 631 native iOS tests passed with zero failures or skips; captured and visually inspected the installed Home Screen icon, docked onboarding guide, onboarding loading state, and Briefing loading state.
- **Remaining:** Optional physical-device motion and small-icon taste check. No commit, push, deployment, or Apple distribution was requested.
- **Commits:** Uncommitted.

### 2026-09-01 — `main` — Briefing audio cleanup follow-up

- **Status:** Complete locally; physical-device Lock Screen verification remains.
- **Scope:** Scoped narration planning and compatibility contract, iOS playback/Now Playing lifecycle, chapter-row presentation, and focused tests.
- **Decisions:** Keep scope as the sole script-generation discriminator; preserve the legacy lens endpoint with a dedicated required-key request; treat each unique Article or Podcast source as exactly one chapter; resume after an interruption only when the interrupted playback session is still current.
- **Changes:** Split malformed multi-source document segments and deduplicated repeated sources; removed the redundant audio script marker; refreshed Now Playing metadata and remote actions on same-target playback; separated current and preferred playback rates; added source artwork loading with stale-request fencing and caching; removed notification and remote-command observers at owner teardown; unified seek state updates; suppressed empty chapter detail copy and accessibility punctuation. Extracted narration presentation and planning into focused API and DB sibling modules to keep the owning modules below their size guardrails.
- **Validation:** Rust formatting, module-size guardrails, warning-denied affected-crate Clippy, four document-planning tests, nine focused provider tests, all 39 API unit tests, public-contract regeneration/drift checks, plist validation, and `git diff --check` passed. The focused iOS run passed all 13 Briefing narration-controller and 4 playback-service tests after correcting remote-command target teardown to use each owning command.
- **Remaining:** Verify exact artwork, metadata, continued playback, controls, chapter transitions, interruption handling, and route loss on a locked physical device. Broader presentation-metadata deduplication and stronger DB narration-plan types were intentionally left for separate changes because they cross module boundaries without improving this feature's behavior.
- **Commits:** Uncommitted.

### 2026-09-01 — `main` — Briefing audio story chapters and iOS system playback

- **Status:** Complete locally; device Lock Screen verification remains.
- **Scope:** Briefing narration contracts, scoped repository planning, audio script generation, SwiftUI chapter presentation, AVPlayer background ownership, Now Playing, and remote commands.
- **Decisions:** Generate grounded scripts only when Listen is requested; keep one exact-title chapter per Article or Podcast document; combine all News lenses into one curated program whose planned source window remains the read-on-finish unit; retain the released lens-key request as an exactly-one compatibility field.
- **Changes:** Added scoped manifests and source-derived chapter metadata; stored versioned long-summary/key-point snapshots for worker-generated scripts; routed News and document chapters through distinct prompts; switched the iOS client from lens programs to Article/Podcast/News programs; published chapter metadata and queue state to iOS Now Playing; added system play, pause, seek, previous, next, interruption, route-loss, and background-audio support.
- **Validation:** Rust formatting and warning-denied Clippy passed across contracts, DB, providers, worker, and API. The two document-planning tests and nine Briefing/provider tests passed; API and contract unit suites passed during the affected-package run. Public OpenAPI and both Swift clients regenerated cleanly and the contract drift check passed. Both plists validate. The native iOS target built for testing and all 13 focused Briefing narration-controller tests passed, including chapter metadata and automatic advance. `git diff --check` passed.
- **Remaining:** Verify exact metadata, continued playback, controls, chapter transitions, and route loss on a locked physical device. No commit, push, deployment, or Apple distribution was requested.
- **Commits:** Uncommitted.

### 2026-09-01 — `main` — Concise News Lens composition prompt and related-source trace

- **Status:** Complete locally; not deployed.
- **Scope:** Tighten News Lens prose without a hard word/character validator, and trace whether related News sources or selected relevant links reach Briefing composition.
- **Decisions:** Keep the existing one-paragraph, three-sentence, exact-source-link contract; instruct the model to produce concise, information-dense synthesis, combine related sources instead of assigning one sentence per source, and omit low-value detail.
- **Changes:** Updated the Briefing composition system prompt only; no validation limit or fallback was added. The trace confirmed same-story members are collapsed behind a canonical representative, selected external links remain a News-detail feature, and Briefing event groups keep related representatives together but are not projected into the flat composition request.
- **Validation:** Rust formatting passed. All 7 focused `briefing_composition` provider tests passed, including the new prompt-contract regression. `git diff --check` passed before the final log update.
- **Remaining:** A separate follow-up may project each planned event-group identity into the composition request so the model can deliberately synthesize related representatives; surfacing cluster members as independent citations would require a larger source-ownership/read-state design and is not part of this prompt-only change.
- **Commits:** Uncommitted.

### 2026-09-01 — `main` — Route onboarding through GPT-5.6 Luna Priority

- **Status:** Complete locally; not pushed or deployed.
- **Scope:** Rust onboarding provider routing, low-reasoning priority request settings, audio-lane discovery prompt, and focused regression coverage.
- **Decisions:** Apply the selected Luna route consistently to every onboarding LLM stage; preserve the existing structured contracts, timeouts, deterministic fallback, and grounded final-selection behavior. Use the evaluated source-diversity prompt for audio planning.
- **Changes:** Replaced the pinned DeepSeek/Wafer model with `openai:gpt-5.6-luna`; added `reasoning.effort=low`, `service_tier=priority`, and `store=false`; removed the obsolete onboarding-specific Wafer pin; moved static model/prompt settings into a small sibling module to preserve the file-size ratchet; and added prompt/routing plus opt-in live-canary coverage.
- **Validation:** All 43 non-live provider tests passed with one live canary ignored by default; warning-denied provider Clippy, Rust formatting, the 696-file module-size guard, stale onboarding DeepSeek/Wafer searches, and `git diff --check` passed. The integrated `main` live Luna Priority structured-output canary passed without deterministic fallback in 3.32 seconds.
- **Remaining:** Deployment is intentionally out of scope until explicitly requested.
- **Commits:** This commit.

### 2026-08-31 — `main` — Install architecture guard tools in Rust CI

- **Status:** Complete locally; queued for the replacement release.
- **Scope:** `.github/workflows/quality-gate.yml`.
- **Decisions:** Declare `ripgrep` as a Rust quality-job dependency because the architecture and iOS wire-boundary guards invoke `rg`, while GitHub's Ubuntu runner image does not guarantee it.
- **Changes:** Install the distribution-provided `ripgrep` package before migrations and architecture validation.
- **Validation:** The pushed quality run reached all four real jobs; both Python islands and native iOS passed, while Rust failed only because `rg` was absent. The complete local release gates are rerun on this replacement commit before push.
- **Remaining:** None.
- **Commits:** This commit.

### 2026-08-31 — `main` — Make the Rust quality workflow compile on GitHub

- **Status:** Complete locally; queued for the replacement release.
- **Scope:** `.github/workflows/quality-gate.yml`.
- **Decisions:** Resolve the build SHA directly from the allowed `inputs` and `github` contexts at job-environment evaluation time instead of referencing the workflow-level `env` context, which GitHub does not expose there.
- **Changes:** Replaced the invalid `env.TESTED_SHA` expression for `NEWSLY_BUILD_SHA` with `inputs.sha || github.sha` while preserving exact-SHA behavior for both reusable and manually dispatched runs.
- **Validation:** Pinned `actionlint` 1.7.7 reproduces the zero-job GitHub failure on the pushed SHA and passes the corrected workflow; the complete release gates are rerun before the replacement push.
- **Remaining:** None.
- **Commits:** This commit.

### 2026-08-31 — `main` — Keep Crawl4AI from reloading application secrets

- **Status:** Complete locally; queued for the current release.
- **Scope:** The isolated document extractor's Crawl4AI adapter and focused tests.
- **Decisions:** Disable python-dotenv before importing Crawl4AI because the third-party package otherwise searches parent directories and repopulates the retired application `.env`, violating the extractor's database-free process contract after the launcher has explicitly removed database variables.
- **Changes:** Set `PYTHON_DOTENV_DISABLED=1` at the adapter boundary before the first Crawl4AI import and added a regression assertion for that process invariant.
- **Validation:** The failure was reproduced from both the repository root and a neutral working directory; focused and full extractor validation are part of the current release rerun.
- **Remaining:** None.
- **Commits:** This commit.

### 2026-08-31 — `main` — Compare paired day and night reading grounds

- **Status:** Complete locally; queued for the current release.
- **Scope:** `docs/brand-exploration-2026-08/day-mode-options.html`.
- **Decisions:** Keep the exploration self-contained and compare the actual Briefing and Reader structures across six paired light/dark palettes. Separate ink contrast, card-to-ground lift, and ground lightness so the design choice is based on distinct readability variables rather than a general impression of greyness.
- **Changes:** Added a responsive browser comparison with palette switching, contrast calculations, representative device frames, and notes describing the purpose and tradeoff of each pair.
- **Validation:** Reviewed the complete HTML, CSS, JavaScript, internal link, and local image references; `git diff --check` passes.
- **Remaining:** None.
- **Commits:** This commit.

### 2026-08-31 — `main` — Left-align detail action icons and enlarge the hero

- **Status:** Complete locally; queued for the current release.
- **Scope:** `DetailActionBar.swift`, `DetailHeroHeader.swift`, `DetailDesign` constants in `ContentDetailPresentationModels.swift`.
- **Decisions:** Pack the action icons from the leading edge instead of distributing them across the full width, so the row reads as a toolbar rather than a stretched strip. Apply the existing `actionIconOpticalInset` in both header variants so the first glyph lines up with the title margin; previously only the text-only header did this. Raise the hero from 260pt to 320pt and move `topEdgeFade` bounds with it, since those offsets are tuned to the hero height.
- **Changes:** Dropped `.frame(maxWidth: .infinity)` from `detailActionBarSegment()`; the surrounding `HStack` already left-aligns via its own `maxWidth: .infinity, alignment: .leading`. Added a negative leading inset to the image-hero action bar and switched the text-only bar from `.horizontal` to `.leading` padding, since the trailing inset is meaningless once icons pack left.
- **Validation:** Built and ran on iPhone 17 Pro (iOS 26.5). Checked the image hero on two saved articles and the text-only hero on a news item; icons align with the title margin in both, and the taller hero also drops the title clear of the floating back button, which previously overlapped it. Grepped for other users of the changed constants — none outside these files.
- **Remaining:** None.
- **Commits:** This commit.

### 2026-08-31 — `codex/rust-backend-migration` — Integrate the Rust migration with current main

- **Status:** Complete locally; not pushed, deployed, or distributed.
- **Scope:** Merge the complete Rust backend and CLI migration with the six newer commits on `main`, including the Slate brand, onboarding, and cold-session restoration changes.
- **Decisions:** Keep Rust as the sole application backend and schema owner; retain the newer `main` iOS and brand implementation; preserve the Rust OpenRouter privacy policy instead of restoring deleted Python onboarding routing; and keep the primary checkout's unrelated uncommitted changes outside this merge.
- **Changes:** Resolved the migration against current `main`, retained explicit iOS dependency composition plus the generated typed server-error case, and reconciled generated asset tracking and architecture history.
- **Validation:** `git diff --check`; architecture guard (module limits, iOS wire boundary, Rust-owned public contract drift); focused native iOS authentication and palette suite, 16 passed and 0 failed.
- **Remaining:** None for local integration. Full Rust, Python-island, native iOS, and local AXe/backend E2E evidence remains recorded on the migration commit; no push, deploy, or Apple distribution was requested.
- **Commits:** Migration commit `2e945899`; integration merge recorded by Git history.

### 2026-08-31 — detached `c77aa869` — Make Share Add Feed subscription results truthful

- **Status:** Complete locally; live canary pending; not committed, pushed, deployed, or distributed.
- **Scope:** Rust Share Action Add Feed validation/finalization, scraper-config persistence, initial feed backfill, and terminal content resubmission semantics.
- **Decisions:** Treat the model-selected URL as a candidate only; preserve the host parser's actual RSS-versus-Atom result; perform all E2B probing before finalization; and mark an action applied only when its active config and any required backfill are durable in the same fenced transaction.
- **Changes:** Added a typed feed-validation result while preserving the URL-only API, converted Add Feed to direct validated scraper subscription instead of indirect Content analysis, made created/reactivated/already-existing outcomes explicit, and allowed an explicit feed-subscription mutation to finalize against an older terminal Content row.
- **Validation:** Focused workspace check passed; two Share workflow tests, the RSS subscription PostgreSQL integration test, two feed-validator tests, and the terminal-row finalizer guard test passed. Formatting and warning-denied Clippy passed for `newsly-worker`, `newsly-db`, and `newsly-e2b`. Tests made no live provider calls.
- **Remaining:** Restart the local Rust worker and run one authenticated Share Add Feed canary, then verify the action result, active config, and backfill task against the local database.
- **Commits:** Uncommitted.

### 2026-08-31 — detached `c77aa869` — Correct feed subscription ownership after review

- **Status:** Complete locally; not committed, pushed, deployed, or distributed.
- **Scope:** Share Add Feed, feed-valued Add to Briefing, Chat subscribe, E2B feed classification, and terminal Content finalization.
- **Decisions:** Keep terminal Content immutable; make the direct host-validated scraper repository the only agent-initiated subscription path; derive podcast classification from parsed audio entries and Substack classification from the effective host; and never report `subscribed: true` for mere Content-analysis queue acceptance.
- **Changes:** Removed the terminal-Content subscription exemption recorded in the preceding entry, moved Add to Briefing feeds and Chat onto validation-before-transaction plus atomic config/backfill persistence, removed the indirect Share feed-submission helper, and omitted E2B's inapplicable `autoPauseMemory=false` create field.
- **Validation:** All 30 `newsly-e2b` tests passed, along with four Share workflow tests, five Content finalizer tests, and the PostgreSQL direct-subscription regression covering created, reactivated, already-existing, and Add to Briefing outcomes. Live task `41` validated `https://this-week-in-rust.org/rss.xml`, completed Share Add Feed, committed active Atom config `1`, and atomically queued backfill task `1118`. The earlier homepage canary passed sandbox creation but reached the agent deadline; the direct-feed retry proved the canonical persistence path. Formatting, all-target compilation, and warning-denied Clippy passed.
- **Remaining:** Production compatibility telemetry and the exact-SHA release/cutover gates remain; no local subscription-path work remains.
- **Commits:** Uncommitted.

### 2026-08-31 — detached `c77aa869` — Complete the local Rust migration and product E2E gate

- **Status:** Complete locally; not committed, pushed, deployed, or distributed.
- **Scope:** Consolidated Rust runtime and CLI, SQLx migrations, Crawl4AI extraction boundary, ingestion and model workers, direct E2B, Share Actions, chat, Learning Decks, stored audio, public contracts, and the dedicated iOS Simulator product surface.
- **Decisions:** Retain Python only for the database-free Crawl4AI extractor and offline eval pipelines; keep exact reviewed bodyless exceptions only for audio-stream `416` responses; give Learning Deck initial and repair runs no application-level request or output-token ceiling while retaining deadlines, tool/file bounds, artifact validation, and browser validation; and keep the disposable queue's historical failed canaries visible rather than claiming a global drain.
- **Changes:** Finished the Rust/Clap `newsbuddy` and Rust operator CLIs, direct ConnectRPC E2B layer, SQLx migration authority, generated Rust OpenAPI/Swift boundaries, canonical script launcher, fail-closed feed subscription ownership, and behavior-preserving worker module splits below the ratchets. Removed the general Python backend, Alembic, Go CLI, and backend Python test trees.
- **Validation:** The locked PostgreSQL-enabled Rust workspace passed 360 tests across 17 packages with zero failures; one opt-in live E2B smoke and six generated ConnectRPC doctests were ignored. Workspace warning-denied Clippy, offline SQLx compilation, formatting, the 695-file architecture guard with 32 ratchets, public-contract drift, and diff checks passed. The retained Python islands passed 39 tests plus Ruff, MyPy, boundary, and package-build checks. Native iOS passed 629 unit tests and the three UI tests. Live local evidence includes five scrape runs, 123 post-fix enrichment completions, 15 extraction-summary-image chains, 97 ready News items, two-turn tool-backed chat, all four Share API modes, direct Add Feed config/backfill persistence, a Learning Deck run recording 9,442 output tokens and a signed browser-validated viewer, and authenticated ranged-audio playback. AXe produced 55 valid captures across the app, including chat, Share-driven additions, Learning Deck, and audio, against the current checkout and disposable Rust backend.
- **Evidence:** `/tmp/newsly-final-rust-tests.log` and `/tmp/newsly-rust-e2e-axe-final/` (notably `17-chat-two-turn.png`, `38-learning-deck-viewer-live.png`, `49-audio-range-playing.png`, `50-audio-range-finished.png`, `63-share-add-feed-direct-completed.json`, `64-share-add-feed-db-proof.txt`, `65-final-runtime-counts.txt`, and `66-learning-deck-over-4k-proof.txt`).
- **Remaining:** A Docker-capable host must build the production image and rehearse existing-database SQLx adoption before any exact-SHA deployment. Production drain/adoption, compatibility telemetry, live health/queue/cost proof, commit/push, and Apple distribution were not performed. The disposable E2B sandbox, snapshot, and template were deleted, the local Rust API was stopped, and the disposable PostgreSQL data directory was stopped and moved to Trash for recoverability; the `/tmp` evidence directory was retained.
- **Commits:** Uncommitted.

### 2026-08-31 — detached `c77aa869` — Repair Rust Knowledge and recent-content timestamp projections

- **Status:** Complete locally; not committed, pushed, deployed, or distributed.
- **Scope:** SQLx content-feed projections and cursor ordering for Knowledge and recently read lists.
- **Decisions:** Decode PostgreSQL `timestamptz` Knowledge-save values as typed UTC timestamps, and normalize `timestamptz` sort keys to UTC `timestamp` values at the SQL boundary so the existing cursor contract remains unchanged.
- **Changes:** Fixed the saved/read timestamp type mismatch that made any nonempty Knowledge list fail during SQLx row decoding; applied the same correction to the latent recently-read failure; and added a PostgreSQL regression covering both projections and their exact UTC values.
- **Validation:** Reproduced the 500 with one saved row in a disposable migrated database and isolated API, where SQLx reported `TIMESTAMPTZ` was incompatible with `NaiveDateTime`. The focused regression passes, scoped warning-denied Clippy and formatting pass, and post-fix HTTP canaries returned one valid item from both Knowledge and recently read. The isolated API was stopped and its disposable database dropped.
- **Remaining:** Full cross-stack gates remain with the parent E2E task. Full all-target Clippy was temporarily blocked by an unrelated concurrent doc-markdown warning in `newsly-agent-runtime/src/transcript.rs`.
- **Commits:** Uncommitted.

### 2026-08-31 — detached `c77aa869` — Consolidate scripts around the Rust runtime

- **Status:** Complete locally; not committed, pushed, deployed, or distributed.
- **Scope:** Local runtime launch/supervision, SQLx environment loading, iOS/AXe local-origin selection, container dispatch, and the repository script inventory.
- **Decisions:** Keep one canonical native launcher, retain Python only for the direct Crawl4AI child and offline eval pipelines, preserve narrow operations/release helpers, and force bounded database pools only when the explicit local-E2E profile is selected.
- **Changes:** Shared dotenv/database/origin parsing in `scripts/lib/rust_runtime.sh`; made `dev.sh` forward one canonical `start_services.sh` invocation; added `--local-e2e`; replaced the extractor's `uv run` wrapper with a frozen-sync direct child; made child exit and signal handling terminate and reap peers exactly once; let iOS and AXe helpers target an explicit API origin; inlined API/scheduler Docker dispatch; and removed the unused duplicate client contract wrapper, Docker wrappers, and Claude documentation helper. The architecture guard now rejects retired Python/Go launchers and duplicate entrypoints.
- **Validation:** Bash syntax passed for every active shell entrypoint; focused help, invalid-argument, URL parsing, dotenv normalization, retired-launcher, and script-reference checks passed. Runtime startup was intentionally left to the parent E2E lane to avoid disturbing its active disposable database, extractor, API, and worker canaries.
- **Remaining:** Complete-stack runtime and AXe product validation continue in the parent E2E task; no production or release action was performed.
- **Commits:** Uncommitted.

### 2026-08-31 — detached `c77aa869` — Cut the user CLI over from Go to Rust

- **Status:** Complete locally; not committed, pushed, packaged in Homebrew, or deployed.
- **Scope:** `newsly-cli`, its command/transport/config/output/polling/library tests, shared request contracts, client codegen, quality gates, repository hooks, and active CLI documentation.
- **Decisions:** Preserve the `newsbuddy` binary, config paths and environment aliases, commands, output envelopes, QR login, and local-library manifests; keep `newsbuddy` separate from the operator-only `newsly-admin`; consume `newsly-contracts` directly instead of generating a second CLI model tree; and retire the checked CLI-specific OpenAPI copy while retaining the internal language-neutral agent operation-inventory test.
- **Changes:** Added the Rust/Clap HTTP client with all 25 leaf commands, stable JSON/text envelopes, unauthenticated QR linking, API-key auth, compound duration parsing, shell completion, forward-compatible success decoding, typed errors, and atomic safe library sync. Removed the complete Go module, generated Go contracts and emitter, filtered checked CLI OpenAPI, Go CI/hook/settings surfaces, and obsolete artifact scripts. Split dispatcher tests out of the production module to remain below the 1,000-line guardrail and removed dead porting helpers. Documented source installation and the external Homebrew packaging boundary.
- **Validation:** All 35 focused Rust CLI tests pass, replacing 40 Go tests without generated-model duplication. Warning-denied workspace Clippy, all 321 Rust workspace tests, locked offline all-target compilation, the architecture and 683-file module-size guards, public-contract drift, shell syntax, `newsbuddy version` and completion smokes, stale-Go searches, and `git diff --check` pass. Six generated ConnectRPC doctests remain intentionally ignored.
- **Remaining:** Update and publish the external `willemave/newsbuddy` Homebrew formula if Homebrew distribution is desired. No repository commit, push, package publication, or deployment occurred.
- **Commits:** Uncommitted.

### 2026-08-31 — detached `c77aa869` — Finish the authenticated iOS composition root

- **Status:** Complete locally; not committed, pushed, deployed, or distributed.
- **Scope:** `AppRuntime`, `AuthenticatedSession`, the root construction graph, authenticated app/root/settings routes, chat notification routing, and focused lifecycle/composition tests.
- **Decisions:** Resolve live process services once in `newslyApp`; require an explicit runtime graph; make every authenticated store, coordinator, and poller an explicit session input; keep route models view-owned; and bind local-notification chat routing to the active account lifetime.
- **Changes:** Replaced the static root factory with an instance-bound graph, removed runtime/session fallback construction and chat/navigation singletons, routed settings, onboarding, Briefing, X, CLI, assistant-feed actions, toast presentation, simulator URL normalization, and route-model dependencies through the graph, and clear notification routing during session detach.
- **Validation:** The Debug iOS Simulator product build and complete native `build-for-testing` pass on iPhone 17 Pro, iOS 26.5. All 39 focused lifecycle, authentication restoration, chat navigation, active-session, assistant-feed, and Learning Deck chat tests pass serially with zero skips; static singleton/factory searches and scoped whitespace checks pass.
- **Remaining:** None in implementation scope. The parent migration still owns the complete serial native and repository gates.
- **Commits:** Uncommitted.

### 2026-08-31 — detached `c77aa869` — Finish the Rust backend migration candidate

- **Status:** Complete locally; not committed, pushed, deployed, cut over, or distributed.
- **Scope:** Final backend/client integration, generated network boundaries, Rust module cleanup, retained Python-island enforcement, and the consolidated local migration gate.
- **Decisions:** Keep Rust as the only application, PostgreSQL, queue, provider, agent, E2B, migration, and operator authority; keep Python only for the authenticated database-free Crawl4AI extractor and offline eval package; preserve lenient decoding only in local/cache domain snapshots while requiring generated models at every HTTP boundary; and leave public-route plus `learning_deck_runs` retirement behind their production telemetry and backfill gates.
- **Changes:** Closed the remaining Swift and Go handwritten wire shapes, added resource-specific News adapters and single-owner search cancellation, made the iOS wire guard fail closed, converted Go errors to the canonical typed envelope, split five migration hotspots into cohesive Rust modules below 1,000 lines, and removed their obsolete module-size exemptions. Finished the authenticated iOS composition root as one instance-bound graph with explicit process, account, and route dependencies.
- **Validation:** Rust formatting and warning-denied workspace Clippy pass. The full PostgreSQL-enabled Rust workspace passes 288 tests with six generated ConnectRPC doctests intentionally ignored; locked offline compilation, SQLx prepare metadata, and a fresh throwaway-database migration pass. A Rust API smoke against the migrated throwaway database returned healthy `/health`, `/health/live`, and `/health/ready` responses, after which the process stopped and the database was dropped. Rust public-contract drift, Go tests/vet, both Compose manifests, every shell entrypoint, module-size and wire guards, and OpenAPI parity pass; parity differs from the 146-operation Python baseline only by the intentional initial-suggestions deletion and new liveness/readiness operations. The retained Python islands pass Ruff, formatting, MyPy, and 39 tests total (4 eval and 35 extractor). The complete serial native run passes all 632 tests (629 unit and 3 UI) with zero failures or skips; its result bundle is `/tmp/newsly-rust-migration-final-20260831-v2.xcresult`. Docker image builds remain unavailable because no Docker daemon is running.
- **Remaining:** Live provider/E2B/extractor/media/object-storage canaries, existing-production-database SQLx adoption, image build, exact-SHA release workflow, deployment, and Apple distribution remain separate authorized release work.
- **Commits:** Uncommitted.

### 2026-08-31 — detached `c77aa869` — Harden the retained Python islands

- **Status:** Complete locally; not committed, pushed, or deployed.
- **Scope:** `python/evals`, the database-free document extractor, Python authority guardrails, and their quality-gate documentation.
- **Decisions:** Treat `python/evals` and `python/document_extractor` as the only Newsly Python package roots; retain one exact docs-only exemption for the historical brand asset generators; and remove the Python extractor HTTP client because Rust is now the only caller and its sole remaining Python consumer was its own test.
- **Changes:** Added a locked MyPy development dependency and package configuration for eval source, scripts, and tests; made MyPy part of the attested eval CI job; fixed the duplicate-summary collection's ambiguous inferred type; made the architecture guard reject missing or additional Python islands and every Python source outside the two roots; removed the obsolete coexistence client and its six tests; and documented the local commands and design-tool exception.
- **Validation:** Both database variables were unset for runtime checks. Evals pass Ruff, formatting, MyPy across 14 files, 4 tests, dependency-boundary inspection, and wheel/sdist builds. The extractor passes Ruff, formatting, MyPy across 8 source files, 35 tests, dependency-boundary inspection, and wheel/sdist builds. The lock is frozen-current; Bash syntax, the 27-file island/12-file docs-exemption inventory with zero unexpected Python files, Python stale-client reference search, scoped whitespace checks, and focused cleanup review pass. The complete repository architecture guard also passes, including 649 module-size checks and public-contract drift.
- **Remaining:** None for the Python-island scope. The full cross-stack release gates remain part of the parent migration's final consolidated validation.
- **Commits:** Uncommitted.

### 2026-08-31 — detached `c77aa869` — Canonical News client mapping and single-owner search cancellation

- **Status:** Complete locally; not committed, pushed, or deployed.
- **Scope:** iOS generated News response mapping plus local-search task ownership and cancellation fencing.
- **Decisions:** Decode the resource-specific `APINewsItemDetailResponse` and `APINewsItemListResponse` contracts before mapping into existing client domain models. Give `SearchViewModel` sole ownership of local debounce, retry, cancellation, and stale-result fencing; the view only forwards query changes.
- **Changes:** Added explicit News detail, summary, and list adapters that leave Content-only fields empty; added generated-wire mapping tests; replaced the view-owned search task with `TaskBag` cancel-and-replace ownership for local and mixed search; and added cancellation-ignoring race tests for replacement, retry, and short-query invalidation.
- **Validation:** The two focused News mapping tests and three focused search ownership tests pass under `xcodebuild`; the scoped Swift files pass whitespace checks. Unrelated concurrently changing test sources were excluded from these focused runs.
- **Remaining:** Run the complete native iOS suite after the concurrent contract and onboarding fixture changes settle.
- **Commits:** Uncommitted.

### 2026-08-31 — detached `c77aa869` — Restore canonical E2B template publication

- **Status:** Complete locally; not published or otherwise called against E2B.
- **Scope:** `e2b.Dockerfile`, the Rust VM bootstrap input boundary, repository tooling, and the manual GitHub publication workflow.
- **Decisions:** Keep `newsly-agent` as the single runtime alias; use the exact-pinned official E2B CLI only as build-time operator tooling; require a full tested current-main SHA and clean template inputs; and keep local check/dry-run modes network-free.
- **Changes:** Added a shell validator/publisher and a production-environment manual workflow. Successful publication resolves the alias through E2B's JSON listing and records its template ID, source SHA, Dockerfile digest, CLI version, and image metadata as an artifact.
- **Validation:** Local `--help`, `--check`, and `--dry-run` pass; Bash syntax, workflow YAML parsing, required Dockerfile/helper checks, Rust workspace metadata validation, and scoped whitespace checks pass. The publication command is pinned to `@e2b/cli@2.18.0` and `e2b@2.46.1` with registry-integrity checks; no credentialed or live E2B call ran.
- **Remaining:** Configure the workflow's project-scoped E2B API-key secret and run it for the exact release SHA before production relies on the rebuilt helper image.
- **Commits:** Uncommitted.

### 2026-08-31 — detached `c77aa869` — Reconcile final Rust migration evidence

- **Status:** Local implementation and consolidated local gates complete; not committed, pushed, deployed, or cut over.
- **Scope:** Architecture, migration implementation/summary records, active development/operations guidance, Python runtime boundary, and final local validation snapshot.
- **Decisions:** Distinguish Newsly-owned Python from the pinned third-party Python-based `yt-dlp` executable used as a Rust-controlled subprocess; preserve `learning_deck_runs` retirement as a production-evidence-gated follow-up; and keep local validation, image availability, live canaries, production SQLx adoption, and deployment as separate facts.
- **Changes:** Added `newsly-contract-codegen` to the workspace inventory, recorded the application-image media-tool exception, aligned active Rust check commands, and replaced obsolete consolidated-gate placeholders with the completed local evidence and explicit release gaps.
- **Validation:** Warning-denied workspace Clippy and the full locked/offline Rust workspace tests pass. Fresh-database SQLx migration and prepare checks pass. The Python islands pass 45 tests plus Ruff, formatting, MyPy, database-boundary, and package-build checks; public-contract drift and Go test/vet pass; native iOS passes 624 unit and three UI tests; both Compose configurations and shell syntax pass. Docker image builds were unavailable because no Docker daemon is running.
- **Remaining:** Run live provider/E2B and production-shaped integration canaries; rehearse existing-database SQLx adoption/interruption; build the production images; then perform an exact-SHA quality/deployment workflow, writer drain, production adoption, authority promotion, and post-deploy health proof if separately authorized. Retire `learning_deck_runs` only after production counts, backfill verification, and an explicit schema migration.
- **Commits:** Uncommitted.

### 2026-08-31 — detached `c77aa869` — Make Rust task contracts authoritative

- **Status:** Complete locally; not committed, deployed, or promoted.
- **Scope:** The 25-type Rust queue enum/specification, task catalog, ownership manifest task entries, task payload-schema hashes, and fixture index.
- **Decisions:** `TaskType::ALL` is the exhaustive inventory and `TaskSpec` is the canonical handler, queue, deduplication, and user-ownership policy. Both checked-in manifests name the concrete Rust handler type; the retired Python context-LLM flag has no replacement because provider dependencies belong to each Rust executor.
- **Changes:** Aligned the catalog and ownership manifest, refreshed the catalog hash, and added a strict Rust corpus test that rejects unknown fields, missing/duplicate/extra task types, orphaned task payload schemas, stale schema or fixture hashes, non-Rust live ownership, and any policy divergence from `TaskSpec`.
- **Validation:** All six `newsly-queue` unit tests and the task-corpus integration test pass; the package is rustfmt-clean, and JSON/hash/static integrity checks plus scoped `git diff --check` pass. Warning-denied focused Clippy is pending the concurrent contract-codegen crate's `Cargo.lock` update; the combined migration gate remains deferred to the requested end-of-work validation.
- **Remaining:** Rerun warning-denied Clippy after the shared workspace lock settles, then include this corpus in the consolidated migration gate.
- **Commits:** Uncommitted.

### 2026-08-30 — detached `c77aa869` — Port generated-image work to Rust

- **Status:** Implemented locally behind Python-default task ownership; not committed, deployed, or cut over.
- **Scope:** `generate_image` provider, prompt, filesystem, queue handler/finalizer, process configuration, executable, container wiring, and processing laws.
- **Decisions:** Preserve Runware-first generation with two inline attempts per model and configured Google fallback; keep news artwork disabled; normalize bounded provider images and their 200-pixel thumbnails outside PostgreSQL; and require the exact queue lease to revalidate the current summary fingerprint and content lifecycle before canonical file publication, metadata, status, and usage are committed. Existing durable artwork is reused unless an operator explicitly enqueues `force=true`.
- **Changes:** Added native Runware and Gemini REST gateways, structured retry/fallback errors and usage, current long-form prompt projection, bounded PNG normalization, attempt-scoped staging and cleanup, source-change fencing, deterministic image metadata, vendor cost attribution, a standalone `newsly-image-worker`, and Docker/Supervisor coexistence wiring.
- **Validation:** `cargo fmt --all` and focused provider/worker compile checks pass; focused unit tests and warning-denied Clippy remain to be run after the shared wiring settles. Broad PostgreSQL/provider/product gates remain deferred to the consolidated migration validation requested by the user.
- **Remaining:** Exercise the handler against the isolated PostgreSQL queue harness and live provider canaries before promoting `generate_image` ownership from Python to Rust.
- **Commits:** Uncommitted.

### 2026-08-30 — detached `c77aa869` — Port the Briefing HTTP surface to Rust

- **Status:** Implemented locally behind Python-default route ownership; not committed, deployed, or cut over.
- **Scope:** All ten authenticated Briefing operations: conditional index reads, Lens pagination, source and Lens read marks, refresh enqueueing, Dig search/summarization, legacy single-episode narration, chaptered narration creation, and narration status.
- **Decisions:** Preserve the user-private ETag and immutable cursor contracts, resolve historical sources only through the user's visibility rules, retire a segment only when its full source batch is read, and keep every database mutation behind the runtime write fence. Refresh and audio generation enqueue through `QueueKernel` in the owning transaction. Dig uses native Exa and Rig provider boundaries with a short ownership check before external work and a fresh fenced usage transaction afterward, so no PostgreSQL transaction spans a provider call.
- **Changes:** Added generated-wire-compatible Briefing/audio contracts, SQLx projections and mutations, native provider gateways, Axum handlers with typed errors and truthful `304`, router/OpenAPI registration, and provider/usage presentation parity.
- **Validation:** `cargo fmt --all`, `cargo check --locked -p newsly-db -p newsly-api`, and `cargo check --locked -p newsly-api --all-targets` pass. The exported Rust OpenAPI contains all ten canonical Briefing operation IDs, the typed `304`, and the request/schema bounds. Broad PostgreSQL, HTTP parity, provider, and product suites remain deferred to the consolidated migration gate as requested.
- **Remaining:** Exercise all ten operations against the isolated PostgreSQL/HTTP parity harness before route promotion. `briefing_refresh` and `generate_audio_episode` remain on their independently Python-default task owners until those worker handlers are promoted.
- **Commits:** Uncommitted.

### 2026-08-30 — `main` — Isolate the retained Python document extractor

- **Status:** Implemented locally; not committed or deployed.
- **Scope:** Versioned Crawl4AI/static/PubMed extraction service, legacy Python coexistence adapter, typed Rust client, migration corpus, and local container wiring.
- **Decisions:** Keep Python authoritative only for bounded document-extraction policy. Refuse database, queue, Newsly JWT, persistence, arbitrary crawler options, and Firecrawl credentials in that process; Rust validates URLs and owns durable workflow/usage state and paid fallback calls.
- **Changes:** Added `python/document_extractor`, public-network validation on initial URLs, redirects, and browser requests, warm single-flight Crawl4AI ownership, discriminated request/results, a size/deadline-bounded Rust client, optional legacy strategy routing, contract schemas/goldens, isolated local/production container wiring with exact-SHA image deployment and mandatory service authentication, and a DB-unset extractor job in the attested quality gate.
- **Validation:** 41 isolated service tests and two legacy-adapter tests pass; targeted Ruff, format, and MyPy pass; Python sdist and wheel builds pass; the Rust client passes format, check, warning-denied Clippy, and all eight crate tests; migration-contract generation/validation, both Compose manifests, workflow YAML, lockfiles, and deploy-script syntax pass. Broad product suites remain deferred to the consolidated gate.
- **Remaining:** Build the Chromium image where a Docker daemon is available, then promote Rust content-task ownership only after shadow comparison covers publisher cleanup, access gates, feed links, PubMed delegation, fallback decisions, and browser recycle/timeouts.
- **Commits:** Uncommitted.

### 2026-08-30 — `main` — Make Codex worktree npm setup reproducible

- **Status:** Complete locally; not committed.
- **Scope:** Codex worktree bootstrap dependency installation.
- **Decisions:** Keep `npm ci` as the deterministic setup command and track the existing valid npm lockfile instead of weakening setup to `npm install`.
- **Changes:** Removed the `package-lock.json` ignore rule so fresh worktrees receive the dependency lockfile required by `npm ci`.
- **Validation:** The current lockfile passes `npm ci --dry-run --ignore-scripts`; Git now exposes it as a repository addition, and the npm setup failure was reproduced as missing-lockfile `EUSAGE` in fresh Codex worktrees.
- **Remaining:** Commit the `.gitignore`, `package-lock.json`, and log changes before expecting newly created worktrees to contain the fix.
- **Commits:** Uncommitted.

### 2026-08-30 — `main` — Remove iOS 26 deprecation warnings

- **Status:** Complete locally; not committed or distributed.
- **Scope:** Apple and X authentication presentation anchors, styled SwiftUI text composition, and the selectable `DigDeeperTextView` implementation.
- **Decisions:** Resolve authentication anchors from the foreground connected scene and use `UIWindow(windowScene:)` only when that scene has no existing window. Preserve mixed text styling through SwiftUI `Text` interpolation. Remove the unused adaptive-color callback rather than replacing its deprecated trait observer with new lifecycle machinery; active wrappers already refresh rendered colors from SwiftUI environment changes.
- **Changes:** Replaced detached `ASPresentationAnchor()` fallbacks, migrated the two deprecated `Text + Text` expressions, and removed the unreferenced `adaptiveTextColor` path and its `traitCollectionDidChange` override.
- **Validation:** The app builds successfully on iPhone 17 Pro, iOS 26.5, with none of the reported deprecation warnings; the only remaining build-log warning is Xcode's App Intents metadata skip for a target without an AppIntents dependency. All 29 focused authentication, X integration, and Briefing figure/text-view tests pass serially. `git diff --check` passes.
- **Remaining:** None for this warning cleanup. No commit, push, or iOS distribution was authorized.
- **Commits:** Uncommitted.

### 2026-08-29 — `main` — Close lifecycle and credential review findings

- **Status:** Complete locally; not committed, pushed, distributed, or deployed.
- **Scope:** iOS lifecycle restoration, Content Detail reader resumption, credential publication/logout semantics, shared onboarding model routing, and brand exploration asset tracking.
- **Decisions:** Make recoverable authentication failure an explicit activation-consumed obligation; restart a still-presented reader body without remounting its cover; distinguish stale terminal events from deletion failures with a typed result; and journal a complete credential target plus baseline before changing any split Keychain leg so recovery never has to guess authority. Keep unjournaled divergence fail-closed. Reuse pydantic-ai's typed OpenRouter settings in one production/eval helper. Expose only the R2/R3 generated brand image folders through scoped ignore exceptions.
- **Changes:** Wired `AppRuntime` activation to authentication retry, resumed cancelled reader-body work, added crash-recoverable credential publication, made stale terminal cleanup silent, consolidated onboarding route settings, and added focused regression coverage for the state-machine transitions and every credential write boundary.
- **Validation:** All 625 native iOS tests pass on the Newsly Regression iOS 26.5 Simulator, including 56 focused authentication, credential-session, and Content Detail cases. All 34 focused onboarding/client architecture tests pass. Ruff, mypy, onboarding production/eval route tests, the module-size guard, current brand script lint/type/format checks, and `git diff --check` pass.
- **Remaining:** Physical-device app/Share Extension publication-interruption testing remains appropriate for release acceptance. No commit, push, deployment, production mutation, or iOS distribution was authorized.
- **Commits:** Uncommitted.

### 2026-08-29 — `main` — Preserve authenticated UI coverage in unsigned release builds

- **Status:** Complete locally and committed; release validation in progress.
- **Scope:** DEBUG E2E credential persistence used by the native iOS release gate.
- **Evidence:** The required `CODE_SIGNING_ALLOWED=NO` XCTest run passed 617 tests but both authenticated lifecycle UI tests reached the landing screen. Simulator logs showed every Keychain write and delete failing with `-34018` because the unsigned app has no Keychain entitlement. The first complete Maestro rerun then passed 38 of 42 selected tests; all four failures were the parameterized Share Extension matrix seeing an authenticated Briefing instead of the expected landing screen. Exact reproduction showed that Simulator retained both App Group credentials and the app-domain `newslyE2EAutoLogin`, host, port, and user overrides after uninstall.
- **Decisions:** Keep production and ordinary DEBUG launches on fail-closed Keychain storage. Only an explicit `newslyE2EEnabled` launch may use the exact App Group suite so process-relaunch, refresh rotation, and the real app-to-Share-Extension credential handoff remain executable without signing. The AXe harness owns removal of that E2E state between cases because Simulator preference domains survive app uninstall.
- **Changes:** Routed credential-envelope, legacy-token, cached-user, and refresh-attempt reads, writes, and deletes through an explicit App Group suite for E2E launches; this avoids `SharedContainer.userDefaults` intentionally selecting standard defaults for launch overrides. Normal builds retain the existing Keychain path unchanged. The AXe clean-install helper now deletes both the exact app bundle and `group.com.newsly` defaults domains as well as resetting the Simulator Keychain.
- **Validation:** Both previously failing authenticated lifecycle UI tests passed under the exact unsigned release command; the full native gate then passed all 619 tests. The definitive Python gate passed 2,911 tests. The first complete Maestro run passed 38 tests and localized the remaining four failures to persistent defaults state. After clearing both domains and explicitly routing E2E credentials through the App Group, all four Share Extension variants passed end to end in 82.67 seconds. Ruff and `git diff --check` pass; a final complete gate cycle remains pending.
- **Release follow-up:** The complete replacement-candidate gates passed (2,911 Python, 619 native iOS, and 42 Maestro tests), and exact SHA `d093a06f` was pushed. Docker Deploy run 33288955496 then built and published the image but two remote pulls were throttled by GHCR with `TOOMANYREQUESTS` responses. Added a bounded five-attempt remote `docker pull` retry with increasing waits so transient registry throttling cannot abort a healthy blue-green release.
- **Remaining:** Rerun the complete Python, native iOS, and Maestro release gates on the deployment-retry commit before pushing the replacement SHA.
- **Commits:** This release-gate correction commit.

### 2026-08-29 — `main` — Newsbuddy logo/brand exploration site

- **Status:** Complete
- **Scope:** `docs/brand-exploration-2026-08/` only; no app code touched.
- **Decisions:** Generated 16 Japanese-aesthetic pastel logo concepts split across Seedream 5.0 Lite (Runware, existing key) and GPT Image 2 (`openai/gpt-5.4-image-2` via OpenRouter — used instead of the codex CLI since OpenRouter exposes the model directly). Per-image color kits extracted with background-masked median-cut quantization, honoring the one-accent+neutrals color doctrine.
- **Changes:** `generate_logos.py` (idempotent, per-concept regen via CLI args), `extract_palettes.py` (emits `palettes.json`/`palettes.js`), `index.html` static explainer with squircle icon crops and per-kit themed briefing-UI phone mocks.
- **Validation:** All 16 generations succeeded (~$0.035/image on Runware); site verified via headless Chrome screenshots.
- **Remaining:** Awaiting direction pick; regenerate any concept with `uv run python docs/brand-exploration-2026-08/generate_logos.py <id>` then rerun `extract_palettes.py`.
- **Commits:** `87f6191` (`docs(brand): add Newsbuddy logo exploration`).

### 2026-08-29 — `main` — Brand exploration: image model bake-off (follow-up)

- **Status:** Complete
- **Scope:** `docs/brand-exploration-2026-08/` only.
- **Decisions:** The first pass used Seedream 5.0 **Lite** — the `.env` default and cheapest tier — rather than a surveyed best model. Surveyed the Runware catalog via `modelSearch` plus OpenRouter's image models, then bake-offed six candidates on two concepts. Nano Banana Pro (`google:4@2`) won clearly; Recraft V4.1 Pro underperformed despite being design-marketed and is the most expensive at $0.21/image. Kept both image sets rather than replacing v1: Nano Banana Pro wins on the geometric/abstract marks, but v1 holds up better on some character marks (mochi, lantern, tsuki).
- **Changes:** `bakeoff.py` (new); `generate_logos.py` parametrized with `--runware-model`/`--out`; `extract_palettes.py` emits per-set palette files; `index.html` gained a model-set toggle that re-renders marks, palettes, and phone mocks.
- **Validation:** 16/16 v2 generations succeeded at $0.138 each (~$2.21 total); both toggle branches verified via headless Chrome; `ruff check` clean.
- **Remaining:** Recraft/Ideogram require fixed 2048² dimensions — handled in `bakeoff.py` only, not in `generate_logos.py`. Vector/SVG output for the winning mark is still unexplored.
- **Commits:** This brand bake-off follow-up commit.

### 2026-08-29 — `main` — Brand exploration round 2: single-glyph reduction

- **Status:** Complete
- **Scope:** `docs/brand-exploration-2026-08/` only.
- **Decisions:** Round 1 read the Japanese brief as iconography (torii, daruma, origami), which was too literal. Round 2 keeps Japanese influence as sensibility only — restraint, negative space, one warm accent — and the prompt now explicitly forbids that motif list. Each concept reduces to a single geometric glyph that must survive at 16px. 25 concepts spread 5-per-model across the pro tier (Seedream 5.0 Pro, Nano Banana Pro, Recraft V4.1 Pro, Ideogram 4.0, GPT Image 2), chosen for idea diversity rather than model comparison.
- **Changes:** `concepts_r2.py` (concept + model registry, single source of truth), `generate_r2.py` (emits `concepts_r2.js` for the site), `gen_runware` gained a `size` param, `extract_palettes.py` covers the r2 set, `index.html` renders per-set concept lists behind a three-way round switcher with a per-set blurb.
- **Validation:** 25/25 generated (~$3.30); `ruff check` clean; site verified via headless Chrome and over Tailscale.
- **Remaining:** Three Recraft outputs collide with existing marks — Lifted Quadrant reads as the Microsoft logo, Signal as the Wi-Fi glyph, and Plane is off-palette. Exclude or re-prompt before shortlisting. Vector output still unexplored. Note 2048² is the only square every model in `MODELS` accepts; Nano Banana Pro rejects 1536.
- **Commits:** Uncommitted

### 2026-08-29 — `main` — Brand exploration round 3: paper-craft objects

- **Status:** Complete
- **Scope:** `docs/brand-exploration-2026-08/` only.
- **Decisions:** Round 2's flat glyphs reduced too far. Direction rebuilt from the user's picked favorites (Ensō & Paper, Washi Bubble, Hanko Seal, the folded-paper marks, Ribbon Eyes), whose shared DNA is a tactile made object with soft shading, visible creases, and a hint of character. Two prompt corrections were needed after test renders: the first style base produced photorealistic product shots, so it now demands a flat 2D illustration and bans photography/3D; the second still drifted into three-quarter perspective, so it now requires flat-on front-facing composition. 25 concepts across the pro tier, with Recraft V4.1 Pro cut to 2 after collisions in both prior rounds.
- **Changes:** `concepts_r3.py`; `generate_round.py` replaces the per-round scripts (`generate_r2.py` deleted) and takes a round name, loading `concepts_<round>.py` and emitting `concepts_<round>.js`; `extract_palettes.py` covers r3; `index.html` switcher extended to four sets with round 3 as default.
- **Validation:** 25/25 generated (~$2.90); `ruff check` clean; site verified via headless Chrome and over Tailscale.
- **Remaining:** Envelope Lift and Wax Seal both read as generic mail icons; Card Stack came back with scenic art inside it; Ribbon Loop reads as an awareness ribbon. Exclude before shortlisting. Vector output still unexplored — no round has produced a true vector.
- **Commits:** Uncommitted

### 2026-08-30 — `main` — Brand exploration round 4: calm, reading, knowledge

- **Status:** Complete
- **Scope:** `docs/brand-exploration-2026-08/` only.
- **Decisions:** Two corrections from round 3. Paper folding is no longer the driving concept — round 3 turned every idea into an origami variation — while the soft illustrated texture stays. And every prior round came out orange because the shared style base asked for "one warm accent"; the palette is now specified per concept and deliberately varied (sage, slate, plum, indigo, teal, moss, olive, mauve), with orange named as a color to avoid. Carried forward: the Ensō brush-ring direction with its warm-grey-plus-blush palette, and the cute bookmark from round 2's Ribbon Eyes. Recraft V4.1 Pro retired after producing the weakest tiles in all three prior rounds; 24 concepts, 6 each across the remaining four models.
- **Changes:** `concepts_r4.py`; `extract_palettes.py` covers r4; `index.html` switcher extended to five sets with round 4 as default, hero copy and date updated.
- **Validation:** 24/24 generated (~$2.30); extracted palettes confirm the spread (indigo `#1d2548`, teal `#2a656d`, plum `#53233a`, sage `#9ca48a`, slate `#2e435e`, olive `#c7b57e`); `ruff check` clean; site verified via headless Chrome and over Tailscale.
- **Remaining:** Per-concept palette specification is the mechanism that fixed color diversity — keep it in any future round. Vector output still unexplored; no round has produced a true vector, so the winning mark still needs redrawing.
- **Commits:** Uncommitted

### 2026-08-30 — `main` — Brand exploration round 5: shortlist variations

- **Status:** Complete
- **Scope:** `docs/brand-exploration-2026-08/` only.
- **Decisions:** Convergence round rather than exploration. The three shortlisted marks — Ensō & Book (r4-01), Bookmark Buddy (r4-07), Reader (r4-21) — each rendered in eight color schemes with light form variation, holding the form recognizable so palettes compare directly. Each family stays on the model that produced its original, since the rendering character being selected for is partly the model's.
- **Changes:** `concepts_r5.py`; `extract_palettes.py` covers r5; `index.html` switcher extended to six sets with round 5 as default; hero copy, blurb escaping and date corrected.
- **Validation:** 24/24 generated; `ruff check` clean; site verified via headless Chrome and over Tailscale.
- **Remaining:** Runware credits were exhausted mid-run — the five Nano Banana Pro bookmarks were recovered by routing to `google/gemini-3-pro-image` on OpenRouter, which is the same underlying model. Runware needs a top-up at my.runware.ai/wallet before any further Seedream or Ideogram work; the OpenRouter path still works. Vector output still unexplored.
- **Commits:** Uncommitted

### 2026-08-30 — `main` — Brand exploration round 6: darker palettes and hybrids

- **Status:** Complete
- **Scope:** `docs/brand-exploration-2026-08/` only.
- **Decisions:** Shortlist narrowed to Ensō · Charcoal, Ensō · Teal, Bookmark · Sage, Reader · Warm Grey. Round split in two: ten palettes pushed darker with the accent constrained to a minority of the mark (the style base now states this explicitly, since earlier rounds let the accent grow to half the shape), then fourteen hybrids crossing the ensō ring, book, bookmark ribbon and blob reader. Bookmark concepts now specify squared shoulders, which fixed the ghost read flagged in round 5.
- **Changes:** `concepts_r6.py`; `extract_palettes.py` covers r6; `index.html` switcher extended to seven sets with round 6 as default, hero copy updated.
- **Validation:** 24/24 generated; `ruff check` clean; site verified via headless Chrome and over Tailscale.
- **Remaining:** Runware is out of credits and needs a top-up at my.runware.ai/wallet — a single test call succeeded on residual balance, which is misleading, so verify with more than one request. This round ran entirely on OpenRouter; Seedream 5.0 Pro has no OpenRouter route, so the ensō concepts fell back to Nano Banana Pro. The two strongest results, Bookmark × Spectacles (r6-15) and Reader × Ribbon Tail (r6-16), are the same fusion reached from opposite directions. Vector output still unexplored.
- **Commits:** Uncommitted

### 2026-08-30 — `main` — Brand exploration round 7: finalists, and an app-accurate mock

- **Status:** Complete
- **Scope:** `docs/brand-exploration-2026-08/` only. No app code touched — the simulator was used read-only for reference screenshots.
- **Decisions:** Two finalists locked: Ensō · Petrol (r6-03) and Reader × Ribbon Tail (r6-16), each rendered across twelve palettes on one model so color is the only variable. Separately, the site's phone mock was rebuilt against the real app rather than invention. The previous mock showed a card feed with thumbnails, read-time chips and unread dots; the actual Briefing has none of those. It is a continuous editorial column on a single 20pt gutter, where stories are inline underlined accent links inside running prose, figures float in the text, and read state is expressed as 0.72 opacity rather than badges. Selected pills invert to ink, not accent. The mock now uses the app's real typefaces (Lora serif + Lato sans) and its two-tab floating capsule bar (Briefing, Knowledge).
- **Changes:** `concepts_r7.py` (palette list drives concept generation); `extract_palettes.py` covers r7; `index.html` phone mock markup and CSS fully replaced with Briefing-column + long-form-reader screens, Lora/Lato added, `TABS` corrected to the real two tabs; switcher extended to eight sets with round 7 as default. Reference screenshots saved to `app_reference/`.
- **Validation:** 24/24 generated; `ruff check` clean; site verified via headless Chrome and over Tailscale. Reference screens captured from `org.willemaw.newsly.local` on booted sim E2D8054B against the local API.
- **Remaining:** The Ensō family is highly consistent across all twelve palettes; the Reader family is not — Ink, Oxblood, Pine and Navy lost or distorted the ribbon tail and would need regeneration. Near-monochrome palettes (Ensō · Ink) leave inline links nearly indistinguishable from body text in the mock, which is a real legibility finding, not a mock bug. Runware still needs a top-up. Vector output still unexplored.
- **Commits:** Uncommitted

### 2026-08-30 — `main` — Brand exploration round 8: image-to-image recolors and two-tone

- **Status:** Complete
- **Scope:** `docs/brand-exploration-2026-08/` only.
- **Decisions:** Round 7's Reader variants were wrong — re-describing an existing mark in text reliably drifts it, and the arched top and deep swallow-tail collapsed back into the round blob from round 4. Fixed at the root by switching to image-to-image: `gen_openrouter` now accepts a `reference` image, and `generate_round.py` reads an optional `REFS` mapping from the round module, so named concepts are edited rather than regenerated. Silhouettes are now held exactly and only color changes. Second half adds two-tone treatments — exactly two flat inks, all shading removed — which also serves as the print / favicon / monochrome-icon reduction.
- **Changes:** `generate_logos.py` (`reference` param, base64 image content part), `generate_round.py` (`REFS` support), `concepts_r8.py`, `extract_palettes.py` covers r8. `index.html` gained a `.concept.twotone` mode that collapses the phone mock to two inks — cards and tints resolve to the ground, structure carried by hairline rules and outlines — applied automatically to concepts whose id contains `-2t-`.
- **Validation:** 28/28 generated; every Reader recolor retained the arched top, swallow-tail and spectacles, confirming the image-to-image fix; `ruff check` clean; two-tone app shots verified via headless Chrome; site verified over Tailscale.
- **Remaining:** Use image-to-image for any further variation of a chosen mark — text re-description is not reliable for this. Runware still needs a top-up (round ran on OpenRouter). Vector output still unexplored; the two-tone flats are the closest thing to a traceable source and are the best candidates to vectorize.
- **Commits:** Uncommitted

### 2026-08-30 — `main` — Brand direction selected; app-screen variants

- **Status:** Complete
- **Scope:** `docs/brand-exploration-2026-08/` only. Design selection is recorded here; no app code changed yet.
- **Decisions:** Direction chosen. **App icon = Ensō · Slate** (`images_r8/r8-03-enso-slate.png`, ring `#404c60`, book `#edd9b3`). **Chat and Knowledge character = Reader · Indigo** (`images_r8/r8-10-reader-indigo.png`, body `#383061`, spectacles `#d3bc78`). The two marks intentionally differ: the ensō carries the app identity, the reader appears only where the app speaks to the user. Screen backgrounds no longer derive from the generated images — those extracted grounds were far too saturated to sit behind body text — and are instead hand-set near-white per scheme.
- **Changes:** New `app-screens.html` presenting four screens (Briefing, Reader, Chat, Knowledge) across four light schemes: Slate on cool grey (the app's real `#f4f5f7` surface), Slate on warm paper, Ink on paper, and Indigo. Chat and Knowledge screens are new — neither existed in the earlier mocks. Linked from `index.html`.
- **Validation:** Rendered via headless Chrome; both pages verified over Tailscale.
- **Chat/Knowledge accuracy:** Both screens were rebuilt against the real implementations after the first pass proved wrong. Chat has **no avatars and no suggested-prompt chips** — messages are bubbles on both sides (assistant left, `surfaceContainer` tint, full column width; user right, warm `chatUserBubble` `#efe7d8`, 72px left gutter; both radius 14, Lato 13), with a timestamp under each, centered process-summary capsules, a thinking bubble carrying dots + elapsed timer + status line, and a composer dock of `+` / "Message" field / mic. The tab bar is hidden on chat. Knowledge is not cards or a deck-with-progress; it is the scrolling editorial masthead ("Knowledge" in Lora 44) plus an "Ask anything…" dock, then a day-grouped timeline of flat 40×40-thumbnail rows whose type is carried entirely by a kicker (`CHAT · 3H AGO`, `DECK · 12M AGO`, `SAVED · THE VERGE · 1D AGO`), with audio rows swapping the chevron for a play circle.
- **Open design decision:** because the real chat has no avatar slot, the chosen Reader character has nowhere to live. It is currently placed in the chat session header as the smallest change that gives it a home; putting it on every assistant message would be a real design change rather than a re-skin. Flagged prominently on the page.
- **Remaining:** The Ink scheme carries a known constraint: body ink and accent are close enough that inline links depend on underline plus weight to separate. Vector output still unexplored — the chosen ensō is a raster brush texture and will need redrawing or tracing for a real app icon asset.
- **Commits:** Uncommitted

### 2026-08-31 — `main` — Day mode retuned to warm cream (option E)

- **Status:** Complete, with one verification gap (below).
- **Scope:** `ReaderPalette.swift`, `AccentColor.colorset`, regenerated dark app-icon assets, `docs/brand-exploration-2026-08/day-mode-options.html`.
- **Diagnosis:** The reported "too grey" day mode was not a contrast failure. The shipped ground was already 96% light and body ink scored 14.9 against it. Two things were actually wrong: the ramp was near-neutral, so it read as grey rather than paper, and card lift was 1.06 in light and 1.08 in dark — surfaces were effectively invisible against the ground, collapsing every screen into one flat field. Six paired light/dark options were built at `day-mode-options.html` with ink contrast, card lift and ground lightness printed per option so the two axes could be judged separately.
- **Decision:** Option E — warm cream by day (`#faf6ea`), warm brown-black at night (`#191510`). It attacks the greyness with hue rather than lightness. Note this fixes the *neutrality* but not the separation: card lift stays 1.06/1.10. If surfaces still read flat in use, option B's lift is the follow-up and is independent of this change.
- **Changes:** Full light and dark ramps retuned to warm cream / warm dark. `onSurfaceTertiary` had to move from `#787261` to `#757060`: at `#787261` it scored 4.44 against the new `surfacePrimary` and failed the project's own contrast test. Dark `brandPrimary` lifted `#93a7c4` → `#9db0cc` to clear the warmer ground, with `AccentColor.colorset` updated in lockstep. The dark app icon and `AppMark`/`BuddyMark` dark variants were regenerated because the old dark icon baked in the previous `#171613` ground and would have sat as a visibly mismatched square on the new one.
- **Validation:** `ReaderPaletteContrastTests` pass (2/2, both modes). Clean build. On-device pixel samples match exactly: `#faf6ea` light, `#191510` dark.
- **Verification gap:** The Briefing and Reader surfaces were **not** re-checked on device. The local API will not start — `newsly-db migrate` aborts with "this database already has Alembic schema history; run `newsly-db baseline --maintenance-barrier-confirmed` before ordinary SQLx migrations". That baseline was deliberately not run. Until the local stack is back, the reading surfaces in this palette have only been seen in the HTML mock.
- **Field bug (2026-09-01):** the gap above was real. A production screenshot showed dark Briefing body text visibly cooler than the title: `appReaderBodyText` in `DesignTokens.swift` hardcoded `#e9ebef` for dark instead of reading the palette, so it kept the cool slate value through the warm retune. Pixel-sampled the screenshot to confirm — title `#efebe1` (warm, B−R = −14), body `#efedf0` (neutral, B−R = +1). Fixed structurally: `readerBodyText` is now a `ReaderPalette` slot (`#24221a` light / `#f3eee1` dark — the same "slightly brighter body at night" intent, in the warm family) and the DesignTokens accessor is a one-line palette read. The contrast test now also asserts `readerBodyText` ≥ 4.5 on both surfaces in both modes. A sweep found the same class of bug in `SelectableMarkdownView`: code-block and inline-code background chips hardcoded cool greys; both now resolve `surfaceTertiary` / `surfaceContainer` from the palette. Remaining intentional hardcode: `statusDestructive` red, which is a distinct semantic hue. Fix verified by build + tests; visual confirmation on the reading surfaces still pending the same API gap — re-check on device.
- **Commits:** `72c0782d`.

### 2026-09-01 — `main` — Design-consistency audit and remediation

- **Status:** Complete, with two visual checks pending the local API.
- **Scope:** iOS client (`newsly/` + `ShareExtension/`), one asset colorset, palette contrast tests.
- **Method:** Full sweep for anything bypassing or contradicting the warm-palette design system, prompted by the readerBodyText field bug being a class rather than an instance.
- **P0 fixes:**
  - `appChromeAccent` returned `.secondaryLabel` and was applied as the root `.tint` in `ContentView`, so every borderedProminent button, toggle, picker, caret and nav chevron rendered cool system grey — the slate accent literally never reached SwiftUI controls, and the guarded `AccentColor` asset was dead. Stale multi-palette-era doctrine; now `brandPrimary`.
  - Seven sites hardcoded `.white` on `brandPrimary`/`brandPrimaryStrong` fills (AddButton, SettingsAccountSection, SettingsCouncilSection, ProcessingStatsView, ContentDetailSwipeOverlay, StructuredSummaryView, TapToTalkMicButton, ShareViewController). The accent inverts to light in dark mode, so white-on-accent was 2.2:1 at night. All now use `surfacePrimary` as the on-accent foreground (the existing onboarding idiom), and a new `testSurfacePrimaryReadsOnAccentFills` guards the pattern.
  - `OnboardingSuggestionCard` tile palette resolved to two indistinguishable creams plus the brand slate, putting near-black monograms on dark slate at 1.85:1 for ~1/3 of sources. Now the three container rungs — distinct, warm, all safe under `onSurface`.
- **P1 fixes:** ShareExtension dropped all stock system chrome (`systemBackground`, `secondaryLabel`, `separator`, `systemGray6`) for palette roles added to `ShareExtensionStyle`; frozen `.cgColor` borders now resolve against `traitCollection` and re-apply via `registerForTraitChanges` on both the controller and option rows. Detail-skeleton `skeletonLine` filled white-at-32% over cream (1.02:1 — an invisible loading state); now ink-alpha like its siblings. Sixteen user-facing "Newsly" strings (error/empty states, incl. the network-error screen photographed earlier) renamed to Newsbuddy, with the three matching test expectations updated.
- **P2 fixes:** launch flash closed with `LaunchBackground` colorset + `UILaunchScreen.UIColorName` in both plists, guarded by `testLaunchBackgroundAssetMatchesSurfacePrimary`; tinted app-icon slot filled with a generated grayscale ensō (was empty → iOS 18 desaturated the cream icon to a washed-out plate); mic button's degenerate two-similar-colors gradient flattened; onboarding discovery-failure banner moved from brand-accent tints to `statusDestructive`; YouTube glyph no longer wears the error red; last non-token hairline moved to `outlineVariant`; `AppChrome` unselected/tab/nav greys moved to palette; `GlassCard` 40pt radius (retired pill geometry, landing card) clamped to `CornerRadius.card`; ToastView deprecated `.cornerRadius` replaced; purple-penguin `Mascot.imageset` deleted; dead `onboardingAmbient*` tokens and `ChatModelProvider.accentColor` (per-provider hues) removed; stale "cool slate + amber" and "watercolor" doctrine comments corrected.
- **Validation:** Full unit-test target green (631 tests; the only 3 failures were the old-name string assertions, updated). Palette contrast suite now 4 tests, all passing. Clean builds throughout.
- **Deferred / eyeball items:** chrome-tint change and ShareExtension restyle not yet visually confirmed (local API still down — same `newsly-db baseline` gap, now also blocked on Alembic head `20260825_01` vs expected `20260829_02` with alembic removed from the checkout; prod-state sync is the documented fix but overwrites local DB/env, not run unprompted). Audit also flagged, unfixed by choice: ~85 raw corner-radius literals beyond the clear outliers, "terracotta" type-token naming, materials desaturating the warm ground (`GlassCard`/`ArticleReaderView`/`GlassSurface` — needs an on-device eyeball), and the hero scrim's hard terminal stop against cream.
- **Commits:** This audit commit.

### 2026-08-30 — `main` — Slate brand implemented in the iOS client

- **Status:** Complete
- **Scope:** iOS client. Plan in `docs/initiatives/2026-08-30-slate-brand-rollout-plan.md`.
- **Decisions:** Scheme is **Slate on warm paper**. App icon is Ensō · Slate; the Reader · Indigo buddy appears only where the app speaks to the user. `ReaderPalette.swift` is the single source of truth for color, so the whole accent change is one file — amber `#99610a` → slate `#3f4c60`, cool grey neutrals → warm paper, and the dark ramp warmed to match with `brandPrimary` lifted to `#93a7c4` rather than substituting another hue.
- **Trap found:** `Assets.xcassets/AccentColor.colorset` must be kept in lockstep with `brandPrimary` — `ReaderPaletteContrastTests.testGlobalAccentAssetMatchesBrandPrimary` asserts it, and editing only `ReaderPalette.swift` fails that test. Updated both.
- **Changes:** `Shared/ReaderPalette.swift` (full light+dark ramp); `AccentColor.colorset`; `AppIcon.appiconset` (ensō replaces the purple penguin, light + dark); new `BuddyMark.imageset` and `AppMark.imageset` with `appearances` dark variants so `Image("BuddyMark")` resolves per mode with no `colorScheme` branching in views. Buddy placed in `ChatComposerDock` (leading menu button, menu wiring and `knowledge.mode_menu` identifier untouched) and `DetailActionBar` (new `buddyActionIcon()` sibling so the shared `actionIcon` helper is unchanged). App icon added to `LoadingView` (session-restore splash) and `SettingsBrandHeader`. New `OnboardingIntroStep` on the previously-dead `.intro` case, with `OnboardingViewModel.step` defaulting to `.intro`; retired `Image("Mascot")` in `LandingView` and `OnboardingChoiceStep`.
- **Asset pipeline:** `docs/brand-exploration-2026-08/build_assets.py` cuts the baked cream field to real alpha and produces the dark buddy by remapping the body hue while protecting the spectacle gold. Re-run it if the source renders change.
- **Validation:** Clean build succeeds with no warnings. `ReaderPaletteContrastTests` pass (2/2) covering both modes. Verified on simulator: `surfacePrimary` samples exactly `#f8f6f1` light and `#1f1e1a` on secondary surfaces dark; Settings shows the ensō; the buddy renders in the composer and at the end of the detail action bar, resolving its dark variant correctly.
- **Onboarding verified (follow-up):** Reached the flow via Debug Menu → "Reset Current User Onboarding". All five steps captured to `docs/brand-exploration-2026-08/onboarding_shots/` with a combined `onboarding_flow.png`. This exposed a redundancy the plan missed: the existing choice step was itself an introduction ("MEET YOUR GUIDE / Newsbuddy / I'm going to help you get onboarded"), so with the new intro ahead of it the user met the buddy twice in a row. `OnboardingChoiceStep` now asks its actual question — eyebrow "GETTING STARTED", title "How should we begin?" — with the buddy reduced from 180pt to 92pt so it reads as continuity rather than a second headline. The `onboarding.choice.screen` accessibility identifier was preserved on the new title. Flow confirmed end to end through to the Knowledge tab.
- **Onboarding redesign (follow-up):** The onboarding chrome never read from `ReaderPalette` — `onboardingSurface` and `onboardingText` in `DesignTokens.swift` hardcoded their own copies of the old surfaces. Both now resolve to `surfacePrimary` / `onSurface`, so onboarding follows the palette. Deleted `WatercolorBackground.swift`; consumers now use flat `surfacePrimary`. Display type was retuned to match the Briefing masthead, and onboarding controls now use the shared palette and geometry.
- **Copy reduction (follow-up):** Titles and buttons now carry the flow; explanatory subtitles were removed where the controls already state the choice. The intro retains the single-line product description.
- **Validation:** Clean build, no warnings. Full onboarding flow re-walked in light and dark modes.
- **Remaining:** Re-verify with a clean install; vector redraw of the ensō remains outstanding.
- **Commits:** `5749656e`, `9655dfd8`, `24682e0a`, `f6810671`.

### 2026-08-29 — `main` — Remove Cerebras integration

- **Status:** Complete locally and committed; not pushed or deployed.
- **Scope:** Provider configuration and model construction, chat/provider contracts, admin and onboarding evals, local environment configuration, privacy and architecture documentation, generated clients, and stored chat-session compatibility.
- **Decisions:** Remove the provider rather than retain weak shared-endpoint models or a dormant integration. Remove the obsolete generic `fast` admin-eval alias instead of silently assigning it to another model. Keep existing production chat sessions usable by migrating retired provider/model assignments to the canonical OpenAI Terra default when the migration is deployed.
- **Changes:** Removed the provider enum, pydantic-ai adapter, API-key setting and configured flag, environment-template/local credential entries, eval candidates and one-off evaluator, secret scanning branch, admin availability branch, documentation claims, and privacy disclosure. Regenerated Swift, Go, and OpenAPI artifacts. Added a data migration for stored chat sessions and rejection coverage for future attempts to select the retired provider.
- **Validation:** Ruff lint and format passed; 76 focused model/admin/onboarding/chat tests, two migration tests, and 57 contract/OpenAPI tests passed; Alembic reports the new migration as the single head; generated public-contract drift and `git diff --check` passed.
- **Remaining:** Deployment will apply the chat-session retargeting migration; no production state was changed in this task.
- **Commits:** `3516ffa0` (`refactor(ai): retire Cerebras integration`) and `3f616b05`
  (regenerated provider/auth contracts).

### 2026-08-29 — `main` — Clean up iOS lifecycle and networking implementation

- **Status:** Complete locally and committed; not pushed, distributed, or deployed.
- **Scope:** The uncommitted iOS lifecycle/networking implementation, its root composition, credential and request core, route-owned Content Detail and Learning Deck seams, focused tests, and client architecture guard.
- **Decisions:** Keep release-gated Keychain/App Group compatibility and the remaining process-shared composition follow-ups intact. Remove only unreachable factory branches, unused protocol or enum surface, redundant state and initialization, duplicate environment publication, and global-first route construction with behavior-equivalent exact dependencies.
- **Changes:** Derived activation state from the canonical lifecycle record; collapsed `AppRuntime` construction; avoided discarded authenticated-session work and repeated cached-user identity reads; removed dead factory builders, optional-auth behavior with no caller, unused credential protocol/payload surface, and low-signal retry plumbing; made Content Detail construct its chat coordinator from the authenticated session on the first render; centralized Apple presentation-anchor lookup; and made Learning Deck disappearance teardown model-owned. A focused regression also fixes source-body publication when a Content Detail route has no content-type hint.
- **Validation:** All 619 `newslyTests` passed, as did both authenticated warm-resume/process-reclaimed UI tests and all 72 client architecture tests. Swift parsing, Ruff lint/format, source searches, and `git diff --check` passed. The module-size guard still reports only the unrelated concurrent `app/services/onboarding/llm_plans.py` change at 501 lines versus its 467-line baseline.
- **Remaining:** Physical-device app/Share Extension access-group validation, release-gated legacy credential mirror retirement, and the broader instance composition migration for `RootDependencyFactory`, process-shared networking construction, and the two narrow `authDidLogOut` observers.
- **Commits:** `039be40b` (`refactor(ios): unify lifecycle and networking`).

### 2026-08-29 — `main` — Implement iOS lifecycle and networking simplification

- **Status:** Core implementation complete, validated locally, and committed; not pushed,
  distributed, or deployed.
- **Scope:** iOS process lifecycle and authenticated-session ownership, app and Share Extension networking, credential restoration and refresh rotation, Briefing/Content Detail/Knowledge/chat/Learning Deck wake behavior, backend refresh replay safety, generated contracts, laws, and architecture documentation.
- **Evidence:** The assessment confirmed several independently reachable causes for the sporadic wake error: nested transport/auth failures escaped the old classifier; selected Briefing Lens failure could survive an inactive interval; Content Detail treated cancellation as a blocking error; initial connectivity could fail before the path settled; cold relaunch had no identity-bound cached shell; and a lost refresh response could consume the only refresh token. Final hand-computation also found that logout could lose to an in-flight refresh, stale validation or terminal callbacks could mutate a newer session, a cancelled lifecycle waiter could cancel a joined explicit Knowledge refresh, non-cooperative reader-body work could publish after suspension, and interrupted compatibility publication could leave divergent token legs. Deterministic tests now drive the real asynchronous inactive-Lens completion and task retirement, rather than assigning failure state directly, and cover the other concrete error shapes and sequencing boundaries without assuming one observed `Try Again` had one cause.
- **Decisions:** Use one fact-only `AppLifecycle`, one authenticated-user session lifetime, one target-neutral HTTP/credential core, retained-value revalidation, and method-safe bounded retry for opted-in `GET`/`HEAD` reads. Keep commands single-send, keep Briefing/chat/deck domain state machines local, avoid a global refresh registry or reachability gate, and do not extract a generic `ReadResource` until a second feature proves the same ownership boundary.
- **Changes:** Added lifecycle generations and correlated transition logging; moved root user state into `AuthenticatedSession`; reduced `APIClient` to three typed operations and one `ClientFailure` vocabulary; enabled connectivity waiting and a shared 250/750 ms safe-read budget; removed the legacy API error, cancellation, descriptor, token-refresh, explicit pre-refresh, multipart transport, extension transport, and authentication-required notification paths; added identity-bound credential envelopes, cached authenticated shells, single-flight/cross-process refresh coordination, and durable replay-attempt persistence; made backend refresh rotation return the same encrypted replacement pair for a bounded repeated attempt; and migrated Content Detail and Knowledge to generation-fenced retained-value behavior. Logout now clears under the refresh lock, verifies every secure and compatibility deletion leg, and reports cleanup failure; auth callbacks are cancel-and-generation fenced; terminal events conditionally match the current account publication; unsafe interrupted credential publication fails unavailable without overwriting either leg; explicit Knowledge refresh ownership survives lifecycle-waiter cancellation; and reader/source-body publication is generation fenced. Refresh fails before network I/O if its replay ID cannot be durably written and read back. Updated the generated Swift/OpenAPI contracts and relevant product laws.
- **Validation:** All 616 `newslyTests` passed on the Newsly Regression iOS 26.5 Simulator. Both authenticated UI regressions passed without skips against the final combined tree: Home/activate preserved `briefing.screen` without a blocking error, and terminate/relaunch restored the cached credential/user shell without E2E auto-login or auth fallback. The final app built, installed, and launched. The consolidated client/backend slice passed 109 tests; Ruff, focused MyPy, Alembic single-head verification, generated public-contract drift, source-boundary searches, and `git diff --check` passed. The module-size guard reports only the unrelated concurrent `app/services/onboarding/llm_plans.py` change at 501 lines versus its 467-line baseline.
- **Remaining:** Validate the real access-group and app/Share Extension cross-process credential path on a physical device. Retain and reconcile legacy Keychain/App Group material until the mixed-version release window closes, then remove the plaintext mirror/fallbacks. A later composition-only change can replace remaining static `RootDependencyFactory`/process-shared construction and the narrow `authDidLogOut` bridge; these are not wake-correctness blockers.
- **Commits:** `aaa92d4c` (backend refresh replay safety), `3f616b05` (generated contracts),
  and `039be40b` (iOS lifecycle/networking implementation).

### 2026-08-29 — `main` — Revise iOS lifecycle/networking approach plan after verification pass

- **Status:** Complete (planning artifact; no product code changed).
- **Scope:** `docs/initiatives/ios-lifecycle-networking-2026-08/` — verified `10-approach-plan.md` against the checkout with five parallel code audits, wrote `11-approach-plan-r2.md`, and marked r1 superseded.
- **Decisions:** Keep r1's architecture and slicing; the core diagnoses (nested `APIError(AuthError(URLError))` classification gap, backend refresh-token burn-before-response with no replay record, extension transport duplication, launch-only memory-only auth) all verified against source. R2 amendments: Slice 8 must migrate the hand-written refresh DTOs (`TokenRefreshResponsePayload`, extension payloads) onto the generated contracts or the attempt ID never reaches the wire (generated refresh types currently have zero call sites); explicitly reverse ios-modernization-2026-07's "no shared extension framework yet" decision with argument; the credential-envelope migration must reconcile and retire the plaintext App Group `UserDefaults` token mirror and cover the access-token-first sign-in publication path; H2 narrowed to the read-retirement-protected selected-Lens path (index/refresh failures already fenced per 2026-08-15); error cutover re-scoped to 44 `isNetworkCancellation` call sites across 17 files plus inline `CancellationError` catches; `ImageCacheService` named as a sixth direct-HTTP sender and deliberately exempted; Slice 9 doc work is section creation plus a `LoadPhase` guideline rewrite, not an append.
- **Changes:** New `11-approach-plan-r2.md` (full self-contained revision, `[r2]` markers on decision-level changes); `10-approach-plan.md` status line now points at r2.
- **Validation:** Five read-only audit agents with file:line evidence across networking/errors, lifecycle/composition, extension/token storage, read-state, and docs/laws/initiatives; all eight cited law IDs confirmed, with dropped clauses (B10 authoritative-server, B13 reconciliation-window scope, K13 sustained-load visibility) restored in r2.
- **Remaining:** Review decisions 1–5 in r2 need agreement; then decompose Slices 0–3 into exact file/test changes and re-estimate the Slice 7 cutover.
- **Commits:** `6b62939a` (`docs(ios): plan lifecycle and networking simplification`).

### 2026-08-29 — `main` — Add onboarding fast-model tool-call eval

- **Status:** Complete locally with live model comparison and committed; not pushed or deployed.
- **Scope:** Newsly onboarding audio-lane planning prompt/schema, fast-model provider routes, eval dataset, runner, tests, and guide.
- **Decisions:** Compare every candidate through the same strict Pydantic tool-output contract because CoreWeave and Baseten do not advertise native JSON-schema output for DeepSeek V4 Flash 0731. Pin OpenRouter providers with fallbacks disabled, reasoning off, required parameters, ZDR, and data collection denied. Use locally authenticated Codex Sol for both the semantic reference and judging; keep all live calls behind an explicit `--execute` flag.
- **Changes:** Added a three-case onboarding dataset and a dry-run-first evaluator for Kimi K2.6 on DeepInfra through OpenRouter and DeepSeek V4 Flash 0731 on CoreWeave, Baseten, Wafer, and Reka. Explicit low-reasoning GLM 5.3 and GLM 5.3 Flash probes are also available outside the default run. The report captures validated tool-call success, tool-call presence, full latency, normalization fallback, deterministic prompt checks, normalized output, and semantic score.
- **Validation:** The durable three-case run used Kimi K2.6/DeepInfra and DeepSeek V4 Flash 0731 on CoreWeave, Baseten, Wafer, and Reka. All successful calls passed the strict schema and mechanical checks. Semantic averages were Kimi 0.733, Wafer 0.667, Reka 0.623, and the single successful Baseten call 0.780. CoreWeave had no compatible required-tool endpoint; Baseten rate-limited two of three calls. A later identical-case smoke test found no eligible full GLM 5.3 endpoint on the pinned Z.AI or Io.net routes, while GLM 5.3 Flash on DeepInfra produced a valid plan in 12,510 ms. Eight focused eval tests, 28 focused model/onboarding tests, Ruff lint/format, targeted MyPy, ZDR endpoint verification, the module-size guard, and `git diff --check` passed.
- **Judge correction:** Removed the generated Sol reference and vague expected-outcome text from judging. The high-reasoning judge now scores only perceived link quality: relevance to the narration, likely source/format diversity, and practical search quality. A score of 0.70 passes in code. Rejudging the stored valid outputs, without new candidate-provider calls, produced averages of Kimi 0.740 (3/3), Wafer 0.700 (2/3), Reka 0.810 (3/3), and Baseten 0.720 (1/1). The corrected rubric consistently identified awkward, incomplete, overly SEO-oriented, or unnecessarily narrow queries as the main weakness.
- **GLM provider benchmark:** Replaced the single-call GLM smoke-test conclusion with three-run streaming measurements on six pinned routes. Full GLM 5.3/Baseten was fastest overall at 880 ms median TTFT and 3,339 ms median validated completion; Fireworks measured 687 ms and 5,586 ms. Novita had no eligible endpoint. GLM 5.3 Flash measured 9,938/9,950 ms on DeepInfra, 6,081/9,956 ms on Wafer, and 14,811/14,813 ms on Reka, whose slowest run took 73 seconds. All 15 successful streams produced schema-valid tool calls. The 18 attempted calls cost $0.0146 total. Added the working Fireworks and Baseten full-model routes as explicit non-default candidates.
- **Final speed/cost shortlist:** Repeated the identical streaming request three times on DeepSeek V4 Flash 0731/Wafer. It measured 1,104 ms median TTFT, 2,449 ms median total, and $0.00047 median exact OpenRouter cost. Against GLM 5.3/Baseten at 880 ms, 3,339 ms, and $0.00159, DeepSeek is the best measured speed-quality-cost balance.
- **Production trial:** Scoped onboarding audio-plan generation to `deepseek/deepseek-v4-flash-0731` on Wafer Fast. The route disables reasoning, forbids provider fallbacks, requires supported parameters, denies provider data collection, and requires ZDR. Other onboarding and global fast-model routes remain unchanged so the model can be evaluated through the real user flow without broadening the rollout. A live call through the actual `preview_audio_lane_plan` production helper completed in 3,410 ms without fallback and returned four validated lanes covering AI engineering, startup strategy, product leadership, and Reddit discovery.
- **Complete onboarding routing:** Profile generation, voice parsing, audio planning, and final source suggestion selection now share DeepSeek V4 Flash 0731 pinned to Wafer Fast with reasoning disabled and fail-closed private routing. A live final-selector canary completed in 1,857 ms and returned grounded Latent Space and r/MachineLearning suggestions from the supplied web evidence.
- **GLM Flash challenge:** Ran three identical streaming requests per route against the selected DeepSeek/Wafer production shape. DeepSeek/Wafer completed 3/3 with 1,401 ms median TTFT, 2,310 ms median validated-object latency, and $0.00026 median cost. GLM Flash/Modal completed 3/3 at 429 ms TTFT but 5,709 ms total and $0.00025. Baseten is ZDR-compatible, but its GLM Flash endpoint supports only `none` and `auto` tool choice, so `tool_choice=required` plus `require_parameters=true` filtered it out before inference. GLM Flash Nitro completed 3/3, selected Wafer each time, and measured 7,066 ms TTFT, 12,051 ms total, and $0.00050. Nine successful calls cost $0.00316 total; DeepSeek/Wafer remains selected because onboarding consumes the complete structured object.
- **Remaining:** No tested route met both the desired latency and semantic quality bar. Tighten the shared audio-plan prompt against unsupported inference, templated searches, malformed query fragments, and target/source mixing, then rerun Kimi and Wafer before changing the production default.
- **Commits:** `06960686` (`feat(onboarding): pin fast planning to Wafer`).

### 2026-08-26 — `main` — Stabilize Share Extension release verification

- **Status:** Complete locally; release validation in progress.
- **Scope:** AXe remote-surface inspection for keyboard-shifted Share Extension forms.
- **Decisions:** Resolve the current control and submit-button frames semantically from a bounded set of remote accessibility points; do not encode one post-keyboard sheet position as authoritative.
- **Changes:** Added candidate-point polling and accessibility-frame center resolution, switched the Create Deck and Chat Share flows to inspect and submit at the resolved controls, and made the pristine-Safari education popover close by its unique semantic button rather than its unreliable icon identifier.
- **Validation:** Ruff and 11 AXe harness unit tests passed; the focused Create Deck and Chat Share flows each passed end-to-end against the live local API and queue on an iPhone 17 Pro simulator running iOS 26.5. The complete pristine-simulator run passed 39/40; its sole failure was the first Safari education popover ignoring an identifier-based close tap, while every Newsly scenario and all later Share modes passed. After stabilizing the delayed education-popover transition, the focused Add to Briefing flow passed from pristine Safari state.
- **Remaining:** Repeat the complete release gates on the final commit before push.
- **Commits:** Uncommitted.

### 2026-08-25 — `main` — Knowledge tab visual design pass

- **Status:** Complete
- **Scope:** iOS Knowledge tab: `KnowledgeTimelineRows.swift`, `KnowledgeTimelineView.swift`, `KnowledgeView.swift`.
- **Decisions:** Kickers only show status when abnormal (deck hides "READY", narration hides "COMPLETED" and gains a relative timestamp) to match the saved-row convention. Search screen field restyled to the timeline composer treatment (`surfaceSecondary` + `borderSubtle`) instead of the heavier `surfaceContainerHighest` pill.
- **Changes:** Saved rows aligned to compact shared metrics (40-point artwork, 10-point gap, and 6-point vertical padding); subtitle suppressed when it duplicates the title; intra-group `Divider()` replaced with `KnowledgeTimelineRowDivider` (explicit hairline aligned to the text column) because `Divider` rendered as a stray vertical tick inside these list rows on iOS 26. Titles stay single-line by request — wrapping made rows too tall.
- **Validation:** Built and ran on iPhone 17 Pro sim (iOS 26.4) against the local server; before/after screenshots of the timeline (light + dark) and Search Knowledge screen; magnified crop confirms the hairline renders horizontally at the text column. The compact-row source regression and focused dark-mode visual assertion passed.
- **Remaining:** Duplicate "Mini NAS Showdown" chat rows in the timeline are a data issue, not addressed.
- **Commits:** `a672ff4f`.

### 2026-08-25 — `main` — Reduce recurring news token usage

- **Status:** Complete and committed; not deployed.
- **Scope:** Short-form news processing, duplicate reconciliation, article-link enrichment, discussion-summary cadence, focused tests, and processing laws.
- **Decisions:** Keep hourly raw discussion refreshes while coalescing LLM summaries; reuse summaries only for definitive URL/external-ID matches so semantic clustering retains its content evidence; rank optional article links only after an item remains representative; cap summary article bodies at an approximate 12,000-token budget while retaining the beginning and conclusion.
- **Changes:** Raised the discussion materiality gate to more than 25 changed comments, added a six-hour minimum summary interval and a 24-hour stale-change refresh, reused exact representative summaries before article resolution or paid calls, moved article-link ranking after relation reconciliation, and bounded oversized short-form article prompts.
- **Validation:** The complete focused service, pipeline, scraper, and API slice passed 175/175 tests. Ruff, formatting checks, focused MyPy, and `git diff --check` passed.
- **Remaining:** The full repository suite was not run. Realized token savings require deployment and a comparable 24-hour production measurement. No production mutation or deployment performed.
- **Commits:** `e7b0b7e3`.

### 2026-08-25 — `main` — Unify Knowledge and Learning

- **Status:** Complete and committed; not deployed.
- **Scope:** Saved-knowledge FTS, host-side chat lookup, the iOS Knowledge/Learning root, bottom navigation, timeline rows, and chat typography.
- **Decisions:** Keep one four-source Knowledge timeline materialized by the screen store; retain the existing list endpoints while adding the saved-activity timestamp required by the unified chronology; make the shared weighted content document authoritative for both queries and the GIN index; preserve legacy `learning` tab persistence by mapping it to Knowledge; keep knowledge-only chat turns VM-free.
- **Changes:** Added ranked Postgres FTS with indexable trigram matching, body snippets, explicit empty results, and targeted `/data` corpus paths; realigned the content GIN and summary-title trigram indexes through a concurrent migration; ordered Knowledge saves by `content_knowledge_saves.saved_at` and exposed that value as `knowledge_saved_at`; registered one host-managed `search_knowledge` tool with complete URL/type results and durable start/success/failure progress; merged saves, chats, decks, and narrations under day delimiters with uniform compact 40-point tiles and single-line trailing-truncated titles; moved loading, recovery, pagination, and the materialized timeline projection into `KnowledgeTimelineViewModel`; collapsed navigation to Briefing and Knowledge; rebuilt the compact bar as a two-item morphing capsule; and reduced chat reading type to 13 points through shared SwiftUI and UIKit tokens. The cleanup pass also split timeline rows and store tests, removed duplicate projections and revision counters, deleted stale Learning-root names, made the migration independent of mutable application code, and updated the visual catalog for unified Knowledge while removing the obsolete standalone Learning screenshot.
- **Validation:** Ruff passed on the touched backend and tests; the final focused backend/API/contract slice passed 135 tests, public contract artifacts were current, and the focused client source/contract slice passed 57 tests with one unrelated Learning Deck portrait-chat assertion deselected. The compact-row source regression passed. The iOS app built for the iPhone 17 Pro simulator, and 39 focused Knowledge timeline, Knowledge chat, and generated-contract Xcode tests passed. The full simulator E2E run completed with 43 passes and two failures; the saved-article chat handoff was fixed and passed on focused rerun. All 10 dark-mode screenshot states across the three visual scenarios were freshly recorded and inspected on an iPhone 17 Pro; nine baselines changed, More remained byte-identical, and the normal visual assertion rerun passed 3/3. The compact Knowledge baseline was subsequently refreshed and inspected after the row-density change, then its focused non-recording assertion passed 1/1 after a clean Simulator build. The full simulator suite was not rerun after those fixes. Cleanup-focused AXe tab reselection and Knowledge deck regeneration scenarios passed together. The migration reached local Alembic head, and a local Postgres `EXPLAIN` of the runtime predicate confirmed a `BitmapOr` across the FTS, summary-title, title, and source indexes.
- **Remaining:** The full repository test suite was not run. No deployment or production mutation performed.
- **Commits:** `a672ff4f`.

### 2026-08-23 — `main` — Harden and simplify snapshot-backed VM persistence

- **Status:** Complete locally and committed; canonical template rebuilt; not deployed.
- **Scope:** Persistent VM revision ownership, snapshot/corpus state, agent-data task contracts and fanout, chat runtime naming, feed network policy, tests, and VM design documentation.
- **Decisions:** Keep the host corpus authoritative and root-owned/read-only while leaving `/data/workspace` writable. Use the snapshot's manifest as the only data-revision checkpoint, derive the reusable-runtime revision from the Dockerfile plus host hardening and corpus installation code, and reject E2B feed sessions that cannot enforce network policy.
- **Changes:** Removed write-only VM/index timestamps and the redundant snapshot-revision column/index; made ledger checksums/index records non-null; stopped delta installs from walking the whole corpus; split sync/index/backfill/reconcile payloads; skipped no-op index rewrites; made pending sync coalescing transaction-safe and added one successor for events racing processing syncs; batched content and news corpus/Briefing fanout through shared refresh primitives; renamed per-turn state around `LazyAgentVmRuntime`; and removed the remaining one-use and arithmetic/magic-count indirections.
- **Validation:** The canonical template built in 24 seconds as `newsly-agent-71aaec31f39836e1`. The 247-test focused gate passed; the full suite finished with 2,836 passed and 40 skipped, with only the two documented unrelated Learning Deck assertions failing. Ruff, formatting for 1,057 Python files, MyPy over 499 application modules, public contracts, Compose renders, migration integration, template validation, focused Vulture, and `git diff --check` passed. Live E2B measured 10.37 s cold user activation, 1.03 s memory-resume delta, and 3.27 s snapshot-recovery delta; real chat measured 2.26 s no-tool, 13.18 s cold-tool, and 4.30 s warm-tool with acquisition at 0/8.77/0.85 s; full cold/warm feed discovery measured 18.21/10.41 s with acquisition at 9.07/0.27 s and found the same canonical Atom feed both times. All canary users, sandboxes, snapshots, mirrors, task state, and durable VM IDs were removed.
- **Remaining:** Production p50/p95, E2B compute/storage cost, and drift measurements after deployment. No production mutation was performed.
- **Commits:** `f34a4d98`, `4ee3a2bc`, `a701aa65`, `92fbc9c0`, `0d98a109`, `90360d0e`, and the documentation commit containing this entry.

### 2026-08-23 — `main` — Replace private Volumes with snapshot-backed incremental VM persistence

- **Status:** Complete locally; not committed or deployed.
- **Scope:** User and system E2B lifecycle, revisioned agent-data transfer, template ownership, chat context budgeting, Briefing fanout, bounded public HTTP, migrations, tests, and VM design documentation.
- **Decisions:** Use memory-preserving pause/resume as the warm path and one clean per-user snapshot as recovery for a missing sandbox. Keep the host corpus authoritative, apply full or revision-delta archives under the user row lock, leave the corpus root-owned/read-only, and keep only `/data/workspace` writable. Do not snapshot the corpus-free feed VM or retain an E2B Volume mode.
- **Changes:** Added durable user snapshot fields and typed agent-data identities; extracted lifecycle ownership into `E2BSandboxPool`; added locked full/delta hydration, tombstones, future-revision recovery, commit-before-cache publication, and snapshot cleanup; derived the template revision from the Dockerfile; increased warm pause to keep memory; added complete-turn chat budgeting; batched ready-news visibility/fanout; and unified bounded public fetch/download transport. Live probing exposed E2B's post-build passwordless-sudo grant, so canonical creation now revokes both sudoers entries and group membership before probing, hydration, snapshotting, or model work; failure kills the sandbox.
- **Validation:** The migration integration passes through head `20260823_01`; 318 focused tests pass. The full suite completed with 2,829 passed and 40 skipped; its only failures are the same two pre-existing unrelated Learning Deck source assertions. Ruff, all-Python formatting, MyPy over 499 application modules, the 779-file/23-ratchet module guard, all 134 architecture tests, generated contracts, Compose renders, template revision `newsly-agent-60168587fedaf740`, focused high-confidence Vulture, and `git diff --check` pass. A live user canary proved read-only corpus enforcement, workspace editing, process and file survival across memory pause, delta/tombstone application, clean-snapshot recovery, later-revision-only hydration, sudo revocation, and cleanup: cold 7.96 s, immediate reuse 174 ms, memory resume plus delta 624 ms, snapshot recovery plus delta 9.27 s. Real no-tool/cold-tool/warm-tool chat measured 1.45/12.82/3.55 s with VM acquisition 0/10.12/1.22 s. Cold/warm full feed discovery measured 26.97/10.03 s with VM acquisition 7.74/1.03 s and validated the same canonical Atom feed. Canary cleanup left no temporary users, system VM row, or user VM IDs.
- **Remaining:** Production p50/p95 and cost/storage measurements after deployment. The full local suite retains only the two pre-existing unrelated Learning Deck assertions noted below; no production mutation was performed.
- **Commits:** Uncommitted.

### 2026-08-25 — `main` — Remove Learning Deck agent request caps

- **Status:** Complete and committed; not deployed.
- **Scope:** Learning Deck generation and artifact-repair agent loops.
- **Evidence:** Production deck `23` / task `88` generated both required artifacts, then browser validation timed out and the repair loop was terminated before its ninth model request by the shared request limit of 8.
- **Changes:** Disabled Pydantic AI request-count limits for both the initial Learning Deck run and its artifact-repair run; recorded the no-fixed-request-budget behavior in law K12 and added regression coverage for both phases.
- **Decisions:** Keep the execution deadline and artifact safety/hosting validation as the operational boundaries; they protect worker and hosting reliability without imposing an arbitrary model-turn count.
- **Validation:** All 19 Learning Deck agent tests passed, including the initial and repair request-limit regression. Ruff lint/format, focused MyPy, and `git diff --check` passed. The broader 69-test Learning Deck slice had 68 passes and the pre-existing unrelated public-share viewer-controls assertion failure.
- **Remaining:** The production attempt remains failed until this change is deployed and the deck is retried. No deployment or production retry performed.
- **Commits:** `819a6ff1`.

### 2026-08-23 — `main` — Clean up the VM execution implementation

- **Status:** Complete locally; canonical template rebuilt; application changes not committed or deployed.
- **Scope:** Shared agent VM tools and lifecycle, chat/assistant routing, agent-data projection, feed-discovery helpers, template reproducibility, tests, and initiative documentation.
- **Decisions:** Keep one canonical five-tool vocabulary and one E2B template. Remove only high-confidence dead compatibility and duplicated state; keep the security-sensitive bounded-public HTTP transports separate for now, and defer a truly indexed agent-data ledger query until the ledger stores typed document identity rather than parsing paths.
- **Changes:** Removed unused write-prefix, personal-library error, full-corpus sync, capability-cache, private re-export, and stale probe plumbing; renamed chat routing around the generic VM; centralized tool-event status; bulk-loaded chat transcripts; encoded and hashed each projected document once; narrowed incremental ledger work; validated capability manifests as objects; simplified feed timeout and batch validation paths; pinned the E2B base digest and Playwright 1.62.1; and advanced the canonical revision to `newsly-agent-2026-08-23.2`.
- **Validation:** The 201-test cleanup focus suite passes. The repository suite passes 2,812 tests with 40 skips and the same two unrelated existing Learning Deck source assertions deselected. Ruff, targeted MyPy, high-confidence Vulture, template validation, public-contract checks, `git diff --check`, the 775-file/23-ratchet module guard, and all 134 architecture tests pass. The digest-pinned uncached E2B build succeeded in 120.58 s. A live `.2` canary passed every CLI/browser capability, reported Playwright 1.62.1, acquired cold in 23.05 s and through immediate persistent reuse in 138 ms, preserved its edited file, and left no sandbox after eviction. The earlier end-to-end samples remain 1.20 s no-tool and 10.53/3.05 s cold/warm VM-tool chat, plus 16.20/8.95 s cold/warm feed discovery and a 464 ms warm candidate batch; paid model/Exa paths were not repeated for this behavior-preserving cleanup.
- **Remaining:** Commit and deploy when requested. A shared bounded-public streaming transport, typed/indexed agent-data ledger identities, and set-based Briefing visibility enqueueing are worthwhile separate changes because they alter security-sensitive or broader persistence/query boundaries. No production mutation was performed.
- **Commits:** Uncommitted.

### 2026-08-23 — `main` — Add exact file editing and canonicalize the E2B template

- **Status:** Complete locally; canonical template built; application changes not committed or deployed.
- **Scope:** Shared agent VM tool registration, E2B template ownership, chat capability routing, tests, and VM architecture/laws.
- **Decisions:** Add one bounded `edit_file` tool based on exact text replacement, with a unique-match default and explicit replace-all. Use the code-owned `newsly-agent` template for every E2B workflow; keep a separate code revision solely to replace stale persisted sandboxes after image changes.
- **Changes:** Added policy-aware exact file editing with workspace and write-prefix enforcement. Removed the optional template setting and provider-default capability branch; the builder, session cache, lifecycle state, usage telemetry, and create requests now share one canonical template identity.
- **Validation:** The 127-test focused VM/tool/template/chat suite and repository suite pass: 2,811 passed, 40 skipped, with the same two unrelated existing Learning Deck assertions deselected. Ruff format/lint, targeted MyPy, template-definition validation, and `git diff --check` pass. The canonical E2B template built successfully in 16 s. A live session acquired it in 8.74 s, passed the canonical capability preflight, recorded revision `newsly-agent-2026-08-23.1`, accepted an exact edit, rejected an ambiguous edit without mutation, and was evicted afterward. E2B listed no matching live canary afterward.
- **Remaining:** Commit and deploy the application changes when requested. The earlier isolated image-debugging template remains an unreferenced E2B dashboard artifact because the installed SDK exposes no template-deletion API; no runtime path selects it.
- **Commits:** Uncommitted.

### 2026-08-23 — `main` — Remove configured-feed response-size ceiling

- **Status:** Complete locally; not committed or deployed.
- **Scope:** Configured RSS and Atom transport shared by podcast, newsletter, and aggregator ingestion.
- **Decisions:** Do not impose an arbitrary byte ceiling on accepted feed documents. Retain public-address validation, pinned dispatch, redirect limits, request timeouts, and bounded feed concurrency; retain the existing response limit for callers that do not explicitly opt out.
- **Changes:** Made the hardened public HTTP response limit optional and disabled it only in the shared configured-feed parser. Added regression coverage proving an explicitly unbounded public fetch can exceed the normal 2 MB default, and updated the processing law and architecture boundary.
- **Validation:** 61 focused HTTP/feed/podcast/Atom/Substack/aggregator tests passed. Ruff format and lint checks passed, and targeted MyPy reported no issues. A live local fetch parsed the 6.37 MB Founders feed with 455 entries; the real podcast scraper produced its configured five items headed by episodes #430, #429, and #428.
- **Remaining:** Deploy the change, then run normal ingestion/backfill to recover Founders episodes #428–#430. No production mutation performed.
- **Commits:** Uncommitted.

### 2026-08-23 — `main` — Implement persistent VM execution layer

- **Status:** Complete locally; not committed or deployed.
- **Scope:** Persistent E2B lifecycle for chat/feed discovery, shared VM tools, per-user agent corpus, batched feed probes, host Analyze URL/media transport, tool progress, migrations, tests, and architecture/law updates.
- **Decisions:** Keep product authority and credentials on the host; acquire chat VMs only inside an invoked VM tool; retain one durable sandbox identity per user and one system identity for feed research; auto-pause without killing on turn close; use E2B Volumes opportunistically and a host-pushed credential-free tar fallback otherwise. Keep existing-sandbox users on the fallback rather than risk cross-process remount kills. Reconcile the corpus in bounded ledger pages followed by bounded DB backfill pages.
- **Changes:** Added durable VM/corpus state and migration `20260823_01`; one four-tool VM vocabulary; lazy chat runtime; E2B template builder; incremental/index/backfill/reconcile handlers and event hooks; credential-free markdown/index/manifest mirror; candidate-host network policy with distributed serialization and one-command per-site curl JSONL; bounded host analysis, Apple/feed, and media paths; separate retry-fenced chat tool progress; account-deletion cleanup; and focused coverage. Removed the per-turn E2B personal-library stack and sandbox media downloader. Live validation additionally fixed the builder's `.env` authentication path, removed E2B-invalid and Chromium-breaking deny selectors, added the required deny-all selector for feed host allowlists, resolved the configured Newsly origin to CIDRs because E2B rejects hostnames in `deny_out`, and exposed Playwright through runtime lookup paths that survive E2B command-session environment stripping.
- **Validation:** Checked installed E2B 2.35 signatures and current official lifecycle, template, user/workdir, network-update, filesystem-write, and private-beta Volume contracts. Migration upgrade/downgrade/upgrade and the older-schema migration integration pass. The repository suite passes 2,810 tests with 40 skips and two unrelated existing Learning Deck assertions deselected; the final focused VM/chat/feed/script regression set passes 105 tests. Ruff, changed-file format, MyPy over 588 sources, module-size and 134-test architecture guards, generated public contracts, Compose rendering, template definition validation, and `git diff --check` pass. A live isolated template build and final cache rebuild succeeded. Live E2B passed all required CLI/browser capabilities, resolved-origin denial, deny/exact-allow/reset feed egress, file persistence, immediate reuse, explicit pause, 60-second auto-pause, and resume. No-tool/default-model chat took 1.20 s with zero E2B acquisition; cold/warm VM-tool chat took 10.53/3.05 s. Full cold/warm feed discovery took 16.20/8.95 s, and a warm two-candidate batch took 464 ms. The pre-existing local system-sandbox record was left unchanged and every canary sandbox/user/mirror was removed.
- **Remaining:** This E2B team returns `403: use of volumes is not enabled`; live `auto` mode correctly fell back to a 494 ms credential-free tar hydration, but Volume mount/quota and Volume billing remain unverified until the team gains entitlement. E2B's provider-managed `169.254.169.254` metadata endpoint bypasses deny-all; it exposed no IAM role in the canary, but this provider exception should be rechecked when E2B's network controls change. Production p50/p95, dashboard vCPU-hours, pause billing, and corpus drift still need post-deployment measurement. The untouched Learning Deck portrait-chat source assertion and hosted-controls marker-value assertion remain independently failing; they are outside this initiative.
- **Commits:** Uncommitted.

### 2026-08-23 — `main` — VM execution layer plan (E2B, chat + feed discovery)

- **Status:** Complete (plan only; no implementation)
- **Scope:** `docs/initiatives/vm-execution-layer-2026-08/plan.md`, superseding the 2026-08-22 draft. Design review of chat E2B lifecycle and pipeline efficiency preceded it.
- **Decisions:** E2B stays; scope is chat and feed discovery only — `analyze_url` and media download come off E2B. One sandbox per user with `sandbox_id` on the user row, `connect`-or-`create`, auto-pause via `lifecycle` at 300 s, no timeout refresh, no kill on close. Per-turn library rsync replaced by a per-user E2B Volume maintained on content events (markdown tree for all user-visible data + `newsly.sqlite` index), mounted at `/data`; pull-tarball fallback if volumes are unavailable. General-purpose template, Exa via `exa-py` + env; no Newsly helper scripts. Self-hosted runtimes (forkd, smolvm, gVisor, flint) and hosted alternatives (Fly Machines, Northflank) evaluated and deferred; production host is AlmaLinux 8 / kernel 4.18, which blocks gVisor and most microVM runtimes without an OS upgrade.
- **Changes:** Plan document only.
- **Validation:** Findings spot-checked in source; SDK capabilities (`lifecycle`, `Volume`, `write_files`, `connect`) confirmed in the installed `e2b` 2.35; E2B dashboard cost evidence ($109.62, 1,646 vCPU-h, ~58 min billed per sandbox) recorded in the plan. Latency figures remain estimates pending step 1 instrumentation.
- **Remaining:** All nine plan steps; verify volume/pause pricing on the E2B plan before step 5.
- **Commits:** Uncommitted

### 2026-08-15 — `main` — Prevent stale Briefing retries after reopen

- **Status:** Complete.
- **Scope:** Briefing iOS lifecycle state, selected-lens pagination, refresh polling, focused native regressions, and the Briefing product law.
- **Decisions:** Preserve selected-lens hydration while the app is merely inactive, but replace it if it is still unfinished when the app becomes active again. Treat deactivation as the boundary that clears action-level refresh failures, and require every asynchronous poll result to still own its task generation before changing UI state.
- **Changes:** Restart unfinished selected-lens work from its preserved cursor on reactivation, clear non-idle refresh phases on deactivation, reject late results from cancelled refresh polls, and add deterministic request suspension/error controls plus three lifecycle regressions.
- **Validation:** All three regressions failed against the prior implementation and passed after the fix; the selected-lens regression also passed while index validation was deliberately blocked. Seventy-four focused Briefing lifecycle, navigation, retention, and read-state tests passed on the iOS 26.4.1 iPhone 17 Pro Simulator. The final tree built, installed, and launched successfully through XcodeBuildMCP, and `git diff --check` passed.
- **Remaining:** None for implementation. The available Simulator is logged out, so a direct visual reopen check of the authenticated Briefing surface still requires a signed-in runtime.
- **Commits:** Uncommitted.

### 2026-08-20 — `main` — Decouple configured feed ingestion from E2B

- **Status:** Complete and committed; not deployed.
- **Scope:** Scheduled RSS, Atom, Substack, podcast, fixed aggregator, and pipeline-time publisher RSS retrieval.
- **Changes:** Routed steady-state configured-source downloads through the shared bounded application HTTP client while retaining E2B for new-feed discovery and validation. Updated the processing law, architecture contract, and focused transport tests.
- **Decisions:** Treat sandboxing as a discovery boundary rather than a prerequisite for routine ingestion; keep downloaded feed/page bytes as inert parser input and preserve per-source error isolation.
- **Validation:** All 53 focused configured-feed, aggregator, publisher-RSS, and podcast-worker tests passed; all 104 focused E2B discovery/validation tests passed; the 137-test architecture guard and 759-file module-size guard passed; touched-file Ruff lint, format, and MyPy passed; `git diff --check` passed. A live normal-process Techmeme fetch parsed 15 entries with no feedparser error and did not acquire E2B.
- **Remaining:** Production will retain the old E2B-coupled ingestion path until this repository change is deployed. No commit, push, deployment, or production mutation requested.
- **Commits:** Uncommitted.

### 2026-08-20 — `main` — Consolidate behavioral laws

- **Status:** Complete locally.
- **Scope:** The eight canonical product-law areas and their documentation index.
- **Changes:** Reduced 126 rules to 96 by merging overlapping invariants and removing implementation-level UI, provider, and lifecycle detail. Added a standing target of 10 to 20 laws per area.
- **Decisions:** Preserve user-visible outcomes, ownership, durability, safety, and recovery guarantees. Keep architecture, provider names, and narrow interaction mechanics in their owning documentation.
- **Validation:** Confirmed 96 sequential laws with 10 to 15 in every area, all index links resolve, and no implementation files, provider names, banned contrast patterns, or stale individual-law references remain. The 759-file module-size guard, all 137 architecture tests, public-contract check, and `git diff --check` passed.
- **Remaining:** No commit, push, deployment, or production mutation requested.
- **Commits:** Uncommitted.

### 2026-08-15 — `main` — Refine Learning Deck reader controls

- **Status:** Complete.
- **Scope:** Portrait deck-chat flyover, hosted Reveal viewer selection and fullscreen chrome, focused tests, Maestro flow, and Learning product law.
- **Decisions:** Keep tap-to-open while adding the expected upward-swipe affordance; make selection visibly emerald without changing deck typography; remove the redundant nested web fullscreen mode while retaining Reveal slide navigation.
- **Changes:** Increased the collapsed chat flyover height, added a vertical swipe recognizer with focused native coverage, strengthened light/dark selection colors, removed the fullscreen button and its JavaScript, and changed the portrait-chat Maestro path to open by swipe.
- **Validation:** Ruff and formatting passed; 23 focused Python viewer/theme tests passed; 5 focused native Learning Deck tests passed on the iOS 26.5 iPhone 17 Pro Simulator; the app built and launched successfully; `git diff --check` passed. The focused Maestro scenario was discovered but skipped because Maestro is not installed locally.
- **Remaining:** Run the updated focused Maestro scenario when Maestro is available for direct end-to-end gesture and screenshot proof.
- **Commits:** This commit.

### 2026-08-15 — `main` — Learning Deck share instructions

- **Status:** Complete.
- **Scope:** Share Extension Create Deck UI, Share Action handoff, Learning Deck VM prompt, focused contracts, and product laws.
- **Decisions:** Reuse the persisted `interests_prompt` field for compatibility while presenting it as general user instructions; explicit user text outranks intermediate agent output.
- **Changes:** Added an optional instructions editor for Create Deck and taught the VM prompt to honor capture, comparison, and investigation requests with source notes.
- **Validation:** Ruff and formatting passed; 93 focused backend/client tests passed, including 11 iOS boundary tests; ShareExtension/main-app Xcode Simulator build passed. Manual AXe inspection on iPhone 17 Pro verified selection, editing, keyboard/accessory reachability, and visual layout. The focused AXe live submission reached the request and recoverable error path, but its ephemeral `127.0.0.1` server was unreachable from the extension process, so no live DB row was asserted in that run.
- **Remaining:** None for implementation; the local extension-to-ephemeral-server harness connection remains an environment limitation if that exact live E2E proof is required later.
- **Commits:** Uncommitted.

### 2026-08-13 — `agent/use-luna-for-fast-llm-defaults` — Route fast LLM defaults to Luna

- **Status:** Complete
- **Scope:** Shared cheap-model routing, Briefing, agent VM tasks, Learning Decks, generated audio scripts, public contracts, and current architecture documentation.
- **Decisions:** Keep DeepSeek Flash available as an explicit OpenRouter model and eval target, but move product features that consume the shared cheap/default tier to `openai:gpt-5.6-luna`.
- **Changes:** Updated the canonical cheap model and replaced direct DeepSeek feature defaults with that shared tier; aligned focused tests, model-dependent fixtures, generated public contracts, and current architecture documentation.
- **Validation:** `pytest tests/ -q`; focused model-default and provider-routing tests; `ruff check .`; public contract regeneration/check; `git diff --check`.
- **Remaining:** None.
- **Commits:** Included in this branch tip.

### 2026-08-08 — `main` — Verify the real Share Extension surface and reconcile client architecture docs

- **Status:** Complete
- **Scope:** Actual UIKit Share Extension presentation from Safari, all four visible outcome selections, Chat input gating, offline recovery, and stale client/Share documentation.
- **Decisions:** Use AXe for HID dispatch and screenshots for state verification because this Simulator exposes only Safari—not the embedded extension subtree—in its accessibility tree. Keep onboarding acceptance claims compositional: deterministic UI/API orchestration plus separate E2B boundary tests and live sandbox canaries, not a single UI-to-live-E2B run.
- **Changes:** Updated `docs/architecture.md` and the client overview to remove deleted discovery/quick-mic client surfaces and describe the current Briefing, Knowledge, Deck, and Chat outcomes. No product code changed.
- **Validation:** On iOS 26.3.1, Safari shared `https://example.com/axe-share-extension` through the system share sheet into Newsbuddy. Screenshots verified the default Add to Briefing state, selectable Add to Knowledge and Create Deck states, Chat disabled with an empty required first message, Chat enabled after typing, and a failed submission retaining the prompt while offering Cancel/Try Again instead of dead-ending. Evidence is retained under `/tmp/newsly-share-extension-probe.brcvmv`; focused prelaunch AXe recovery passed 1/1 and `git diff --check` passed.
- **Remaining:** The encompassing task still requires the quota-gated final Oracle Fable verdict. Successful backend execution for each mode remains covered by transport/state/backend tests rather than this intentionally offline UI probe.
- **Commits:** Uncommitted; no push, deployment, or production change was performed.

### 2026-08-08 — `main` — Final whole-app acceptance pending Oracle Fable reset

- **Status:** In progress
- **Scope:** Whole-app interaction, E2B feed/sandbox, voice, Share Extension, navigation/recovery, migration, queue durability, simplification, and dead-end acceptance after the nested review and remediation pass.
- **Decisions:** Keep the exact test-passed tree frozen while waiting for the explicitly required Fable review; do not substitute another model, bypass the account limit, or add speculative cleanup. Trusted provider/control-plane APIs remain host-managed, while every untrusted page/feed candidate is fetched and validated in E2B.
- **Changes:** The encompassing implementation now includes the verified E2B boundary and lifecycle fixes, truthful feed/backfill outcomes, owned-task and chat durability fixes, Share and Learning recovery, reduced SwiftUI render-path work, and deterministic voice metering/onboarding/podcast cancellation coverage described by the entries below.
- **Validation:** The warning-visible backend suite passed 2,627 tests with zero warnings; Ruff, mypy, architecture, contract, migration, Go, Vulture, compile, lock, duplicate-test, and diff checks passed. Native testing passed 476/476 on iOS 26.3.1. The retained dark-mode Maestro catalog passed 19/19 on iOS 26.5 in 324.53 seconds. The second-simulator AXe matrix passed 12/12 in 84.84 seconds and produced 62 non-empty screenshots plus 62 matching accessibility-state JSON files. Live E2B canaries verified command execution, file round-trip/listing, and Daring Fireball feed discovery inside E2B with process-cache drain afterward.
- **Remaining:** The final read-only Oracle Fable pass could not start because the configured Claude.ai Max account returned HTTP 429 with a 03:30 PDT reset. After reset: rerun Fable against this exact tree, reproduce and narrowly fix only concrete P0–P2 findings, run proportional gates if code changes, add a completion follow-up, and perform the final diff/status check. Physical-device microphone permission, acoustic threshold, route/interruption, Bluetooth/headset, and live transcription-quality checks remain manual limitations.
- **Commits:** Uncommitted; no push, deployment, production configuration change, or authentication change was performed.

### 2026-08-07 — `main` — Make every ingestion feed-sandbox seam deterministic in tests

- **Status:** Complete
- **Scope:** Analyze URL Apple podcast resolution, podcast-media Apple publisher RSS resolution, and content-worker extracted feed-link detection.
- **Decisions:** Unit/integration tests inject a fake feed runtime and assert its exact user/execution identity plus detector/HTTP-service handoff. Live E2B remains a separate canary; ordinary unit tests must not acquire or reuse a real paid sandbox.
- **Changes:** Added three wiring regressions and removed accidental real/cached E2B acquisition from the Apple podcast worker test.
- **Validation:** 27 tests across the three affected pipeline modules passed; focused Ruff, mypy across all three production callsites, and `git diff --check` passed.
- **Remaining:** The encompassing app task owns the final full suite and simulator gates.
- **Commits:** Uncommitted

### 2026-08-07 — `main` — Make Hacker News provider failures truthful

- **Status:** Complete
- **Scope:** Hacker News top-list and per-story provider failure accounting plus sandbox-boundary documentation.
- **Decisions:** Keep the fixed Hacker News Firebase JSON API host-managed as an explicit trusted provider/control-plane API. The aggregator does not fetch linked publisher pages; those remain subject to their owning processing boundary. Preserve partial story progress while exposing every provider failure in `ScraperStats`.
- **Changes:** Added HTTP status validation and scrape-error recording for top-list and per-story calls; documented the provider exception and publisher-page boundary; added full-outage and partial-progress regressions.
- **Validation:** 9 focused aggregator/registry/shim tests passed; Ruff, focused mypy, and `git diff --check` passed.
- **Remaining:** The encompassing app task owns the final repo-wide and simulator gates.
- **Commits:** Uncommitted

### 2026-08-07 — `main` — Recover failed Share submissions without a product dead end

- **Status:** Complete
- **Scope:** Submission detail recovery for terminal Share Action, legacy Share Sheet, and feed-subscription failures.
- **Decisions:** Treat this status feed's self-submission flag as the canonical recovery boundary instead of coupling UI behavior to current or legacy `submitted_via` spellings. Preserve no-action rationale behavior; expose only validated HTTP(S) URLs; keep non-self assistant/X paths and unsafe URLs unrecoverable.
- **Changes:** Extended `SubmissionStatusItem.recoveryURL` to self-submission errors, added concise retry guidance and the stable `submission.retry` accessibility identifier, retained `submission.no_action.retry`, and added native/source-contract/AXe regressions.
- **Validation:** 13 focused backend/client contract tests passed; Ruff and `git diff --check` passed; 13 `SubmissionStatusViewModelTests` passed in `/tmp/newsly_share_recovery.xcresult`; the current-build AXe failure-recovery flow passed with asserted UI state, screenshot evidence, and the system Share sheet in `/tmp/newsly_axe_failed_recovery_20260807_2127`.
- **Remaining:** The encompassing app task owns the final full native, combined Maestro/AXe, multi-simulator, and Oracle Fable gates.
- **Commits:** Uncommitted

### 2026-08-07 — `main` — Retire dead backend surfaces and enable E2B chat libraries

- **Status:** Complete
- **Scope:** Discovery HTTP retirement and generated contracts, analyze-URL workflow injection, Learning Deck legacy execution, chat personal-library sandbox/runtime, architecture notes, and focused tests.
- **Decisions:** Remove the zero-caller `/api/discovery` product surface while preserving scheduled feed discovery, weekly discovery chat projection, and onboarding discovery. Replace five one-method analyze-URL protocols with direct bound-method callables. Retire the legacy Learning Deck generator/dedicated E2B session only after the local PostgreSQL snapshot showed four completed runs, zero non-terminal legacy rows, and zero linked active runs missing workspaces; retain historical run tables, artifact pointers, and sandbox metadata for presentation. Make E2B the source default for chat personal-library tools while retaining explicit local/disabled rollback modes.
- **Changes:** Deleted the discovery router, API-only DTOs/commands/repository/tests and regenerated Swift/Go/OpenAPI artifacts; collapsed analyze-URL injection; removed `learning_deck_generation.py`, `learning_deck_sandbox.py`, legacy settings, and obsolete tests; required canonical `llm_tasks` workspaces for new decks; hardened E2B chat sandbox initialization/hydration cleanup; centralized the duplicated personal-library tool trio in `chat_turn_runtime.py`; added current and historical sandbox cost visibility.
- **Validation:** Ruff passed on all touched Python; 131 focused discovery-boundary, Learning Deck, chat/sandbox, analyze-workflow, weekly-discovery, and app tests passed; both analyze-URL feed-subscription suites passed (12 tests) through their deterministic E2B boundary; generated public contracts are current. Deterministic E2B tests cover hydration, bounded search/list/read, close, and kill-on-bootstrap-failure. The existing ignored local/runtime env files still explicitly select `disabled`; no production config mutation or deployment was performed.
- **Remaining:** The encompassing app task owns full-suite/native/AXe gates and the explicit production config switch to `CHAT_SANDBOX_PROVIDER=e2b` when accepting per-turn E2B cost.
- **Commits:** Uncommitted

### 2026-08-07 — `main` — Move feed research and validation into E2B

- **Status:** Complete
- **Scope:** Every untrusted feed-specific page/RSS path: assistant and mixed Search discovery, weekly discovery, onboarding suggestions, add-feed resolution, scraper-config validation, ingestion detection, scheduled RSS scrapers/aggregators, Apple publisher RSS, content-analyzer RSS media lookup, VM lifecycle, and product-state projection.
- **Decisions:** Keep trusted provider/control-plane APIs (Exa, Apple/iTunes, Spotify, Podcast Index, and models) host-managed, but fetch any untrusted page or publisher-feed URL they return only through E2B. Require the canonical `FeedDetector` to receive injected HTTP and construct it only in the E2B runtime. Checkpoint a paid discovery run once per active Sunday-based weekly window so projection retries cannot rebill.
- **Changes:** Added the sandbox HTTP adapter/runtime and migrated all feed-specific fetchers to it; removed host-HTTP detector fallbacks; reused user-scoped sandboxes with task-isolated scratch paths; hardened cache acquisition, poisoned-session eviction, bootstrap cleanup, and shutdown drain. Discovery now preserves a completed paid run across projection retries, marks canonical active subscriptions in discovery and mixed Search, and onboarding completion enqueues the canonical `discover_feeds` task once when missing. Generated public contracts expose mixed-search `is_subscribed` state.
- **Validation:** Repository-wide `ruff check app tests` passed; 256 consolidated feed, onboarding, mixed-search, scraper, worker, lifecycle, router, and adjacent backend tests passed; public contracts are current. Focused feed-source mypy passed before the final integration sweep; the final expanded rerun is currently stopped only by a concurrent `share_actions.py` local-variable redefinition outside this scope. A configured live E2B canary fetched `lucumr.pocoo.org` inside the sandbox and resolved `https://lucumr.pocoo.org/feed.atom` as Atom. A two-thread live reuse canary produced one sandbox (`reused=false/true`) with isolated per-task marker files; all canary and accidentally created test sandboxes were explicitly killed or drained.
- **Remaining:** The encompassing app task owns the final full-suite/native/AXe gates and production rollout. Production must have valid E2B credentials/network access; no deployment or runtime configuration was changed here.
- **Commits:** Uncommitted

### 2026-08-07 — `main` — Post-audit feed truth and race hardening

- **Status:** Complete
- **Scope:** Strict feed-sandbox provider boundary, scraper/backfill outcome truth, feed URL identity, discovery telemetry, duplicate submission finalization, and scraper batch races.
- **Decisions:** Reject non-E2B sessions even when the generic VM setting selects local; keep independent feed failures isolated while exposing them in the owning run; treat `errors > 0` with no saved/duplicate items as a failed backfill; use one canonical URL identity before paid validation. Preserve the existing fast batch transaction plus isolated retry because it is behaviorally equivalent to per-row savepoints for a losing URL race.
- **Changes:** Added strict provider rejection; propagated Atom/Substack/Podcast fetch exceptions into `ScraperStats`; made onboarding and Analyze URL backfills report error-only results as unavailable/failed; corrected agent tool-call telemetry to read `new_messages()`; centralized canonical feed URL normalization and short-circuited existing subscriptions before sandbox validation. The shared tree's duplicate-submission race path now re-enters canonical existing-row finalization so inbox, Knowledge, and read state are applied; batch persistence retries each item independently after a conflict so later items survive.
- **Validation:** 302 consolidated feed/onboarding/search/scraper/worker/router tests passed; 97 focused truth/provider/canonical/race tests passed; targeted Ruff passed; mypy passed across 16 affected sources; public contracts and `git diff --check` passed. The duplicate-finalization and mid-batch conflict regressions passed in the focused set. A full backend run before the final hardening changes reached 2,362 passed and 16 skipped with one unrelated concurrent iOS wire-model-manifest failure.
- **Remaining:** The encompassing task owns final repo-wide lint/full-suite, native/AXe gates, and rollout. No deploy or production configuration change was made.
- **Commits:** Uncommitted

### 2026-08-07 — `main` — Full-app design assessment sweep (AXe)

- **Status:** Complete
- **Scope:** Assessment only, no code changes. AXe-driven simulator sweep of every reachable screen (29 screenshots, light + dark): tabs, item details, comments, More-sheet utilities, all Settings sections and sub-screens, sources lists, error states.
- **Decisions:** Findings centralized into a prioritized small-improvements list (P1 rough edges / P2 consistency / P3 investigate) rather than piecemeal fixes; report delivered to the user (session scratchpad `design-assessment-2026-08-07.md`).
- **Changes:** None (report only). Headline P1 items: unify error voice + retry label, stop leaking backend error strings in Submissions, humanize Processing Stats intervals, drop always-on "Active" pills, fix Recently Read missing-thumbnail alignment, unify date formats, reader top scrim for the floating back chevron.
- **Validation:** All findings backed by screenshots taken this session on iPhone 17 Pro sim against the local dev server.
- **Remaining:** Implement the list (none started); investigate the empty accessibility tree observed via AXe describe-ui (possible bridge glitch, VoiceOver smoke test recommended).
- **Commits:** Uncommitted

### 2026-08-07 — `main` — AI-services disclosure added to Settings legal section

- **Status:** Complete
- **Scope:** `Views/Settings/SettingsLegalSection.swift`.
- **Decisions:** User asked to surface the landing card's legal block in-app; Privacy/Terms/Support links already existed under Settings → Legal & Support, so the missing piece was the AI-services disclosure sentence, added as a quiet footnote under the links card (same wording as the landing consent text, minus the agree clause which only belongs at sign-in).
- **Changes:** Footnote Text (`appCaption`, `onSurfaceTertiary`) below the legal links card.
- **Validation:** Debug build; navigated Knowledge → More → Settings in the simulator and screenshotted the section.
- **Remaining:** None.
- **Commits:** Uncommitted

### 2026-08-07 — `main` — Mascot purple added to onboarding ambient wash

- **Status:** Complete
- **Scope:** Follow-up to the 2026-08-06 amber-monochrome pass: `DesignTokens.swift`, `WatercolorBackground.swift`.
- **Decisions:** Per user request, the cool depth blob in the watercolor background is now a muted mascot purple instead of `surfaceContainerHigh` slate — new token `onboardingAmbientMascot` (`#cfc0ed` light / `#45376d` dark). Amber remains dominant; purple is the single cool note tying the background to the mascot artwork.
- **Changes:** Added the token; swapped the third blob in both the animated and reduce-motion static backgrounds.
- **Validation:** Debug build + landing screenshots in light and dark on iPhone 17 Pro sim. `tests/client/test_ios_spacing_tokens.py`: 28 passed, only the pre-existing `test_cached_async_image_fades_use_motion_tokens` failure.
- **Remaining:** None.
- **Commits:** Uncommitted

### 2026-08-06 — `main` — Onboarding/landing amber-monochrome design pass

- **Status:** Complete
- **Scope:** iOS onboarding + landing visual identity: `DesignTokens.swift` onboarding tokens, `WatercolorBackground.swift`, `LandingView.swift`, `OnboardingMicButton.swift`, `OnboardingLoadingStep.swift`, `tests/client/test_ios_spacing_tokens.py`.
- **Decisions:** Aligned the first-run flow with the app's single-amber-accent doctrine (user chose "amber monochrome, both modes"). Onboarding surface/text now match reader palette charcoal/slate; selection accent is `brandPrimary` amber (was green); the four ambient blob hues (blue/peach/green/sky) became an amber tonal ramp (amber/bronze/cream) plus one cool `surfaceContainerHigh` slate blob for depth. Title glow no longer cycles four hues — fixed amber (`WatercolorBackground.titleGlow`). White neumorphic sheen on the mic button and its shadow drops to ~20% strength in dark mode.
- **Changes:** Token repaint in `DesignTokens.swift`; blob palette + glow simplification in `WatercolorBackground`; mic stop-icon/pulse ring moved from salmon ambient to the amber accent; finalizing sparkle gradient collapsed to solid amber; landing static/animated glow unified.
- **Validation:** Debug build + full onboarding walkthrough on iPhone 17 Pro sim (E2E launch args + onboarding fixture, local dev server) with screenshots of landing, choice, audio, suggestions, aggregators, and Reddit steps in light and dark. `tests/client/test_ios_spacing_tokens.py` passes except pre-existing `test_cached_async_image_fades_use_motion_tokens` failure from uncommitted `CachedAsyncImage.swift` edits that predate this session.
- **Remaining:** `ios_onboarding_personalized` Maestro flow and dark visual baselines not re-run; onboarding screens are not in the baseline set so no baseline updates expected.
- **Commits:** Uncommitted

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
### 2026-08-02 — `willem/bound-admin-log-memory` — Bound remote log-query memory

- **Status:** Complete
- **Scope:** Production incident diagnosis plus `admin.remote_ops` structured-log readers and focused admin/queue tests.
- **Decisions:** Treat the simultaneous Docker DNS and lease-heartbeat failures as host-memory-pressure symptoms, not a PostgreSQL outage or network recreation. Keep the existing lease fencing and retry policy; fix the unbounded operator log reads that materially contributed to the host-wide OOM.
- **Changes:** Stream log range/search records and stop at the requested limit, retain only the bounded structured tail, and use a bounded heap for newest exception results instead of materializing the full JSONL corpus.
- **Validation:** Production was inspected read-only: the kernel recorded a global OOM at `2026-08-02T17:05:02Z`, Docker health-check execs timed out simultaneously, PostgreSQL stayed up with zero restarts, and the four affected tasks later completed. Ruff passed for `admin` and `tests/admin`; 79 admin/deployment tests and 54 focused admin/heartbeat tests passed; targeted mypy and `git diff --check` passed.
- **Remaining:** Deploy through the normal workflow, then monitor host OOM records and remote log-command RSS; no production mutation, restart, commit, or push was performed here.
- **Commits:** Included in this commit.

### 2026-08-02 — `willem/bound-admin-log-memory` — Remote log-query cleanup follow-up

- **Status:** Complete
- **Scope:** Review and behavior-preserving cleanup of the bounded remote log readers.
- **Decisions:** Preserve global timestamp ordering for structured tails instead of assuming logger/PID filenames are chronological; leave log-retention policy and operator-container isolation for separate review.
- **Changes:** Replaced the filename-order tail deque with bounded timestamp selection and strengthened the regression across deliberately misordered filenames.
- **Validation:** Ruff passed for `admin` and `tests/admin`; 117 admin, deployment, and heartbeat tests passed; targeted mypy and `git diff --check` passed.
- **Remaining:** Define retention for 3.1 GB of indefinitely retained production JSONL logs and consider running remote operator commands in a resource-limited one-off container.
- **Commits:** Included in this commit.

### 2026-08-02 — `main` — Expand implementation principles

- **Status:** Complete
- **Scope:** Root agent guidance for simplicity, compatibility, modularity, and dependency reuse.
- **Decisions:** Remove obsolete internal paths by default, but retain explicitly owned compatibility for active external contracts and staged migrations so deployed clients and data transitions remain safe.
- **Changes:** Added rules favoring the simplest complete implementation, clear component boundaries, existing dependency capabilities, and established libraries that reduce overall complexity.
- **Validation:** Reviewed the resulting Markdown and ran `git diff --check`.
- **Remaining:** None.
- **Commits:** Included in this commit.

### 2026-08-02 — `main` — Restore X API module-size compliance

- **Status:** Complete
- **Scope:** Release-gate remediation for the X API module-size guardrail.
- **Decisions:** Keep the public `app.services.x_api` import surface intact while moving its response value objects to the existing `x_models` owner.
- **Changes:** Relocated X user, tweet, token, fetch-result, and page dataclasses into `x_models`; `x_api` re-exports the imported names for compatibility.
- **Validation:** Focused Ruff, 31 X API/integration tests, module-size guardrails, and the commit-time mypy check passed.
- **Remaining:** Complete the release gates and production deployment.
- **Commits:** Included in this release.

### 2026-08-04 — `main` — Simplify Knowledge image loading

- **Status:** Complete
- **Scope:** Shared cached-image presentation and Knowledge saved-row artwork.
- **Decisions:** Keep progressive thumbnail-to-full loading for large artwork, but make compact Knowledge rows select one thumbnail-first URL; remove implicit image fades and the Knowledge-only saturation pass so cached and downloaded images render identically.
- **Changes:** Removed shared image-assignment animation, reduced Knowledge artwork to one request path, and removed the now-unused list-thumbnail color treatment.
- **Validation:** The pre-change seeded Knowledge visual flow passed. After the change, all 3 `ImageCacheServiceTests` passed, the iOS Simulator build succeeded, and the same seeded primary-tabs/Knowledge visual flow passed. One intermediate Maestro attempt lost its XCUITest driver connection before reaching the app assertion; the clean retry passed. `git diff --check` passed.
- **Remaining:** None.
- **Commits:** Uncommitted.

### 2026-08-04 — `main` — Validate personalized onboarding with real processing

- **Status:** Complete
- **Scope:** Personalized iOS onboarding, AXe Simulator runs, live local backend discovery, fake speech input, and repeated-run reliability.
- **Decisions:** Use the existing deterministic fake speech transcriber while preserving the real onboarding API, queue, provider, persistence, backfill, and first-edition paths; suppress notification authorization only in debug E2E launches so stale system permission dialogs cannot interrupt repeated automation.
- **Changes:** Added an E2E-only guard around the post-onboarding notification permission request. Production permission behavior is unchanged.
- **Validation:** Three signed Simulator runs completed through AXe against the local API and workers with distinct transcripts. All three discovery runs completed; 40 selected source configurations were persisted in total; each user reached the Welcome Briefing and queued a first edition. Onboarding copy and generated lane/source text remained readable, with only intentional single-line truncation for unusually long generated source names.
- **Remaining:** None.
- **Commits:** Uncommitted.

### 2026-08-06 — `main` — Scroll active root tab to top on reselection

- **Status:** Complete locally.
- **Scope:** iOS compact root-tab selection and the Briefing, Knowledge, and Learning scroll containers.
- **Decisions:** Treat only a tap on the already-selected compact tab as a scroll request; preserve ordinary tab switching and target only the active Briefing lens.
- **Changes:** Routed per-tab request counters from the app shell into each root scroll container and added reduced-motion-aware animated jumps to stable top anchors.
- **Validation:** Pending focused iOS build and tests.
- **Remaining:** Run validation and inspect the final diff.
- **Commits:** Uncommitted.

### 2026-08-06 — `main` — Complete root-tab reselection scrolling

- **Status:** Complete.
- **Scope:** Final validation of the iOS active-tab scroll-to-top behavior.
- **Decisions:** Keep the request counter in `TabCoordinatorViewModel` so same-tab selection semantics are independently testable; leave unrelated cached-image contract drift untouched.
- **Changes:** Added focused coordinator regression coverage and made the existing source contract resilient to the expanded `BriefingView` initializer.
- **Validation:** The iOS Simulator build succeeded; all 7 `TabCoordinatorViewModelTests` passed; 54 of 55 client contract tests passed, with the sole failure belonging to pre-existing `CachedAsyncImage` fade-removal work; `git diff --check` passed.
- **Remaining:** None for this feature.
- **Commits:** Uncommitted.

### 2026-08-07 — `main` — Preserve source context in article chats

- **Status:** Complete.
- **Scope:** iOS article/podcast chat timeline presentation.
- **Decisions:** Treat linked-content context as part of the conversation timeline rather than an empty-chat placeholder, so it remains available after the first turn and in reopened chats.
- **Changes:** Render the existing article preview card ahead of populated linked-content timelines and expose a stable accessibility identifier for regression coverage.
- **Validation:** The iOS Simulator build succeeded; the focused Maestro-backed populated article-chat/council flow passed with the source-context assertion; `git diff --check` passed for the touched files.
- **Remaining:** None.
- **Commits:** Uncommitted.

### 2026-08-07 — `main` — Make backend product outcomes truthful and subscriptions immediate

- **Status:** Complete for the backend correctness slice; broader full-app implementation remains in progress.
- **Scope:** Feed subscription orchestration and public contracts, discovery subscription, content-insert race recovery, Share Action Add Links aggregation, onboarding discovery task outcomes, assistant tool telemetry/link formatting, and scraper batch conflict coverage.
- **Decisions:** Reuse the queue's caller-owned transactional batch enqueue and existing backfill task; return `created` or `already_subscribed` without treating idempotency as an HTTP error; preserve Analyze URL's existing synchronous initial-download evidence while routing creation through the canonical command; treat persisted onboarding provider failures and all-failed Add Links runs as terminal non-success outcomes rather than retrying paid discovery blindly.
- **Changes:** Added a canonical idempotent subscription command that atomically creates a config and deduplicated first-backfill task, exposed optional subscription outcome/task fields across OpenAPI, Swift, and Go, converged discovery and detected-feed creation, finalized race-losing submissions through the normal existing-row path, persisted structured partial/all-failed Add Links results, propagated onboarding discovery service failures to queue results, and extracted assistant tool names from canonical Pydantic message parts.
- **Validation:** Ruff passed for all touched Python and test files; 70 focused router/service/pipeline tests passed; 63 assistant/chat/submission-status compatibility tests passed; 67 contract tests passed; 12 discovery/scraper-batching tests passed; public contract drift check and all Go CLI tests passed; `git diff --check` passed.
- **Remaining:** Full-app integration, native/AXe/Maestro validation, live provider/E2B canaries, physical-device voice checks, and Oracle Fable review are owned by the encompassing implementation goal.
- **Commits:** Uncommitted.

### 2026-08-07 — `main` — Resolve Add to Briefing targets in the E2B Share Action

- **Status:** Complete for the backend and public-contract slice.
- **Scope:** Composite Share Extension Add to Briefing mode, E2B result schema and prompt, host-side application, and regression coverage.
- **Decisions:** Let the E2B Share Action resolve a shared URL to one discriminated target: a validated continuing feed, an individual Briefing-eligible item, or no action. Do not ingest an arbitrary shared homepage as a fallback, and keep final persistence in the existing subscription and content commands.
- **Changes:** Added the `add_to_briefing` mode and prompt, typed feed/content target models, a single host action applicator, feed resolution through the existing subscription pipeline, individual-item resolution through normal inbox ingestion, and explicit missing-target/no-action handling. Regenerated OpenAPI, Swift, and Go artifacts.
- **Validation:** 29 focused Share Action tests passed; 35 combined Share Action and iOS contract-boundary tests passed; 54 public-contract tests passed; public contract drift and all Go CLI tests passed; the full non-iOS-E2E Python suite passed with 2,368 tests and two warnings; `git diff --check` passed.
- **Remaining:** Native Share Extension, AXe/Maestro, physical-device voice, live E2B canaries, and Oracle Fable verification are owned by the encompassing implementation goal.
- **Commits:** Uncommitted.

### 2026-08-07 — `main` — Harden iOS voice sessions and accessibility automation

- **Status:** Complete for the iOS voice and accessibility slice.
- **Scope:** Shared dictation ownership, every live voice consumer, deterministic voice testing, unreachable discovery-personalization UI, and stable screen identifiers across authentication, onboarding, Briefing, Knowledge, Learning, More, Settings, content detail, and article reader surfaces.
- **Decisions:** Reserve one exclusive speech session synchronously before permission or recorder work awaits; make cancel release ownership immediately; keep recording deadlines and lifecycle cleanup in the provider; drive E2E success, empty, failure, silence, no-speech, maximum-duration, and background paths through the production session contract; attach screen identifiers to one stable header leaf instead of accessibility ancestors that flatten their controls.
- **Changes:** Added session-scoped event streams and per-recording files, centralized all consumers on `VoiceDictationCoordinator`, added scripted launch scenarios and focused regressions, exposed starting/recording/transcribing/failed UI states, removed the unreachable discovery-personalization sheet/view model/factory, and moved screen identifiers to masthead, navigation-title, metadata, or status-title leaves.
- **Validation:** The iOS 26.4 Simulator build succeeded; 47 focused native voice/onboarding/consumer tests and 21 Python source-contract tests passed; AXe verified onboarding starting, recording, transcribing, no-speech retry, success, and background cancellation, then verified distinct screen and control elements through onboarding, Briefing, Knowledge, Learning, More, Settings, content detail, and the article reader; `git diff --check` passed.
- **Remaining:** Real microphone permission prompts, acoustic silence thresholds, hardware route changes, and live backend transcription still require a physical-device/provider pass; the deterministic harness covers their app-level terminal contracts without spending provider calls.
- **Commits:** Uncommitted.

### 2026-08-07 — `main` — Tune per-surface voice deadlines and close coverage gaps

- **Status:** Complete.
- **Scope:** Voice deadline ownership, onboarding recording policy, Learning Deck focus dictation tests, and personalized-onboarding Maestro selectors.
- **Decisions:** Make recording deadlines part of each reserved speech session; default every surface to a 10-second no-speech timeout and 60-second absolute cap, while keeping onboarding intentionally shorter with a 30-second absolute cap. Keep fixture content assertions text-based, but use stable accessibility IDs for deterministic screen and control selection.
- **Changes:** Added `SpeechRecordingDeadlines`, passed it from each coordinator into the live provider, removed hard-coded provider deadlines, set onboarding's explicit cap, added focused Learning Deck focus coverage for manual, automatic, no-speech/retry, and cancel paths, and migrated the personalized onboarding flow to stable leaf selectors.
- **Validation:** All 53 affected native voice-consumer tests and 23 Python source-contract tests passed; the personalized onboarding YAML parsed as 17 Maestro commands and all 9 iOS content-flow tests collected; `git diff --check` passed.
- **Remaining:** The encompassing audit still owns the full Maestro runtime pass and physical-device microphone/provider checks.
- **Commits:** Uncommitted.

### 2026-08-07 — `main` — Remove confirmed SwiftUI render-path work

- **Status:** Complete.
- **Scope:** Learning timeline projection, Learning chat previews, and Knowledge saved-route ID projection.
- **Decisions:** Rebuild the merged Learning timeline only when one of its three owning source collections changes; flatten chat Markdown as part of that cached projection; maintain ready saved-content IDs in `ContentListViewModel` and read them only when opening a detail route.
- **Changes:** Added source revision counters for chats, decks, and narrations; made `LearningView` keep revision-driven timeline state; moved chat preview parsing into timeline construction; removed the ready-ID `compactMap` and per-row array input from Knowledge rendering.
- **Validation:** All 21 focused native timeline, content-list, Learning chat, and Learning Deck tests passed; all 38 focused client source-contract tests and Ruff checks passed; the iOS Simulator build completed as part of the native test run; `git diff --check` passed.
- **Remaining:** Runtime profiling with Instruments can quantify the frame-time delta, but no profiling claim is required for these mechanism-confirmed hot paths.
- **Commits:** Uncommitted.

### 2026-08-07 — `main` — Complete cross-stack interaction, sandbox, and dead-end hardening

- **Status:** Complete.
- **Scope:** Every untrusted feed-research/fetch path, feed subscription and onboarding outcomes, E2B agent lifecycle, assistant/chat sandboxing, duplicate-state reconciliation, iOS interaction latency, navigation and recovery states, all voice consumers, content action sheets, Share Extension actions, accessibility automation, visual regressions, and obsolete backend/client paths.
- **Decisions:** Make E2B the sole runtime for untrusted feed and page retrieval while retaining host calls only for trusted provider/control-plane APIs; fail truthfully when the sandbox or downstream work fails instead of returning false success; converge feed creation on one typed idempotent command and canonical content user-state ownership; remove unused APIs and abstractions instead of preserving internal compatibility; reserve one synchronous exclusive speech session and test every app-level terminal state deterministically; place AX identifiers on stable leaf elements so automation never flattens interactive descendants; compare complete sheets after those IDs moved to title leaves.
- **Changes:** Added reusable, poison-evicting E2B session management and canonical feed-research execution across assistant search, mixed Search, weekly discovery, onboarding, validation, subscription, ingestion, scheduled RSS/Atom/Substack/podcast work, Apple Podcasts, and content media analysis; keyed session creation by template and namespace so unrelated E2B work no longer serializes while same-namespace acquisition remains singleflight and shutdown-safe; hardened chat workspace bootstrap, telemetry, citations, and cleanup; added typed subscription/backfill outcomes, duplicate-race recovery, historical URL resolution, and loser-to-winner user-state reconciliation; extracted cohesive feed candidate, onboarding audio-run, and canonical-state modules to stay within architecture limits; removed the dead `/api/discovery` surface, legacy Learning Deck generator/sandbox, dead Swift repositories/services/models/views, and unused wire fields; fixed Search/detail/chat routing, FIFO chat handoff, Knowledge/Learning/Search failure recovery, Share Extension retry/open/copy fallbacks, and narration completion; unified all onboarding/chat/Knowledge/Learning Deck/tweet dictation on the shared session contract; reduced confirmed SwiftUI render-path work; added stable AX IDs including the empty Briefing state, three new voice Maestro flows, a More→Search→detail→chat flow, source-contract tests, and meaningful full-screen visual baselines.
- **Validation:** Real E2B feed and personal-library chat canaries verified feed discovery/parsing, workspace bootstrap, query execution, exact-namespace reuse, poison eviction, cleanup, shutdown drain, and post-drain rejection; four concurrent 200 ms namespace creations fell from 0.838 seconds serialized to 0.211 seconds keyed, a 74.8% wall-time reduction and about 3.97× throughput. The final backend suite passed 2,393 tests with two warnings; Ruff, mypy across 467 files, public-contract generation, Go test/vet, the 748-file/23-ratchet architecture guard with 112 guard tests, high-confidence Vulture scanning, YAML/document parsing, and `git diff --check` passed. Native iOS testing passed 429/429 on an iPhone 17 Pro Simulator. The final clean dark-mode Maestro catalog passed all 19 paths in 350.97 seconds after visual inspection. AXe drove Briefing→Knowledge→Learning→More→Search→detail→chat, verified leaf controls and empty-state reachability, and exercised successful and no-speech Learning Deck focus voice; earlier AXe runs covered onboarding success, no-speech retry, and background cancellation, while deterministic native/Maestro coverage exercised empty, start failure, transcription failure, silence, maximum duration, chat, Knowledge, Learning Deck, and tweet voice paths. Two broad Oracle reviews found correctness seams that were remediated; final Oracle Fable independently passed all 15 named checks with no P0–P2 finding, and a focused follow-up passed the last Share Extension interaction delta.
- **Remaining:** Real microphone permission prompts, acoustic thresholds, hardware route/interruption behavior, and live provider transcription quality still require a physical-device pass. Production remains unchanged: no config activation, deployment, commit, or push was performed, and the current production chat-sandbox setting was not altered.
- **Commits:** Uncommitted.

### 2026-08-07 — `main` — Close final voice and Share Extension lifecycle seams

- **Status:** Complete.
- **Scope:** Failed voice stops during an awaiting start and Share Extension submission controls after terminal or recovery transitions.
- **Decisions:** Treat every failed session stop as cancellation unless another caller already released it; keep one presentation-state predicate as the owner of Share Extension submission eligibility.
- **Changes:** Failed speech stops now invoke provider cancellation exactly once, preventing an awaiting start from acquiring late recorder ownership; Share Extension controls and tap routing now permit only ready, invalid-URL recovery, and recoverable retry phases while ignoring prohibited submitting, authentication, manual-fallback, and completed taps.
- **Validation:** All 22 focused native voice/session/Share Extension tests passed on an iPhone 17 Pro iOS 26.5 Simulator; all 20 client service/view-model source-contract tests passed; `git diff --check` passed.
- **Remaining:** Physical-device microphone, acoustic, route/interruption, and live transcription checks remain as documented above; no app-level P1/P2 lifecycle gap remains in these two paths.
- **Commits:** Uncommitted.

### 2026-08-07 — `main` — Finish E2B concurrency and independent acceptance

- **Status:** Complete.
- **Scope:** Final E2B session-start latency, empty-Briefing automation reachability, Share Extension mutation/retry safety, and independent review of the completed hardening diff.
- **Decisions:** Keep E2B session creation singleflight only within the same template/namespace key; use the existing presentation-state predicate as the sole owner of whether Share Extension inputs can mutate; independently validate required chat input inside the submit handler so retry callbacks cannot bypass it.
- **Changes:** Replaced process-wide E2B creation serialization with keyed singleflight plus drain-safe lifecycle accounting; exposed the empty Briefing masthead as `briefing.screen`; froze Share Extension option and chat controls outside begin-capable phases and added a handler-level non-empty-chat guard.
- **Validation:** The final backend suite passed 2,393 tests, the native suite passed 429 tests, and the clean dark-mode Maestro catalog passed all 19 flows. Focused post-review checks passed 11 Share Extension source contracts and 11 native state/transport tests. AXe completed the rebuilt end-to-end navigation and voice acceptance path, and Oracle Fable returned PASS for both the 15-check release audit and the focused final delta with no P0–P2 findings. `git diff --check` passed.
- **Remaining:** Only the physical-device voice/provider checks documented above; production was not changed and no commit or push was performed.
- **Commits:** Uncommitted.

### 2026-08-07 — `main` — Close production VM and RSS outage seams

- **Status:** Complete.
- **Scope:** Production generic-agent sandbox selection and RSS-cluster scraper failure reporting.
- **Decisions:** Permit the host-local generic VM only for development and tests; require E2B for model-authored generic agent work in production. Preserve RSS-cluster partial-progress behavior while recording an isolated E2B feed outage as an error instead of a successful empty scrape.
- **Changes:** Production settings now reject disabled or local generic VM providers, and Techmeme-style RSS cluster fetch failures now flow through the existing `ScraperStats` error channel.
- **Validation:** The direct regression set passed 16 tests; the broadened settings, aggregator, scraper-handler, E2B session, Learning Deck, and Share Action set passed 108 tests; Ruff and touched-file mypy passed; `git diff --check` passed.
- **Remaining:** None for these seams.
- **Commits:** Uncommitted.

### 2026-08-07 — `main` — Bound every Exa provider request

- **Status:** Complete.
- **Scope:** Shared Exa transport plus onboarding profile, parallel discovery, enrichment, and audio-discovery callers; audited chat, assistant, generic-agent, machine-search, feed discovery/detection, briefing, podcast, YouTube-equivalent, and evaluation-script paths.
- **Decisions:** Reuse the existing 30-second canonical HTTP timeout instead of adding another setting; let callers replace it only with a tighter budget; keep timeout ownership at the shared Exa boundary so default consumers cannot bypass it.
- **Changes:** Made the singleton Exa transport deadline-bound for both search and contents calls, added one shared request-client selector for tighter caller budgets and expired-budget short-circuiting, and propagated the existing 8/12/25-second onboarding budgets through sequential and parallel search helpers.
- **Validation:** All 236 focused Exa, onboarding, assistant, chat, feed-discovery/detection, podcast, briefing, worker, YouTube-equivalent, and machine-agent tests passed; touched-file Ruff and mypy passed; `git diff --check` passed.
- **Remaining:** None.
- **Commits:** Uncommitted.

### 2026-08-07 — `main` — Bound generic E2B agent sessions and VM I/O

- **Status:** Complete.
- **Scope:** Learning Deck and Share Action agent deadlines, model-authored shell commands, generic VM file reads/writes/listings, and production E2B credentials.
- **Decisions:** Give each configured agent run one monotonic deadline; clamp shell and file operations to both per-operation limits and the remaining run budget; stream remote reads under a hard byte cap; cap file listings before returning them to the model; require the explicit E2B key that the runtime consumes when production selects the mandatory E2B provider.
- **Changes:** Propagated deadlines through generic VM creation and model request settings; added bounded local and E2B commands, reads, writes, and listings with typed timeout failures and deterministic stream cleanup; preserved hidden relative paths in listings; and rejected production startup without an E2B API key.
- **Validation:** All 107 focused and adjacent settings, E2B/feed-runtime, Learning Deck, Share Action, queue-handler, and API tests passed; touched-file Ruff and mypy passed; a live isolated E2B canary verified bounded create, write, read, list, bash, and cleanup behavior; `git diff --check` passed.
- **Remaining:** None for generic VM availability; Exa request deadlines are validated separately at the shared provider boundary.
- **Commits:** Uncommitted.

### 2026-08-07 — `main` — Close migration, Share Action, and download-status failure edges

- **Status:** Complete.
- **Scope:** Task-ownership migration safety, Share Action callback failure persistence, and feed initial-download status reconciliation.
- **Decisions:** Fence new vendor-usage references before cleaning legacy orphans; parse legacy JSON identifiers without throwing; recover aborted callback transactions by rollback and durable row reload; report retried work as attempted and expired task evidence as unavailable rather than perpetually queued.
- **Changes:** Reordered the vendor-usage NOT VALID foreign key, added range-safe task and audio identifier backfills, persisted failed Share Action task/action state after an aborted SQL transaction while preserving the original exception, and made initial-download retry and retention projections truthful.
- **Validation:** Both PostgreSQL migration tests passed, including concurrent-writer, out-of-range payload, upgrade, validation, and downgrade coverage; all 51 focused Share Action, query, and router tests passed; touched-file Ruff, mypy, and diff checks passed.
- **Remaining:** None for these failure paths.
- **Commits:** Uncommitted.

### 2026-08-07 — `main` — Close immediate-speech and stale audio lifecycle seams

- **Status:** Complete for the three confirmed voice/audio lifecycle regressions.
- **Scope:** Production microphone metering, onboarding cancellation during recorder startup, and podcast overview playback while content detail navigates or disappears.
- **Decisions:** Compare microphone power with the preceding threshold before updating the existing 0.3-second calibration and freeze calibration after speech; suppress cancelled or stale onboarding startup errors after leaving the audio step; invalidate podcast preparation at every awaited boundary while stopping only playback whose target belongs to the departing content.
- **Changes:** Added a small pure metering state reducer that preserves the existing thresholds and deadline precedence, guarded onboarding's late startup catch, added controller-owned podcast request generations around episode creation and stream resolution, and made content-detail disappearance invalidate pending audio even when no content remains loaded.
- **Validation:** All 37 focused native metering, speech-session, scripted-speech, onboarding, podcast-controller, and narration-playback tests passed on an iPhone Simulator; all 22 focused iOS async/service boundary and transcription-router tests passed; touched-file `git diff --check` passed.
- **Remaining:** Simulator tests deterministically cover software state transitions, but real microphone permission prompts, acoustic levels, loud ambient noise above the initial -42 dB threshold, Bluetooth/headset route changes, interruptions, and live transcription quality still require a physical-device/provider pass.
- **Commits:** Uncommitted.

### 2026-08-08 — `main` — Complete whole-app interaction, sandbox, voice, and dead-end hardening

- **Status:** Complete for the whole-app implementation and simulator-verifiable acceptance goal.
- **Scope:** Final independent review and remediation across iOS navigation and recovery, Share Extension lifecycle, every voice consumer, paid queue ownership and retry behavior, podcast/feed truthfulness, E2B feed and media boundaries, sandbox cleanup/reuse, and trusted YouTube media limits.
- **Decisions:** Preserve the reviewed working tree while Oracle Fable inspects it; treat only independently reproducible P0–P2 findings as completion blockers; keep untrusted feeds and non-YouTube remote media inside E2B; poison and evict reusable sandboxes when scratch cleanup cannot be proven; bound trusted yt-dlp work with a separate process, process-group termination, a total deadline, and a 500 MB cap; leave production, authentication configuration, deployment, commits, and pushes untouched.
- **Changes:** Closed the queued-chat replacement dead end, first-load detail cancellation skeleton, Share Extension non-web URL and cancellation gaps, and the voice auto-stop/manual-stop transcription race; made paid lease reclaim consume retries atomically and fenced completed, cross-user, and wrong-owner targets; made malformed podcast XML and E2B discovery outages fail truthfully; made feed size/deadline failures candidate-local while failed cleanup poisons the session; routed arbitrary enclosure downloads through a bounded E2B bridge; and bounded the retained trusted yt-dlp path, including stale partial cleanup.
- **Validation:** The exact final code tree passed 2,669 backend tests, 480 native iOS tests on iOS 26.5, all 19 Maestro flows, and all 12 AXe scenarios on a second iOS 26.3.1 Simulator with 62 screenshots and 74 JSON artifacts. AXe also verified the real Share Extension Cancel path back to Safari. Fresh development-only E2B canaries passed generic VM write/read/list/bash, Daring Fireball feed validation, bounded media transfer, and remote cleanup without database or production writes. Ruff, mypy across 472 files, the 752-file/23-ratchet architecture guard with 137 tests, public contracts, Go test/vet, dependency checks, compileall, migration head/current `20260807_02`, and `git diff --check` passed. Final read-only Oracle Fable independently re-audited all ten original findings and seven follow-ups and returned `FINAL_VERDICT: PASS` with no concrete P0–P2.
- **Remaining:** Physical-device microphone permission, acoustic/noise thresholds, Bluetooth/headset route changes, interruptions, and live transcription-provider quality remain hardware/provider acceptance work; optional Instruments profiling and Fable's non-blocking pre-existing infrastructure backlog are not release blockers for this goal.
- **Commits:** Uncommitted; no push, deployment, production configuration change, or authentication change was performed.

### 2026-08-08 — `main` — Stabilize the thermo-review cleanup tranche

- **Status:** Complete for Phase 0 of the follow-up maintainability review.
- **Scope:** Queued chat session reuse, mixed-search contract compatibility, refresh-token rotation/deletion serialization, onboarding discovery ownership, E2B session lifecycle, task-queue gateway forwarding, and the existing scraper/audio/mechanical cleanup tranche.
- **Decisions:** Keep `ConsumedRefreshToken` as the server-side replay ledger; hold a shared user-row lock through refresh-token consumption so account deletion cannot race its foreign key; scope persisted onboarding runs to the queued owner; preserve the gateway contract that forwards only explicitly supplied optional arguments; retain namespace singleflight while allowing quiescent lock keys to be reclaimed.
- **Changes:** Reused `require_writable_session` in assistant-turn creation; made missing mixed-search subscription state decode as `false`; owner-scoped audio-discovery runs and validated positive queued run IDs; cleaned up newly created E2B sandboxes when telemetry fails; restored sparse queue forwarding; and changed the per-namespace lock map to weak values.
- **Validation:** Ruff passed on every Phase 0 file; 256 focused scraper, audio, Agent-VM, Learning Deck, feed-discovery, onboarding, chat-command, X-bookmark, and queue-gateway tests passed; 35 focused chat/auth/generated-contract tests and 15 onboarding/task-spec tests passed; generated public contracts were current; and `APIContractsGeneratedTests` passed natively on an iPhone 17 Pro iOS 26.3 Simulator.
- **Remaining:** The attached Phase 1–3 backend and iOS cleanup slices remain; each will keep its own focused gate before the final full-suite, AXe, and Oracle Fable review. No commit, push, deployment, production configuration change, or authentication change was performed.
- **Commits:** Uncommitted.

### 2026-08-08 — `main` — Apply the cross-stack thermo review and cleanup plan

- **Status:** Implementation and exact-tree local validation complete; the final Oracle Fable verdict remains after its 6:30pm Pacific quota reset.
- **Scope:** The attached Phase 1–3 plan plus deeper backend, E2B/feed/media, queue, iOS interaction, accessibility, voice, navigation, and dead-code review.
- **Decisions:** Keep feeds and untrusted feed discovery in E2B; retain `ConsumedRefreshToken` as the one-time refresh replay ledger; prefer narrow shared helpers over the proposed generic voice controller, mutation journal, token-rotation core, error-row hierarchy, and Learning timeline owner; do not alter authentication configuration. Treat Vulture as lead generation and preserve dynamically registered handlers, schema fields, and package exports.
- **Changes:** Unified scraper-run status, positive task payloads, queue and subscription outcomes, URL handling, feed lookup, and onboarding search concurrency; hardened Agent-VM idle eviction, podcast provider deadlines/admission/cache/token reuse, E2B HTTP/media contexts, narration and voice-session ownership, mutation journals, and Briefing equality; extracted podcast result normalization and shared bounded Exa batching to restore module ratchets; added shared SwiftUI scroll-to-top and optional accessibility-ID modifiers; gated scripted speech to Debug; removed brittle source-shape tests and confirmed dead assignments; fixed generic pages linking to Substack being misclassified as Substack publications; and stopped feed-discovery runs from overriding the model's canonical naive-UTC timestamp, which had made a just-completed empty run miss the UTC weekly cache during Saturday evening Pacific time and repeat paid work.
- **Validation:** Focused backend slices passed throughout, including 186 E2B/feed/media tests, 70 final feed/runtime tests, 41 Exa/onboarding/podcast tests, and the 137-test architecture guard; the exact final backend tree passed 2,718 tests in 175.25 seconds. Ruff passed app/tests/scripts; mypy passed all 472 application files; public contracts, Go test/vet, lock/dependency checks, compileall, duplicate-test guard, the 753-file/23-ratchet architecture guard, migration head/current `20260807_02`, and `git diff --check` passed. Native iOS passed 485/485 on iOS 26.5. The complete iOS E2E directory passed 38/38 on iOS 26.4: 19 Maestro flows, 14 AXe product scenarios, and 5 AXe harness tests; the dedicated AXe matrix also passed 14/14 on iOS 26.3. Fresh development-only E2B canaries passed generic VM I/O/bash, Daring Fireball page-to-Atom discovery, bounded media transfer, and zero-cache cleanup without database or production writes.
- **Remaining:** The required exact-tree Oracle Fable invocation hit its Claude session limit without review text and is scheduled to rerun after the stated 6:30pm Pacific reset. Physical-device microphone permission, acoustics, Bluetooth/route interruptions, and live transcription-provider quality remain hardware/provider acceptance work. No commit, push, deployment, production configuration change, or authentication change was performed.
- **Commits:** Uncommitted.

### 2026-08-08 — `main` — Run the cross-runtime AXe interaction matrix

- **Status:** Complete for simulator-verifiable interaction acceptance.
- **Scope:** Share Extension/feed discovery, chat-driven new-content discovery, Knowledge projection, Learning Deck creation and voice focus, mixed Search, onboarding voice, navigation/reselection, error recovery, and every deterministic chat voice terminal state.
- **Decisions:** Stop Oracle Fable work at the user's direction and spend no further Fable credits. Run AXe serially because its HID daemon is process-global, pair every dispatched action with fresh accessibility-tree or screenshot evidence, and keep the already-green development E2B canaries as the paid sandbox proof instead of creating new sandboxes for this AXe-only pass. Simulator fake speech remains the deterministic state-machine boundary; physical microphone/provider quality remains separate hardware acceptance.
- **Changes:** Expanded the state-verifying AXe matrix to 20 product paths; made transient toasts ignore hit testing; gave programmatically routed chats an explicit path-pop close action without intercepting their header; exposed stable Back and Learning progress semantics; and propagated debug-login server settings into the app group so the real Share Extension submits to the matrix's dynamic live API. The four Share modes now prove UI-to-API-to-database/queue handoff, chat feed discovery proves subscription and backfill projection into Knowledge, and Learning Deck focus proves its queued processing projection.
- **Validation:** The complete 20-test AXe matrix passed 20/20 on iOS 26.4.1 in 172.12 seconds (`/tmp/newsly-deep-axe-matrix-264-20.9xiqkZ`) and 20/20 on iOS 26.3.1 in 172.82 seconds (`/tmp/newsly-deep-axe-matrix-263-20-green.Fiu5yz`). Each run retained 140 screenshots and 160 JSON artifacts with zero empty files. Focused reruns passed chat/feed/Knowledge 1/1, Learning Deck voice focus 1/1, all Share modes 4/4, and the iOS 26.3 Share representative 1/1. Ruff formatting and lint passed for the AXe harness/matrix, 53 focused Python/source-boundary tests passed, 40 focused native Share/chat/Learning Deck tests passed with zero skips or failures, and `git diff --check` passed.
- **Remaining:** Real microphone permission, acoustic/noise thresholds, Bluetooth/headset route changes, interruptions, and live transcription-provider quality require a physical-device pass. No Fable invocation, paid E2B rerun, commit, push, deployment, production configuration change, or authentication change was performed in this matrix pass.
- **Commits:** Uncommitted.

### 2026-08-08 — `main` — Clean the complete implementation change set

- **Status:** Complete for behavior-preserving cleanup and affected-path validation.
- **Scope:** The entire current backend, E2B/feed/sandbox, queue/cron, iOS, Share Extension, Search, chat/voice, Learning Deck, test, and documentation change set.
- **Decisions:** Keep the refresh-token consumption model because database uniqueness is the replay-protection boundary used by concurrent rotation and account deletion. Preserve intentional external audio-delivery compatibility, layer-local session validation, stable SwiftUI observation structure, and explicit AXe scenario steps; avoid speculative controller, repository, or harness abstractions. Do not spend more Fable or E2B credits for this cleanup pass.
- **Changes:** Centralized the feed-backfill eligibility policy; removed redundant chat ownership, lifecycle, and timestamp bookkeeping; removed an unused private Learning Deck sandbox argument and an unreachable Agent-VM path fallback; derived voice and success flags from their canonical state; hoisted repeated Search content-ID mapping; made the payload-free Share recovery case explicit; simplified cron enqueue/status accounting; removed redundant AXe sleeps; and formatted all changed Python sources, updating 48 files that had drifted.
- **Validation:** The non-iOS-E2E backend suite passed 2,723 tests with 39 simulator tests separated; the focused cleanup backend slice passed 130/130. The architecture guard passed 753 files/23 ratchets and 137 tests, with public contracts current. Native iOS passed 485/485. The affected AXe matrix passed 13/13 on the explicit iOS 26.4 simulator and current app bundle, retaining 94 screenshots and 107 JSON artifacts with no empty files under `/tmp/newsly-cleanup-axe-affected`; all four Share modes separately passed UI-to-API-to-database/queue. An unqualified run's two Share failures were independently traced to its auto-selected older iOS 26.3 simulator/default stale bundle, where the extension could not reach the ephemeral server—not to product code. Ruff formatting and lint passed all 293 changed Python files; mypy passed 472 application files; high-confidence Vulture, Go test/vet, lock, compileall, duplicate-test, and `git diff --check` gates passed.
- **Remaining:** Physical-device microphone permission, acoustics, Bluetooth/headset routes, interruptions, and live transcription-provider quality remain hardware/provider acceptance work. Three untouched Markdown fixture/documentation files retain their pre-existing fenced-Python formatter baseline. No Fable invocation, paid E2B rerun, commit, push, deployment, production configuration change, or authentication change was performed.
- **Commits:** Uncommitted.

### 2026-08-09 — `main` — Re-audit the complete change set after cleanup

- **Status:** Complete for the second behavior-preserving cleanup pass.
- **Scope:** Current backend queue/search/feed-research and cron changes plus Share Extension, chat voice tests, and Learning voice tests.
- **Decisions:** Limit edits to residuals created or exposed by this implementation. Keep the public audio-delivery compatibility boundary and staged migration fallbacks; leave older submission-outcome helper duplication, Apple Podcasts title-tokenization, and evaluation-script snapshot redesign for explicit broader work.
- **Changes:** Deferred mixed-search subscription reads until feed options exist; skipped resolved-owner queries for ownerless queue batches; reused one access-grant timestamp per batch; removed enqueue arguments already inferred by the queue boundary; collapsed feed-discovery read counting from one query per user to one grouped query; removed dead feed-research dispatch state, a newly unused feed-detection argument/logger, and unread voice-test state; removed an unconsumed Share recovery action and simplified ordered shared-URL handling.
- **Validation:** 129 focused backend tests passed. The architecture guard passed 753 files/23 ratchets and 137 tests with public contracts current; Ruff formatting/lint passed all 293 changed Python files; mypy passed 473 sources; and high-confidence Vulture found nothing. Native chat/Learning/Share testing passed 53/53. All four live Share Extension modes passed through AXe on the explicit iOS 26.4 simulator in 59.17 seconds, retaining 47 screenshots and 51 JSON artifacts with no empty files under `/tmp/newsly-cleanup-second-axe-share`. `git diff --check` passed.
- **Remaining:** The previous exact-tree full backend, 485-test native, and affected 13-path AXe gates remain the broad baseline; this pass reran proportional gates for its narrow delta. Physical-device voice acceptance remains separate. No Fable invocation, paid E2B rerun, commit, push, deployment, production configuration change, or authentication change was performed.
- **Commits:** Uncommitted.

### 2026-08-09 — `main` — Repair duplicate Briefing pullquotes and refine quote type

- **Status:** Complete.
- **Scope:** Briefing layout policy/repair, the production-backed layout eval suite, and iOS Briefing pullquote typography.
- **Decisions:** Treat repeated pullquotes as deterministic layout debris after case/whitespace normalization; retain the first occurrence and every distinct quote; preserve the production segment `4351` shape as the regression fixture; keep the visual adjustment local to the existing pullquote view.
- **Changes:** Added duplicate-pullquote assessment and repair warnings, froze the three-copy podcast failure as an eval case, added focused coverage proving distinct quotes remain, and reduced the pullquote's base serif-italic size from 20 to 15 points while preserving Dynamic Type scaling.
- **Validation:** All 70 focused Briefing repair, composer, and eval tests passed; touched Python Ruff checks passed; the complete iOS Simulator app build succeeded with Xcode 26.5; `git diff --check` passed.
- **Remaining:** Production segment `4351` remains immutable and will still display its stored duplicates until it is retired/refreshed; newly composed layouts will be repaired after deployment. No production mutation or deployment was performed.
- **Commits:** Uncommitted.

### 2026-08-09 — `main` — Finish the whole-change-set cleanup

- **Status:** Complete for the third diff-only, behavior-preserving cleanup pass.
- **Scope:** The entire current dirty change set, including the later Briefing duplicate-pullquote repair and typography tranche.
- **Decisions:** Change only newly introduced redundancies with identical behavior; preserve the refresh-token replay ledger, public audio-delivery compatibility, staged migration fallbacks, explicit AXe scenario steps, and unrelated dirty-worktree work. Do not spend Fable or E2B credits or rerun simulators for source-neutral cleanup.
- **Changes:** Reused the Briefing policy's existing normalized pullquote text; removed a dead feed-runtime timeout fallback and an unreachable private curl-URL fallback; and let AXe subprocesses inherit the current environment through Python's default instead of copying it explicitly.
- **Validation:** All 69 focused feed-runtime, AXe-harness, Briefing repair, and Briefing-eval tests passed. Ruff lint and formatting passed all 296 changed Python files; high-confidence Vulture and `git diff --check` passed. The previously recorded full backend, native iOS, AXe, Share Extension, architecture, contract, and proportional cleanup gates remain the broad baseline.
- **Remaining:** Broader architectural redesigns and physical-device voice/provider acceptance remain outside behavior-preserving cleanup. No Fable invocation, paid E2B rerun, simulator run, commit, push, deployment, production configuration change, or authentication change was performed.
- **Commits:** Uncommitted.

### 2026-08-09 — `main` — Make Briefing pullquotes LLM-authored suggestions

- **Status:** Complete.
- **Scope:** Audio/long-form Briefing composition prompts, structured model-output contracts, production-backed layout evals, and the previously reduced iOS pullquote typography.
- **Decisions:** Treat pullquotes as uncited editorial callouts authored by the Briefing composer, not verbatim source quotations. Keep suggestions in a separate top-level model-response section and have layout blocks select them by ID. Enforce reference integrity and one selection per suggestion without extracting source quotes or deduplicating generated text.
- **Changes:** Added typed `suggested_quotes` and `suggestion_id` output fields, resolved selected suggestions into the existing persisted pullquote block shape, removed deterministic fallback pullquotes and the earlier text-deduplication repair, advanced the prompt version to `briefing-v5`, and rewrote production segment `4351` as a regression for three separately suggested callouts. The 25% iOS font reduction remains unchanged.
- **Validation:** All 170 Briefing service and production-eval tests passed; focused mypy passed; touched Python Ruff and formatting checks passed; the unchanged 25% iOS font reduction retained its successful full Simulator build; `git diff --check` passed.
- **Remaining:** Production segment `4351` remains immutable until retired/refreshed. No production mutation or deployment was performed.
- **Commits:** Uncommitted.

### 2026-08-09 — `main` — Replace generated codebase docs with product laws

- **Status:** Complete.
- **Scope:** Documentation routing, generated repository maps, and canonical cross-product behavior.
- **Decisions:** Preserve architecture, coding guidance, operations, and historical initiative material; remove the generated folder-by-folder documentation and its generator; make laws normative, concise, implementation-independent, and testable.
- **Changes:** Added a laws index and focused laws for accounts, content, Briefing, Knowledge/Learning, chat, sharing/sources, audio/voice, and processing/reliability; removed the generated reference tree and generator; redirected current documentation guidance; and decoupled source-boundary tests from generated documentation copy.
- **Validation:** All 55 affected iOS source-boundary tests passed; touched-test Ruff passed; the architecture guard passed 753 files, 23 ratchets, and 137 tests; public contracts were current; all laws links resolved; deleted documentation had no live references; `git diff --check` passed.
- **Remaining:** None.
- **Commits:** Uncommitted.

### 2026-08-09 — `main` — Repair Learning Deck source handoff and retry

- **Status:** Complete for the diagnosed Share Action failure and reader retry path.
- **Scope:** Share Action presentation handoff, canonical content recovery, Learning Deck retry API, iOS reader failure actions, public contracts, and focused regression coverage.
- **Decisions:** Reuse the Share Action's prepared Knowledge content only when the selected URL identifies the same source; preserve genuinely different agent-selected sources. Treat `canonical_content_id` as the durable redirect for duplicate shells. Retry only failed or cancelled attempts on the same stable deck, retain any prior successful artifact, and make repeat taps idempotent only while that explicit retry is active. Keep generation retry, status reconnection, and WebView reload as separate client actions.
- **Changes:** Added trusted prepared-source deck creation, X-status URL identity matching, canonical source rebinding before generation, `POST /api/learning/decks/{deck_id}/retry`, retry-attempt provenance, the iOS retry service/state flow with double-tap protection, direct WebView reload, stable retry accessibility identifiers, and the regenerated OpenAPI contract.
- **Validation:** Ruff passed all touched Python files. All 63 focused Share Action, Learning Deck service/generation, and router tests passed. All 12 focused native Learning Deck reader/status tests passed on the iOS 26.3.1 iPhone 17 Pro simulator, including generation retry, double-tap idempotence, status reconnection, and existing reader lifecycle coverage. Public contract drift check and `git diff --check` passed.
- **Remaining:** No production data repair, deployment, commit, or push was performed.
- **Commits:** Uncommitted.

### 2026-08-10 — `main` — Require LLM-authored Briefing segments

- **Status:** Complete locally.
- **Scope:** Briefing composition failure behavior and its canonical product law.
- **Decisions:** Never publish deterministic fallback prose when LLM composition is unavailable, malformed, or policy-invalid; fail the refresh set after its existing retries and preserve the last usable edition.
- **Changes:** Removed automatic deterministic fallback publication from every LLM Briefing composition failure path, removed deterministic prose repair for missing source coverage, and replaced orchestration tests' non-LLM publication shortcut with an injected model-output fixture.
- **Validation:** All 187 Briefing service, production-eval, and Briefing API tests passed; focused mypy passed for all changed production modules; Ruff formatting and lint passed all changed Python files; `git diff --check` passed.
- **Remaining:** Existing immutable deterministic production segments remain until separately retired or regenerated. No production mutation, deployment, commit, or push was performed.
- **Commits:** Uncommitted.

### 2026-08-10 — `main` — Make Agent-VM artifact paths canonical

- **Status:** Complete locally.
- **Scope:** Shared local/E2B file-path resolution, agent file tools, Learning Deck artifact validation and repair, and focused regression coverage.
- **Decisions:** Make agent-facing file paths workspace-relative; normalize echoed absolute paths only when they identify the current task workspace; reject traversal and foreign absolute paths visibly; preserve local physical symlink containment; keep provider paths in internal diagnostics and contract-relative paths in prompts and user-facing failures.
- **Changes:** Added the shared resolver and typed path failure, aligned both VM providers and file-tool results, made Learning Deck missing-file reports structured and path-safe, added explicit workspace-relative prompt guidance, and reproduced task `55` with an absolute repair write that now converges on the canonical artifact.
- **Validation:** All 214 affected Agent-VM, feed-research, media, Share Action, and Learning Deck tests passed; 4 prompt/module-guard tests passed; the 752-file/23-ratchet module guard and 137-test architecture guard passed with public contracts current. Ruff passed all touched Python files, focused mypy passed all 5 changed production modules, and `git diff --check` passed.
- **Remaining:** Deploy before retrying production deck `14`; no production retry, deployment, commit, or push performed.
- **Commits:** Uncommitted.

### 2026-08-10 — `main` — Consolidate content Knowledge actions

- **Status:** Complete locally.
- **Scope:** The iOS content-detail action bar, Knowledge action sheet, chat/council/Learning Deck handoffs, stable accessibility identifiers, Maestro flows, and dark-mode visual baselines.
- **Decisions:** Reuse the Knowledge tab's `books.vertical.fill` icon for the action hub; offer exactly Start Chat, Ask a Council, and Create Learning Deck; keep narration as its own direct audio action; remove the duplicate standalone deck action and obsolete podcast/deep-dive sheet choices.
- **Changes:** Replaced the Chat actions sheet with a compact two-tile-plus-row Knowledge actions sheet, routed all three destinations through the existing coordinators and sheet handoff, removed dead detail-only deep-dive/research paths, and updated affected E2E flows and snapshots.
- **Validation:** All 37 focused iOS source-contract tests passed; Ruff passed all touched Python harness files; the generic iOS Simulator build succeeded; four focused dark-mode Maestro scenarios passed for the visual baseline, Start Chat, Council, and Learning Deck paths; `git diff --check` passed.
- **Remaining:** None. No commit, push, deployment, or production mutation was performed.
- **Commits:** Uncommitted.

### 2026-08-11 — `main` — Align the detail action hub with Learning

- **Status:** Complete locally.
- **Scope:** The far-right content-detail action icon and its focused source-contract coverage.
- **Decisions:** Use the app's canonical Learning `sparkles` symbol while preserving the existing action, layout, and accessibility contract.
- **Changes:** Replaced the Knowledge books symbol on the action hub with the Learning sparkle symbol and added a focused regression assertion.
- **Validation:** Focused source-contract test passed; `git diff --check` passed.
- **Remaining:** None. No commit, push, deployment, or production mutation was performed.
- **Commits:** Uncommitted.

### 2026-08-13 — `main` — Make Learning Decks portrait-native

- **Status:** Complete locally.
- **Scope:** Learning Deck generation prompt, artifact contract, hosted Reveal viewer/theme, portrait iOS reader, and deck-chat presentation.
- **Decisions:** Treat iPhone portrait as a primary 720 × 1280 composition while retaining 1280 × 720 landscape; opt new artifacts into the responsive viewer with explicit metadata and keep unmarked stored decks on the legacy canvas. Present portrait chat as a collapsed flyover so it does not permanently resize the deck WebView.
- **Changes:** Added the responsive generation/layout contract and validation marker, responsive house-theme rules, marker-aware viewer fitting, and a compact portrait chat flyover with a smaller expanded detent and condensed empty state.
- **Validation:** All 110 focused Learning Deck backend, router, prompt/theme, iOS layout/accessibility contract, and task-generation tests passed. Focused mypy passed for all four changed backend modules; Ruff format and lint passed; the 752-file/23-ratchet module guard, 137-test architecture guard, and public-contract check passed. The iOS app built successfully on the iOS 26.5 regression simulator, and all 3 focused `LearningDeckReaderViewModelTests` passed. `git diff --check` passed.
- **Remaining:** Existing stored decks retain the legacy canvas until separately regenerated. No commit, push, deployment, production mutation, or deck regeneration performed.
- **Commits:** Uncommitted.

### 2026-08-13 — `main` — Clean up portrait-native Learning Deck changes

- **Status:** Complete locally.
- **Scope:** Behavior-preserving cleanup of the uncommitted portrait generation, hosted viewer, and focused test changes.
- **Changes:** Reused the canonical responsive-layout marker in Python validation, consolidated repeated valid-deck fixtures, corrected stale landscape-only documentation, clarified viewer fit naming and branches, cached the two viewer variants, and coalesced duplicate Reveal relayout requests.
- **Validation:** All 60 focused Learning Deck service, viewer, artifact, smoke, route, and iOS accessibility-contract tests passed. Ruff format and lint passed, focused mypy passed for all four affected backend modules, and `git diff --check` passed.
- **Remaining:** The browser validation gate remains unchanged; broader validation redesign is outside this cleanup. No commit, push, deployment, production mutation, or deck regeneration performed.
- **Commits:** Uncommitted.

### 2026-08-13 — `main` — Second-pass Learning Deck cleanup

- **Status:** Complete locally.
- **Scope:** A second behavior-preserving audit of the uncommitted portrait-native Learning Deck diff.
- **Changes:** Consolidated duplicate portrait/landscape Reveal canvas setup in the browser validator, clarified the dual-orientation layout contract, and retained all existing relayout triggers while coalescing same-frame requests.
- **Validation:** All 60 focused Learning Deck and iOS accessibility-contract tests passed. Ruff format and lint passed, focused mypy passed for all four affected backend modules, and `git diff --check` passed.
- **Remaining:** No additional high-confidence cleanup remains in the reviewed surface. No commit, push, deployment, production mutation, or deck regeneration performed.
- **Commits:** Uncommitted.

### 2026-08-13 — `main` — Resolve Learning Deck structural review findings

- **Status:** Complete locally.
- **Scope:** Responsive generation ownership, hosted-viewer validation, portrait chat state, and executable iPhone regression coverage.
- **Decisions:** Keep one typed layout profile as the owner of prompt, artifact, viewer, and validation dimensions; validate the exact hosted HTML across every slide and both phone orientations; use device orientation rather than keyboard-sensitive visual-viewport geometry; and model portrait flyover and sheet chat as separate presentations of one mode-free chat panel.
- **Changes:** Extracted the canonical layout profile and hosted Playwright validator, replaced the agent's embedded browser script, measured per-slide overflow and occupancy in portrait and landscape, made responsive versus legacy viewer fitting declarative, moved flyover expansion state into its presentation wrapper, and added a deterministic completed-deck Maestro scenario. Removed parent accessibility identifiers that masked the flyover's peek and collapse controls.
- **Validation:** All 62 focused Learning Deck backend and iOS source-contract tests passed; Ruff and focused mypy passed; the 754-file/23-ratchet module guard, all 137 architecture tests, and public-contract check passed. The browser validator's generated JavaScript passed `node --check`. The iOS app built successfully, all 3 focused `LearningDeckReaderViewModelTests` passed, and the new portrait-chat Maestro scenario passed on the iOS 26.5 iPhone 17 Pro simulator.
- **Remaining:** The hosted Playwright validator was covered by typed outcome and command-generation tests but was not run in a real browser-capable Agent VM during this local pass. No commit, push, deployment, production mutation, or deck regeneration performed.
- **Commits:** Uncommitted.

### 2026-08-14 — `main` — Compact sparse News Briefing entries sooner

- **Status:** Complete locally.
- **Scope:** News Briefing compaction threshold and focused regression coverage.
- **Decisions:** Preserve the 25-minute freshness deadline for low-volume news, but allow two small News segments to be recomposed together instead of waiting for three so consecutive single-link entries converge sooner.
- **Changes:** Lowered the minimum News compaction donor/source count from three to two and added a production-shaped release-path test covering two singleton segments becoming one two-source segment.
- **Validation:** All 158 backend Briefing service tests passed; Ruff format and lint passed for the touched Python files; `git diff --check` passed.
- **Remaining:** No production mutation, deployment, commit, or push was performed.
- **Commits:** Uncommitted.

### 2026-08-14 — `main` — Optically align content-detail action icons

- **Status:** Complete locally.
- **Scope:** Content-detail action-bar symbol alignment only.
- **Decisions:** Preserve the common 44-point centered tap targets and correct only the SF Symbol whose visible artwork sits low in its intrinsic canvas.
- **Changes:** Raised the Share symbol by one point while leaving the already-centered external, reader, Knowledge, narration, and Learning actions unchanged.
- **Validation:** The iOS app built successfully for the iOS 26.5 iPhone 17 Pro simulator, all 9 focused accessibility/navigation source-contract tests passed, the deterministic dark-mode content-detail Maestro visual flow passed, and `git diff --check` passed.
- **Remaining:** No commit, push, deployment, or production mutation performed.
- **Commits:** Uncommitted.

### 2026-08-14 — `main` — Reproduce missed Chloé Bakalar news clustering

- **Status:** Investigation complete; no production behavior change.
- **Scope:** Production evidence for three unmerged Chloé Bakalar stories in the AI & Society Briefing lens, plus a curated title-clustering eval case.
- **Decisions:** Keep the current thresholds unchanged: the real embedding matcher clusters the three production titles when they are candidates. Treat the pre-semantic 150-row candidate cap as the reproduced failure seam; a larger static cap would only defer the same ingestion-burst failure.
- **Changes:** Added the three production primary titles to the curated news-relation eval corpus.
- **Validation:** Production showed three singleton news rows and three separately published lens segments; all processing tasks completed without errors. There were 202 newer ready global rows created before the second item and 151 before the third, while the production cap is 150. A rollback-only local reproduction with 151 filler candidates produced three singleton clusters and `candidate_count=150` / `prefilter_empty` traces; the adjacent-title real-embedding eval passed with F1 1.000. All 102 focused news-relation tests, Ruff, and `git diff --check` passed.
- **Remaining:** Design a bounded retrieval change that performs exact/lexical candidate narrowing before the final semantic cap, then add a production-shaped regression proving burst gaps do not hide same-story candidates. No commit, push, deployment, or production mutation performed.
- **Commits:** Uncommitted.

### 2026-08-14 — `main` — Brighten dark reader body text

- **Status:** Complete locally.
- **Scope:** Main reader body text in dark mode only.
- **Decisions:** Keep headings, metadata, navigation, the dark surface, and light mode unchanged; use the subtle option from a four-state Simulator sweep.
- **Changes:** Lifted the dedicated dark reader-body color from `#E5E7EC` to `#E9EBEF`.
- **Validation:** Compared current, subtle, medium, and high treatments against the same seeded Briefing screen on the iOS 26.5 iPhone 17 Pro simulator; both focused palette tests passed, the final app build succeeded, and the selected treatment was recaptured in the same seeded screen.
- **Remaining:** No commit, push, deployment, or production mutation performed.
- **Commits:** Uncommitted.

### 2026-08-15 — `main` — Recover Learning Deck sources across duplicate X URL variants

- **Status:** Complete locally.
- **Scope:** Production diagnosis and repository fix for Learning Deck source ingestion when an X post already exists under its canonical status URL.
- **Decisions:** Treat the canonical X post itself and an expanded external target through the same duplicate-resolution path; keep the duplicate shell as a durable redirect, move user-owned overlays to the canonical row, and expose stable Learning Deck source errors instead of database details. Limit retry-time sibling discovery to unknown shells whose latest URL-analysis task failed, so healthy sources and in-flight ingestion cannot be pre-empted.
- **Changes:** Made tweet and ordinary URL analysis detect an existing typed row before a classification or canonical-URL rewrite, mark the incoming shell skipped with `canonical_content_id`, preserve a non-conflicting shell identity, and relink Knowledge/read/inbox/chat state. Retry-time source resolution can conservatively recover a ready exact-URL or same-X-status sibling when the original redirect transaction rolled back, so the existing deck can use the normal retry action after deployment. Learning Deck API presentation now maps source-pipeline error types to concise client-safe messages. Post-edit cleanup consolidated duplicate redirect mutation, removed redundant temporary state and a one-use error wrapper, and replaced a stringly typed test status with the existing enum.
- **Validation:** All 82 focused analyze-URL, Learning Deck source/generation, and Share Action tests passed; both focused retry-router tests passed; Ruff formatting/lint, focused mypy, module-size guardrails, and all 137 architecture tests passed; `git diff --check` passed. A high-effort, read-only Fable review confirmed the root cause and required narrowing retry-time discovery; the healthy-source negative regression covers that correction. The broader architecture guard reached an unrelated pre-existing OpenAPI description drift and the full Learning Deck router file reached an unrelated hosted-viewer marker assertion; neither touched this change.
- **Remaining:** Canonical database overlays move atomically, while personal Markdown projections retain their existing lazy self-heal instead of adding new post-commit orchestration in this patch. Public-contract artifacts already drift from the current schema description, and the hosted-viewer assertion remains independently failing. No production mutation, deck retry, deployment, commit, or push performed.
- **Commits:** Uncommitted.

### 2026-08-15 — `main` — Restore News clustering and Briefing compaction convergence

- **Status:** Complete locally.
- **Scope:** Production-backed diagnosis and systemic repair for missed same-story News clustering and Briefing News lenses accumulating singleton paragraphs.
- **Decisions:** Rank the full lookback by an index-backed lexical title document before applying the bounded semantic candidate cap; resolve normalized exact relation keys independently of that cap and converge every exact representative. Treat complete current eligibility, including read state, as part of append and compaction plans. Compose without a long transaction, then publish under the per-user Briefing state lock only if the version, pending ownership, composition coverage, source eligibility, and donor snapshots remain unchanged. Repair or remove unavailable donors instead of letting one stale source block a lens, and compact News only when the replacement reduces segment count or repairs invalid coverage.
- **Changes:** Added production-shaped Chloé candidate-window, exact-cluster convergence, competing-refresh, read-during-composition, pending-read, and stale-donor regressions. Extracted canonical News search expressions plus a relation-candidate repository, added matching title GIN and normalized exact-key hash indexes, and corrected the contradictory Meta-positive fixture while retaining the existing Meta-versus-Microsoft negative family. Centralized eligible-unread source resolution; narrowed discussion hydration; moved Briefing state ownership and atomic publication into focused modules; made read marking transaction-preserving and state-lock ordered; and retained failed or stale append work for a later sweep. Updated the Briefing law and architecture notes to match the publication contract.
- **Validation:** The focused Chloé real-embedding eval passed at F1 1.0; Luna and the previous DeepSeek model each composed the three production sources into one valid passage in the earlier model comparison. The corrected cross-company earnings negative retained precision 1.0 but exposed pre-existing within-company under-clustering at recall 0.333, so no global similarity threshold was changed without a full calibration. All 8 frozen Briefing evals and 190 focused Briefing/relation/search/read/assistant-search tests passed. Ruff, formatting, focused MyPy, module-size guardrails, and diff checks passed. The migration upgraded, downgraded, and upgraded locally; forced PostgreSQL plans used the title relation GIN, canonical News search GIN, and exact-story hash indexes. The initial max-effort Fable review's requested law and exact-URL corrections were applied; the final rerun was blocked by Claude Code's session limit after roughly fifteen minutes with no result.
- **Remaining:** Recalibrate the existing within-company earnings recall case only with a full positive/negative threshold sweep; it is not caused by candidate retrieval and was not weakened ad hoc here. No commit, push, deployment, or production mutation performed.
- **Commits:** Uncommitted.

# 2026-08-17 — Require full Briefing segments to pass before marking read

- **Branch:** `main`
- **Scope:** iOS Briefing viewport-driven read marking.
- **Changes:** Changed the read threshold from a segment's midpoint to its bottom edge, so the complete rendered segment must pass above the pinned readable viewport boundary before its sources are marked read. Preserved the initial-offscreen and mark-once guards, and updated the Briefing law and focused boundary tests.
- **Decisions:** Keep the existing per-segment source batching and pinned-chrome boundary; require a strict bottom-edge crossing rather than introducing a percentage or whole-lens read state.
- **Validation:** All 14 `BriefingReadMarkingTests` passed on the iOS 26.4 iPhone 17 Pro Simulator, including bottom-edge before/exactly-at/after-boundary cases; the app and test targets built successfully, and `git diff --check` passed.
- **Remaining:** The authenticated scrolling interaction was not exercised because it would mutate read state. No commit, push, deployment, or production mutation requested.
- **Commits:** Uncommitted.

### 2026-08-17 — `main` — Stream durable chat partials and focus Learning Deck chat

- **Status:** Complete.
- **Scope:** Shared queued chat execution, contextual-assistant capabilities, Exa routing, async status contracts, and Learning Deck chat presentation.
- **Changes:** Added per-turn contextual-assistant tool filtering, explicit Exa routing for current/external deck questions, lazy personal-library sandbox startup, richer deck/source grounding, one shared detached chat executor, exact lease plus attempt-generation terminal fences, durable cumulative partial snapshots, partial status DTOs and generated contracts, coalesced iOS partial reconciliation, and peek/compact/focused deck chat states with a three-quarter-height option. Deep research and the older queued dig-deeper entry point now use the same lease/generation fence. Cleanup extracted the pure capability policy, made queue lease and generation inputs mandatory, checked generation before terminal idempotency, isolated advisory snapshot failures from canonical turns, bounded dig-deeper reclaim retries, fixed Learning Deck route precedence, removed dead result/prompt plumbing, and made client partial replacement and scrolling linear per update.
- **Decisions:** Keep `chat_messages` as the canonical final transcript and treat partial text as advisory; stream only confirmed final-response text rather than tool planning; reuse the existing Exa client and existing queue/task layers instead of adding a variance service or another delivery transport; preserve the one-minute no-progress polling budget while active cursor progress uses a 500 ms cadence; start E2B only for turns that select personal-library tools.
- **Validation:** The initial 210 focused backend chat, assistant, deep-research, dig-deeper, queue, task-handler, and API tests passed; the final cleanup reran 164 focused backend tests successfully. Targeted MyPy, Ruff, and formatting passed; the 759-file/23-ratchet architecture guard, all 137 architecture tests, public-contract regeneration check, and `git diff --check` passed. The migration upgraded, downgraded, and upgraded locally, and the database and migration graph both report `20260817_01` at head. The iOS app built, installed, and launched on the iOS 26.5 Newsly Regression simulator, and all 36 focused polling, full-chat, deck view-model, and flyover tests passed.
- **Remaining:** The Simulator is at the unauthenticated Apple login screen, so the real Learning Deck flyover could not be navigated for authenticated visual interaction QA; runtime logs showed only the simulator runtime's duplicate WebCore/WebKit accessibility-class warning. No commit, push, deployment, or production mutation requested.
- **Commits:** Uncommitted.

### 2026-08-20 — `main` — Close feed, chat ownership, and streaming review gaps

- **Status:** Complete locally.
- **Scope:** Strict-review remediation for assistant capability routing, configured-source downloads, queued provider ownership, Deep Research retries, chat runtime boundaries, and Learning Deck transcript position.
- **Decisions:** Keep new-feed discovery and validation in E2B, while routine accepted-source downloads use the normal host pipeline with public-network redirect checks and a hard response limit. Treat contextual routing as primary guidance rather than an exclusive schema choice except in explicitly constrained product modes. Renew the exact queue lease immediately before paid provider work, and make a Deep Research provider response a durable resumable identity.
- **Changes:** Preserved the normal host ingestion path while adding bounded streaming downloads and redirect/private-address rejection; kept normal assistant capabilities available for compound search-and-action turns; split advisory partial streaming and exact-lease queue orchestration out of `chat_turn_runtime.py`; fenced provider submission; persisted and reused Deep Research response IDs; and stopped Learning Deck partial updates from pulling readers back to the bottom after they scroll upward. Updated the concise laws and architecture notes without increasing any law area beyond its 10–20-law budget.
- **Validation:** All 225 focused backend feed, assistant, chat, queue, pipeline, and API tests passed. Ruff passed across `app`, `tests`, and migrations; focused MyPy passed for 15 production modules; the module guard, all 137 architecture tests, and public-contract check passed. Migration `20260820_01` upgraded, downgraded, and upgraded successfully. The iOS app built, installed, and launched on the iOS 26.5 Newsly Regression simulator, and all 36 focused polling, full-chat, deck view-model, and flyover tests passed.
- **Remaining:** The simulator is at the unauthenticated Apple login screen, so the real Learning Deck flyover could not be exercised visually while a response streamed. No commit, push, deployment, or production mutation was requested or performed.
- **Commits:** Uncommitted.

### 2026-08-20 — `main` — Clean up feed and queued-chat review fixes

- **Status:** Complete locally.
- **Scope:** Behavior-preserving cleanup of the configured-feed HTTP boundary, contextual assistant routing, Deep Research retry state, and the adjacent Learning Deck/law changes.
- **Decisions:** Dispatch accepted-source downloads only to the public unicast addresses that were actually validated, preserve the original Host and TLS identity, bypass environment proxies, and close each pinned connection so virtual hosts cannot share an IP-keyed pooled connection. Leave the queued-turn extraction and view-specific near-bottom tracking separate because each already has clear ownership.
- **Changes:** Pinned every bounded download and redirect hop to its validated address, rejected multicast targets, preserved the canonical response URL, collapsed duplicate small-talk matching, and removed pass-through aliases, predeclared optional state, and an unreachable Deep Research condition. The Learning Deck behavior and concise laws required no further edits.
- **Validation:** All 112 focused feed, HTTP, assistant-routing, Deep Research, queue-streaming, and pipeline tests passed. Ruff lint/format, `git diff --check`, and focused MyPy over 17 production modules passed. The module guard checked 761 files with 23 ratchets, all 137 architecture tests passed, and public contracts remained current. A live `https://example.com/` canary returned HTTP 200 through the pinned-address path with the original response URL.
- **Remaining:** The existing provider-submit-to-response-ID commit window cannot be made atomic across the external provider and PostgreSQL; the persisted response ID prevents repeat submissions after that durable point. No iOS source changed in this cleanup, so the prior native build/test result was not rerun. No commit, push, deployment, or production mutation was requested or performed.
- **Commits:** Uncommitted.

### 2026-08-20 — `main` — Record feed, chat, and law updates

- **Status:** Complete.
- **Scope:** Configured-feed transport, durable queued chat, concise behavioral laws, and architecture notes.
- **Decisions:** Keep feed, chat, and documentation changes in topical commits; leave the separate Briefing viewport-threshold work uncommitted.
- **Changes:** Recorded routine configured-source ingestion outside E2B in `922d838d`, durable streamed chat with exact lease fencing in `cedd2e59`, and the consolidated 10–20-law areas plus matching architecture boundaries in this documentation commit.
- **Validation:** Both implementation commits passed their staged-file Ruff, format, MyPy, and module-size hooks. The final chat fixture pass added 119 focused passing backend tests; the existing high-confidence Vulture advisory baseline remains unchanged.
- **Remaining:** Deploy separately when requested. The independent Briefing read-threshold implementation and its documentation remain local.
- **Commits:** `922d838d`, `cedd2e59`, and this commit.

### 2026-08-22 — `main` — Diagnose repeated Pixel 11 Briefing coverage

- **Status:** Investigation complete; eval coverage added locally.
- **Scope:** Read-only production diagnosis of the August 12 Hardware & Systems Briefing passages shown in the supplied screenshot, plus production-shaped News relation evals.
- **Decisions:** Separate canonical same-story relation matching from event-level Briefing grouping. Do not lower global similarity thresholds or treat every Pixel launch angle as a duplicate: the current matcher recovers the clear Fold-launch and price-hike pairs but over-merges distinct launch, hands-on, and product-angle groups in the precision guard. Keep production unchanged until an event-grouping design preserves exact source coverage.
- **Changes:** Added focused positive eval families for the Pixel 11 Pro Fold launch and Pixel 11 price hike, plus a grouped negative family covering the broader launch, Fold hands-on, Camera Looks, AI Pro trial, Watch health, and SL2T stories. No runtime behavior changed.
- **Validation:** Production health was green at active image `f27c8bac`; all 11 named `process_news_item` tasks completed without retries or errors, while their rows remained singleton representatives. The screenshot text maps to persisted segments `4707`, `4709`, and `4728`. The old newest-150 retrieval window placed the late official Fold source `25488` beyond the earlier Fold candidates, while the current focused real-embedding eval passed both positive families at F1 1.0 and intentionally failed the boundary family at precision 0.273/recall 1.0, exposing over-merging rather than a threshold-recall problem. Both focused relation tests passed; Ruff lint and format checks passed.
- **Remaining:** The historical matcher did not persist candidate scores, so the exact rejection reason for the earlier within-window singleton decisions cannot be reconstructed conclusively. Recommended follow-up is an event-family layer in the existing PostgreSQL/Briefing pipeline, semantic recompaction across already-full News windows, and durable relation-decision telemetry before any backfill. No commit, push, deployment, or production mutation performed.
- **Commits:** Uncommitted.

### 2026-08-22 — `main` — Diagnose unmerged Pixel 11 launch coverage in the News Briefing

- **Status:** Diagnosis complete; evals added; no matcher change.
- **Scope:** Production evidence for fourteen unmerged Google Pixel 11 / Pixel Watch 5 items from 2026-08-12 (ids 25308–25357, 25488, 25647) that rendered as repeated paragraphs in the News Briefing lens, plus curated clustering eval cases.
- **Evidence:** Every item is `cluster_size=1` with no representative in production. On 2026-08-12 production ran image `3f2f9756`, whose candidate window was the 150 newest ready representatives by `ingested_at`. At each item's `processed_at`, 244–455 representatives with a newer `ingested_at` already existed (≈586 representatives were created that day), so the partner item was outside the window and never scored. `9db24b09` (deployed 2026-08-15/16) replaced that window with lexical ranking over the full lookback; the current matcher with real embeddings merges all four production pairs (Fold launch, Pixel 11/Pro/XL launch, price hike, Watch 5) at F1 1.0. Existing rows were never re-reconciled, so the Briefing still shows them split.
- **Changes:** Added `prod_2026_08_12_pixel_11_pro_xl_launch` and `prod_2026_08_12_pixel_watch_5_launch` positive cases alongside the pending Fold-launch and price-hike cases. The pending negative `prod_2026_08_12_negative_pixel_11_launch_vs_adjacent_angles` fails under real embeddings (precision 0.27): the Fold launch absorbs the Pixel 11/Pro/XL launch (combined 0.877, primary) and the Fold hands-on (0.884, primary); the price-hike story only stays separate because the `12gb`/`g6` detail veto blocks the bridge merge after a 0.789 secondary acceptance. Left failing as a documented gap pending a product decision on same-event/different-product granularity.
- **Validation:** Real-embedding eval passed for all four positive Pixel cases; `pytest -k pixel` (4 passed); Ruff lint/format clean on the cases file.
- **Production check:** Daily merge rate for global ready items was 4–9% from Aug 9–15 and 21–29% from Aug 16 onward (Aug 18–19 were low-volume ingestion days), confirming the retrieval fix is live. 4,427 pre-fix representatives (created Aug 9–16 06:00 UTC) are still unread by any user, 2,292 of them from Aug 12 onward; the eight Pixel pair items already carry `news_item_read_status` rows.
- **Remaining:** Decide whether to re-reconcile still-unread pre-`9db24b09` representatives (one-off backfill) so the Briefing compacts them; decide the same-event granularity rule before tuning thresholds or the detail veto. No commit, push, deployment, or production mutation performed.
- **Commits:** Uncommitted.

### 2026-08-22 — `main` — Widen relation lookback and size Briefing news windows by event

- **Status:** Complete locally.
- **Scope:** Follow-up to the Pixel 11 dedupe diagnosis: same-story candidate lookback and Briefing news window planning.
- **Decisions:** Raise `news_list_related_lookback_days` from 7 to 14 (production has no env override). Make a Briefing news window hold up to `briefing_news_window_max` *events* rather than sources: canonical representatives covering one event are grouped before slicing and always land together, so a launch with seven angles composes as one passage instead of spanning three. Grouping uses the Briefing category embedding model on title/summary/key points with a greedy centroid threshold (`briefing_news_event_similarity`, default 0.78 — calibrated on the Aug 12 Pixel titles: launch angles and Watch 5 score 0.78–0.94, Samsung Fold 0.73–0.75, iPhone/Halo ≤0.68) plus a shared distinctive title token guard. Embedding failure falls back to arrival order. Citations are not surfaced anywhere new.
- **Changes:** Added `app/services/briefing/event_grouping.py`; `plan_windows` takes `source_of` and slices by events (refresh append and compaction both pass it; non-news tiers unchanged). Added `tests/services/briefing/test_event_grouping.py` (grouping, token guard, threshold rejection, failure fallback, event-counted windows); stubbed the encoder in two progressive-refresh tests whose numbered fixture titles would legitimately group into one event. Updated law B4.
- **Validation:** 286 Briefing/relation/module-size tests passed with zero network embedding calls; Ruff lint/format and mypy clean on touched files.
- **Follow-up (same day):** Compaction now counts events too. Added nullable `briefing_segments.event_groups` (migration `20260822_01`; null = one event per source for legacy rows), persisted from `plan_event_windows` on append and compaction. `briefing_fragmentation_metrics` takes per-segment event groups and reports `unread_event_count` / `window_event_limit` (an event stays unread while any source is unread); `_compaction_donors` admits news segments with ≤2 unread events; `admin briefing status` reports the same fields. Migration upgraded, downgraded, and upgraded locally; 419 Briefing/admin/relation/model/guardrail tests passed with zero network embedding calls; Ruff and mypy clean. `tests/migrations/test_task_ownership_chat_turn_migration.py` fails identically without these changes (pre-existing `partial_text` duplicate column).
- **Remaining:** The over-merge of same-event/different-product pairs in the relation matcher remains a product decision. No commit, push, or deployment performed.
- **Commits:** Uncommitted.

### 2026-08-24 — `main` — Restore final Briefing podcast read completion

- **Status:** Complete locally.
- **Scope:** iOS Briefing trailing scroll geometry for viewport-driven read marking.
- **Evidence:** Production persisted two podcast reads after the full-passage threshold shipped, confirming the read API and ordinary segment crossing remain healthy. The final segment had only 40 points of bottom content margin, so its bottom edge could not cross the pinned read boundary at maximum scroll.
- **Changes:** Size the trailing scroll clearance from the visible container and pinned read boundary so the final segment in Podcasts, Articles, or News can pass fully above the readable viewport and trigger the existing mark-once flow. Added focused clearance regressions.
- **Decisions:** Preserve the full-passage product law and existing read-state pipeline; fix the unreachable terminal geometry instead of weakening the threshold or special-casing podcasts.
- **Validation:** All 16 `BriefingReadMarkingTests` passed on the iOS 26.5 Newsly Regression simulator; the focused test run built the app and test targets successfully.
- **Remaining:** Authenticated simulator interaction remains unavailable on the clean regression simulator. No production mutation, commit, push, or deployment performed.
- **Commits:** Uncommitted.

### 2026-08-24 — `main` — Correct terminal Briefing read geometry with live reproduction

- **Status:** Complete and committed; not deployed.
- **Scope:** Recheck and repair automatic read completion for the final Podcast segment.
- **Evidence:** Reproduced against the authenticated local showcase user on the iOS 26.5 Newsly Regression simulator. The first two Podcast segments decremented the unread count from 3 to 1 and persisted normally. At maximum scroll, the final rendered segment remained visibly below the pinned boundary, the count stayed at 1, and no final read mark was persisted.
- **Cause:** The first repair subtracted an outer-coordinate read boundary from `ScrollGeometry.containerSize.height`, which is a local size rather than a position in the same coordinate space. That under-allocated the terminal scroll clearance whenever the viewport started below the Briefing chrome.
- **Changes:** Measure the scroll viewport's bottom edge in `briefing.read-tracking`, the same named coordinate space as the segment and pinned boundary, and derive clearance from those two positions. Added a regression covering a viewport whose origin is displaced by overlaid chrome.
- **Decisions:** Preserve strict full-segment passage and the existing optimistic/debounced read pipeline; correct the coordinate-space mismatch rather than relaxing the completion threshold.
- **Validation:** All 17 `BriefingReadMarkingTests` passed. Rebuilt and installed the app on the iOS 26.5 Newsly Regression simulator, reset the authenticated local showcase user to three unread Podcast segments, and repeated the same scroll: counts moved `3 → 2 → 1 → 0`; production-shaped persistence reported zero Podcast segments and zero unread sources afterward. Simulator logs recorded all three optimistic publications and index reconciliation through version 4. `git diff --check` passed.
- **Remaining:** No production mutation or deployment performed.
- **Commits:** `18e8fb87`.

### 2026-08-24 — `main` — Identify Briefing articles and podcasts by title and source

- **Status:** Complete and committed; not deployed.
- **Scope:** Briefing composition metadata and prompts for article and podcast passages.
- **Decisions:** Require the exact provided work title and available publication or show name near the beginning of each treatment; omit unavailable source names instead of inventing them.
- **Changes:** Added the content source name to the composition payload, tightened both deep-tier prompts, advanced the composition prompt version to `briefing-v6`, and recorded the attribution behavior in law B14.
- **Validation:** All 60 focused prompt, composer, and source tests passed; Ruff and `git diff --check` passed on the touched scope.
- **Remaining:** Existing persisted Briefing segments retain their original text until normal regeneration. No deployment or production mutation performed.
- **Commits:** `75b3e35`.

### 2026-08-26 — `main` — Stabilize scroll reads and Knowledge loading

- **Status:** Complete locally.
- **Scope:** iOS Briefing News read persistence during rapid scrolling and the unified Knowledge timeline's initial/image loading presentation.
- **Evidence:** Authenticated Simulator tracing showed segment geometry and the read API were healthy, but successive scroll marks reused one cancellable debounce task. A later mark could cancel a request after it reached the server, leaving the visible unread count behind persisted state until the Briefing reopened. A cold-cache Knowledge recording showed Narrations publishing alone before the slower saved/chat/deck sources arrived, while each uncached timeline thumbnail also displayed its own spinner.
- **Changes:** Split Briefing read debounce and persistence into separate tasks, serialize pending batches through one non-cancellable drain, and retain retry/reconciliation behavior. Coalesced Knowledge's four initial source loads into one merged timeline publication, and replaced per-thumbnail spinners with the row's stable feature icon while explicitly disabling replacement animation. Added focused regressions for in-flight read persistence, atomic initial timeline publication, and the compact artwork contract.
- **Decisions:** Preserve strict full-segment read geometry, optimistic counts, source-level partial failure behavior, and live post-load timeline updates. Remove only intermediate loading publications and image-slot motion rather than hiding useful final artwork.
- **Validation:** All 67 focused Briefing read-marking/view-model, image-cache, and Knowledge timeline tests passed; all 33 iOS spacing/source contracts passed; the app built and ran on iPhone 17 Pro, iOS 26.4. Twelve rapid News scroll gestures reduced the visible aggregate from `524 → 461`, eight serialized read-mark requests returned HTTP 200, and the authoritative index also ended at 461 sources. A cleared-cache 5 fps recording transitioned directly from Briefing to the complete Knowledge timeline without the former narration-only intermediate list or thumbnail spinners. `git diff --check` passed.
- **Remaining:** The client fixes still require a separately distributed iOS build; the backend production workflow does not distribute through Apple surfaces.
- **Commits:** Recorded in the topical release series following `452001b9`.

### 2026-08-26 — `main` — Recover the blocked production search migration

- **Status:** Complete locally.
- **Scope:** Production retry safety for the canonical Knowledge/content search indexes and future `content_metadata` writes.
- **Evidence:** The 2026-08-26 Docker deployment stopped before the blue-green slot switch at Alembic revision `20260825_01`. Production remained on revision `20260823_01` with `idx_contents_search_document_gin` invalid/not-ready and one JSON row (`contents.id = 29873`) containing a decoded NUL that PostgreSQL could store as `json` but could not extract as text for the new index expression. A current read-only recheck found the same single repair candidate and unchanged failed index state.
- **Changes:** Sanitize decoded control characters inside the migration while preserving literal `\\u0000` text; lock selected rows; build and swap named replacement indexes without dropping a valid predecessor first; reuse a valid replacement left by an interrupted swap; and retry only the exact PostgreSQL `22P05` NUL failure so writes from the still-live old deployment cannot recreate the race during `CREATE INDEX CONCURRENTLY`. Added a `SanitizedJSON` bind type for future ORM writes and shared its recursive policy with the existing admin repair command. Updated two stale Learning Deck release assertions to the current peek/compact/focus flyover and no-second-fullscreen product law.
- **Decisions:** Keep the migration self-contained, preserve clean metadata byte-for-byte, preserve supported JSON comparison/path behavior, fail normally for non-NUL index errors, and leave production untouched until the tested changes are explicitly released.
- **Validation:** PostgreSQL integration tests reproduce the exact production failure, a concurrent old-app write during index build, a valid replacement left before rename, and cleanup after retry exhaustion. Focused migration/model/admin/search tests, Ruff, and mypy pass. All 91 focused native iOS tests pass on iPhone 17 Pro, iOS 26.4.1, and all 72 client source contracts pass. The definitive full backend suite passes with 2,863 passed and 40 skipped; the only earlier failures were two independently reproduced stale Learning Deck assertions, whose contracts now match the current product law.
- **Remaining:** The release workflow must prove the production migration and live health; no direct production data repair is planned.
- **Commits:** Recorded in the topical release series following `452001b9`.

### 2026-08-26 — `main` — Accept clean-simulator URL confirmations in iOS release tests

- **Status:** Complete locally; release validation in progress.
- **Scope:** AXe iOS release harness handling for custom URL launches on a newly created iOS 26.5 simulator.
- **Evidence:** The complete release gate reached an iOS-owned `Open in “Newsbuddy”?` confirmation after `simctl openurl`. Because the harness only waited for the app expectation, the alert remained above later tests and caused unrelated launch assertions to fail. Once accepted, a clean Safari profile also showed its one-time `items in the More menu` popover above the toolbar.
- **Changes:** Detect the exact Newsbuddy system confirmation during URL assertions and tap its enabled `Open` button before resuming the existing product-state check. Dismiss Safari's identified first-run popover before opening its More menu. Added focused detector tests for the valid URL prompt and unrelated, incomplete, or disabled states.
- **Decisions:** Keep all product assertions and release flows unchanged; handle only the one-time OS-owned confirmation at the URL boundary. Give content-detail chat routes explicit replacement intent so the root atomically replaces the active detail path, while notifications and ordinary queued routes retain non-interrupting behavior.
- **Follow-up:** The completed AXe matrix exposed a real content-detail handoff race: chat creation succeeded, but dismissing the Knowledge detail and separately publishing the route could leave the user on the refreshed timeline. Removed that timing dependency, preserved replacement intent through the shared route queue, and added focused coordinator and source contracts.
- **Validation:** Focused unit, full Python, native iOS, and complete clean-simulator UI release gates pending on the resulting commit.
- **Remaining:** Commit the harness fix, rerun every release gate, then push and verify the production deployment.
- **Commits:** Recorded in the topical release series following `6a93f1e9`.

### 2026-08-27 — `main` — Repair iOS Briefing scroll reads and Knowledge launch flicker

- **Status:** Complete locally; release validation in progress.
- **Scope:** Briefing terminal scroll geometry, optimistic read reconciliation, read-mutation transaction visibility, and Knowledge initial/image loading presentation.
- **Evidence:** Production Build 200 was confirmed as exact commit `865ebd8`, but its News scroll sessions sent no Briefing read-mark POSTs. The regression began when terminal clearance became dependent on a state-writing scroll-frame observer. Local authenticated tracing then exposed two independent repaint races: a pre-read index response could publish after optimistic state, and a successful version-700 POST could be followed immediately by a version-699 GET because the request-scoped database dependency committed after sending the response. A 10 fps cold-cache recording isolated the Knowledge flash to one frame of `Loading knowledge`, not image download progress.
- **Decisions:** Preserve the strict full-segment read threshold and optimistic styling. Derive terminal clearance from stable scroll-container geometry and chrome inset, reject canceled index deliveries and pre-mutation versions only during read reconciliation, and make the server's accepted read version durable before responding. Keep a delayed loading affordance for genuinely slow Knowledge loads while suppressing the fast transient state and image replacement animation.
- **Changes:** Removed the feedback-prone Briefing viewport-frame observer, hardened non-finite geometry, added generation and read-reconciliation version fencing, committed both Briefing read endpoints before response, delayed the Knowledge initial spinner by 250 ms, and disabled saved-row image replacement animation. Added focused geometry, race, and cross-session persistence regressions and updated the Briefing and Knowledge laws.
- **Validation:** Both new race regressions failed before their fixes and passed afterward. The focused Briefing and Knowledge XCTest slices passed before the final release run. A rebuilt authenticated simulator marked three remaining eight-source Cybersecurity segments in sequence, including the terminal segment (`381 → 373 → 365 → 357` aggregate unread); PostgreSQL and a fresh index agreed at version 703 with zero unread and zero active segments in that lens. A cleared-cache 10 fps recording transitioned directly into the complete Knowledge timeline without the prior loading frame.
- **Remaining:** Run the complete Python, native iOS, and Maestro release gates; commit, push to `main`, and verify the Docker production deployment. Apple distribution remains a separate Xcode build path.
- **Commits:** `e5eca42c`, `db614e05`, `663ead4f`, and this release-gate correction commit.

### 2026-08-27 — `main` — Remove lazy-row dependency from Briefing scroll reads

- **Status:** Complete; committed and pushed to `main` for iOS distribution.
- **Scope:** iOS Briefing News scroll passage, lazy row lifecycle, focused XCTest coverage, and a real isolated AXe scroll regression.
- **Evidence:** Production Build 196 (`f27c8bac`) emitted 84 ordinary read-mark requests, while Build 197 (`ec85be806`) emitted none despite 88 lens reads. The only Briefing read-path change between them was `4ee3a2bc`, which moved the trigger from midpoint passage to strict full-body passage. Builds 198–201 retained the failure; Build 201's sole read request followed a refresh-driven layout pass. The full-body per-row geometry callback occurred late enough for `LazyVStack` recycling or callback coalescing to lose the crossing.
- **Decisions:** Preserve law B11's strict full-body threshold and initial-offscreen guard. Make the scroll view the single moving observer, store each rendered segment's stable content frame centrally, and compare it with raw `ScrollGeometry.contentOffset`. Retain the existing optimistic and debounced persistence pipeline because production and local evidence prove it commits correctly once the view emits a passage.
- **Changes:** Replaced the per-row moving Boolean geometry modifier and cross-`TabView` coordinate dependency with a lens-page tracker keyed by segment ID. The tracker retains frames after lazy eviction, requires actual viewport visibility, infers forward fling passage between coalesced samples, rejects zero-height placeholders and stale older-generation callbacks, preserves same-generation appends, and keeps per-pixel read sampling from driving header view-model work. Added an isolated AXe regression that scrolls News and asserts UI/DB read agreement before any refresh.
- **Validation:** All 517 native iOS tests passed, including all 26 `BriefingReadMarkingTests`; all 72 client source-contract tests passed; Ruff, E2E collection, harness unit tests, and `git diff --check` passed. An isolated AXe run on iPhone 17 Pro, iOS 26.4, proved launch `News=6`/DB rows `0`, then one upward swipe changed the UI `6 → 5 → 4`, persisted two matching rows through one `POST /api/briefing/read-marks`, retired two segments, and enqueued no refresh; an exact-checkout rerun after final hardening also passed while allowing scroll inertia to advance the count further. A separate authenticated iOS 26.5 Simulator fast fling changed News `24 → 0`; four optimistic segment publications appeared immediately, the server persisted all 24 sources in one batch, and a fresh developer-user status reported version 2 with zero News segments and unread sources.
- **Remaining:** Final physical-device acceptance requires a distributed iOS 27 build containing this commit.
- **Commits:** This commit.

### 2026-08-27 — `main` — Recover Briefing passage across early layout callback ordering

- **Status:** Complete; iOS distribution pending.
- **Scope:** Briefing read-tracker startup geometry, slow-scroll and grouped-source AXe coverage, and production build/session verification.
- **Evidence:** Production remained healthy on exact backend image `ac3d55e7`, but the reported physical-device session at 20:42 UTC still identified itself as `newsly/201` and emitted no read-mark POST. Build 201 maps to `9b645d85`; internal TestFlight Build 202 maps to `ac3d55e7`, but no Build 202 production traffic was observed. The earlier Build 201 session reproduced the refresh-only shape: it emitted a read-mark POST only after `POST /api/briefing/refresh`. Isolated iOS 26.4/26.5 runs on `ac3d55e7` proved ordinary, incremental, immediate-after-lens-selection, and immediate-after-tab-return scrolling all persist without refresh. Static execution exposed the remaining seam: scrolling can occur while the pinned read boundary is nil, and the committed tracker discarded that traversal before boundary or late lazy-row geometry arrived.
- **Decisions:** Preserve strict full-segment passage and the initial-offscreen guard. Retain the minimum finite scroll offset observed while a page is actively eligible, then use that history with the current boundary, viewport, and stored row frame. Reset it on disable or document generation change. This makes boundary, viewport-height, and lazy-row frame callbacks order-independent without treating a restored already-past segment as read.
- **Changes:** Hardened `BriefingScrollReadTracker` for boundary-before-frame and boundary-before-usable-viewport ordering. Added incremental strict-boundary, dynamic-height, late-boundary, late-frame, invalid-viewport, disable/re-enable, and no-traversal regressions. Parameterized AXe drag distance/duration and strengthened the real News regression to use eight two-source segments, assert each segment retires atomically, prove UI/database agreement, and prove a second passage still works after optimistic render-model replacement.
- **Validation:** All 525 native iOS tests passed, including 34 `BriefingReadMarkingTests`; all 72 client source-contract tests and all 16 AXe harness unit tests passed; Ruff, E2E collection, and `git diff --check` passed. Both real AXe profiles passed against the current build in 25.72 seconds: a standard swipe and a slow incremental drag, with 16 grouped sources, persisted UI/database agreement, continued scrolling, and zero Briefing refreshes. A separate isolated matrix recorded `8 → 6 → 4 → 2 → 0` across four modest drags and one fling, with matching batched POSTs and no refresh.
- **Remaining:** The physical iOS 27 device has not exercised Build 202, and this callback-order hardening is newer than Build 202. A subsequent Xcode Cloud build is still required before device acceptance. No production deployment or iOS distribution was performed for this follow-up.
- **Commits:** This commit.

### 2026-08-27 — `main` — Keep Briefing chrome aligned after document replacement

- **Status:** Complete locally; not committed or distributed.
- **Scope:** iOS Briefing cold-resume and refresh layout after an ordered Lens document is replaced.
- **Evidence:** A replacement increments the Lens document generation and gives its `ScrollView` a new identity, intentionally returning it to offset zero, while the view-owned chrome model and view-model pinned flag retained the previous fully collapsed state. The new page therefore applied the full expanded-header content inset beneath a still-collapsed overlay, leaving a persistent blank band above the first passage.
- **Changes:** Reset the replaced Lens's collapse amount and pinned flag at its document-generation boundary, without changing other Lens positions or ordinary background/tab-return behavior. Added focused coverage that the reset is Lens-local.
- **Decisions:** Preserve law B13's top reset for an ordered-document replacement and preserve scroll position when no replacement occurs; synchronize the header to that existing reset rather than removing the document identity boundary.
- **Validation:** All 35 `BriefingReadMarkingTests` and all 45 `BriefingViewModelTests`/`BriefingViewModelRetentionTests` passed after cleanup. The app rebuilt and launched on the authenticated iOS 26.5 Newsly Regression simulator, where the Podcast page rendered its first passage directly beneath the expanded chrome with no blank band. A lock/unlock cycle from a fully collapsed Podcast page preserved its correct scroll position without introducing a blank band. `git diff --check` passed.
- **Remaining:** No commit, push, production mutation, or iOS distribution performed.

### 2026-08-28 — `main` — Harden Learning Deck browser validation

- **Status:** Complete locally; not committed or deployed.
- **Scope:** Learning Deck viewer injection, Reveal runtime contract, browser-validation diagnostics, repair ownership, and public failure presentation.
- **Evidence:** Production deck 24 had two failed generation attempts whose artifacts reached browser validation but timed out while comparing horizontal Reveal indices with an omitted vertical index. The viewer also rewrote the first textual `Reveal.initialize(...)` occurrence, which corrupted qualified and assignment-form initializers. A clean browser canary then exposed that the unversioned Reveal CDN had advanced to 6.0.1 and activated mobile scroll view before the viewer configured slide mode.
- **Changes:** Removed generated-script rewriting; switched the viewer to Reveal's public scroll-view API; normalized all slide indices; pinned newly generated external Reveal assets to 6.0.1; and added phase, orientation, target-slide, runtime, request, response, and Reveal-state diagnostics. Repair ownership now defaults closed and only generated-artifact failures explicitly opt into one model repair; malformed reports and process/internal failures stop without spending a repair call. Viewer fitting follows initialization and viewport changes without reconfiguring on every slide transition. Public responses now hide agent and validator internals.
- **Decisions:** Validate the exact hosted viewer shell, preserve authored scripts byte-for-byte, make the supported Reveal runtime explicit, and keep detailed reports in internal task logs. Existing stored decks remain viewable through the public viewer configuration path.
- **Validation:** Ruff and mypy passed on the touched Python scope; all 93 focused Learning Deck service/router regressions passed after cleanup. A fresh clean E2B Chromium run of the production validator passed a two-slide assignment-form initializer in portrait and landscape, checked both slides without overflow, and completed a next/previous round trip. Replaying the two retained failed artifacts produced structured, repairable slide-overflow reports instead of opaque timeouts. `git diff --check` passed.
- **Remaining:** No commit, push, deployment, production task retry, or stored-artifact mutation performed.

### 2026-08-28 — `main` — Add generated Learning Deck thumbnails to Knowledge

- **Status:** Complete locally; not committed or deployed.
- **Scope:** Runware thumbnail generation, Learning Deck artifact publication and API projection, stable signed-image caching, and compact Knowledge deck artwork.
- **Changes:** Generate one direct 1024×1024 typographic deck cover from the completed deck title and source notes with Recraft V4.1 through Runware; publish it as `assets/thumbnail.png` in the successful artifact bundle; expose a private signed thumbnail URL; and render it in the existing 40-point Knowledge artwork slot. Added a stable cache identity keyed by deck and successful attempt so rotating signed URLs do not clear or redownload unchanged artwork. Thumbnail failure remains non-blocking and the existing feature icon remains the fallback. Extracted the shared Runware request, retry, and usage-recording path into its own service so `image_generation.py` remains below the repository module-size guardrail.
- **Decisions:** Keep the thumbnail inside the existing immutable artifact bundle rather than adding database state or an SVG composition path. Give thumbnails their own Recraft model and native dimensions, independent of the infographic provider/model, and do not fall back to another paid image provider when Runware is unavailable.
- **Validation:** Ruff passed on the touched backend and test scope; all 62 focused Learning Deck image, artifact, generation, service, router, and smoke tests and all 47 relevant iOS source-contract tests passed; all seven focused `ImageCacheServiceTests` and `LearningDeckAPIMappingTests` passed on iPhone 17 Pro Max, iOS 26.4. Public contracts were regenerated and checked. After the Runware extraction, the module-size guardrail, Ruff, mypy, and all seven focused image-generation tests passed again.
- **Remaining:** A four-model Runware comparison on the local DeepSpec deck selected Recraft V4.1 for its exact typography, deck-cover hierarchy, simple system mark, and 40-point legibility; the four successful requests cost $0.1495 total. Broader visual consistency across varied deck topics remains to be observed. No commit, push, deployment, production generation, or stored-artifact mutation performed.

### 2026-08-28 — `main` — Select Seedream for article and podcast images

- **Status:** Complete; committed and pushed to `main`.
- **Scope:** Two real articles and two real podcasts, FLUX.2 Dev and Seedream 5.0 Lite, and baseline/minor/major prompt variants.
- **Evidence:** All 24 Runware generations succeeded for $0.4896 total. Seedream followed the no-text editorial brief more consistently; FLUX.2 repeatedly introduced poster text, labels, or generic AI-device imagery. The minor prompt edit produced the preferred set across the four sources.
- **Changes:** Made Runware and `bytedance:seedream@5.0-lite` the code defaults for long-form images; request its native 2848×1600 2K output without an unsupported negative prompt; added the selected minor anti-generic art direction and a no-recognizable-invented-likeness rule; and registered the $0.035 request price for usage estimates. Explicit alternate Runware models retain their existing dimensions and negative-prompt behavior.
- **Decisions:** Use Seedream 5.0 Lite plus the minor anti-generic art direction as the production default. Prefer a single 2K output and persistent reuse; Runware charges the same $0.035 for 2K and 3K.
- **Validation:** Built and browser-checked a local interactive comparison report with 24 gallery cards, four selected results, exact prompts, filters, and full-size views; all 48 optimized image assets returned successfully. Ruff passed on the touched Python scope; all 28 focused image-generation and vendor-cost tests and all 90 core settings tests passed; `git diff --check` passed.
- **Remaining:** The report and generated evaluation assets live under `.tmp/main-image-prompt-lab-2026-08-28/` and are intentionally untracked. The ignored local deployment-environment source is aligned to Seedream, but changing live deployed environment state is separate from this code commit.

### 2026-08-30 — detached `c77aa869` — Add the Rust PostgreSQL queue and worker kernel

- **Status:** Implemented locally as a migration slice; no task handler has been cut over.
- **Scope:** SQLx enqueue/dedupe/access grants, task-runtime stamping, fair `SKIP LOCKED` claims, opaque leases, renewal/finalization/cancellation fencing, retries and deferrals, expired paid-work reclaim accounting, PostgreSQL notification fan-out with polling fallback, and the connection-free Rust worker execution loop.
- **Decisions:** Keep task executor owner/version/namespace immutable from enqueue through finalization; lock active runtime-ownership rows in the enqueue transaction; restrict Rust workers to registered handler namespaces; represent queue cancellation as terminal `failed` because the public queue status contract has no cancelled state; and keep provider handlers outside the kernel so no database transaction can cross external work.
- **Validation:** Targeted `cargo fmt` and `cargo check -p newsly-queue -p newsly-worker --tests` passed. Generated task-catalog and queue-transition fixtures are compiled into focused Rust tests but broad execution is deferred to the consolidated migration gate as requested.
- **Remaining:** Register concrete Rust task handlers and a process entrypoint only alongside each task-type ownership cutover; run PostgreSQL concurrency/integration fixtures and the complete Rust/Python release gate at the final validation phase.

### 2026-08-30 — detached `c77aa869` — Add the first Rust content-worker slice

- **Status:** Implemented locally behind Python-default task ownership; not committed, deployed, or cut over.
- **Scope:** Rust `ANALYZE_URL` and `PROCESS_CONTENT` orchestration, the authenticated v1 Python document-extractor client, Rust-owned Firecrawl fallback, canonical body storage, SQLx content/feed/usage persistence, and the standalone content-worker process.
- **Decisions:** Keep prepare input connection-free during all external work, then lock the exact live queue lease before publishing content state, every extractor usage event, Firecrawl usage, downstream work, and the queue transition in one transaction. Accept delegation only from PubMed resolution, reuse a sufficiently large analysis body without a second extractor call, and keep runtime ownership seeds on Python.
- **Changes:** Registered the two handlers under a Rust-only ownership scope; added lease-loss cancellation, typed failure/retry mapping, local content-addressed body staging, analysis-time canonical overlay relinking, feed metadata/subscription and initial backfill handoff, podcast-media handoff, and `SUMMARIZE` enqueueing. Added fail-closed worker configuration, redacted diagnostics, graceful shutdown, and PostgreSQL `LISTEN/NOTIFY` wakeups with polling fallback.
- **Validation:** Targeted formatting, warning-denied Clippy across `newsly-queue` and `newsly-worker`, package checks, and focused library/fixture tests passed. Broad PostgreSQL concurrency, extractor shadow, and product release suites remain deferred to the consolidated migration gate.
- **Remaining:** Before ownership promotion, add S3-compatible body storage, port or isolate instruction/X-specific `ANALYZE_URL` behavior, validate/classify extractor feed links with the bounded feed runtime, and run live PostgreSQL lease/finalization plus publisher/extractor shadow canaries. Generic feed candidates currently use conservative URL heuristics and the Apple podcast handoff relies on the existing downstream resolver.
- **Commits:** Uncommitted.

### 2026-08-30 — detached `c77aa869` — Port admin API-key management to Rust

- **Status:** Implemented locally behind Python-default route ownership; not committed, deployed, or cut over.
- **Scope:** SQLx API-key generation, SHA-256-only persistence, constant-time verification helpers, display-once create results, newest-first summary queries, idempotent revocation, admin-user attribution, signed `admin_session` compatibility, form handling, server-rendered management HTML, login redirects, typed errors, route write fences, and truthful OpenAPI operations for all three existing admin routes.
- **Decisions:** Preserve the exact `newsly_ak_<8 hex>_<32 base64url>` bearer format and both one-time plaintext response aliases; never load hashes on list paths or expose plaintext after creation; keep the existing lazy system-admin identity without issuing a no-op update on every page view; authenticate before parsing form/path input; and preserve `303` redirect behavior for missing sessions and successful revocation.
- **Validation:** Targeted Rust formatting and `cargo check -p newsly-api` passed. The exported Rust OpenAPI contains `adminApiKeysPage`, `adminApiKeysCreate`, and `adminApiKeysRevoke` at the canonical paths, with the form body represented as `Body_adminApiKeysCreate`. Broad database and HTTP parity suites remain deferred to the consolidated migration gate as requested.
- **Remaining:** Run cross-runtime cookie, rendered-page, PostgreSQL create/list/revoke, and API-key authentication golden fixtures before promoting any route owner to Rust.
- **Commits:** Uncommitted.

### 2026-08-30 — detached `c77aa869` — Port Learning Deck HTTP and SQL ownership to Rust

- **Status:** Implemented locally behind Python-default route and task ownership; not committed, deployed, or cut over.
- **Scope:** All nine authenticated Learning Deck operations, their generated Rust wire contracts and OpenAPI operation IDs, SQLx list/detail/create/retry/delete/share projections and mutations, queue/access handoffs, canonical-content retry recovery, signed private/share URLs, and post-commit artifact cleanup.
- **Decisions:** Keep `llm_tasks` canonical with legacy-run read fallback; preserve one active deck attempt per user, idempotent same-deck create/retry behavior, stable share nonces, private URL expiry, safe public error projection, and immutable prepare data around external object deletion. Every state mutation verifies its route-ownership version in the same transaction and enqueues durable work before commit.
- **Validation:** `cargo fmt --all`, `cargo check --locked -p newsly-api --all-targets`, and focused `git diff --check` passed. Broad PostgreSQL/HTTP/token parity and full release validation remain deferred to the consolidated migration gate as requested.
- **Remaining:** Exercise the nine operations against the isolated PostgreSQL parity harness and stored legacy decks before promoting route ownership; generation remains on the Python-owned `RUN_LLM_TASK` executor until its Rust handler is cut over.
- **Commits:** Uncommitted.

### 2026-08-30 — detached `c77aa869` — Port X bookmark synchronization to Rust

- **Status:** Implemented locally behind Python-default task ownership; not committed, deployed, or cut over.
- **Scope:** Native `SYNC_INTEGRATION` execution for X, official OAuth refresh and bookmark pagination, Python-compatible Fernet credentials, SQLx integration state/ledger/usage persistence, canonical content ingestion, per-user Knowledge routing, and the standalone Twitter-queue worker process.
- **Decisions:** Preserve the Python trigger and cooldown behavior, ten-page/five-bookmark bounds, UTC-day X resource billing deduplication, and unrecoverable OAuth-refresh classification. Commit a short immutable prepare plan before every provider call, cancel HTTP work on lease loss, then publish refreshed credentials, snapshots, ledger/checkpoints, Knowledge saves, downstream queue work, usage, sync status, and the task transition in one exact-lease transaction. Lock the active user before the connection during finalization so account deletion wins cleanly without deadlock or stale output.
- **Changes:** Added reusable provider-layer integration-token crypto; typed X token, identity, and bookmark clients; an isolated X sync repository; a lease-aware handler/finalizer; oldest-first canonical tweet ingestion with snapshot provenance and canonical-destination ledger repair; canonical content relinking for X ledger rows; and an isolated Rust `x_sync_worker` binary scoped only to Rust-owned `sync_integration` rows on the Twitter queue.
- **Validation:** `cargo fmt --all -- --check` and one combined `cargo check --locked -p newsly-providers -p newsly-db -p newsly-worker --all-targets` passed; worker-only Clippy also passed without warnings. Broad PostgreSQL/provider parity and product suites remain deferred to the consolidated migration gate as requested.
- **Remaining:** Switch the Rust API integration routes to the shared provider cipher, exercise refresh rotation, reauthentication, checkpoint pagination, account deletion, duplicate canonicalization, and retry/finalization against isolated PostgreSQL and mocked X, then promote only the `sync_integration` ownership namespace after those gates pass. X-specific Rust `ANALYZE_URL` processing remains a separate content-worker cutover prerequisite; during coexistence downstream tasks continue to follow their independently stamped runtime owner.
- **Commits:** Uncommitted.

### 2026-08-30 — detached `c77aa869` — Port the remaining Content and News API operations to Rust

- **Status:** Implemented locally behind Python-default route ownership; not committed, deployed, or cut over.
- **Scope:** Native Rust implementations for Content/News article conversion, series backfill, summary narration and MP3 delivery, typed tweet suggestions, Share Action submission status projection, Content and News discussion refresh, podcast episode discovery, and sectioned mixed search.
- **Decisions:** Preserve the existing generated wire contracts and operation IDs; keep every provider request outside a PostgreSQL transaction; re-open a fresh, ownership-fenced transaction only for final persistence and queue enqueueing; use SQLx and the queue kernel for durable writes; and degrade mixed-search provider sections independently while retaining local results.
- **Changes:** Added typed contract, SQLx repository, provider-gateway, and Axum route modules. Article conversion now saves to Knowledge and enqueues processing plus agent-data synchronization atomically; feed backfill persists inbox entries and processing work atomically; discussion refresh persists provider results through the canonical Content/News stores; narration uses the native ElevenLabs client; and tweet generation uses the native Rig structured-output runtime.
- **Validation:** Targeted formatting passed for the five new/extended Rust modules; `cargo check --locked -p newsly-providers --all-targets`, `cargo check --locked -p newsly-db --all-targets`, and `cargo check --locked -p newsly-api --all-targets` passed. The exported Rust OpenAPI contains 117 operations and all ten canonical operation IDs. Broad PostgreSQL/provider parity and product release suites remain deferred to the consolidated final gate as requested.
- **Remaining:** Exercise the ten routes against isolated PostgreSQL and mocked external providers before promoting their runtime owners from Python to Rust; validate installed-client response parity through the final generated-contract gate.
- **Commits:** Uncommitted.

### 2026-08-30 — detached `c77aa869` — Port onboarding and agent onboarding to Rust

- **Status:** Implemented locally behind Python-default route and task ownership; not committed, deployed, or cut over.
- **Scope:** Native Rust implementations for profile generation, voice parsing, fast discovery, audio-discovery startup, onboarding completion, and the agent start/status/complete surface, preserving all eight public operation IDs.
- **Decisions:** Keep the pinned onboarding model and exact privacy/provider-routing policy in the provider boundary; perform LLM, search, and feed-validation calls without a PostgreSQL transaction; re-open a fresh ownership-fenced transaction for persistence and atomic queue enqueueing; and reuse the canonical discovery, scraper-config, first-edition, inbox-seeding, image-generation, and durable task models rather than introducing a parallel onboarding store.
- **Validation:** Focused Rust formatting and `cargo check --locked -p newsly-contracts -p newsly-providers -p newsly-db -p newsly-api --all-targets` passed. The exported Rust OpenAPI contains `buildOnboardingProfile`, `parseOnboardingVoice`, `runOnboardingFastDiscover`, `startOnboardingDiscoverAudioDiscoveryFlow`, `completeOnboardingFlow`, `startOnboarding`, `getOnboarding`, and `completeOnboarding`. Broad PostgreSQL/provider parity and product suites remain deferred to the consolidated final gate as requested.
- **Remaining:** Exercise all eight operations against isolated PostgreSQL and mocked OpenRouter, Exa, and feed-validation dependencies before promoting their runtime owners from Python to Rust; validate generated Swift/Go contract parity in the final contract gate.
- **Commits:** Uncommitted.

### 2026-08-30 — detached `c77aa869` — Port podcast and tweet media workers to Rust

- **Status:** Implemented locally behind Python-default task ownership; not committed, deployed, or cut over.
- **Scope:** Native Rust execution for `PROCESS_PODCAST_MEDIA`, `DOWNLOAD_TWEET_VIDEO_AUDIO`, and `TRANSCRIBE_TWEET_VIDEO`, including bounded direct media downloads, allowlisted yt-dlp extraction, tolerant ffmpeg normalization, Apple Podcasts/RSS resolution, OpenAI transcription, canonical body staging, usage recording, and the standalone media-worker process.
- **Decisions:** Commit a short immutable content snapshot before external work, make every network/provider/subprocess operation lease-aware, and publish content state, usage, downstream queue work, and task completion in one fresh exact-lease-fenced transaction. Confine tweet media reads to the configured root, reject symlinks and oversized files, and remove tweet attempt files only after the fenced transaction commits. Keep all three ownership namespaces Python-default until parity canaries pass.
- **Validation:** `cargo check -p newsly-worker --all-targets` passed. Focused provider, SQLx mutation, and worker media suites passed with 3, 3, and 4 tests respectively; the Supervisor media stanza parsed successfully. Broad PostgreSQL/provider parity, live yt-dlp/ffmpeg/OpenAI canaries, Docker build, and the product release suites remain deferred to the consolidated final gate as requested.
- **Remaining:** Exercise success, retry, terminal-provider, lease-loss, and fallback paths against isolated PostgreSQL and mocked or disposable providers, then promote only the three media task namespaces after the final contract/release gate.
- **Commits:** Uncommitted.

### 2026-08-30 — detached `c77aa869` — Port short-form News processing to Rust

- **Status:** Implemented locally behind Python-default task ownership; not committed, deployed, or cut over.
- **Scope:** Native `ENRICH_NEWS_ITEM_ARTICLE` and `PROCESS_NEWS_ITEM` execution, private Python document-extractor integration, Rust Firecrawl recovery and article-body storage, strict short-form summary/relevant-link contracts, hosted OpenRouter Qwen embeddings, canonical relation reconciliation, usage persistence, Briefing/Agent Data fanout, and a standalone News worker.
- **Decisions:** Commit short immutable prepare snapshots before every external call and publish only through a fresh exact-lease-fenced transaction. Reuse local or exact-representative summaries before paid work; keep extraction failure soft; restore retrying rows to `new`; reject production local embedding/reranker configuration instead of retaining a Python fallback; and leave SentenceTransformers/reranker experiments in the offline Python eval island.
- **Validation:** Focused Rust formatting and `cargo check -p newsly-providers -p newsly-worker` plus the standalone `news_item_worker` target passed. Focused unit/Clippy checks and broader PostgreSQL/provider parity remain in progress for this slice; the consolidated product gate remains deferred as requested.
- **Remaining:** Exercise exact-summary reuse, extraction fallback, relation merges, candidate/source races, lease loss, retries, fanout, and usage writes against isolated PostgreSQL and mocked providers before promoting either task namespace from Python to Rust.
- **Commits:** Uncommitted.

### 2026-08-31 — detached `c77aa869` — Cut public contract authority over to Rust

- **Status:** Implemented locally; not committed, pushed, deployed, or distributed.
- **Scope:** Rust public OpenAPI export, filtered agent OpenAPI 3.0 projection, checked schema regeneration/drift scripts, frozen Swift/Share/Go compatibility inputs, and retirement of Python-owned contract gates.
- **Decisions:** Make the `newsly-api` Utoipa document the single route/schema authority; fail agent export when any allowlisted CLI operation is absent; expose `--public-only` and `--go-only` Cargo-backed checks while retaining `--python-only` only as a no-Python CI alias; and keep checked client source models frozen until a schema-native emitter can represent their open-enum, lenient-decoding, default, ordering, and client-only-enum policies.
- **Changes:** Added Rust-side agent filtering and OpenAPI 3.1-to-3.0 nullable/exclusive-bound normalization; switched all active export/regeneration/check entrypoints to Rust and shell; removed Python migration-corpus generation and legacy Pydantic/FastAPI contract-authority tests; removed the stale initial-suggestions operation; preserved all six typed Share Extension boundaries; and restored explicit `date-time` semantics for the 48-field Swift/Go compatibility union in Rust contract types.
- **Validation:** Seven focused Rust exporter tests passed, covering all API success-response schemas and their reviewed streaming/204 exceptions, unique and stable operation IDs, canonical errors, the strict 20-operation agent projection, retired-route absence, client date semantics, and the typed Share Extension boundary. `scripts/check_public_contracts.sh` passed end to end with SQLx offline metadata; both shell exporters reproduced checked artifacts byte-for-byte; shell syntax, Rust formatting, Ruff, the Rust-snapshot iOS endpoint-parity test, and `go test ./internal/api` passed.
- **Remaining:** Replace the dormant Pydantic-backed Swift and Go emitters with a Rust- or schema-native generator before changing frozen client model sources. Their outputs remain protected by native client compilation and fixture behavior tests, not by Python regeneration.

### 2026-08-31 — detached `c77aa869` — Generate every public client boundary from Rust OpenAPI

- **Status:** Implemented locally; not committed, pushed, deployed, or distributed.
- **Scope:** Rust/schema-native app Swift, Share Extension Swift, and Go CLI type generation; reviewed client surface and escape-hatch policy; real byte-for-byte drift checks; and client-only compatibility vocabulary represented in Utoipa instead of emitter constants.
- **Decisions:** Keep Utoipa components as the wire-shape authority and use `contracts/client_codegen_policy.toml` only for reviewed target selection, source naming, enum openness, lenient legacy decoding, defaults OpenAPI cannot represent, discriminated-union selection, and untyped JSON allowlisting. Fail closed on missing schemas, unsupported unions, discriminator/name collisions, unregistered enum references, target-crossing references, and unreviewed arbitrary JSON. Treat a one-entry `oneOf`/`anyOf` as a transparent described-schema wrapper; only a union with an explicit null member changes nullability. In Go, represent optional scalar wire fields with pointers so explicit false and zero remain distinguishable from omission.
- **Changes:** Added `newsly-contract-codegen`; registered task, summary-version, owned News DTO, onboarding, Council-persona input, and the open discriminated `SubmissionResult` union surfaces in the reviewed policy; replaced existence-only Swift/Share/Go checks with generated temp artifacts and byte diffs; made regeneration write and format every client boundary; moved shared client contract fixtures under `contracts/fixtures`; updated the CLI to send server-owned onboarding suggestion IDs and aggregator keys; and removed active documentation that described the client sources as frozen.
- **Validation:** Locked generator tests passed, warning-denied focused Clippy passed, regeneration and byte-for-byte drift checks passed, and Go tests/vet passed. The generated Swift and Go sources contain typed known submission-result variants plus an unknown-case escape hatch. The app and standalone Share Extension schemes compile with signing disabled, and all 13 focused generated-contract iOS tests pass; only the pre-existing missing AppMark/BuddyMark asset warnings remain.
- **Commits:** Uncommitted.

### 2026-08-31 — detached `c77aa869` — Align documentation with final Rust authority

- **Status:** Complete locally; not committed, pushed, deployed, or distributed.
- **Scope:** Agent guidance, coding conventions, Rust and contract READMEs, system architecture, processing law, and the Rust migration design/implementation/summary record.
- **Decisions:** Make Axum/Tokio/SQLx/Rig/direct E2B the sole application authority; retain Python only for the database-free Crawl4AI extractor and offline evals; preserve the iOS composition, lifecycle, credential-publication, and Share Extension invariants; and distinguish local implementation from consolidated validation and production cutover.
- **Changes:** Replaced obsolete FastAPI/Python-coexistence architecture with the Rust modular-monolith end state, mapped the four audit blockers into Phase 0 prerequisites, separated contract redesign from telemetry-gated deletion, recorded final SQLx/contract/queue/E2B ownership, reflected removal of `app/`, `admin/`, `migrations/`, and backend `tests/`, recorded the client-owned `client/newsly/Maestro/` paths, and added explicit remaining release gates.
- **Validation:** `git diff --check` passed for the documentation scope; Markdown fences are balanced; every local Markdown link target and authoritative runtime/client path exists; and current guidance contains no stale FastAPI, Python-default, or coexistence authority phrase. No code or product test was run for this documentation-only lane.
- **Remaining:** Run the consolidated Rust, PostgreSQL, provider/E2B, contract, Go, iOS, Share Extension, container, exact-SHA, migration-adoption, and production deployment gates described in the summary.
- **Commits:** Uncommitted.

### 2026-08-31 — detached `c77aa869` — Make onboarding completion run-owned

- **Status:** Implemented locally; not committed, pushed, deployed, or distributed.
- **Scope:** Personalized iOS completion, Rust public contracts, Axum orchestration, SQLx ownership checks, and the onboarding-discovery worker contract.
- **Decisions:** Keep persisted discovery-run and suggestion IDs through the client selection state; allow a runless request only for the explicit non-personalized path; derive feeds, titles, and subreddit names from owned server state; keep profile context server-owned instead of echoing it through completion; and revalidate the completed run plus the exact selected ID set inside the final transaction.
- **Changes:** Added suggestion IDs to discovery status; replaced echoed source/subreddit/profile completion payloads with `discovery_run_id` and `selected_suggestion_ids`; rejected foreign, unfinished, duplicate, invalid, and cross-run selections; removed the second `onboarding_discover` enqueue from completion; retired the worker's runless profile-discovery branch; and added focused client/request and worker contract coverage.
- **Validation:** Focused formatting and Rust compilation passed across contracts, providers, SQLx, Axum, and the worker. Two Axum request/task-law tests, four onboarding-worker tests, and one isolated PostgreSQL ownership/unfinished/cross-run selection test passed. The focused native iOS test build reached a concurrent generated-timestamp mismatch before executing tests; the contract generator owns that active mismatch. Warning-denied Clippy resolved the onboarding findings and then stopped on unrelated active worker-lane findings.
- **Remaining:** Finish the in-progress Rust OpenAPI client regeneration, rerun the focused native onboarding tests, and include the personalized/default flows in the consolidated migration gate.
- **Commits:** Uncommitted.

### 2026-08-31 — detached `c77aa869` — Separate canonical News response DTOs

- **Status:** Implemented locally; not committed, pushed, deployed, or distributed.
- **Scope:** Rust-owned `NewsItemSummaryResponse`, `NewsItemDetailResponse`, and `NewsItemListResponse` contracts; canonical News presenters and route/OpenAPI response registrations; and typed Content summary-version correction.
- **Decisions:** Keep the installed-client `contents`/date/type/pagination wrapper and the few required Content-shaped compatibility keys with truthful fixed News values, while omitting optional Content-only processing, artwork, feed, and long-form summary fields. Continue resolving News exclusively through `news_items`; do not add a Content-ID fallback. Represent Content summary versions as a typed enum whose Serde and Schemars wire value remains the JSON integers `1` and `2`.
- **Validation:** Focused Rust formatting passed. All four `newsly-contracts` tests passed, including numeric summary-version round trips and News wrapper/bag-shape assertions. Both News presenter tests, the three affected OpenAPI exporter tests, and `cargo check -p newsly-api --all-targets --locked` passed. Warning-denied Clippy passed for `newsly-contracts`; the API-wide Clippy run remains blocked by unrelated pre-existing warnings in active migration lanes.
- **Remaining:** Regenerate the reviewed Swift and Go contract artifacts and migrate native/CLI callsites to the new generated News types in the contract-codegen lane; then run the consolidated native-client and public-contract drift gates.
- **Commits:** Uncommitted.

### 2026-08-31 — detached `c77aa869` — Correct final Rust-migration documentation claims

- **Status:** Complete locally; not committed, pushed, deployed, or cut over.
- **Scope:** Bounded accuracy pass across the architecture, migration implementation plan, and processing laws against the current Rust contracts and Learning Deck repository.
- **Decisions:** Mark the submission-result work complete only after the canonical Rust union and generated client boundaries landed; retain `learning_deck_runs` as a legacy compatibility and cleanup ledger until production-count, backfill, and schema-migration gates permit retirement; and state Rust-only ownership as a post-cutover production invariant rather than a completed deployment fact.
- **Validation:** Inspected the current Rust submission contract and Learning Deck repository plus the final authority migration; `git diff --check` passed for this documentation scope. No code or product test ran for this documentation-only pass.
- **Remaining:** Run the consolidated migration/release gates already recorded elsewhere, and retire `learning_deck_runs` only after its production evidence and backfill gates pass.
- **Commits:** Uncommitted.

### 2026-08-31 — detached `c77aa869` — Record the canonical submission-result boundary

- **Status:** Documentation aligned with the implemented local contract; not committed, pushed, deployed, or cut over.
- **Scope:** Rust migration architecture, implementation and summary records, the Content and Reading law, and migration-log status.
- **Decisions:** Treat the generated discriminated `SubmissionResult` as canonical; require the temporary installed-client top-level fields to agree until telemetry permits removal; and keep `learning_deck_runs` retirement explicitly pending production counts, backfill verification, and schema migration.
- **Validation:** Inspected the Rust contract, client-generation policy, and checked OpenAPI artifacts; documentation-only diff checks passed. Focused union/API and generation-drift evidence is recorded by the implementation lanes; this pass did not claim a full workspace or native-client gate.
- **Remaining:** Root integration must record the final Rust, contract, Go, and native-client gate results. Live provider/E2B canaries, production SQLx adoption, deployment, compatibility telemetry, and Learning Deck legacy-ledger retirement remain separate release or post-release work.
- **Commits:** Uncommitted.

### 2026-08-31 — detached `c77aa869` — Exercise local Rust ingestion and harden provider/finalization boundaries

- **Status:** Implemented and locally validated; not committed, pushed, deployed, or distributed.
- **Scope:** Disposable PostgreSQL/Rust API and worker canaries, the retained Crawl4AI extractor, all four Share Extension API modes, Runware request serialization, and content-finalization lock ordering.
- **Decisions:** Serialize Runware's provider-defined `taskUUID` spelling explicitly instead of relying on camel-case derivation. Standardize submitted-user-before-content locking across submission and content finalization; revalidate attribution under the content lock and use a savepoint retry so a first-submission race does not repeat completed external work. Keep non-chat Share Action agent tasks queued rather than spending on three additional E2B/model canaries after the deterministic chat path completed.
- **Validation:** Fresh SQLx migrations passed. The retained Python extractor had 35 tests pass, and a live public Crawl4AI extraction of `https://www.rust-lang.org/learn` returned 4,470 Markdown characters with no warning after installing its pinned Playwright Chromium runtime. The focused Rust API/extraction/provider/queue/worker suite had 161 tests pass before the fixes; afterward all 38 provider and 70 worker library tests passed, including exact Runware wire-key/UUIDv4 coverage and a real PostgreSQL concurrent lock-order regression. Warning-denied Clippy passed for both crates. The Share `chat` mode completed through API, durable queue, content submission, and chat-session creation; `add_to_briefing`, `bookmark_only`, and `presentation` each returned typed `202 queued` responses and durable `run_llm_task` rows with their worker intentionally stopped.
- **Remaining:** Root integration still owns the consolidated full-workspace, iOS/AXe, container, deployment, and production gates. The three non-chat Share tasks require separately authorized bounded live agent/E2B execution if completion rather than API/queue acceptance is required. Two disposable databases remain available for root inspection of the original deadlock and the fixed canary until final cleanup.
- **Commits:** Uncommitted.

### 2026-08-31 — detached `c77aa869` — Restore Rust module-size ratchets

- **Status:** Complete locally; not committed, pushed, deployed, or distributed.
- **Scope:** The image-generation provider, content-finalization repository, and namespaced iOS E2E fixture modules that exceeded their existing line-count ratchets during the Rust migration and local canary work.
- **Decisions:** Preserve behavior and every existing limit; move inline tests into private test submodules and isolate only the E2E fixture metadata builders into a cohesive private module.
- **Validation:** The affected provider, database, and worker crates compile across all targets. Eight image-provider tests, four fixture tests, and five content-repository tests passed, including the isolated PostgreSQL lock-order regression. Warning-denied Clippy passed for all three crates, and the complete architecture guard passed with 691 files checked, the iOS wire guard clean, and public contract artifacts current.
- **Commits:** Uncommitted.

### 2026-08-31 — detached `c77aa869` — Preserve unbounded Learning Deck model iteration

- **Status:** Implemented and locally validated; not committed, pushed, deployed, or distributed.
- **Scope:** Newsly-owned agent request limits, the Rig execution loop, finite provider/share/chat callsites, and Learning Deck initial/repair generation.
- **Decisions:** Represent the model-request cap as an optional positive integer; retain every existing finite cap as `Some(limit)`; and give Learning Deck generation no request-count or output-token cap while preserving its execution deadline, tool-call, artifact-size, artifact-contract, and browser-validation bounds.
- **Validation:** Formatting and all-target compilation passed for the agent runtime, providers, and worker. All 3 agent-runtime, 41 provider, and 70 worker library tests passed against the disposable local PostgreSQL instance, including focused finite/unbounded loop tests. Warning-denied Clippy passed across all targets in the three affected crates.
- **Commits:** Uncommitted.

### 2026-08-31 — detached `c77aa869` — Remove the Learning Deck output-token ceiling

- **Status:** Implemented and locally validated; not committed, pushed, deployed, or distributed.
- **Scope:** Initial and repair Learning Deck agent requests plus the canonical Knowledge and Learning law.
- **Decisions:** Restore the uncapped model-output behavior of the pre-migration Learning Deck agent while retaining the execution deadline, tool-call limit, bounded file operations, artifact size and contract validation, and browser validation.
- **Changes:** Replaced the Rust-only 4,000-token per-request ceiling with no application-level output-token limit for both generation and repair. Added a focused regression that also proves the existing unbounded request count while preserving the configured tool-call and deadline safeguards.
- **Validation:** Focused formatting, tests, compilation, and warning-denied Clippy passed for `newsly-worker`; no live provider call ran in this lane.
- **Commits:** Uncommitted.

### 2026-08-31 — detached `c77aa869` — Validate local Learning Deck and audio artifacts through iOS

- **Status:** Implemented and locally validated; not committed, pushed, deployed, or distributed.
- **Scope:** Rust-seeded Learning Deck and audio fixtures, private deck hosting, authenticated stored-audio delivery, and the dedicated iOS Simulator Knowledge timeline.
- **Decisions:** Keep live generation-follow streams lengthless, but serve immutable stored audio with its exact length, a MIME-matched filename, and one standards-compliant byte range. Reject malformed, multipart, empty, and unsatisfiable ranges with `416` and `Content-Range`; advertise `206` and `416` in OpenAPI. Isolate stored-audio transport and its tests in a private module instead of increasing the API module-size ratchet.
- **Validation:** Deck `2` detail, private viewer URL, and signed 704-byte HTML returned `200`; the deck rendered in the dedicated iPhone 17 Pro Simulator. Audio episode `3` returned a valid 16,044-byte PCM WAV. Full, `bytes=0-1`, and unsatisfiable requests returned `200`, `206` (`Content-Range: bytes 0-1/16044`), and `416` (`Content-Range: bytes */16044`) respectively. AXe showed the inline control transition from pause to play, while simulator logs recorded `readyToPlay`, first progress at 0.345 seconds, and normal end. All 7 focused range/header tests, API all-target compilation, warning-denied API Clippy, formatting, diff checks, and the module-size guard passed.
- **Evidence:** `/tmp/newsly-rust-e2e-axe-final/38-learning-deck-viewer-live.png`, `49-audio-range-playing.png`, `50-audio-range-finished.png`, and `50-audio-range-playback-log.txt`.
- **Commits:** Uncommitted.

### 2026-08-31 — detached `c77aa869` — Bound Share feed-discovery request headroom

- **Status:** Implemented and locally validated; not committed, pushed, deployed, or distributed.
- **Scope:** Rust Share Action agent request budgeting for the `add_feed` workflow.
- **Decisions:** Keep the configured finite request limit unchanged for every other Share mode. Give only `add_feed` four additional model requests, capped at the existing maximum of 50, because validating a publication homepage requires direct probing, feed discovery, and candidate validation beyond the six setup/direct-validation rounds observed in the local canary.
- **Validation:** Three focused request-budget tests passed. `newsly-worker` compiled across all targets and passed warning-denied Clippy; formatting checks passed. No provider or E2B call ran in this implementation lane.
- **Remaining:** Rebuild the local worker once and retry the failed This Week in Rust homepage Share request to confirm it completes within the new default cap of 12.
- **Commits:** Uncommitted.
# 2026-08-31 — `main` — tolerate audited legacy partial-index rendering during SQLx adoption

- Kept baseline adoption fail-closed while canonicalizing the one exact, production-observed PostgreSQL rendering variant for `uq_learning_deck_runs_user_active`.
- The accepted legacy definition and predicate are semantically identical to the frozen baseline; all other catalog differences still fail the fingerprint comparison.
- Added a focused regression test for the legacy rendering.
# 2026-08-31 — `main` — accelerate exact-SHA Rust releases and harden SQLx adoption

- Removed disposable production-image builds from the reusable quality workflow; exact-SHA images are now built and published once after all quality jobs pass.
- Added a pinned Cargo Chef dependency layer to the Rust production image and moved extractor revision metadata after Python/Chromium dependency installation.
- Compared the complete frozen and production catalog inventories. The only differences are two audited PostgreSQL renderings of equivalent `ANY(character varying[])` predicates; the verifier now accepts either complete manifest-hashed catalog snapshot without normalizing live evidence, so every third rendering or unrelated catalog difference still fails closed.
- Made the inventory ignore SQLx's internal test-harness schema and classify direct database-owner versus `pg_database_owner` schema authority under the same logical label. The disposable drill still rejected a genuinely broader `PUBLIC CREATE` grant, proving that privilege drift remains fail-closed.
- Added an exact-image, read-only eligibility preflight before downtime and a verified, ownership-and-grant-preserving custom-format PostgreSQL backup after the writer barrier but before any first-adoption write. A partial post-baseline SQLx history now stops automated deployment for operator inspection even when its checksums form an exact embedded prefix.
- Added focused acceptance, rejection, and exact-history coverage and documented the release-pipeline and database-recovery contracts. A PostgreSQL 15 disposable adoption drill passed the full legacy-catalog preflight, produced a restorable custom-format backup, recorded all seven SQLx migrations, and preserved a sentinel application row.

# 2026-08-31 — `main` — fail image packaging before production cutover

- Fixed the document extractor image so Playwright setup does not trigger a premature project build or install development tools, the complete project is explicitly installed into its virtual environment, and production starts the installed entrypoint directly without an implicit `uv` synchronization against a read-only filesystem.
- Added post-publish, pre-deploy smoke tests for the exact Rust migration binary and the exact extractor image under production-like read-only and health-check constraints.
- Made the Nginx slot switch retry its local post-reload health probe so a graceful worker handoff cannot spuriously roll routing back to the stopped prior slot.
- Production recovery kept the completed SQLx authority migration, used the verified pre-adoption backup as its recovery point, and restored service with the exact Rust image plus a metadata-only extractor recovery image while the durable image fix proceeds through the full release gate.
