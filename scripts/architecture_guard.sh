#!/usr/bin/env bash
set -euo pipefail

uv run ruff check \
  app/core \
  app/models/metadata_access.py \
  app/pipeline/task_specs.py \
  app/services/content_lifecycle.py \
  app/queries/list_submission_statuses.py \
  app/queries/queue_health.py \
  app/queries/search_mixed.py \
  app/routers/auth.py \
  app/main.py \
  admin \
  scripts/report_legacy_news_links.py \
  scripts/report_content_metadata_keys.py \
  tests/core \
  tests/contracts/test_content_api_fixtures.py \
  tests/models/test_metadata_access.py \
  tests/pipeline/test_task_specs.py \
  tests/queries/test_list_submission_statuses.py \
  tests/queries/test_queue_health.py \
  tests/queries/test_search_mixed.py \
  tests/services/test_content_lifecycle.py \
  tests/routers/test_api_submission_status_list.py \
  tests/routers/test_auth.py \
  tests/admin/test_config_and_output.py
uv run pytest \
  tests/core/test_security.py \
  tests/core/test_deps_admin.py \
  tests/core/test_settings_database.py \
  tests/contracts/test_content_api_fixtures.py \
  tests/models/test_metadata_access.py \
  tests/pipeline/test_task_specs.py \
  tests/queries/test_list_submission_statuses.py \
  tests/queries/test_queue_health.py \
  tests/queries/test_search_mixed.py \
  tests/services/test_content_lifecycle.py \
  tests/routers/test_api_submission_status_list.py \
  tests/routers/test_auth.py \
  tests/admin/test_config_and_output.py \
  -v
scripts/check_public_contracts.sh
