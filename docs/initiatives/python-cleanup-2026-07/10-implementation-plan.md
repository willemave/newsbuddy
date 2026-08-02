# Python Cleanup — Implementation Plan (2026-07)

## Goal

Execute the 2026-07-05 full Python audit: fix 15 latent bugs, delete ~13,500 lines of verified-dead
code, land the highest-leverage performance fixes (HTTP connection hygiene, worker import weight,
queue retention, scraper batching), and collapse the five biggest duplicated subsystems — without
changing product behavior. Every work package below is sized to land as one reviewable PR.

## Provenance

Findings come from a 13-agent parallel audit over `app/`, `admin/`, `scripts/`, and `tests/`
(897 files, ~202k lines). Full report with all 218 findings:
<https://claude.ai/code/artifact/5fe3b888-cc78-497f-9533-1738f1dc7225>

Every dead-code claim was verified by repo-wide reference search (app, tests, scripts, admin,
client, docs, config, crontabs, supervisor, CI, `.claude/` skills) at audit time. **Re-verify at
execution time** — the greps are cheap and the codebase moves.

Companion audits already on file: `docs/architecture-improvement-plan-2026-06-09.md`,
`docs/initiatives/vulture-dead-code-2026-05/`, `docs/initiatives/test-simplification-2026-05/`.
This plan supersedes none of them; overlaps are noted inline.

## Ground rules

1. **One work package = one PR.** No mixing deletion PRs with behavior-change PRs. Deletion PRs
   must be verifiable as no-behavior-change by review alone.
2. **Verify before delete.** For each symbol: `rg <name>` across the repo including string
   references (task types and admin actions are dispatched by string name), `docs/`, `.claude/`,
   `crontab`, `docker/crontab`, `supervisor.conf`, `client/`, `cli/`, and `config/`.
3. **After every package:** `uv run ruff check .`, targeted `uv run pytest tests/<area>`, and
   `uv run vulture`. Prune `vulture_whitelist.py` entries that pointed at deleted code — the
   whitelist must shrink, never grow, during this initiative.
4. **Docs stay honest.** When scripts or admin commands are removed, update
   `docs/library/operations/command-index.md` (already stale — see WP1.4) and any
   `docs/codebase/` page that referenced them.
5. **Migrations** (dropped columns, NOT NULL changes) ride Alembic as usual; column drops ship
   one release after the code stops writing them.
6. Items marked **DECIDE** need Willem's call before the containing package starts. They are
   collected in "Decisions needed" at the bottom.

---

## Phase 0 — Latent bug fixes

Small, independent, immediately valuable. Each lands with a regression test. Order within the
phase does not matter.

### WP0.1 — HTTP retry never retries 5xx
[app/services/http.py:186](../../../app/services/http.py) — the tenacity predicate excludes
`httpx.HTTPStatusError`, while `categorize_http_error` deliberately re-raises retryable 5xx
unwrapped "because retryable". Fix: introduce `RetryableHttpError`, raise it for retryable
statuses, include it in the retry predicate. Test: mock transport returning 503, 503, 200 —
assert three attempts and success; assert 404 does not retry.

### WP0.2 — Feed fetches have no timeout
[app/scraping/atom_unified.py:140](../../../app/scraping/atom_unified.py),
`substack_unified.py:142`, `podcast_unified.py:76`, `aggregators/_rss_cluster.py:52` — all call
`feedparser.parse(url)`, which fetches via urllib with no timeout; one hung host stalls the whole
scrape run. Fix: fetch bytes via the shared HTTP service (with its timeout), pass
`response.content` to `feedparser.parse`. This is the minimal fix; connection pooling and
conditional GETs come in WP2.6/WP3.4. Test: feed a slow-server fixture, assert timeout raises and
the run continues to the next feed.

### WP0.3 — `result.all_messages` stored as bound method
[app/services/chat_agent.py:1147](../../../app/services/chat_agent.py) and `:1619` assign
`result.all_messages` without calling it. Combined with the dead-field finding (WP1.10), the
right fix is to **delete** `ChatRunResult.all_messages` and `.tool_calls` entirely (never read
anywhere) rather than add the parens.

### WP0.4 — Tool-call logging is permanently empty
`getattr(result, "tool_calls", [])` at chat_agent.py:1100/1355/1587 and
assistant_router.py:1487 — pydantic-ai run results have no `tool_calls` attribute. Same root
cause in [app/services/feed_discovery.py:1246](../../../app/services/feed_discovery.py)
(`_summarize_tool_calls` always returns 0). Fix: derive tool names from
`result.new_messages()` `ToolCallPart`s — `assistant_eval.build_assistant_trace` already shows
how. Test: agent stub emitting one tool call; assert the log extra contains it.

### WP0.5 — Admin errors dashboard shows oldest entries
[app/admin_web/logs.py:698](../../../app/admin_web/logs.py) — `_get_recent_errors` reads
forward and stops at `limit`; `_get_recent_structured_events` correctly uses `reversed(lines)`.
Fix: match the sibling. Test: JSONL fixture with 20 entries, limit 10, assert the newest 10.

