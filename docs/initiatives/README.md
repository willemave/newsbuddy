# Initiatives

Change-oriented docs that used to live under `docs/plans/`, `docs/specs/`, and `docs/research/` now live here, grouped by initiative instead of document type.

## Layout

- Each initiative gets its own folder.
- Filenames use sortable prefixes such as `10-design.md`, `20-plan.md`, and `30-summary.md`.
- Canonical shipped behavior belongs in `docs/laws/`; supporting feature notes may live in `docs/library/features/`.

## Authority and status

Initiative documents preserve point-in-time change context. Current product
invariants live in `docs/laws/`, the system design lives in
`docs/architecture.md`, and dated implementation and validation records live
in `docs/log.md`.

## Concat command

```bash
find docs/initiatives -type f -name '*.md' | sort | xargs cat
```
