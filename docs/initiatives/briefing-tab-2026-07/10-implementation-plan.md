# Briefing Tab — Implementation Plan (2026-07)

## Goal

Ship the Mad-Lib newspaper briefing as a first-class product surface: a new iOS tab that renders
an LLM-composed, continuously updated "unread edition" of the user's backlog as three swipeable
tiers — **Podcasts**, **Articles**, then **News categories** — with inline images, tappable
source links that open a modal detail view, selection-driven live "dig deeper", full-stream
narration, scroll-based read tracking, and incremental (append-only) backend generation so we
never pay to regenerate prose we already have. DeepSeek v4 Flash
(`openrouter:deepseek/deepseek-v4-flash`) powers **all** LLM calls.

A local settings toggle swaps the reading experience: **Classic** (existing Long + Fast tabs)
vs **Briefing** (the new tab replaces both).

## Context & provenance

This productionizes the prototype built in `scripts/generate_unread_briefing_newspaper.py`
(LLM-owned typed layout blocks) and `scripts/render_madlib_style_lab.py` (HTML renderer with
selection-driven dig-deeper), with artifacts in `outputs/unread_briefing_prototype/user_1_current/`.
The prototype proved:

- The LLM can own page composition via a **flat** typed block document (passage / figure /
  pullquote) reliably, given a deterministic repair pass.
- Tiered register works: longer informational narratives for podcasts/articles, short punchy
  category groups for news.
- Selection-driven dig deeper (Exa search + DeepSeek digest, two-staged for perceived speed)
  is fast enough to feel live (~2s sources, ~6s summary).
- Full-page generation for ~85 sources costs ~215k tokens with pre-generated insight
  learn-mores; dropping learn-mores (decision D6) cuts deep-tier output ~40%.
- DeepSeek Flash has sharp edges (see "DeepSeek Flash guardrails" appendix) that the repair
  pipeline must own permanently.

Prototype scripts stay untouched as a lab; production code is a new vertical.

## Recorded decisions

1. **D1 — Model**: every briefing LLM call defaults to `OPENROUTER_DEEPSEEK_FLASH_MODEL_SPEC`
   ([app/core/model_defaults.py:4](../../../app/core/model_defaults.py)). Never
   `openai:gpt-5.4-mini`. Model spec is a setting per call-site so it can be overridden.
2. **D2 — Native SwiftUI rendering, no WebView.** The client renders the typed block document
   natively. We already have the machinery: `SelectableMarkdownView` /
   `DigDeeperTextView` (UITextView + custom "Dig Deeper" edit-menu action,
   [SelectableMarkdownView.swift:13](../../../client/newsly/newsly/Views/Components/SelectableMarkdownView.swift)),
   `CachedAsyncImage`, MarkdownUI. WebView would fight sheets, selection, narration, and read
   tracking.
3. **D3 — Server-normalized "runs" contract.** The LLM emits markdown with
   `[title](newsly://briefing/{kind}/{id})` links and `{{insight:id}}…{{/insight}}` markers;
   the **server** repairs and parses that into typed paragraphs of runs
   (`text | source_link | insight`). The client never regex-parses prose. Raw markdown is kept
   server-side on the segment row for debugging/regeneration only.
4. **D4 — Append-only segments + compaction** (see "Incremental refresh"). A segment is an
   immutable LLM-composed block document over a frozen set of 3–6 sources. New content appends
   new segments; reads retire segments; fragmentation triggers compaction. Steady-state cost is
   ~one generation per source, ever.
5. **D5 — Read state derives from existing truth tables** (`content_read_status`,
   `news_item_read_status`). No parallel briefing read state. Briefing read-marks call the
   existing mark-read commands, then do briefing bookkeeping (segment retirement, version bump).
6. **D6 — Insights are hint ranges only.** Production drops pre-generated `learn_more` /
   `follow_up_questions` (the main token cost and window-size limiter in the prototype). An
   insight is `{insight_id, title}` + its marked text range; digging is always live.
7. **D7 — Dig deeper = new lightweight synchronous endpoints** under `/briefing/dig/*` reusing
   `exa_search` + `get_basic_agent`, two-staged (search → summarize) exactly like the prototype.
   The existing `DIG_DEEPER` task type ([app/services/dig_deeper.py](../../../app/services/dig_deeper.py))
   is an async chat bootstrapper — different feature, unchanged.
8. **D8 — Narration rides the audio-episode pipeline.** New `AudioEpisodeKind.BRIEFING_NARRATION`
   episodes are synthesized from segment `narration_text` via the existing ElevenLabs
   `ContentNarrationTtsService` + `GENERATE_AUDIO_EPISODE` task, streamed with
   `follow_audio_episode_stream_chunks`, and mark sources read on play via the existing
   `mark_audio_episode_sources_read_on_play` hook (extended to mixed source kinds).
9. **D9 — Settings toggle is a local `@AppStorage` enum** (`readingExperience`:
   `classic | briefing`) in `AppSettings`, matching every existing client setting
   ([AppSettings.swift:92-98](../../../client/newsly/newsly/AppSettings.swift)). Backend
   generation is gated separately by `settings.briefing_enabled_user_ids` (default `[1]`).
10. **D10 — Scheduling uses existing queue primitives only.** There is no in-app cron. Refresh
    is (a) event-driven: content/news "ready" hooks enqueue a debounced `BRIEFING_REFRESH`
    (dedupe key + future `available_at` = coalescing window), and (b) a self-rescheduling
    sweep: the handler enqueues its own successor. Plus an `admin briefing` CLI group.