### WP0.6 — ChatGPT hand-off URL doubles the article body
[app/queries/get_content_chat_url.py:57-89](../../../app/queries/get_content_chat_url.py) —
body appended via `prompt_parts` and again via `content_text`. Fix: keep the body only in
`content_text`. Test: assert body substring appears exactly once and URL length halves for a
long-body fixture.

### WP0.7 — PDF misconfiguration classified as retryable
[app/processing_strategies/pdf_strategy.py:106](../../../app/processing_strategies/pdf_strategy.py)
— `NonRetryableError` raised inside the `try` whose `except Exception` swallows it. Fix: move
the config guard above the `try`; re-raise `NonRetryableError` in the except. Test: unset
`PDF_GEMINI_MODEL`, assert task fails non-retryable.

### WP0.8 — Robust client phantom setting + malformed UA
[app/http_client/robust_http_client.py:44](../../../app/http_client/robust_http_client.py) —
`getattr(settings, "HTTP_CLIENT_USER_AGENT", …)` references a setting that cannot exist
(`extra="ignore"`), and the default UA string contains a stray trailing `)`. Fix: read
`settings.http_timeout_seconds` where the docstring promises it, fix the UA constant, delete the
phantom getattr. Also close streamed responses on the success path (`:135`) — wrap in a proper
context manager. (Full get/head dedup waits for WP3.1.)

### WP0.9 — `admin db explain` always errors
[admin/remote_ops.py:122](../../../admin/remote_ops.py) prefixes SQLite-only
`EXPLAIN QUERY PLAN` on a Postgres runtime. **DECIDE D1:** port to Postgres `EXPLAIN` (and relax
[admin/sql_guard.py:49](../../../admin/sql_guard.py)) or delete the subcommand in WP1.8.
Default recommendation: delete — nothing references it.

### WP0.10 — Hardcoded prod SSH coordinates in service code
[app/services/prompt_debug_report.py:38](../../../app/services/prompt_debug_report.py) —
`SyncOptions` defaults embed `remote_user`/`remote_host`. Fix: source from settings/env, keep
fields default-empty. (The sync path itself is dead per WP1.8; do this only if the sync path
survives D1/WP1.8 — otherwise the deletion covers it.)

### WP0.11 — Small correctness fixes, one PR
- [app/services/weekly_discovery_chat.py:49](../../../app/services/weekly_discovery_chat.py):
  `_user_local_date` ignores `user`, hardcodes UTC. **DECIDE D2:** wire a real user timezone or
  simplify to `datetime.now(UTC).date()` and rename honestly. Recommendation: simplify (single
  user, UTC has been the de-facto behavior all along).
- [app/models/metadata/summaries.py:558](../../../app/models/metadata/summaries.py):
  `NewsSummary` `extra="allow"` contradicts advertised `additionalProperties: false` → drop the
  `json_schema_extra` override (the strict LLM-facing variant is `GeneratedNewsSummary`).
- [app/services/gateways/object_storage_gateway.py:121](../../../app/services/gateways/object_storage_gateway.py):
  `copy()` is dead *and* would corrupt binary objects — deleted in WP1.10; do not fix, delete.

**Phase 0 exit criteria:** all fixes merged with tests; `pytest tests/services tests/scraping
tests/admin tests/queries tests/processing_strategies` green.

---

## Phase 1 — Pure deletion (~13,500 lines, no behavior change)

Safest-first order. Each package ends with the ground-rule checks (ruff, pytest, vulture prune).

### WP1.1 — Broken scripts (cannot run)
Delete `scripts/run_news_pipeline_eval.py`, `scripts/generate_news_pipeline_eval_html_report.py`,
`scripts/run_news_pipeline_embedding_matrix.py`, `scripts/export_news_pipeline_eval_cases.py`
(~1,100 lines). All import `app.services.news_pipeline_eval` / `news_pipeline_eval_models`,
which no longer exist. Zero risk.

### WP1.2 — Completed one-off migrations/backfills/remediations
Delete: `migrate_sqlite_to_postgres.py`, `migrate_session_to_user.py`,
`backfill_content_bodies.py`, `resize_thumbnails.py`, `retranscribe_podcasts.py`,
`cancel_ineligible_generate_image_tasks.py`, `manage_apple_signin_link.py`,
`reset_onboarding.py`, `enqueue_past_day_summarization.py`. All are finished one-shots with zero
references. If any feel worth keeping as recipes, move the pattern into
`docs/library/operations/` — do not keep executable code as documentation.

