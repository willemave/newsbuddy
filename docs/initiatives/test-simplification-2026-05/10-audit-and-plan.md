# Test Simplification Initiative

Status: proposed

Created: 2026-05

Supersedes: `docs/initiatives/test-refactor/` for forward-looking test cleanup.

## Goal

Make the Python test suite easier to change as the app model boundaries mature.

The project should keep the behavioral confidence it currently gets from a broad
test suite, but reduce the cost of model changes, metadata contract changes, and
pipeline refactors. The target posture is:

- Standardize fixtures around the current `app.models` boundaries.
- Simplify tests by removing repeated setup and brittle private assertions.
- Prefer integration and contract tests for user-visible workflows over rigid
  unit tests that mirror implementation details.

## Current Audit

Snapshot from the current working tree:

- `1503` collected cases after parametrization by `pytest --collect-only`.
- `1489 passed, 12 skipped` on the latest full-suite run.
- `243` Python files under `tests/`; `225` are `test_*.py` modules.
- About `49k` lines of Python test code.
- Two explicit markers exist: `integration` and `ios_e2e`.

Test distribution by top-level folder:

| Area | Test files | Test definitions |
| --- | ---: | ---: |
| `tests/services/` | 70 | 472 |
| `tests/routers/` | 42 | 287 |
| `tests/pipeline/` | 23 | 131 |
| `tests/processing_strategies/` | 12 | 99 |
| `tests/core/` | 14 | 74 |
| `tests/scraping/` | 22 | 73 |
| `tests/models/` | 10 | 51 |
| `tests/admin/` | 9 | 53 |
| `tests/integration/` | 8 | 28 |
| Other folders | 33 | 135 |

Largest maintenance hotspots:

| File | Lines | Main issue |
| --- | ---: | --- |
| `tests/routers/test_api_chat.py` | 1910 | Many workflows and raw row setup in one file |
| `tests/pipeline/test_analyze_url_handler.py` | 1404 | High-value coverage, but repeated content/X setup |
| `tests/services/test_news_processing.py` | 1054 | Repeated `NewsItem` construction and prompt/metadata assertions |
| `tests/pipeline/test_content_worker.py` | 967 | Heavy monkeypatching around worker internals |
| `tests/services/test_news_relations.py` | 829 | Large case matrix with custom setup |
| `tests/processing_strategies/test_html_strategy.py` | 742 | Mock-heavy provider behavior mixed with extraction contracts |
| `tests/ios_e2e/test_maestro_content_flows.py` | 709 | Real flow coverage, needs clearer scenario fixtures |
| `tests/services/test_x_integration.py` | 691 | Integration value is high, setup should be centralized |

Fixture and setup observations:

- `tests/conftest.py` is already the main shared fixture hub, with factories for
  users, content rows, content status, knowledge saves, read status, chat
  sessions, processing tasks, news items, integration connections, auth headers,
  clients, and JSON content samples.
- The suite still has many direct `Content(...)`, `NewsItem(...)`,
  `ProcessingTask(...)`, `ChatSession(...)`, and `ContentData(...)`
  constructions inside test bodies.
- The JSON fixtures in `tests/fixtures/content_samples.json` are useful, but
  they reflect persisted row shape more than the current model-layer split.
- Many tests are integration tests in practice because they use a real
  PostgreSQL harness, FastAPI `TestClient`, or task handlers, but are not marked
  or organized as integration tests.
- The older `docs/initiatives/test-refactor/` snapshot is stale: it records 882
  passing tests and an older suite layout.

## Target Test Model

Use the model layer as the fixture contract.

The current `app.models` split is:

- `app.models.db`: SQLAlchemy rows only.
- `app.models.api`: FastAPI request/response DTOs.
- `app.models.domain`: internal transfer objects and mappers.
- `app.models.metadata`: persisted metadata contracts and tolerant accessors.
- `app.models.internal`: private worker/service payloads.
- `app.models.llm`: strict LLM structured-output schemas.

Tests should build data at the same boundary they exercise:

| Test target | Preferred fixture input |
| --- | --- |
| Router/API behavior | Persisted DB rows plus API payload builders |
| Query/service behavior with persistence | Persisted DB rows via row factories |
| Pipeline handler/workflow behavior | Persisted rows plus `TaskEnvelope`/payload builders |
| Pure model and mapper behavior | `ContentData`, metadata models, and explicit mapper fixtures |
| External provider adapters | Small contract payloads and fake clients |
| Prompt and LLM schemas | Structured output models and narrow prompt contract checks |

Avoid constructing lower-level rows when the test is meant to exercise a higher
boundary. For example, a router test should normally call an endpoint with a
visible content/news scenario instead of manually wiring every related row in
the test body.

## Principles

1. Integration by default for workflows.
   Router, queue, pipeline, feed, chat, knowledge, and X/bookmark behavior should
   be tested through the public app boundary or a real handler boundary with the
   PostgreSQL harness.

2. Unit tests for pure logic only.
   Keep unit tests for validators, URL normalization, metadata accessors, small
   mappers, provider argument construction, and deterministic formatting. Do not
   unit-test private orchestration steps when an integration test can verify the
   workflow output.

3. Fixtures express product scenarios.
   Prefer names like `visible_article`, `ready_news_item`, `x_bookmark_article`,
   `queued_youtube_download`, and `saved_knowledge_item` over inline object
   construction.

4. Assertions should target contracts, not implementation sequence.
   Prefer final DB state, response payloads, task queue rows, metadata contracts,
   and generated DTOs over exact call-order assertions against private helpers.

5. Patch at external edges.
   Monkeypatch network clients, LLM gateways, file storage, and third-party SDKs.
   Avoid patching internal helpers when a fake gateway or persisted scenario can
   drive the same behavior.

6. Keep exact string tests rare.
   Prompt tests should assert durable obligations and schema requirements, not
   incidental wording unless the wording is part of a production contract.

## Fixture Standardization Plan

Create a small fixture support layer under `tests/support/` and progressively
move shared setup out of individual test files.

Proposed modules:

- `tests/support/builders.py`
  - DB row builders: user, content, news item, processing task, scraper config,
    chat session, chat message, integration connection.
  - Domain builders: `ContentData`, article metadata, podcast metadata, news
    metadata, discussion payloads, summaries.
  - API payload builders for content submission, chat, onboarding, scraper
    config, and integration endpoints.

- `tests/support/scenarios.py`
  - Higher-level persisted scenarios: visible article, visible podcast, ready
    news item, X bookmark with snapshot, article converted from news, saved
    knowledge item, active processing task, failed retryable task.

- `tests/support/fakes.py`
  - Fake LLM gateway, fake queue gateway, fake HTTP gateway, fake object store,
    fake provider clients, and common usage-record assertions.

- `tests/support/assertions.py`
  - Contract assertions for content list items, content details, news cards,
    task state transitions, vendor usage records, and metadata shape.

Keep `tests/conftest.py` as the pytest wiring layer. It should expose the
fixtures, not contain all builder logic.

## Simplification Plan

### Phase 1: Define taxonomy and guardrails

Deliverables:

- Add marker guidance for `unit`, `integration`, `contract`, and `ios_e2e`.
- Document which markers are expected for new tests.
- Add a short testing guide under `tests/fixtures/` or `docs/library/guides/`.
- Keep the full suite green before any large conversion.

Acceptance criteria:

- New tests have an obvious bucket.
- Integration tests are not hidden among unit-looking modules.
- Developers know when to use DB/client fixtures and when to keep a pure unit
  test.

### Phase 2: Build model-based fixture support

Deliverables:

- Extract reusable builders from `tests/conftest.py` into `tests/support/`.
- Add domain builders for `ContentData` and metadata models.
- Add persisted scenario fixtures for content, news, chat, queue, and X flows.
- Keep backward-compatible fixture names while converting call sites.

Acceptance criteria:

- New tests rarely call `Content(...)`, `NewsItem(...)`, or `ContentData(...)`
  directly.
- Fixture defaults produce valid, visible, list/detail-ready objects.
- Changing a model default usually requires one fixture update, not many test
  file edits.