11. **D11 — Lens taxonomy**: fixed `podcasts` (tier `audio`) and `articles` (tier `longform`)
    lenses; dynamic news category lenses assigned by aggregator topic when present, else
    embedding-vs-centroid via the existing local `news_embeddings` encoder; genuinely new
    clusters become new lenses (LLM names them). No "for-you" lens in v1 (flag-gated stretch).
12. **D12 — Read granularity is per-segment**: when a segment scrolls fully past the top of the
    viewport (or its narration finishes), all its source keys are batch-marked read — same
    collect-and-batch pattern as `ShortNewsListViewModel`
    ([ShortNewsListViewModel.swift:40-75](../../../client/newsly/newsly/ViewModels/ShortNewsListViewModel.swift)).
13. **D13 — Images reuse the existing static pipeline** (`/static/images/content/{id}.png`,
    `build_content_image_url` / `build_thumbnail_url`,
    [app/utils/image_urls.py](../../../app/utils/image_urls.py)). No new image storage.
14. **D14 — Single column.** Mobile-first: figures render full-width or inset within one column;
    the LLM's `placement` hint maps to full/inset styling, never multi-column floats.
15. **D15 — Prompt-cache-friendly prompts.** Static system + tier rules form a stable prefix;
    per-window source payload comes last, so DeepSeek/OpenRouter automatic context caching
    applies across windows (usage tracking already records `cache_read_tokens`).
16. **D16 — Typed contracts end-to-end.** All briefing payloads are Pydantic models registered
    in `contracts_registry.py` and code-generated to Swift/Go. Flat optional-field block model
    (no discriminated unions — both a generator constraint and a DeepSeek constraint).

## Target shape

```
app/
  models/db/briefing.py            # BriefingLens, BriefingSegment, BriefingPendingSource, BriefingState
  models/api/briefing.py           # all briefing contracts (registered in contracts_registry)
  models/contracts.py              # + BriefingTier, BriefingBlockType, BriefingRunKind,
                                   #   TaskType.BRIEFING_REFRESH, AudioEpisodeKind.BRIEFING_NARRATION
  services/briefing/
    __init__.py
    sources.py                     # unread source assembly (content + news) -> BriefingSource
    lenses.py                      # taxonomy, category assignment, new-lens detection
    composer.py                    # window planning, prompt build, LLM call w/ retry+timeout, usage
    repair.py                      # deterministic guardrails (ported from prototype)
    normalize.py                   # markdown+markers -> paragraphs/runs; narration_text
    refresh.py                     # orchestration: pending pool -> segments; retire; compact; masthead
    read_marks.py                  # source-key mark-read + segment retirement + version bump
    dig.py                         # exa search + digest for live dig deeper
    narration.py                   # lens narration episode creation/reuse
  prompts/briefing/
    layout.md                      # #system, #prose_rules, #deep_rules, #news_rules, #window
    lens_naming.md                 # name/deck for a new news category lens
    masthead.md                    # small deck-refresh call
  pipeline/handlers/briefing_refresh.py
  routers/api/briefing.py
admin/                             # + `briefing` command group (status / refresh / costs)
client/newsly/newsly/
  Models/Briefing/                 # thin wrappers over generated contracts if needed
  Services/BriefingService.swift   # API calls
  ViewModels/BriefingViewModel.swift
  ViewModels/BriefingDigViewModel.swift
  Views/Briefing/
    BriefingView.swift             # masthead + lens pills + paged lenses
    BriefingLensPageView.swift     # LazyVStack of segments + seen tracking
    BriefingPassageView.swift      # UITextView-backed selectable prose (runs -> NSAttributedString)
    BriefingFigureView.swift
    BriefingPullquoteView.swift
    BriefingDigPanel.swift         # inline staged sources -> summary
    BriefingSourceSheet.swift      # news compact sheet / ContentDetailView modal
    BriefingNarrationBar.swift
```

## Data model

New tables (one Alembic migration, `uv run alembic revision --autogenerate -m "add briefing tables"`):

```
briefing_lenses
  id PK · user_id int idx · key str(64) · tier str(16)            # audio|longform|news
  title str(220) · deck text · position int · status str(16)      # active|retired
  centroid JSON null                                              # news category embedding centroid
  created_at · updated_at · retired_at null
  UNIQUE (user_id, key)

briefing_segments
  id PK · lens_id FK idx · user_id int idx
  blocks JSON                     # normalized contract-shaped block document
  markdown_raw text               # canonical LLM output, server-side only
  narration_text text
  source_keys JSON                # ["content:123", "news:456"]
  status str(16) idx              # active|retired|compacted|degraded
  model str(64) · prompt_version str(16)
  input_tokens int · output_tokens int · generation_ms int · warnings JSON
  created_at idx · updated_at
  (ordering: created_at DESC within a lens — newest segments on top)

briefing_pending_sources
  id PK · user_id int idx · lens_key str(64) null                 # null = awaiting assignment
  source_kind str(16) · source_id int · enqueued_at
  UNIQUE (user_id, source_kind, source_id)

briefing_states
  user_id PK · version int default 0                              # bumped on any mutation
  masthead_title str(220) · masthead_deck text
  last_append_at null · last_sweep_at null
```

Read state stays in `content_read_status` ([app/models/db/content.py:128](../../../app/models/db/content.py))
and `news_item_read_status` ([app/models/db/news.py:164](../../../app/models/db/news.py)); GET
endpoints join against them live for per-source `read` flags, so reads from Classic tabs are
always reflected even without a version bump.