### WP1.3 — One-off evals/probes/experiments
Delete 17 zero-reference scripts: `evaluate_onboarding_cerebras.py`, `evaluate_feed_detection.py`,
`evaluate_youtube_equivalents.py`, `debug_anthropic_eval_failures.py`,
`debug_firecrawl_403_urls.py`, `debug_newspaper4k_403_urls.py`,
`probe_google_vertex_us_central1.py`, `validate_feed_discovery.py`,
`run_assistant_action_evals.py`, `test_interleaved_summary.py`,
`test_share_instruction_flow.py`, `test_share_instruction_direct.py`,
`generate_svg_infographics.py`, `generate_ascii_infographics.py`, `generate_thumbnails.py`,
`benchmark_fluxdev_prompt_study.py`, `benchmark_infographic_model_options.py`.

### WP1.4 — Superseded scripts + ops index regeneration
- Delete: `analyze_errors.py` (+ its two prompt fragments under `app/prompts/scripts/`),
  `dump_database.py`, `run_twitter_sync.py` (shim; crontabs call `run_twitter.py`),
  `run_title_clustering_opus.py`, `export_title_clustering_dataset.py`,
  `add_user_scraper_config.py`, `build_prompt_debug_report.py` (identical to
  `admin debug prompt-report`).
- **DECIDE D3** before touching: `poll_notes_requests.py` (may back the Apple Notes
  request-executor workflow), `import_config_feeds.py`, `bootstrap_user_feeds.py`,
  `run_supervisor_status.py` (legacy supervisor deploy path).
- Regenerate `docs/library/operations/command-index.md` — it already lists a nonexistent script
  (`backfill_summary_kind.py`) and several now-deleted ones. Fold `dump_system_stats.py` into
  `admin health snapshot` here or leave as dev tool (D3).

### WP1.5 — Twitter GraphQL scraper
[app/services/twitter_share.py](../../../app/services/twitter_share.py): delete the GraphQL
client (~700 of 912 lines — `fetch_tweet_detail`, `TwitterCredentials`,
`TweetFetchParams/Result`, `TweetInfo`, `QueryIdSnapshot`, bundle/query-id discovery,
tombstone/article/note extraction) and `tests/services/test_twitter_share.py` coverage of it.
Keep `extract_tweet_id`, `is_tweet_url`, `canonical_tweet_url` — relocate into `x_api.py` or a
small `x_urls.py`, and delete the byte-identical `x_api.is_tweet_url` duplicate at the same
time so exactly one home remains ([app/services/url_detection.py:14](../../../app/services/url_detection.py)
is the main consumer). Retire `settings.twitter_query_id_cache` and
`twitter_auth_token`/`twitter_auth_token_configured`
([app/core/settings.py:186,400,403,714](../../../app/core/settings.py) — the alias at `:408`
already maps `TWITTER_AUTH_TOKEN` → `x_app_bearer_token`).

### WP1.6 — Legacy podcast pipeline generation
[app/pipeline/podcast_workers.py](../../../app/pipeline/podcast_workers.py): delete
`PodcastDownloadWorker.process_download_task` (~310 lines) and
`PodcastTranscribeWorker.process_transcribe_task` (~235 lines) + `cleanup_service` — production
routes DOWNLOAD_AUDIO / TRANSCRIBE / PROCESS_PODCAST_MEDIA exclusively through
`PodcastMediaWorker.process_media_task`; only tests call the legacy bodies. Fold the surviving
download helpers (`_download_with_retry`, `_download_youtube_audio`, URL helpers) into
`PodcastMediaWorker`, drop the redundant `transcribe_worker` warm-up at `:803`, and delete
`worker._process_podcast_sync` (`:876`, one legacy test caller). Retarget or delete the
affected tests. This roughly halves the file.

### WP1.7 — Scraping legacy surface
- YAML loaders: `load_atom_feeds`/`load_substack_feeds` + config-path/warning plumbing
  ([app/scraping/atom_unified.py:34-102](../../../app/scraping/atom_unified.py), mirrored in
  `substack_unified.py`) — production loads from DB via `list_active_configs_by_type`.
  Retarget `tests/test_atom_scraper.py` / `tests/scraping/test_substack_scraper.py` at the DB
  path (fixtures already exist in `tests/scraping/`).
- Back-compat shims `hackernews_unified.py`, `techmeme_unified.py` + `config/techmeme.yml` —
  imported only by their own legacy tests; retarget tests at the aggregator classes.
- Unreachable NEWS branches in [app/scraping/base.py:158-261](../../../app/scraping/base.py)
  (everything gated on `content_type_value == NEWS` after the `continue` at `:153`).
- Dead `"front"` subreddit branches in `reddit_unified.py:130-144` (loader skips `"front"`).
- `podcast_unified.py`: unused `_emit_missing_config_warning`, `_sanitize_filename` (no callers
  repo-wide); drop the ignored `config_path` constructor params on all three feed scrapers.

