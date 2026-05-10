# Model Boundaries Refactor

Status: mostly complete on `willem/refactor/model-boundaries`.

## Goals
- [x] Split ORM, API DTOs, domain objects, metadata contracts, internal payloads, and LLM schemas into separate packages.
- [x] Delete old catchall/barrel modules after updating imports.
- [x] Keep DB tables, DB columns, stored metadata shape, queue values, and public API fields stable.
- [x] Remove model-layer imports from services.
- [x] Remove hidden ORM `content_metadata` reshaping.
- [x] Preserve generated public API contracts.

## Completed Structure
- [x] `app/models/contracts.py`
- [x] `app/models/db/`
- [x] `app/models/api/`
- [x] `app/models/domain/`
- [x] `app/models/metadata/`
- [x] `app/models/internal/`
- [x] `app/models/llm/`

## Completed Moves
- [x] `schema.py` ORM classes moved into `models/db`.
- [x] `user.py` split into `models/db/users.py`, `models/api/users.py`, `models/api/auth.py`, and `models/domain/user_profile.py`.
- [x] `metadata.py` split into the `models/metadata` package.
- [x] `ContentData`, content mappers, display helpers, form helpers, chat render metadata, scraper run stats, and discovery result models moved into `models/domain`.
- [x] `api/common.py` split by API surface.
- [x] `content_submission.py` moved to `models/api/submissions.py`.
- [x] `pagination.py` moved to `models/api/pagination.py`.
- [x] Feed discovery LLM schemas moved to `models/llm/feed_discovery.py`.
- [x] Content analysis structured-output schemas moved to `models/llm/content_analysis.py`.
- [x] Scraper config feed URL validation moved from model schemas to `app/services/scraper_config_validation.py`.

## Verification
- [x] `uv run ruff check app admin scripts tests`
- [x] `uv run pytest -q`
- [x] `scripts/check_public_contracts.sh`
- [x] Old import grep for removed modules.
- [x] Model import smoke test.
- [x] SQLAlchemy metadata comparison against HEAD in an isolated worktree.

## Remaining Follow-Ups
- [ ] Optional: add an import-linter rule or small test that enforces the documented model boundary rules.
- [ ] Optional: regenerate broader codebase docs after this branch lands so all package inventories reflect the new tree.
- [ ] Optional: investigate existing local-database Alembic drift separately. The local DB reports legacy tables/indexes and column/index differences that are not caused by this refactor, while model metadata matches HEAD.