### Phase 3: Convert the high-churn workflow tests

Start with the files that combine size, model churn, and behavioral importance:

1. `tests/routers/test_api_chat.py`
2. `tests/pipeline/test_analyze_url_handler.py`
3. `tests/services/test_news_processing.py`
4. `tests/pipeline/test_content_worker.py`
5. `tests/routers/api/test_content_stats.py`
6. `tests/routers/test_api_content_submission.py`
7. `tests/services/test_x_integration.py`

For each file:

- Extract repeated setup into scenario builders.
- Preserve the important endpoint/handler behavior.
- Delete duplicate tests that assert the same final state through different
  private seams.
- Split only when the split clarifies a product workflow or contract.

Acceptance criteria:

- Each converted file loses repeated setup without losing coverage of user
  visible behavior.
- Test names describe product behavior, not helper implementation.
- Monkeypatching moves toward gateways/provider edges.

### Phase 4: Collapse brittle unit tests into contract/integration tests

Target categories:

- Router tests that manually inspect service internals.
- Pipeline worker tests that assert private helper call order.
- Prompt tests that assert incidental wording.
- Provider tests that mock too deeply below the provider boundary.
- Metadata tests that duplicate the same normalization path at several layers.

Replacement strategy:

- Keep one or two pure tests for edge-case logic.
- Add or keep an integration/contract test at the real boundary.
- Remove redundant unit cases once the boundary test proves the behavior.

Acceptance criteria:

- A model or service refactor does not require changing tests that prove the
  same external behavior.
- Important regressions still fail at the endpoint, handler, or mapper boundary.

### Phase 5: Prune and reorganize

Deliverables:

- Split oversized files by workflow where useful.
- Move reusable local helpers into `tests/support/`.
- Remove unused sample fixtures and stale docs.
- Update `docs/initiatives/test-refactor/` or mark it as historical after this
  initiative has active implementation notes.

Acceptance criteria:

- Top maintenance hotspots are smaller or have most setup extracted.
- `tests/conftest.py` is a thin fixture registry.
- Fixture docs match the current model split.

### Phase 6: Add governance

Deliverables:

- Add a checklist for new tests:
  - Does this use the highest useful boundary?
  - Does this use a shared fixture or scenario builder?
  - Is the assertion on a durable contract?
  - Is monkeypatching limited to external edges?
- Add a lightweight audit command that reports direct ORM/domain constructors in
  tests and very large test files.

Acceptance criteria:

- Test complexity does not creep back after the cleanup.
- Reviewers can reject brittle unit tests with a written project standard.

## Success Metrics

Track these before and after each phase:

- Direct raw model constructions in tests reduced by at least 75%.
- Top five largest test files reduced by at least 30% or split by clear
  workflow.
- `tests/conftest.py` reduced to fixture wiring and no longer holds most builder
  implementation.
- Full suite remains under roughly two minutes on the current local setup.
- New router and pipeline behavior is covered by integration or contract tests
  unless the code is pure logic.
- Prompt tests assert durable prompt obligations, not full prose snapshots.

## Non-Goals

- Do not reduce confidence just to reduce test count.
- Do not force every test through FastAPI or the database.
- Do not rewrite all tests in one pass.
- Do not remove focused unit tests for pure functions, validation, mapping, or
  provider payload construction.

## First Implementation Slice

The first slice should be deliberately small:

1. Add `tests/support/builders.py` with domain and DB row builders for content,
   news items, users, and processing tasks.
2. Make `tests/conftest.py` delegate those existing factories to the new
   builders without changing test call sites.
3. Convert `tests/routers/api/test_content_stats.py` as a pilot because it has
   repeated visible-content and news-item setup but a clear endpoint boundary.
4. Run:
   - `.venv/bin/pytest tests/routers/api/test_content_stats.py -q`
   - `.venv/bin/pytest tests/models tests/domain tests/routers/api/test_content_stats.py -q`
   - `.venv/bin/ruff check tests/support tests/conftest.py tests/routers/api/test_content_stats.py`

If that slice is clean, continue to `test_api_chat.py` and
`test_analyze_url_handler.py`.