### WP1.8 — Admin CLI dead surface
- `admin events` group end-to-end: `_build_events_parser`, `_handle_events`
  ([admin/cli.py:352,807](../../../admin/cli.py)), dispatch branch
  ([admin/remote.py:155](../../../admin/remote.py)), `events_list`
  ([admin/remote_ops.py:382](../../../admin/remote_ops.py)), `_format_events`
  ([admin/output.py:380](../../../admin/output.py)), hardcoded `"events"` block in
  `health_snapshot` + its formatter line.
- `db explain` + `pragma` allowlist entry (per D1).
- Legacy knobs: `remote_python` (`--remote-python`, `ADMIN_REMOTE_PYTHON`, config field,
  `.env.example` line), `--remote-db-path`/`--remote-context-source` "direct" mode
  ([admin/cli.py:109-123](../../../admin/cli.py), [admin/ssh.py:78-81](../../../admin/ssh.py);
  one test at `tests/admin/test_cli_fix.py:23`).
- `debug prompt-report` sync-db path: `--skip-sync-db` becomes the only behavior; delete
  `DEFAULT_LOCAL_DB_PATH`'s stale SQLite value (covers WP0.10).
- `admin/__init__.py` unused `__version__`.

### WP1.9 — Briefing & learning-deck prototype residue
- Centroid experiment: `_assign_by_centroid`, `_centroid_for_sources`
  ([app/services/briefing/lenses.py:271,876](../../../app/services/briefing/lenses.py)) and the
  never-enabled `briefing_centroid_assignment_enabled` setting
  ([app/core/settings.py:300](../../../app/core/settings.py)).
- **DECIDE D4 — compaction:** `_compact_fragmented_lenses`
  ([app/services/briefing/refresh.py:756](../../../app/services/briefing/refresh.py)) is
  unreachable in production (only the non-releasing path calls it, and production always runs
  `release_db_during_compose=True`). Either wire it into the releasing path deliberately (it was
  decision D4 of the briefing plan) or delete it with `COMPACTION_WINDOW_INDEX`. This decision
  gates WP3.6.
- Write-only columns: stop persisting `BriefingSegment.markdown_raw`
  ([app/models/db/briefing.py:43](../../../app/models/db/briefing.py)) — **DECIDE D5**, the
  briefing plan kept raw markdown "for debugging/regeneration"; if that still stands, document
  it and keep. `retired_at` (`:26`, written at lenses.py:230/254, never read) → drop column via
  migration or comment as audit-only.
- `learning_deck_artifacts.store_learning_deck_artifact(extra_text_assets=…)` param (never
  passed); `LearningDeckCommandResult` subclass + `run_command` indirection in
  `learning_deck_sandbox.py`; duplicate `guess_*_content_type` (keep the artifacts one);
  `BriefingSourceKey.value` property.

### WP1.10 — app/ grab-bag of verified-dead symbols
One PR, mechanical, each item is delete + prune references:
- `ContentProcessingWorkflow` + export
  ([app/pipeline/workflows/content_processing_workflow.py:13](../../../app/pipeline/workflows/content_processing_workflow.py)).
- `html_strategy._run_coro_sync` (`:208`, superseded by `_ReusableCrawlerManager`).
- `extract_internal_urls` strategy hook + worker call site
  ([app/pipeline/worker.py:636](../../../app/pipeline/worker.py)) — every strategy returns `[]`.
- `HttpGateway.robust_get/robust_head/close` + unused `RobustHttpClient` field
  ([app/services/gateways/http_gateway.py:43](../../../app/services/gateways/http_gateway.py)).
- `LlmGateway.summarize` + `_summarizer` field ([app/services/gateways/llm_gateway.py:41](../../../app/services/gateways/llm_gateway.py)).
- `ObjectStorageGateway.copy` (ABC + both impls) — latent binary-corruption bug.
- News-thumbnail block in [app/services/image_generation.py:469-617](../../../app/services/image_generation.py)
  (~200 lines: `_generate_news_thumbnail`, `InterestingScore`,
  `_analyze_content_interestingness`, `_get_mood_from_score`, `_build_news_thumbnail_prompt`)
  — gated behind an unconditional "disabled" return.
- `ChatRunResult.all_messages`/`.tool_calls` (with WP0.3/WP0.4).
- `admin_eval` unreachable ThreadPool branch (`:229-242`, constant is 1).
- `XList`/`_map_list`, `fetch_user_tweets(exclude=…)` in `x_api.py`; phantom statuses +
  always-zero fields in `x_integration._resolve_combined_sync_status` (`:784`).
- tweet_suggestions manual JSON-parse path (`:251-311,375-386`) — pre-typed-output legacy.
- `DeepResearchResult.sources` (hardcoded None).
- `llm_summarization`: unreachable passthrough-set entries (`:181`), never-set
  `provider_hint`/`model_hint` (`:296`); `llm_prompts` no-op branch + duplicated prompt-type set
  (`:8,42`); `llm_agents` shadowed module TypeVar (`:11`); summarize handler's unreachable else
  + `provider_override` plumbing ([app/pipeline/handlers/summarize.py:363](../../../app/pipeline/handlers/summarize.py)).