## API contracts

All models in `app/models/api/briefing.py`, enums in `app/models/contracts.py`, registered in
`app/models/contracts_registry.py`, regenerated via `scripts/regenerate_public_contracts.sh`
(checked by `scripts/check_public_contracts.sh`; iOS one-shot:
`client/newsly/scripts/regenerate_api_contracts.sh`).

```
BriefingIndexResponse   { version:int, masthead_title:str, masthead_deck:str,
                          generated_at:datetime|None, lenses:list[BriefingLensSummary] }
BriefingLensSummary     { key, tier:BriefingTier, title, deck, position:int,
                          segment_count:int, unread_source_count:int }
BriefingLensResponse    { version:int, lens:BriefingLensSummary,
                          segments:list[BriefingSegmentDto], sources:list[BriefingSourceDto] }
BriefingSegmentDto      { id:int, created_at, status, narration_text:str,
                          blocks:list[BriefingBlockDto], source_keys:list[str] }
BriefingBlockDto        { type:BriefingBlockType,                       # passage|figure|pullquote
                          weight:str|None,                              # feature|brief (passage)
                          paragraphs:list[BriefingParagraphDto]|None,   # passage
                          source_key:str|None, image_url:str|None,      # figure/pullquote
                          thumbnail_url:str|None, caption:str|None,
                          placement:str|None,                           # full|inset
                          text:str|None }                               # pullquote
BriefingParagraphDto    { runs:list[BriefingRunDto] }
BriefingRunDto          { kind:BriefingRunKind,                         # text|source_link|insight
                          text:str, source_key:str|None, insight_id:str|None, bold:bool }
BriefingSourceDto       { source_key, kind:str, id:int, title:str, summary:str|None,
                          key_points:list[str]|None, url:str|None, image_url:str|None,
                          thumbnail_url:str|None, published_at:datetime|None,
                          content_type:APIContentType|None, read:bool }
BriefingReadMarkRequest { source_keys:list[str] }        BriefingReadMarkResponse { marked:int, version:int }
BriefingDigSearchRequest    { fragment:str(3..300) }
BriefingDigSearchResponse   { results:list[BriefingDigSearchResult], elapsed_ms:int }
BriefingDigSearchResult     { title, url, snippet, published_date:str|None }
BriefingDigSummarizeRequest { fragment, passage_context:str(..2000),
                              results:list[BriefingDigSearchResult] }
BriefingDigSummarizeResponse{ summary:str, model:str, elapsed_ms:int }
BriefingNarrationRequest    { lens_key:str }             # response: existing AudioEpisodeResponse
BriefingRefreshResponse     { enqueued:bool, version:int }
```

Endpoints (`app/routers/api/briefing.py`, registered in `app/main.py` beside
[app/routers/api/news.py](../../../app/routers/api/news.py); auth via the standard
`get_current_user` dependency):

| Method | Path | Behavior |
|---|---|---|
| GET | `/briefing` | Index. Honors `If-None-Match: W/"v{version}"` → `304`. |
| GET | `/briefing/lenses/{key}` | Segments + deduped sources for one lens (lazy per-lens fetch keeps payloads small). |
| POST | `/briefing/read-marks` | Splits keys → `bulk_mark_read` ([app/routers/api/read_status.py:79](../../../app/routers/api/read_status.py)) + `bulk_mark_news_items_read`; retires fully-read segments; bumps version. Idempotent. |
| POST | `/briefing/refresh` | Pull-to-refresh: enqueues `BRIEFING_REFRESH` (dedupe collapses spam), returns current version. |
| POST | `/briefing/dig/search` | `exa_search(query=fragment[:200], num_results=4, max_characters=900)`; usage feature=`briefing_dig`. |
| POST | `/briefing/dig/summarize` | DeepSeek digest of fragment + passage + results (3–5 sentences, informational register — port `DIG_SYSTEM_PROMPT` from `scripts/serve_dig_deeper.py`). |
| POST | `/briefing/narration` | Create-or-reuse `BRIEFING_NARRATION` audio episode for the lens; playback via existing audio-episode endpoints. |

Rate limit dig: cap per-user hourly calls by counting recent `vendor_usage_records` rows with
`feature='briefing_dig'` (no new infra); 429 beyond `settings.briefing_dig_hourly_limit` (60).

## Generation architecture

### Source assembly (`sources.py`)

- **Longform/audio**: unread `contents` with `status=COMPLETED`, `classification='to_read'`,
  `content_type in (article|podcast)` for the user (join `content_read_status`), newest first,
  capped by `briefing_backlog_limit_audio` (12) / `_longform` (20) at bootstrap. Fields: title,
  url, summary (existing summary utilities), key points, image URLs via `build_content_image_url`
  / `build_thumbnail_url`, published/created timestamps.
- **News**: unread READY `news_items` visible to the user (reuse
  [app/services/news_feed.py](../../../app/services/news_feed.py) visibility rules), cluster
  representatives only (`representative_news_item_id`), capped `briefing_backlog_limit_news` (40).
- Source key format is the prototype's: `content:{id}`, `news:{id}`.

### Lens taxonomy & category assignment (`lenses.py`)

1. `podcasts` and `articles` lenses are fixed rows created on first refresh.
2. News assignment, per pending item: aggregator topic slug if present
   (`raw_metadata.aggregator.topic`) → else embed title+summary with `encode_news_texts`
   ([app/services/news_embeddings.py](../../../app/services/news_embeddings.py)) and assign to the
   nearest active lens centroid above `briefing_category_similarity` (0.55); update centroid
   (running mean).
