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

### 2026-08-29 — `main` — Preserve authenticated UI coverage in unsigned release builds

- **Status:** Complete locally and committed; release validation in progress.
- **Scope:** DEBUG E2E credential persistence used by the native iOS release gate.
- **Evidence:** The required `CODE_SIGNING_ALLOWED=NO` XCTest run passed 617 tests but both authenticated lifecycle UI tests reached the landing screen. Simulator logs showed every Keychain write and delete failing with `-34018` because the unsigned app has no Keychain entitlement.
- **Decisions:** Keep production and ordinary DEBUG launches on fail-closed Keychain storage. Only an explicit `newslyE2EEnabled` launch may use the app sandbox's durable defaults so process-relaunch and refresh-rotation coverage remain executable without signing.
- **Changes:** Routed credential-envelope, legacy-token, cached-user, and refresh-attempt reads, writes, and deletes through standard defaults for explicit E2E launches; normal builds retain the existing Keychain path unchanged.
- **Validation:** Both previously failing authenticated lifecycle UI tests passed under the exact unsigned release command. `git diff --check` passed.
- **Remaining:** Rerun the complete Python, native iOS, and Maestro release gates on the resulting commit before push.
- **Commits:** This release-gate correction commit.

### 2026-08-29 — `main` — Newsbuddy logo/brand exploration site

- **Status:** Complete
- **Scope:** `docs/brand-exploration-2026-08/` only; no app code touched.
- **Decisions:** Generated 16 Japanese-aesthetic pastel logo concepts split across Seedream 5.0 Lite (Runware, existing key) and GPT Image 2 (`openai/gpt-5.4-image-2` via OpenRouter — used instead of the codex CLI since OpenRouter exposes the model directly). Per-image color kits extracted with background-masked median-cut quantization, honoring the one-accent+neutrals color doctrine.
- **Changes:** `generate_logos.py` (idempotent, per-concept regen via CLI args), `extract_palettes.py` (emits `palettes.json`/`palettes.js`), `index.html` static explainer with squircle icon crops and per-kit themed briefing-UI phone mocks.
- **Validation:** All 16 generations succeeded (~$0.035/image on Runware); site verified via headless Chrome screenshots.
- **Remaining:** Awaiting direction pick; regenerate any concept with `uv run python docs/brand-exploration-2026-08/generate_logos.py <id>` then rerun `extract_palettes.py`.
- **Commits:** `87f6191` (`docs(brand): add Newsbuddy logo exploration`).

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