- `extract_summary_text` alias + no-op `or` fallbacks
  ([app/utils/summary_utils.py:61](../../../app/utils/summary_utils.py),
  `title_utils.py:175`, `personal_markdown_library.py:483`).
- `app/models/metadata/discussion.py` (docstring-only module); stale archaeology comments
  (summaries.py:820, articles.py:68, news.py:115, db/users.py:30, http_client/__init__.py:1).
- Dead settings: `max_content_length`, `http_max_retries`, `max_retry_attempts`,
  the retired conversational-agent settings plus their `vulture_whitelist.py`
  entries and `QueueSettingsView` echo.
- Blanket `# ruff: noqa: F401` headers on 11 `app/models/api/` files → run
  `ruff --ignore-noqa --select F401`, delete the ~60 dead imports, keep noqa only in true
  re-export hubs (`app/models/db/__init__.py`, `app/models/metadata/__init__.py`). Run contract
  codegen (`scripts/regenerate_public_contracts.py`) afterward to prove nothing shifted.

### WP1.11 — Test-only production code + tests cruft
- `read_status_repository.get_read_content_ids/is_content_read/clear_read_status` + the
  `app/services/read_status.py` shim (its own TODO says delete);
  `knowledge_repository.list_knowledge_content_ids/clear_knowledge_library` + wrappers.
- tests/: unused fixtures (`integration_connection_factory`, `ios_onboarding_personalized_fixture`,
  `fixtures_dir`), legacy `db` fixture (rename 6 usages to `db_session`),
  `test_fixtures_example.py` (157 lines of fixtures-testing-fixtures), empty `tests/presenters/`,
  move stray root-level `test_atom_scraper.py`/`test_main.py` into their layer dirs, replace the
  six never-firing conditional `pytest.skip` guards in pagination tests with hard assertions.

**Phase 1 exit criteria:** full `uv run pytest` green; `uv run vulture` clean with a strictly
smaller whitelist; `scripts/check_module_size_guardrails.py` passes; command-index regenerated;
contract regeneration produces no diff.

---

## Phase 2 — Performance quick wins

Independent of Phase 3 consolidations; land in any order.

### WP2.1 — Pooled HTTP clients (minimal version)
Hold two long-lived `httpx.Client`s (verify on / verify off for `SSL_BYPASS_DOMAINS`) on
`HttpService` ([app/services/http.py:174](../../../app/services/http.py)); same one-client
treatment for `x_api._request_json` (`:518`), `podcast_search` (3 sites),
and `image_generation` bare `requests.*` (`:949` → module-level `Session`). Full three-layer
consolidation is WP3.1; this package only stops the per-request TCP+TLS churn.