3. Unassigned items pool in `briefing_pending_sources(lens_key NULL)`. When ≥
   `briefing_new_lens_min_items` (4) unassigned items are mutually cohesive (pairwise sim ≥
   threshold), one small LLM call (`prompts/briefing/lens_naming.md`) names the new lens
   (title + deck + slug) and it becomes active. Otherwise stragglers older than 24h fold into a
   `misc` news lens.
4. Empty lenses (all segments retired, no pending) retire after `briefing_lens_idle_days` (7).

### Composer (`composer.py` + `prompts/briefing/layout.md`)

Direct port of the prototype generator, minus learn-mores:

- **Window planning**: deep tiers (audio/longform) chunk into windows of
  `briefing_window_min..max` (3..6) sources; news windows up to 8. Per-window figure budget from
  remaining lens budget (`briefing_max_figures_deep=12`, `_news=6`).
- **Prompt**: stable prefix (`#system` + `#prose_rules` + tier rules) then window payload
  (JSON source metadata + image availability + window note) — D15. Prose rules keep the exact
  link example (prevents bare `[newsly://…]` citations) and the `{{insight:id}}` marker spec
  with 2–3 insights per deep passage, compact informational register (1–3 sentences per source,
  no "the real twist is"). Loaded via `prompt_library.load_prompt("briefing/layout#deep_rules")`
  etc. ([app/services/prompt_library.py](../../../app/services/prompt_library.py)).
- **Output type**: flat `ComposerBlock` / `ComposerLayout` Pydantic models (same shapes as the
  prototype `LayoutBlock` — flat optionals, `extra="forbid"`); `get_basic_agent(model_spec,
  ComposerLayout, system)` — OpenRouter path already wraps `NativeOutput(strict=True)` with 2
  structured-output retries ([app/services/llm_agents.py:49](../../../app/services/llm_agents.py)).
- **Timeout/retry**: port the prototype's thread-pool timeout wrapper (`LLM_ATTEMPTS=2`,
  `briefing_llm_timeout_seconds=300` — windows are small now); on total failure emit a
  deterministic fallback segment flagged `status='degraded'` (sweeps retry degraded segments).
- **Usage**: record via `extract_usage_from_result` → `record_model_usage`
  ([app/services/vendor_usage.py](../../../app/services/vendor_usage.py)) with
  `feature='briefing_compose'`, `user_id`, `task_id` so `admin usage` reports it.

### Repair pipeline (`repair.py`) — ported verbatim from prototype, in order

1. Drop unknown-typed blocks / unknown keys.
2. Figure validation: source exists in window, image exists, placement normalized (`full|inset`),
   no leading figure, per-lens cap enforcement.
3. Pullquote text sanity (length, strip heading noise).
4. Passage `text`→`markdown` field recovery (DeepSeek sometimes swaps fields).
5. `close_unpaired_insights()` — close at next sentence boundary or before next opener.
6. Strip stray/unknown markers; drop insights whose markers vanished.
7. Coverage repair: any window source not cited in prose gets a deterministic brief
   passage appended.
8. Insight-id prefixing: `{lens}_{w#}_` to guarantee global uniqueness across windows/segments.

### Normalization (`normalize.py`)

Ports the renderer's parsing (LINK_RE, INSIGHT_RE, sentence splitting) to produce:
- `paragraphs[] -> runs[]` (text / source_link / insight / bold), splitting on sentence
  boundaries into 2–3 sentence paragraphs with markers stashed so links never split;
- `narration_text`: prose with link titles inlined, insight markers dropped, captions and
  pullquotes skipped — ready for TTS.

### Masthead

Deterministic title (`settings.briefing_masthead_title`, default "The Unread Times"); deck
refreshed by one small LLM call per append batch (`prompts/briefing/masthead.md`, ≤400 output
tokens): old deck + titles of newly appended sources → 2 sentences naming what's new.

## Incremental refresh (the token-efficiency core)

**Primitives** (all existing): task dedupe via partial unique index on
`(dedupe_key, active_status)` ([app/models/db/tasks.py:58](../../../app/models/db/tasks.py)),
delayed visibility via `available_at`, retry via `finalize_task`
([app/services/queue.py](../../../app/services/queue.py)).

**Triggers**
1. *Event-driven append*: when content completes summarization
   (hook in [app/services/content_lifecycle.py](../../../app/services/content_lifecycle.py)
   next-task decision) or a news item reaches READY
   ([app/services/news_processing.py](../../../app/services/news_processing.py) —
   `process_news_item`), and the owning user is in `briefing_enabled_user_ids`: insert a
   `briefing_pending_sources` row and enqueue `BRIEFING_REFRESH` with
   `dedupe_key=f"briefing_refresh:{user_id}"` and `available_at = now + briefing_debounce_seconds`
   (900). Bursts coalesce into one task; the pool accumulates meanwhile.
2. *Self-rescheduling sweep*: the handler always re-enqueues itself with
   `available_at = now + briefing_sweep_seconds` (3600) — freshness backstop, degraded-segment
   retry, age-out, and compaction all ride the sweep (D10; there is no cron framework).
3. *Manual*: `POST /briefing/refresh` (client pull-to-refresh) and
   `uv run -m admin briefing refresh --user 1 [--full]`.

**`BRIEFING_REFRESH` handler algorithm** (`refresh.py`; queue `TaskQueue.LLM`, payload
`RequiredUserPayload + {mode: append|sweep|full}`):

