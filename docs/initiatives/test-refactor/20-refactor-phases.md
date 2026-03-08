# Python Test Refactor Strategy and Execution Phases

Derived from: `docs/initiatives/test-refactor/10-test-inventory.md` and coverage data.

## Key Findings

- 142 active test modules; `882 passed, 0 skipped`.
- 5 test modules map to targets below 60% coverage.
- 0 modules are fully skipped and currently provide no executable coverage.
- 3 modules are broad/large and should be split for maintainability.
- 16 small unit modules likely miss edge cases.

Lowest-coverage production modules (global):
- `app/services/image_generation.py`: 0.0% (239 statements)
- `app/services/deep_research.py`: 0.0% (219 statements)
- `app/services/google_flash.py`: 0.0% (19 statements)
- `app/utils/deprecation.py`: 0.0% (19 statements)
- `app/services/anthropic_llm.py`: 0.0% (17 statements)
- `app/routers/logs.py`: 11.9% (487 statements)
- `app/pipeline/handlers/analyze_url.py`: 20.3% (271 statements)
- `app/services/whisper_local.py`: 20.5% (78 statements)
- `app/services/http.py`: 22.4% (134 statements)
- `app/services/apple_podcasts.py`: 22.4% (116 statements)
- `app/pipeline/handlers/generate_image.py`: 23.5% (51 statements)
- `app/services/openai_realtime.py`: 25.6% (78 statements)

## Refactor Phases

1. Test Infrastructure Hardening
   - Fix fixture/resource leaks and warning noise (DB/session/client lifecycle).
   - Normalize high-reuse fixtures and helper factories in `tests/conftest.py`.

2. Dead/Weak Test Cleanup
   - Rewrite or remove fully skipped modules that no longer validate behavior.
   - Tighten minimal tests (<=2 cases) with negative/error-path assertions.

3. High-Risk Coverage Expansion
   - Add focused tests for modules under 60% coverage and user-visible behavior.
   - Prioritize API routers, workflow handlers, gateway adapters, and HTTP clients.

4. Module Decomposition in Tests
   - Split very large test files into topic-focused modules (validation, errors, success paths).
   - Keep fixtures centralized and use parametrization to reduce duplication.

5. Governance and Regression Controls
   - Add/adjust per-module coverage thresholds for critical areas.
   - Keep CI stable with deterministic mocks and no network dependency.

## Execution Completed In This Pass

- Phase 1: completed (fixture lifecycle hardening in `tests/conftest.py`).
- Phase 2: completed (replaced skipped admin dashboard tests with executable coverage).
- Phase 3: completed for selected hotspots (`robust_http_client`, `youtube_strategy`, gateway facades).
- Phase 4 and 5: partially deferred (topology split/CI thresholds not yet enforced).
