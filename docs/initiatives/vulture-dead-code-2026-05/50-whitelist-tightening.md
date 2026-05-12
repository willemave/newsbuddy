# Vulture Whitelist Tightening

**Date:** 2026-05-12  
**Scope:** Remove whitelist entries that were masking real dead code or avoidable pytest-only names.

## What Changed

- Removed obsolete public constants from `app/constants.py`:
  - checkout worker names and timeout defaults
  - old worker concurrency defaults
  - unused aggregate-platform summary skip set
  - duplicate `SUMMARY_KIND_INSIGHT_REPORT` string constant
- Removed `TaskSpec.handler_key`; dispatch now uses handler `task_type`, and the task spec registry only owns queue, payload validation, and dedupe policy.
- Removed unused `StoredObjectMetadata.etag`.
- Replaced unused `stub_valid_feed_url` test parameters with explicit `@pytest.mark.usefixtures(...)` markers.
- Deleted the matching entries from `vulture_whitelist.py`.

## Coverage Delta

Configured Vulture remains clean:

```bash
uv run vulture
```

Result: pass, exit code `0`.

The no-whitelist report is smaller:

```bash
uv run vulture app admin scripts tests/admin tests/scripts tests/services tests/pipeline tests/processing_strategies tests/scraping tests/queries tests/models tests/routers tests/utils --min-confidence 60 --sort-by-size
```

Before this pass: 262 findings.  
After this pass: 248 findings.

Removed from the no-whitelist report:

- 10 obsolete constants in `app/constants.py`
- `app/pipeline/task_specs.py` `handler_key`
- `app/services/gateways/object_storage_gateway.py` `etag`
- 2 unused `stub_valid_feed_url` fixture parameters in `tests/routers/test_onboarding.py`

## Verification

```bash
uv run ruff check app/services/gateways/object_storage_gateway.py app/constants.py app/pipeline/task_specs.py tests/routers/test_onboarding.py vulture_whitelist.py
```

Result: pass.

```bash
uv run pytest tests/routers/test_onboarding.py tests/services/test_feed_subscription.py tests/services/test_scraper_config_service.py tests/routers/api/test_content_detail_subscribe.py tests/routers/test_api_discovery.py tests/pipeline -q
```

Result: 169 passed.

```bash
uv run pytest tests/services/test_news_item_discussions.py tests/pipeline/test_task_specs.py -q
```

Result: 6 passed.

## Remaining Whitelist Shape

Most remaining entries are still legitimate dynamic boundaries:

- Pydantic fields and `model_config`
- SQLAlchemy mapped fields
- `TypedDict` fields used through dict access
- pytest `pytestmark` and mock `side_effect`
- framework/runtime attributes such as log file handler `namer`

Future tightening should focus on one category at a time. The highest-leverage remaining targets are broad pytest names and any duplicate Pydantic/API fields that can be proven not to be part of a request, response, generated client, or persisted metadata contract.