1. Assign lens keys to unassigned pending news (embeddings; maybe create new lens).
2. Per lens: if pending count ≥ `briefing_window_min` **or** oldest pending >
   `briefing_pending_max_age_seconds` (2700), consume up to `window_max` into a composer window →
   repair → normalize → insert `briefing_segments` row; delete consumed pending rows. Newest
   segments sort first (created_at DESC) — newspaper freshness.
3. Retirement: segments whose sources are all read → `retired`; news-tier segments whose sources
   are all older than `briefing_news_max_age_days` (4) → `retired` (stale news ages out even
   unread; longform/audio persist until read).
4. Compaction: if a lens has > `briefing_max_segments_per_lens` (12) active segments, or ≥3
   segments each with ≤2 unread sources, regenerate one merged window from the leftover unread
   sources and mark the donors `compacted`. Tokens ∝ leftover unread only.
5. Degraded retry: re-compose `degraded` segments (bounded attempts).
6. Masthead deck refresh if anything appended; bump `briefing_states.version` once per mutation
   batch; re-enqueue sweep successor.

Every step is idempotent per task-queue conventions (re-running consumes nothing twice because
pending rows are deleted transactionally with segment insertion).

**Bootstrap**: first run for a user builds lenses from up to the backlog caps (a few windows per
lens). Everything after is incremental.

**Token budget** (Flash pricing, prototype-measured): full prototype run ≈ 215k tokens / 85
sources ≈ 2.5k/source *with* learn-mores; without them ≈ 1.5–1.8k/source, paid **once** per
source. At 30–60 new sources/day ≈ 50–100k tokens/day steady state, plus ~1–2k/day masthead and
occasional compaction windows. Daily full regeneration of an 85-source edition would cost 6–10×
that and grow with backlog; append-only doesn't. Stable prompt prefixes make window calls hit
DeepSeek context caching (D15); `cache_read_tokens` is already recorded.

## iOS architecture

### Tab + settings

- `AppSettings` gains `@AppStorage("readingExperience") var readingExperienceRaw: String`
  (enum `ReadingExperience: classic|briefing`, default `classic`).
- [ContentView.swift:73-171](../../../client/newsly/newsly/ContentView.swift): when `briefing`,
  the Long Form + Fast News tabs are replaced by one **Briefing** tab (icon `newspaper`);
  Knowledge and More stay. `Tab` enum gains `.briefing`; `TabCoordinatorViewModel` handles the
  conditional set (no background-refresh coupling — briefing refreshes itself via version polling
  on foreground/tab-enter).
- `SettingsView` gains a "Reading experience" picker (Classic / Briefing), following the existing
  toggle pattern ([SettingsView.swift](../../../client/newsly/newsly/Views/Settings/SettingsView.swift)).

### Data flow

`BriefingService` (protocol + live impl over `APIClient`): `fetchIndex(ifNoneMatchVersion:)`,
`fetchLens(key:)`, `markRead(sourceKeys:)`, `digSearch(fragment:)`,
`digSummarize(...)`, `requestNarration(lensKey:)`, `requestRefresh()`.
`BriefingViewModel`: holds index + per-lens page state; loads the selected lens lazily and
prefetches neighbors; pull-to-refresh → `requestRefresh()` then short version-poll; re-checks
version on `scenePhase == .active` and tab entry. Listens for `.contentMarkedAsRead` to reconcile
with Classic-surface reads.

### Rendering

- `BriefingView`: masthead (reuse `EditorialMastheadHeader` styling — Lora serif,
  [DesignTokens.swift:183](../../../client/newsly/newsly/Views/Shared/DesignTokens.swift)),
  lens pill bar (chip styling per design tokens), `TabView(.page(indexDisplayMode: .never))` for
  left/right swipes between lenses (podcasts → articles → news categories, by lens `position`),
  pills synced to page selection, 2px progress hairline per lens (seen fraction).
- `BriefingPassageView`: UIViewRepresentable UITextView (extend the `DigDeeperTextView` pattern —
  the custom "Dig Deeper" edit-menu action already exists). A small
  `BriefingAttributedTextBuilder` maps runs → `NSAttributedString`: `source_link` runs get
  `.link` (`newsly://briefing/{kind}/{id}`) + accent color + underline; `insight` runs get a
  faint dotted-underline style (guidance affordance, per prototype); feature vs brief weight maps
  to type scale (serif-leaning feature passages, denser brief ones). Link taps intercepted by the
  textview delegate → source sheet. **No markdown parsing on-device** (D3).
- `BriefingFigureView`: `CachedAsyncImage(url:thumbnailUrl:targetSize:)` full-width or inset,
  caption in `.appCaption`, grayscale-friendly treatment per the near-monochrome color doctrine.
- `BriefingPullquoteView`: serif display quote, hairline rules.

### Interactions

- **Source tap** → `BriefingSourceSheet`:
  - `content:*` → sheet-presented `ContentDetailView(contentId:contentType:navigationSurface: .briefing)`
    — initializer already supports standalone presentation
    ([ContentDetailView.swift:121](../../../client/newsly/newsly/Views/ContentDetailView.swift));
    full action bar comes along free (open original, share, reader, knowledge save, podcast
    audio, **learning deck**, **deep-dive chat**). Add the `.briefing` case to
    `ContentDetailNavigationSurface`.
  - `news:*` → compact detent sheet (title, summary, key points, open original via `SafariView`,
    mark read, "dig into this").
