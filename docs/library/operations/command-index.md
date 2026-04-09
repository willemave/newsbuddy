# Operations Command Index

Use this index as the single entrypoint for operational scripts.

## Deploy

- GitHub Actions [`.github/workflows/docker-racknerd-deploy.yml`](../../../.github/workflows/docker-racknerd-deploy.yml) - Supported production app deploy path.
- `scripts/deploy/push_envs.sh` - Env-only helper to sync `.env.racknerd` to the host outside the normal GitHub deploy flow.
- `scripts/deploy/common.sh` - Shared deploy functions (`require_option_value`, owner parsing, env sync promotion).
- `scripts/check_and_run_migrations.sh` - Safe migration runner with settings validation.

## Runtime

- `scripts/start_services.sh` - Unified local runtime launcher for `all`, `server`, `workers`, `scrapers`, `watchdog`, `scheduler`, and `migrate`.
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
- `scripts/export_agent_openapi_schema.py` - Export the filtered CLI schema to `cli/openapi/agent-openapi.json`.
- `scripts/generate_ios_contracts.py` - Generate iOS enum contracts from backend canonical enums.
- `scripts/generate_ios_openapi_artifacts.sh` - Generate checked-in Swift OpenAPI client/types artifacts.
- `scripts/generate_agent_cli_artifacts.sh` - Regenerate the filtered CLI schema and generated Go client.
- `scripts/regenerate_public_contracts.sh` - Regenerate all checked-in public contract artifacts.
- `scripts/check_public_contracts.sh` - Verify all checked-in public contract artifacts are current.
- `client/newsly/scripts/regenerate_api_contracts.sh` - One-command iOS contract regeneration workflow.
- `scripts/check_duplicate_tests.py` - Detect duplicate test module names between roots.
- `scripts/check_module_size_guardrails.py` - Enforce line-count guardrails for high-churn modules.
