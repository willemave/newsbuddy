# Vulture Dead-Code Baseline

**Date:** 2026-05-12  
**Scope:** Goal 2 baseline only; no code deletion or whitelist changes.

## Commands

Configured production scan:

```bash
uv run vulture
```

Result: clean. The current config scans `app`, `admin`, and `scripts` with `min_confidence = 100`.

Broad baseline scan:

```bash
uv run vulture app admin scripts tests/admin tests/scripts tests/services tests/pipeline tests/processing_strategies tests/scraping tests/queries tests/models tests/routers tests/utils --min-confidence 60 --sort-by-size
```

Result: 521 findings. Vulture exited with code `3`, which means findings were present.
The exact line list can be regenerated with the command above; this report is the durable classified baseline.

Higher-confidence baseline:

```bash
uv run vulture app admin scripts tests/admin tests/scripts tests/services tests/pipeline tests/processing_strategies tests/scraping tests/queries tests/models tests/routers tests/utils --min-confidence 80 --sort-by-size
```

Result: 2 findings, both pytest fixture parameters in `tests/routers/test_onboarding.py`.

## Finding Summary

| Metric | Count |
|---|---:|
| Total broad findings | 521 |
| 60% confidence | 519 |
| 100% confidence | 2 |
| Unused variables | 235 |
| Unused functions | 156 |
| Unused methods | 70 |
| Unused attributes | 47 |
| Unused classes | 12 |
| Unused properties | 1 |

Largest path clusters:

| Path group | Count | Initial classification |
|---|---:|---|
| `app/models/metadata` | 83 | Pydantic contract false positives |
| `app/services` | 78 | Mixed: agent tools, ORM attributes, real candidates |
| `app/routers/api` | 62 | FastAPI route entrypoint false positives |
| `app/models/api` | 62 | Pydantic response/request false positives |
| `app/core` | 54 | Settings/logging/framework false positives |
| `app/admin_web` | 21 | Jinja/FastAPI admin route false positives |
| `scripts` | 19 | Mixed script DTOs and possible cleanup |
| `tests/pipeline` | 18 | pytest/mock false positives |

Additional measured clusters:

- 166 findings are under non-ORM model packages such as `app/models/api`, `app/models/metadata`, `app/models/domain`, `app/models/internal`, and `app/models/llm`.
- 87 findings are under `app/routers`, `app/admin_web`, or `app/main.py`, where FastAPI/Jinja decorators create dynamic entrypoints.
- 59 findings are `model_config`.
- 33 findings are test `side_effect` assignments.
- 11 findings are under `app/models/db`, mostly SQLAlchemy mapped attributes.

## False-Positive Categories

### Pydantic and Settings Contracts

Most model findings are fields, `model_config`, validators, or serializers used by Pydantic v2 rather than direct Python calls. This includes API DTOs, metadata payload contracts, internal service DTOs, script argument models, and `BaseSettings` fields in `app/core/settings.py`.

Examples:

- `app/core/settings.py:*` provider/env fields and settings validators
- `app/models/api/content.py:*` response fields and `model_config`
- `app/models/metadata/summaries.py:*` LLM summary schema fields
- `scripts/dump_system_stats.py:*` local Pydantic report models

### FastAPI and Admin Route Entrypoints

Route functions are registered through decorators, so Vulture sees many endpoint callables as unused.

Examples:

- `app/main.py:369` `health_check`
- `app/routers/api/chat.py:837` `create_session`
- `app/routers/api/discovery.py:128` `get_discovery_suggestions`
- `app/admin_web/dashboard.py:719` `admin_dashboard`

### SQLAlchemy Mapped Attributes

Mapped columns and relationship-like fields are used through SQLAlchemy instrumentation, queries, migrations, and serialized ORM state.

Examples:

- `app/models/db/content.py:*` storage metadata columns
- `app/models/db/cli.py:*` CLI approval/claim timestamp columns
- `app/models/db/integrations.py:*` X sync ledger fields

### Dynamic Agent and Tool Functions

Assistant/chat tools are callable through pydantic-ai/tool registration and runtime dispatch. Vulture reports these as unused if there is no direct Python call edge.

Examples:

- `app/services/assistant_router.py:*` knowledge, search, read-status, conversion, and deep-research tools
- `app/services/chat_agent.py:*` personal-library and web-search tools

### Pytest and Mock Conventions

Tests include pytest-level globals, fixture parameters, local fixture helpers, and `Mock.side_effect` assignments. These are consumed by pytest or mock internals rather than direct references.

Examples:

- `tests/routers/test_onboarding.py:19` and `:356` `stub_valid_feed_url`
- `tests/routers/api/test_content_detail_subscribe.py:9` `pytestmark`
- `tests/pipeline/test_content_worker.py:*` `side_effect`

### Public Constants and External Contracts

Some constants are exported contracts for worker names, defaults, summary kinds, API enums, or external clients. A direct Python reference check is not sufficient for deletion.

Examples:

- `app/constants.py:*` worker names, checkout defaults, aggregate platform lists, summary kinds
- `app/models/api/content_actions.py:*` enum-like request values and API fields

## Potential Cleanup Candidates

No finding is a safe deletion candidate from Vulture alone in this baseline. The configured 100-confidence production scan is clean, and the broad 80-confidence scan only found pytest fixture parameters.

After Goal 3 installs a whitelist, these low-confidence items should be rechecked first because they are not obviously framework-bound:

- `app/services/news_embeddings.py:74` `clear_news_embedding_cache`
- `app/repositories/search_repository.py:69` `content_search_supports_full_text`
- `app/services/x_integration.py:468` `sync_x_bookmarks_for_user`
- `app/services/url_detection.py:174` `get_url_handler_name`
- `app/processing_strategies/registry.py:70` `list_strategies`
- `app/scraping/base.py:106` `_save_items`
- `app/pipeline/task_handler.py:22` `FunctionTaskHandler`
- `app/pipeline/checkout.py:*` checkout maintenance helpers
- `app/utils/deprecation.py:*` deprecated-field logging helpers

Each candidate needs an `rg` check, route/tool/CLI registration check, and targeted test run before deletion.

## Goal 3 Input

Build the whitelist around categories, not individual one-off lines:

- Pydantic v2 DTO fields, `model_config`, validators, and serializers.
- `BaseSettings` fields and settings validators.
- SQLAlchemy mapped columns and relationship attributes.
- FastAPI route functions and Jinja admin route functions.
- Dynamic assistant/chat tool functions.
- pytest fixtures, `pytestmark`, and mock `side_effect` assignments.
- Public constants that are part of worker, API, or client contracts.

Then rerun the broad 60-confidence scan and classify whatever remains into:

- delete now,
- keep with explicit whitelist,
- keep but add coverage/reference,
- needs deeper investigation.