- **Dig deeper**: user selects any phrase → existing edit-menu "Dig Deeper" action fires with the
  selection; tapping an insight hint range auto-selects it (set `selectedRange`) and opens the
  panel directly. `BriefingDigPanel` renders inline under the passage: staged
  `dig/search` (sources appear ~2s) → `dig/summarize` (digest streams in), in-memory cache keyed
  by normalized fragment, one retry, graceful error state. Guidance line under the lens deck:
  "Select any phrase to dig deeper — faint underlines mark spots worth a closer look."
- **Seen tracking**: segment frames observed in the lens scroll view; when a segment's bottom
  crosses above the viewport top, its `source_keys` join a batch published via
  `collect(.byTime(300ms))` → `markRead` (mirror of ShortNews). Version bump comes back in the
  response; hairline updates immediately.

### Narration

`BriefingNarrationBar` per lens ("Listen · 12 min"): `POST /briefing/narration` → episode id →
existing `NarrationPlaybackService` streaming playback (AVPlayer + authorized media resource,
[NarrationPlaybackService.swift](../../../client/newsly/newsly/Services/NarrationPlaybackService.swift)),
speed control reused. Server marks sources read on play (D8); client also refreshes on completion.
Episode `input_hash` (existing unique constraint on `audio_episodes`) makes narration idempotent
until the lens changes. Foreground-only in v1 (no `UIBackgroundModes` today); background audio +
Now Playing is a listed stretch.

## Phased implementation plan

Each phase is a self-contained brief: an implementing agent should read `CLAUDE.md`,
this doc's *Recorded decisions* + the relevant architecture section, then the phase. House
rules apply: never commit/push unless asked; `ruff check` touched Python + focused `pytest`;
regenerate contracts when `app/models/api/*` or `contracts.py` change and verify with
`scripts/check_public_contracts.sh`; iOS builds/tests via XcodeBuildMCP or
`xcodebuild test -scheme newsly -destination 'platform=iOS Simulator,name=iPhone 16'`.

### Phase 0 — Schema, contracts, settings scaffolding (S)

- [ ] Migration + `app/models/db/briefing.py` (four tables above; export via `models/db/__init__`).
- [ ] Enums in `app/models/contracts.py`: `BriefingTier`, `BriefingBlockType`, `BriefingRunKind`,
      `TaskType.BRIEFING_REFRESH`, `AudioEpisodeKind.BRIEFING_NARRATION`.
- [ ] `app/models/api/briefing.py` with every contract listed above; register in
      `contracts_registry.py`; run `scripts/regenerate_public_contracts.sh`.
- [ ] Settings block in `app/core/settings.py` (all `briefing_*` knobs named in this doc, with
      the defaults given; `briefing_model` defaults to `OPENROUTER_DEEPSEEK_FLASH_MODEL_SPEC`).
- [ ] Empty `app/services/briefing/` package.
- Tests: model round-trip (create/query each table), contract snapshot import, settings defaults.
- Acceptance: `uv run alembic upgrade head` clean on fresh DB; `scripts/check_public_contracts.sh`
  passes; generated Swift/Go contain `BriefingIndexResponse`.

### Phase 1 — Source assembly, lens taxonomy, normalization (M, LLM-free)

- [ ] `sources.py`: unread longform/audio + news assembly per spec (image URLs included).
- [ ] `lenses.py`: fixed lenses; topic-slug assignment; embedding centroid assignment
      (`encode_news_texts`); new-lens candidate detection (cohesion math only — naming call is a
      stub injected in Phase 2); misc-fold; lens retirement.
- [ ] `normalize.py`: markdown+markers → paragraphs/runs; `narration_text`; port regexes and
      `close_unpaired_insights` from the prototype renderer.
- Tests (bulk of the phase): golden parser tests covering every DeepSeek quirk (unclosed
  insights, stray markers, bare bracket links, links spanning sentence splits), narration
  derivation, centroid assignment/threshold edges, source eligibility filters (read/age/status).
- Acceptance: `uv run pytest tests/services/briefing/ -v` green; parsing handles the archived
  `outputs/unread_briefing_prototype/user_1_current/newspaper.json` blocks without loss
  (fixture-driven).

### Phase 2 — Composer + repair (M)

- [ ] `app/prompts/briefing/layout.md` (+`lens_naming.md`, `masthead.md`) — port prototype
      prompt text, minus learn-more/follow-up instructions, restructured for cache-stable prefix.
- [ ] `composer.py`: window planner, prompt builder, `get_basic_agent` call with thread-pool
      timeout + `LLM_ATTEMPTS`, deterministic fallback (degraded), usage recording.
- [ ] `repair.py`: the 8-step pipeline above.
- [ ] Lens-naming + masthead-deck calls.
- [ ] `admin briefing generate --user 1 --lens podcasts` (or a `scripts/`-level dev entry) for
      manual one-lens generation against local DB.
- Tests: repair steps individually (feed it hand-built malformed layouts reproducing each quirk);
  window planning math incl. figure budgets; composer with a faked agent (no network); fallback
  path marks `degraded` and records warnings.
- Acceptance: manual run against local `newsly_prod`-derived DB produces a valid normalized
  segment for a real lens; `vendor_usage_records` row written with `feature='briefing_compose'`.

### Phase 3 — Refresh orchestration + pipeline task (M)

- [ ] `TASK_SPECS` entry (`TaskQueue.LLM`, payload model, dedupe) +
      `app/pipeline/handlers/briefing_refresh.py` + dispatcher registration
      ([app/pipeline/sequential_task_processor.py](../../../app/pipeline/sequential_task_processor.py)).
