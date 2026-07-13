# Briefing Figure Placement Plan

## Goal

Make compact-width Briefing pages prefer small inline figures with wrapped text, while retaining
an explicit `full` LLM layout hint for the occasional large stacked figure.

## Canonical contract

- Define `BriefingFigurePlacement` once in the shared contracts layer with `inset` and `full`.
- Use that enum at the LLM composer and HTTP API boundaries so generated clients receive the same
  closed vocabulary.
- Canonicalize missing, malformed, or future stored values to `inset` during normalization.
- Preserve an explicit valid `full` value through repair, persistence, presentation, and rendering.
- Keep deterministic/backfilled figures `inset` so non-LLM paths follow the preferred layout.

## Composition policy

- Update long-form and audio prompts to choose `inset` by default.
- Permit `full` only as a deliberate editorial exception for an image that materially establishes
  the source or story; do not use it merely because an image is available.
- Limit the prompt guidance to at most one full figure per composed window.

## iOS rendering

- Pair adjacent inset figures with substantive passages on every horizontal size class.
- On compact width, render a smaller trailing figure and wrap passage text around it.
- On regular width, retain the existing larger floating figure.
- Render `full` figures as the existing large stacked content-width block.
- Keep short passages and figures without usable images in the stacked fallback path.

## Verification

- Test normalization defaults and explicit `full` preservation.
- Test repair preserves valid placement while canonicalizing invalid placement to `inset`.
- Test the shared API contract and regenerated Swift types.
- Add Swift tests for compact/regular inline metrics and full-vs-inset layout selection.
- Run focused Briefing Python tests, contract codegen checks, Swift tests/build, Ruff, and diff checks.
