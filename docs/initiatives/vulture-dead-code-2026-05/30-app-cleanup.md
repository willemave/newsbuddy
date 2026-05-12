# Vulture App Cleanup

**Date:** 2026-05-12  
**Scope:** Goals 4 and 5 app/test cleanup after the whitelist made the report actionable.

## What Changed

- Removed unused production helpers across `app/services`, `app/repositories`, `app/models`, `app/core`, `app/pipeline`, `app/scraping`, `app/processing_strategies`, and `app/utils`.
- Deleted obsolete modules with no remaining imports:
  - `app/models/domain/content_form.py`
  - `app/pipeline/checkout.py`
  - `app/utils/deprecation.py`
- Removed legacy `ContentWorker.checkout_manager` wiring after verifying no checkout methods were called.
- Removed obsolete test-only helpers reported by Vulture.

## Verification

```bash
uv run vulture
```

Result: exit code `3` with only Goal 6 admin/script findings remaining. There are no `app/` or `tests/` findings in the configured scan.

Remaining findings:

```text
scripts/benchmark_fluxdev_prompt_study.py:48: unused variable 'process_prompt_text'
scripts/benchmark_infographic_model_options.py:57: unused variable 'fluxdev_image_src'
scripts/benchmark_infographic_model_options.py:58: unused variable 'fluxdev_model'
scripts/benchmark_infographic_model_options.py:59: unused variable 'fluxdev_cost'
scripts/benchmark_infographic_model_options.py:60: unused variable 'fluxdev_elapsed'
scripts/export_title_clustering_dataset.py:67: unused attribute 'row_factory'
scripts/generate_test_data.py:162: unused variable 'X_AUTHORS'
scripts/generate_test_data.py:170: unused variable 'X_LISTS'
scripts/generate_test_data.py:176: unused variable 'X_POST_TEXTS'
admin/cli.py:451: unused function '_sync_database'
admin/remote_ops.py:55: unused function '_load_schema_models'
```

```bash
uv run ruff check app/core/deps.py app/core/logging.py app/core/observability.py app/core/settings.py app/constants.py app/models/api/api_keys.py app/models/api/chat.py app/models/api/news.py app/models/api/pagination.py app/models/api/users.py app/models/domain/content.py app/models/domain/user_profile.py app/models/metadata/__init__.py app/models/metadata/base.py app/models/metadata/news.py app/models/metadata/summaries.py app/models/metadata/summary_contracts.py app/pipeline/podcast_workers.py app/pipeline/task_handler.py app/pipeline/task_models.py app/pipeline/worker.py app/pipeline/workflows/content_processing_workflow.py app/processing_strategies/html_strategy.py app/processing_strategies/registry.py app/repositories/content_feed_query.py app/repositories/content_repository.py app/repositories/search_repository.py app/scraping/aggregators/brutalist.py app/scraping/base.py app/scraping/podcast_unified.py app/services/deep_research.py app/services/discussion_fetcher.py app/services/exa_client.py app/services/llm_models.py app/services/longform_artifact_prompts.py app/services/news_embeddings.py app/services/news_relations.py app/services/openai_llm.py app/services/queue.py app/services/tweet_suggestions.py app/services/twitter_share.py app/services/url_detection.py app/services/x_api.py app/services/x_integration.py app/testing/postgres_harness.py app/utils/error_logger.py app/utils/json_repair.py tests/integration/test_pipeline_with_fixtures.py tests/pipeline/test_content_worker.py tests/pipeline/test_worker_gate_page_fallback.py tests/processing_strategies/test_html_strategy.py tests/scraping/test_podcast_scraper_integration.py tests/services/test_news_relations.py
```

Result: pass.

## Goal 6 Input

Handle `admin/` and `scripts/` separately. The remaining findings are entrypoint-adjacent or one-off scripts, so validate with admin-focused tests and script smoke checks rather than folding them into the app cleanup pass.