- [ ] `refresh.py` algorithm (assign → append → retire → compact → degraded-retry → masthead →
      version bump → re-enqueue sweep).
- [ ] Ingestion hooks: content-lifecycle completion + news READY → pending row + debounced
      enqueue, gated by `briefing_enabled_user_ids`.
- [ ] `read_marks.py` + version bump; `admin briefing status|refresh|costs`.
- Tests: pool-threshold behavior (min window, max age force), dedupe/debounce (second enqueue
  coalesces), retirement on read + news age-out, compaction trigger + donor marking, idempotent
  re-run after simulated crash mid-batch, sweep self-rescheduling, hooks no-op for non-enabled
  users.
- Acceptance: seed unread backlog locally → enqueue → worker builds a full multi-lens edition;
  add 3 new news items → exactly one debounced task appends exactly one new segment.

### Phase 4 — Read API + narration backend (M)

- [ ] `routers/api/briefing.py`: all seven endpoints, ETag/304 on index, per-lens assembly with
      live read flags, read-marks delegating to existing bulk commands, dig endpoints (Exa +
      digest with `feature='briefing_dig'` usage + hourly rate limit), refresh trigger.
- [ ] `narration.py`: build `script_text` from active segments' `narration_text`,
      create-or-reuse episode by `input_hash`, store briefing source keys in episode `script`
      metadata; extend `mark_audio_episode_sources_read_on_play` to handle `content:`/`news:`
      keys (adapter, existing news-id path untouched).
- [ ] OpenAPI export + contracts regen; update `docs/library/reference/openapi.json`.
- Tests: router tests per endpoint (`tests/routers/test_api_briefing*.py`) — 304 path, read-mark
  idempotency + retirement + version bump, dig with mocked exa/agent + rate-limit 429, narration
  create/reuse, auth required everywhere.
- Acceptance: `uv run pytest tests/routers -k briefing -v` green;
  `scripts/check_public_contracts.sh` passes; manual curl walkthrough documented in PR notes.

### Phase 5 — iOS foundations: toggle, tab, data, skeleton (M)

- [ ] `ReadingExperience` setting + Settings picker + conditional tab swap
      (`ContentView.swift`, `TabCoordinatorViewModel` — preserve the long-form
      no-background-refresh test contract for Classic mode).
- [ ] Contracts regen on client (`client/newsly/scripts/regenerate_api_contracts.sh`).
- [ ] `BriefingService` (protocol + live), `BriefingViewModel` (index + lazy lens loads +
      neighbor prefetch + version re-check on activation + pull-to-refresh), skeleton
      `BriefingView` with pills + paged lenses rendering plain paragraph text (`Text` runs
      concatenated), figures via `CachedAsyncImage`, pullquotes basic.
- [ ] Seen tracking: segment-passed observer → 300ms batch → `markRead`; progress hairline.
- Tests: `BriefingViewModelTests` with mock service (initial load, 304 short-circuit,
  pagination between lenses, read-mark batching timing, version-poll after refresh) mirroring
  `LearningDeckReaderViewModelTests` conventions.
- Acceptance: toggle swaps tabs live; briefing renders a real local edition end-to-end in the
  simulator against the local server; scrolling past a segment marks its sources read (verify in
  Classic tab and DB).

### Phase 6 — iOS rich prose, sheets, dig deeper (L)

- [ ] `BriefingAttributedTextBuilder` (runs → NSAttributedString with link/insight/bold styling;
      unit-tested) + `BriefingPassageView` UITextView wrapper (extend `DigDeeperTextView`:
      selection menu action, link-tap delegate, insight-tap auto-select via `selectedRange`).
- [ ] `BriefingSourceSheet`: news compact sheet; content → `ContentDetailView` in `.sheet`
      with new `.briefing` navigation surface (verify learning-deck + chat actions work from
      the sheet).
- [ ] `BriefingDigPanel` + `BriefingDigViewModel`: staged search→summarize, per-fragment cache,
      retry, error copy ("Couldn't dig into that just now — try again."), guidance line UI.
- [ ] Typography polish: feature/brief scales, serif masthead/lens decks, dotted insight
      underlines, near-monochrome figure treatment.
- Tests: builder golden tests (runs → attributes incl. ranges), `BriefingDigViewModelTests`
  (staged flow, cache hit, search failure fallback messaging), sheet routing tests.
- Acceptance: on-device/sim walkthrough — tap link → detail modal with working learning-deck
  button; select arbitrary phrase → live dig (sources <3s, digest <8s); tap hint underline →
  auto-select + dig; airplane mode shows graceful dig error.

### Phase 7 — Narration end-to-end (M)

- [ ] `BriefingNarrationBar` (create/poll episode → stream via `NarrationPlaybackService`,
      chunk-follow endpoint, speed control, progress).
- [ ] Read-on-play verification (server hook) + client edition refresh on completion; regenerate
      episode when lens version changed (`input_hash` mismatch → new episode).
- [ ] Duration estimate in bar (from `script_text` length pre-completion; episode duration after).
- Tests: narration VM tests (episode reuse vs regenerate, playback state transitions, completion
  → refresh); backend test for mixed-kind read-on-play.
- Acceptance: "Listen" on Articles narrates every active segment in order; finishing playback
  leaves those sources read everywhere; replay reuses the cached episode.

### Phase 8 — Hardening, ops, rollout (M)

- [ ] Payload budget: per-lens response target <300KB — assert in router test with a fat
      seeded edition; prune `BriefingSourceDto` fields if exceeded.
