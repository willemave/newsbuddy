# Initiatives

Change-oriented docs that used to live under `docs/plans/`, `docs/specs/`, and `docs/research/` now live here, grouped by initiative instead of document type.

## Layout
- Each initiative gets its own folder.
- Filenames use sortable prefixes such as `10-design.md`, `20-plan.md`, and `30-summary.md`.
- Stable shipped-behavior docs should stay in `docs/library/features/`, not here.

## Current initiatives
- `agentic-feed-discovery/`
- `atom-scraper-2025-10/`
- `authentication-2025-10/`
- `cli-rewrite-2026-03/`
- `codebase-hardening-2026-03/`
- `codebase-refactoring/`
- `dig-deeper-selection-menu/`
- `llm-call-sites/`
- `news-button-navigation-2025-11/`
- `news-grouped-view-2025-10/`
- `news-screenshot-thumbnail/`
- `onboarding-speech-realtime/`
- `pipeline-reliability/`
- `podcast-sources-2025-11/`
- `settings-ui-modernization/`
- `share-sheet-instruction-processing/`
- `swiftui-list-views-refactor/`
- `test-refactor/`
- `twitter-share-scraper/`

## Concat command

```bash
find docs/initiatives -type f -name '*.md' | sort | xargs cat
```