### WP2.2 — Lazy heavyweight imports in workers
Move `import torch/transformers` inside functions in
[app/services/news_reranker.py:9](../../../app/services/news_reranker.py) and
`import torch/whisper` in [app/services/whisper_local.py:4](../../../app/services/whisper_local.py)
(mirroring `news_embeddings.py`'s existing lazy pattern). Every worker process currently pays
seconds of import and ~1GB RSS via the `sequential_task_processor → worker → podcast_workers`
import chain. Verify with `time uv run python -c "import app.pipeline.sequential_task_processor"`
before/after.

### WP2.3 — Queue hygiene
- Retention: add a periodic cleanup (self-rescheduling task or supercronic entry) deleting
  completed/failed `processing_tasks` older than ~14 days — the table is never pruned anywhere
  today. **DECIDE D6:** retention window.
- Scope `get_backpressure_status` ([app/services/queue.py:807](../../../app/services/queue.py))
  to the two pending counts it actually thresholds, instead of `get_queue_stats()`'s five
  unbounded aggregates per scrape-loop iteration.
- Dequeue index fix (`:397`): order by `available_at` directly (column is NOT NULL), migrate
  `retry_count` to NOT NULL DEFAULT 0, drop both `coalesce()` wrappers. Simplify
  `_lookup_active_task_by_dedupe_key` (`:98`) to a plain `.first()` — the partial unique index
  guarantees at most one active row.

### WP2.4 — Object-storage gateway
[app/services/gateways/object_storage_gateway.py](../../../app/services/gateways/object_storage_gateway.py):
record vendor usage only for writes/deletes (or sample reads) instead of a dedicated DB
session + commit per op including `exists()` probes (`:173`); build `StoredObjectMetadata` from
`len(data)` instead of the HEAD-after-every-PUT (`:204`).

### WP2.5 — Settings view caching
[app/core/settings.py:610](../../../app/core/settings.py): `queue`/`auth`/`storage`/`providers`/
`discovery`/`integrations`/`observability` properties → `functools.cached_property` (settings
object is a cached singleton, so this is safe).

### WP2.6 — Scraper save batching
[app/scraping/base.py:127-256](../../../app/scraping/base.py): prefetch existing
`(url, content_type)` pairs with one `IN` query per batch; commit per scraper run instead of per
item; drop the per-item `refresh()` (flush provides ids). Keep semantics identical — same items
saved, same stats. (Conditional GETs / ETag persistence needs a place to store validators per
feed; that rides WP3.4 where the fetch path is unified.)

### WP2.7 — Small wins, one PR each or batched pragmatically
- Admin CLI: drop the client-side `context_override` SSH pre-fetch
  ([admin/ssh.py:77](../../../admin/ssh.py)) — remote side already self-resolves; halves
  per-command latency.
- X bookmark sync: one request with `max_results=50` instead of 5 pages of 10
  ([app/services/x_integration.py:47](../../../app/services/x_integration.py)).
- Search endpoints: run the two external searches concurrently
  ([app/queries/search_mixed.py:19](../../../app/queries/search_mixed.py),
  `search_external_results.py:14`).
- Whisper chunked transcription: bounded `ThreadPoolExecutor` over independent chunks
  ([app/services/openai_llm.py:350](../../../app/services/openai_llm.py)).
- Audio stream polling: progressive backoff 0.25s → 2s + session reuse
  ([app/services/audio_episodes/__init__.py:1139](../../../app/services/audio_episodes/__init__.py)).
- Admin web: convert `async def` views doing sync work to `def`
  (`logs.py:47,132,176`, `usage.py:21`); tail-N log reads instead of whole-file parses; fix the
  insight-reports 4-per-user N+1 (`insight_reports.py:30`).
- Sessions held across long ops: split load/work/persist in
  [app/pipeline/podcast_workers.py:769](../../../app/pipeline/podcast_workers.py) (media path)
  and [app/pipeline/handlers/summarize.py:110](../../../app/pipeline/handlers/summarize.py) —
  the `release_db_during_compose` pattern in briefing refresh is the model.
- Legacy HN comment fetch: point `_build_hackernews_payload`
  ([app/services/discussion_fetcher.py:362](../../../app/services/discussion_fetcher.py)) at
  the Algolia single-request tree fetch already implemented in
  `news_item_discussions._fetch_hackernews_comments` (`:570`). Interim fix; full unification is
  WP3.3.

**Phase 2 exit criteria:** scrape-cycle wall time and worker RSS measured before/after (capture
in `20-verification.md`); queue table has a retention path; no admin-web route blocks the loop.

---

## Phase 3 — Consolidations

One refactor per package, each with a short design note in this directory before code
(`3x-<topic>-design.md`) if the shape isn't already dictated below. Ordered by leverage;
WP3.1–WP3.4 are independent of each other.

### WP3.1 — One HTTP client layer
Collapse `app/services/http.py` (588 lines; fetch/head/fetch_content triplication),
`app/http_client/robust_http_client.py` (get/head ~100-line duplication, hostname-mismatch
tolerance), and the ad-hoc callers (`podcast_search`, `firecrawl_client`, `apple_podcasts`)
onto a single pooled sync client module with one retry/categorization policy: `fetch`, `head`,
`fetch_bytes`, plus the hostname-tolerant variant the processing strategies need. Gateway facade
(`http_gateway.py`) wraps the result or is deleted (its robust half died in WP1.10). Expected:
−600–800 lines, one home for WP0.1's retry semantics. Migration order: build the module, port
`RobustHttpClient` callers (processing strategies), port `HttpService` callers (scrapers,
enrichment), delete both old layers.

### WP3.2 — One chat-turn lifecycle runner
Extend [app/services/chat_turn_runtime.py](../../../app/services/chat_turn_runtime.py) with a
parameterized runner (deps-builder + persist-strategy + trace name) absorbing the four
copy-pasted state machines: `chat_agent.run_chat_turn` (:945), `process_message_async` (:1181),
`generate_initial_suggestions` (:1493), `assistant_router.process_assistant_turn_async` (:1282).
Include the duplicated personal-library runtime builder (chat_agent:862 /
assistant_router:1251). Fold in the blocking-work fix: prep phase (history load, library sync,
sandbox creation, tracker commits) runs in the threadpool, not on the event loop. Expected:
−500–700 lines and one place where rollback/history semantics can no longer drift.

### WP3.3 — One discussion-comments provider
Extract a `discussion_comments` module (HN via Algolia, Reddit via PRAW: fetch, tree walk,
normalize, retryability classification) consumed by both
[app/services/discussion_fetcher.py](../../../app/services/discussion_fetcher.py) (legacy
content path) and [app/services/news_item_discussions.py](../../../app/services/news_item_discussions.py)
(news-item path). Kills the 12 underscore-private cross-imports and the duplicated Reddit walk;
each file keeps only its persistence/summary orchestration. Expected: −400 lines.

### WP3.4 — One feed-scraper base
`FeedScraper` base in `app/scraping/rss_helpers.py`: fetch via the WP3.1 client (timeout,
pooled, ETag/If-Modified-Since validators persisted per feed config) → bozo/encoding
classification (single `ENCODING_OVERRIDE_EXCEPTIONS`) → limited entry iteration → canonical
item build. Subclasses supply platform key, content type, entry filter/transform:
`atom_unified`, `substack_unified` (~330 duplicated lines today), `podcast_unified`, and
`_rss_cluster`'s fetch third. Also parallelize `runner.run_all` with a small
`ThreadPoolExecutor` and make the HN aggregator's 1+N item fetches concurrent. Expected:
−400 lines, conditional GETs everywhere at once.

### WP3.5 — One summary-kind type system
Collapse onto the `SummaryKind`/`SummaryVersion` enums
([app/models/contracts.py:170](../../../app/models/contracts.py)): delete the string constants
in `app/constants.py:47`, merge the two inference helpers (`app/utils/summary_metadata.py:18` vs
`app/models/metadata/summary_contracts.py:40` — note their fallback rules differ; pick the
contracts one and write characterization tests first), and replace the ~230 lines of per-kind
flattening in `ContentData` with per-model methods or a `SummaryKind`-keyed dispatch table.
Fix the double-validation in `ContentData.validate_metadata`
([app/models/domain/content.py:51](../../../app/models/domain/content.py)) in the same pass.
Run contract codegen to confirm no wire-format change.

### WP3.6 — Briefing refresh single path (gated on D4)
Make `release_db_during_compose=True` the only orchestration in
[app/services/briefing/refresh.py](../../../app/services/briefing/refresh.py); route tests and
`generate_test_data.py` through it; reuse `composer.plan_windows` instead of the inline
re-implementation (`:486`). Fold in the column-only briefing index/read-marks queries
(`presentation.py:132`, `read_marks.py:63`), the retirement N+1 batch fix (`refresh.py:727`),
lazy `briefing_context` (`sources.py:275`), and the subpackage's triplicated clean-string /
ordered-dedupe helpers. Expected: −~180 duplicated lines plus the compaction decision made real.

### WP3.7 — Admin CLI declarative registry
One registry (`action name → argparse spec + payload fields + remote function + coercions`)
consumed by both [admin/cli.py](../../../admin/cli.py) and [admin/remote.py](../../../admin/remote.py),
replacing the 4-place-per-subcommand plumbing and the 25-branch dispatch chain; add a
`_open_session` contextmanager replacing 13 copies of engine/sessionmaker/dispose in
`remote_ops.py`. Expected: −~300 lines. (The SSH double-hop fix landed in WP2.7.)

### WP3.8 — Right-side-out subpackages
Invert `app/services/audio_episodes/` (1,741-line `__init__.py`, 4 re-export shims — one
re-exporting 11 private helpers) and `app/services/onboarding/` (~2,400-line `__init__.py`):
implementations move into the submodules, `__init__.py` becomes the thin re-export layer.
Import sites don't change. Mechanical but large diff; do it in two PRs (one per package) with
`git log --follow`-friendly moves.

### WP3.9 — Eval-harness common module
`eval_common.py` for the verbatim-duplicated `_extract_result_payload`,
`_resolve_prompt_settings`, `_build_news_context` (~55 lines, also copied in
`prompt_debug_report.py:750`), suite-runner boilerplate, and the single surviving legacy
`result.data` fallback removal across `admin_eval` / `summary_eval` / `assistant_eval`.

### WP3.10 — Chat router → commands
Move the four mutating endpoints out of [app/routers/api/chat.py](../../../app/routers/api/chat.py)
(`update_session`, `delete_session`, `create_assistant_turn`,
`_refresh_assistant_session_context`) into `app/commands/` mirroring `send_chat_message`;
replace the inline metadata block (`:288`) with `resolve_session_article_metadata`; collapse the
~15 aliased imports and repoint the two tests importing via router re-exports. Do the same for
`news.convert_news_item_to_article` (→ existing command) and `discovery.clear_discovery_suggestions`
(→ single UPDATE). Also delete the redundant `sessions_with_any_messages` query in
`build_session_summaries` ([app/queries/chat_read_models.py:461](../../../app/queries/chat_read_models.py)).

### WP3.11 — Shared micro-helpers
One PR: `_clean_string` ×8 → `app/utils/text.py`; `_utcnow_naive` ×6 → `app/utils/time.py`
(or reuse existing); `_duration_ms` ×9 → `app/core/timing.py`; optional-string coercion ×4 →
one util; feed-URL helpers (`_looks_like_feed_url` ×3, `_extract_urls_from_text` ×2,
normalizers ×6) → `app/services/feed_urls.py`; share-token twins
(`audio_episode_tokens` / `learning_deck_tokens`) → `share_tokens.py` parameterized by token
type; admin datetime parsing ×3 → one helper. Where copies diverge (comma handling in
`_coerce_non_negative_int`), write a characterization test and pick one behavior explicitly.

---

## Phase 4 — Infrastructure

### WP4.1 — Test harness rework
Session-scoped `postgres_harness` per xdist worker + per-test isolation via SAVEPOINT-based
transaction rollback (standard SQLAlchemy pattern); TRUNCATE fallback fixture for the few
real-commit tests (`vendor_usage_db`, ios_e2e live-server flows). Add `pytest-xdist`, run
`-n auto` locally and in CI. Target: full suite from ~4–5 min to under 1 min. Also: scope
`sample_contents` to module + tmp_path image dir. Builds on
`docs/initiatives/test-simplification-2026-05/`.

### WP4.2 — Extended ruff adoption
Add `PERF`, `C4`, `RET`, `PIE`, `FURB`, `PTH` to `[tool.ruff.lint] select`; autofix the 28
mechanical fixes; work down the remainder (~200 total today — hotspots `admin_web/logs.py`
and 9× obsolete `fromisoformat(x.replace("Z",…))`). Adopt
`PLW`/`ARG` as advisory (`--select` in a periodic check, not CI-blocking) until the count is
near zero. Flatten the stateless service classes (`TweetSuggestionService`,
`TweetTargetResolver`, `PaginationCursor` namespace) to module functions per the repo's own
functions-over-classes rule.

### WP4.3 — SQLAlchemy typed models (policy, not a big-bang)
`app/models/db/` is SA 1.x-style untyped `Column` on `declarative_base()`. Policy: new models
use `DeclarativeBase` + `Mapped[…]`/`mapped_column`; existing models migrate opportunistically
when a package is already being touched (WP3.6 briefing, WP3.8 audio are natural first
candidates). No dedicated migration PRs until the tail is small.

---

## Sequencing

```
Phase 0 (bugs)          — start immediately; all packages independent
Phase 1 (deletions)     — WP1.1→1.4 immediately (scripts, independent of everything)
                          WP1.5–1.11 after Phase 0 merges touching the same files
Phase 2 (perf)          — after Phase 1 lands in each area (avoids rebasing deletions)
Phase 3 (consolidation) — WP3.1–3.4 in parallel branches; WP3.6 gated on D4;
                          WP3.5/3.10 after WP1.10 (dead imports/symbols out of the way)
Phase 4                 — WP4.1 any time; WP4.2 after Phase 1 (less code to fix);
                          WP4.3 is standing policy
```

Suggested first week: WP1.1 + WP1.2 + WP1.3 (pure deletions, zero decisions), WP0.1 + WP0.2
(the two production-relevant bugs), and the D1–D6 decisions below so nothing downstream blocks.

## Verification strategy

- Every deletion PR: full `uv run pytest`, `uv run ruff check .`, `uv run vulture`
  (whitelist must shrink), `scripts/check_module_size_guardrails.py`, and for anything touching
  API models: `scripts/regenerate_public_contracts.py` with a clean diff.
- Perf packages: record before/after numbers in `20-verification.md` — scrape-cycle wall time
  (`admin logs tail` timestamps), worker import time
  (`time python -c "import app.pipeline.sequential_task_processor"`), worker RSS
  (`admin health snapshot`), test-suite wall time.
- Consolidations: characterization tests first where copies diverged (WP3.5 inference fallbacks,
  WP3.11 coercion helpers), then refactor against them.
- Production checks after deploy of Phases 1–2: `uv run -m admin logs exceptions --limit 20`
  daily for a week; queue depth via `admin health snapshot`.

## Decisions needed

| # | Decision | Recommendation |
|---|----------|----------------|
| D1 | `admin db explain`: port to Postgres `EXPLAIN` or delete | Delete (zero references) |
| D2 | `weekly_discovery_chat` timezone: real user tz or explicit UTC | Explicit UTC, rename helper |
| D3 | `poll_notes_requests.py`, `import_config_feeds.py`, `bootstrap_user_feeds.py`, `run_supervisor_status.py`, `dump_system_stats.py`: keep or delete | Verify Apple Notes workflow first; likely keep poll_notes, delete the rest |
| D4 | Briefing lens compaction: wire into production path or delete | Wire in — it was a recorded briefing-plan decision; deleting reverses D4 of that plan |
| D5 | `BriefingSegment.markdown_raw`: keep as debugging artifact or stop persisting | Keep short-term, add retention (segments are retired anyway); revisit after WP3.6 |
| D6 | `processing_tasks` retention window | 14 days completed/failed |
| D7 | Briefing prototype scripts (`generate_unread_briefing_*`, `render_madlib_style_lab`, ~7k lines, currently "developer tool") | Keep until briefing tab ships, then archive the losers |
| D8 | `SCRAPER_METRICS` counters (no production reader) | Surface in `admin health snapshot`; else delete |

## Out of scope

- Anything in `client/` (SwiftUI) or `cli/` (Go) — separate initiatives
  (`ios-modernization-2026-07` covers the client).
- Behavior changes to summarization prompts, model routing, or briefing composition.
- The `docs/architecture-improvement-plan-2026-06-09.md` structural items not listed here.