- [ ] Perf: lens page `LazyVStack`, image prefetch for next lens, index-only foreground polls.
- [ ] Ops: `admin briefing status` (lenses/segments/pending/degraded/version + last sweep),
      `admin briefing costs` (vendor usage rollup for `briefing_*` features); structured
      `logger.error` + `extra` on every repair warning class for drift detection.
- [ ] Seed path: extend `scripts/generate_test_data.py` so the simulator gets a briefing edition
      out of the box.
- [ ] Kill switches verified: user removed from `briefing_enabled_user_ids` → hooks/sweep no-op;
      client toggle back to Classic restores old tabs exactly.
- [ ] Docs: `docs/architecture.md` briefing subsection (data model + queue flow),
      `docs/codebase/` entries for `app/services/briefing/` and the iOS Briefing folder; update
      `docs/initiatives/README.md` status.
- Acceptance: full checklist walkthrough on fresh local DB + simulator; production deploy plan
  (run migration, set `briefing_enabled_user_ids=[1]`, sweep bootstrap, watch
  `admin briefing status`).

### Dependencies & parallelism

```
P0 ─► P1 ─► P2 ─► P3 ─► P4 ─► P5 ─► P6 ─► P7 ─► P8
        └─(P2 prompts can start alongside P1)   (P6 and P7 are parallel after P5)
```
P5 UI scaffolding can begin against mocked `BriefingService` as soon as P0 lands (contracts
exist); everything else is sequential on its arrow.

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| DeepSeek Flash drift breaks composition | Repair pipeline is load-bearing by design; every repair emits a structured warning; sweep retries `degraded`; deterministic fallback always renders. |
| Windows time out | Small windows (≤6), no learn-mores, 300s budget, 2 attempts, thread-pool timeout (OpenRouter default is 120s — composer must own its own budget). |
| Fragmented editions read choppy | Compaction thresholds + newest-first ordering; masthead deck stitches continuity. |
| Read-state divergence between briefing and Classic tabs | Single source of truth (D5) + live read joins on GET + `.contentMarkedAsRead` client notification interop. |
| Dig latency/failures feel broken | Two-staged response, inline sources within ~2s, cache, retry, honest error copy; rate-limited to protect spend. |
| Narration cost (ElevenLabs) on long lenses | `input_hash` reuse; per-lens (not per-edition) episodes; duration cap `briefing_narration_max_chars` with "narrate newest first" truncation. |
| Payload bloat on big backlogs | Per-lens lazy endpoints, backlog caps, segment retirement/age-out, 300KB budget test. |
| No cron infra | Self-rescheduling sweep + event debounce (D10) — both pure queue primitives with dedupe. |

## Open questions (defaults chosen, revisit post-v1)

1. "For-you" personalized lens — excluded from v1 (D11); could return using the reranker once
   `news_list_reranker_enabled` graduates.
2. Background audio + Now Playing for narration — requires `UIBackgroundModes`; stretch.
3. Per-source read granularity on scroll (vs per-segment D12) — revisit if segment-level feels
   too eager in practice.
4. Insight hints could seed suggested dig chips (prototype's follow-up questions) — one extra
   small call per window if wanted later.

## Appendix A — DeepSeek Flash guardrails (paid-for lessons, keep forever)

- Discriminated unions in the output schema hang until timeout → **flat model, optional fields,
  validate per-type in repair** (D16).
- Deep windows >5–7 sources time out once outputs balloon → small windows; learn-mores removed.
- Emits bare `[newsly://…]` citations unless the prompt shows one exact link example → keep the
  literal example in `#prose_rules`.
- Sometimes writes passage prose into `text` instead of `markdown` → repair step 4 recovers.
- Leaves ~half of `{{insight}}` markers unclosed → `close_unpaired_insights` in repair AND
  normalize (defense in depth).
- Ignores per-window figure budgets → hard cap enforcement in repair, never trust the prompt.

## Appendix B — Prototype → production mapping

| Prototype | Production home |
|---|---|
| `LayoutBlock` / `GeneratedLayoutBriefing` (flat) | `ComposerBlock` / `ComposerLayout` in `composer.py`; wire contract is the normalized `BriefingBlockDto` |
| `DEEP_LAYOUT_RULES` / `NEWS_LAYOUT_RULES` / `PROSE_RULES` | `app/prompts/briefing/layout.md` sections via `prompt_library` |
| `repair_layout` + `close_unpaired_insights` | `services/briefing/repair.py` |
| `generate_lens_layout` windowing + insight prefixing | `services/briefing/composer.py` + `refresh.py` |
| `freeze_podcast_sources` / categories.json lens freeze | `sources.py` + `lenses.py` + `briefing_pending_sources` (no JSON snapshots) |
| Render-side LINK_RE/INSIGHT_RE parsing, split_paragraphs | `services/briefing/normalize.py` (server-side, once) |
| `scripts/serve_dig_deeper.py` /api/search + /api/summarize | `/briefing/dig/search` + `/briefing/dig/summarize` (authed, rate-limited, usage-tracked) |
| JS scroll-scan seen tracking + hairline | `BriefingLensPageView` observer + `markRead` batching (D12) |
| JS selection pill + hint auto-select | UITextView edit menu ("Dig Deeper") + `selectedRange` auto-select |
| Static HTML bottom sheets | `BriefingSourceSheet` (news) / `ContentDetailView` modal (content) |
| Mirrored prod images | `/static/images/...` URLs straight from contracts (D13) |
