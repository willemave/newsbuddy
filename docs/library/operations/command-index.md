# Operations Command Index

Use this index as the single entrypoint for operational scripts.

## Deploy

- `scripts/deploy/push_app.sh` - Full app deploy (rsync + optional env refresh, dependency install, supervisor restart).
- `scripts/deploy/push_envs.sh` - Env-only deploy helper (delegates to shared deploy env-sync flow).
- `scripts/deploy/common.sh` - Shared deploy functions (`require_option_value`, owner parsing, env sync promotion).
- `scripts/check_and_run_migrations.sh` - Safe migration runner with settings validation.

## Runtime

- `scripts/start_server.sh` - API server launcher.
- `scripts/start_workers.sh` - Queue workers launcher.
- `scripts/start_scrapers.sh` - Scraper launcher.
- `scripts/start_queue_watchdog.sh` - Queue watchdog launcher.
- `scripts/workers.sh` - Worker convenience wrapper.

## Diagnostics

- `scripts/analyze_errors.py` - Analyze persisted error logs.
- `scripts/dump_system_stats.py` - Runtime/system stats snapshot.
- `scripts/queue_control.py` - Queue inspection and control utilities.
- `scripts/view_remote_errors.sh` - Pull and inspect remote error logs.
- `scripts/sync_logs_from_server.sh` - Sync remote logs locally.

## Data and Backfills

- `scripts/backfill_summary_kind.py` - Backfill summary discriminator metadata.
- `scripts/cancel_ineligible_generate_image_tasks.py` - Cancel pending image tasks outside visible feed rules.
- `scripts/reset_content_processing.py` - Reset stuck/failed processing state.
- `scripts/reset_errored_content.py` - Reset errored content records.

## Contracts and Tooling

- `scripts/export_openapi_schema.py` - Export current OpenAPI schema to `docs/library/reference/openapi.json`.
- `scripts/generate_ios_contracts.py` - Generate iOS API contracts from OpenAPI.
- `client/newsly/scripts/regenerate_api_contracts.sh` - One-command iOS contract regeneration workflow.
- `scripts/check_duplicate_tests.py` - Detect duplicate test module names between roots.
- `scripts/check_module_size_guardrails.py` - Enforce line-count guardrails for high-churn modules.
