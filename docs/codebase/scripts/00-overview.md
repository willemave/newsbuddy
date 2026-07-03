# scripts/

Source folder: `scripts`

## Purpose
Developer, operator, generation, evaluation, data maintenance, and local smoke-test scripts that sit outside the importable `app`, `admin`, and `cli` packages.

## Runtime behavior
- Shell scripts start local services, workers, scrapers, Postgres, bgutil providers, migrations, contract generation, deployment helpers, and backups.
- Python scripts cover fixture generation, queue repair, scraper/import maintenance, eval exports/runs, prompt reports, image experiments, production snapshot loading, and one-off diagnostics.
- Scripts are intentionally task-focused. They should use app services and models where possible rather than duplicating business rules.

## Important groups
| Group | Examples | Purpose |
|---|---|---|
| Local runtime | `dev.sh`, `start_services.sh`, `start_server.sh`, `start_workers.sh`, `setup_local_postgres.sh` | Start or prepare local development services. |
| Queue and repairs | `queue_control.py`, `watchdog_queue_recovery.py`, `reset_errored_content.py`, `reconcile_stale_long_form_processing.py` | Inspect, reset, or recover processing state. |
| Scraping/import | `run_scrapers.py`, `start_scrapers.sh`, `import_config_feeds.py`, `bootstrap_user_feeds.py`, `run_integration_sync.py` | Run scheduled ingestion or seed feed configs. |
| Contracts | `export_openapi_schema.py`, `generate_ios_contracts.py`, `generate_go_contracts.py`, `generate_agent_cli_artifacts.sh`, `regenerate_public_contracts.sh` | Regenerate checked-in API contract artifacts. |
| CLI smoke | `test_agent_cli_local_e2e.py`, `test_auth_flow.sh` | Exercise local machine-facing workflows. |
| Evals and reports | `run_news_eval.py`, `run_summary_evals.py`, `generate_eval_html_report.py`, `build_prompt_debug_report.py` | Build eval datasets and reports. |
| Data fixtures | `generate_test_data.py`, `fixture_discussions.py`, `export_news_items_raw_snapshot.py` | Create local/test data snapshots; `generate_test_data.py --briefing` also builds a deterministic Briefing edition and verifies append refresh. |
| Guardrails | `architecture_guard.sh`, `check_module_size_guardrails.py`, `check_public_contracts.sh`, `check_duplicate_tests.py` | Keep structure, contracts, and tests in bounds. |

## Integration points
- Script-specific tests live under `tests/scripts`.
- Contract scripts feed the Go CLI (`cli/internal/api/contracts_gen.go`, `cli/openapi/agent-openapi.json`) and iOS generated enum/model files (`client/newsly/newsly/Models/Generated`).
- Scripts under `scripts/` do not require tests unless production behavior depends on them or the task explicitly asks.
