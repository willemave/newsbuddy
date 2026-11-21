# Briefing Source Cards (Articles and Podcasts)

Status: Implemented 2026-11-20 (Phases 1, 2, 4; Phase 3 not scheduled). See §11 for as-built deviations.

Replace the text-first rendering of the Articles and Podcasts Briefing lenses
with one card per source, using mockup variant A3 (`mockup-a3.png`).
News keeps its current newspaper-brief rendering.

## 1. Goal

Each article or podcast segment renders as a card:

1. A near-square hero image filling the card width, with a dark bottom fade.
2. Publisher kicker and serif title overlaid in cream type on that fade.
3. A frosted-glass length chip at the image's top left (`10 min read`,
   `1 hr 6 min`).
4. One or two short paragraphs of composed summary.
5. A footer with a hairline rule above it: type icon, publication date,
   Mark read, and Save to Knowledge.

"Done" means:

- It works with real production data in light mode, dark mode, and large
  Dynamic Type.
- Every current Briefing invariant still holds: read marking, long-press
  actions, Dig Deeper, citations, and Listen.
- No figure or pullquote code remains in the client, the server, or the
  public contract.

## 2. Current state (verified)

- **Segments.** Article and podcast segments are LLM-composed, one source per
  segment (law B4). The `longform`/`audio` prompt asks for 3–5 sentences,
  roughly 100–200 words, plus pullquotes and an inset figure per source
  (`newsly-providers/src/briefing_composition.rs`). News uses a separate
  markdown-only schema (`briefing_composition/news.rs`), so figures and
  pullquotes exist only in Articles and Podcasts.
- **Normalizer** (`newsly-worker/src/briefing_refresh/normalize.rs`):
  - It backfills figures and canonicalizes their placement.
  - `repair_text` collapses all whitespace, including paragraph breaks.
  - `paragraphs_from_markdown` then re-splits every three sentences, so the
    number of rendered paragraphs is ceil(sentences ÷ 3).
- **Block types.** Stored blocks decode into the closed `BriefingBlockType`
  enum, both on the server (`newsly-api/src/briefing.rs` segment mapping) and
  in Swift. One unknown block type fails the whole lens response.
- **Prompt version.** `BRIEFING_COMPOSITION_PROMPT_VERSION` only feeds the
  composition cooldown fingerprint. Bumping it recomposes nothing.
- **Full rebuild.** Production refresh producers send only `append` or
  `sweep`; nothing requests a Full rebuild.
- **iOS rendering.** `BriefingSegmentView` (`Views/Briefing/BriefingLensContentViews.swift`)
  renders a `BriefingSegmentHeadlineView`, then passages, floating or full
  figures, and pullquotes.
- **Source fields.** `APIBriefingSource` has every field the card needs except
  saved state.
- **Read marking.** `markSegmentSeen` → `markSourcesSeen` in
  `BriefingViewModel.swift` is optimistic, cancels index loads already in
  flight, flushes on a debounce, and reconciles under B13. An explicit tap can
  use it unchanged.
- **Audio.** Current clients' chaptered Listen builds scripts from source
  snapshots (`AdaptedLens`), not from passages. Two exceptions:
  - The legacy request with `lens_key` and no scope speaks `narration_text`
    word for word, so old apps' Listen gets shorter when passages do.
  - Document-style chapter duration estimates come from `narration_text`.
- **Podcast audio.** There is no player for original episodes. All podcast
  audio in the app is generated: lens chapters, or the detail-view council
  episode.
- **Images.**
  - Generated hero images are 16:9.
  - The artwork prompt already keeps the subject in the central 60% width and
    upper 65% height, treats the side edges as expendable, and leaves the
    lower 35% safe for overlays (`image_generation/prompt.rs`). That suits a
    bottom-anchored title on a near-square crop.
  - Thumbnails are capped at 200px, so they work only as placeholders.
  - Content needs generated artwork to be admitted to Briefing, so cards
    without an image happen only when loading fails.
- **Maestro.** Flows depend on the `briefing.segment.<id>` identifiers.
  Baselines exist only for `iphone17pro-dark`.
- **Uncommitted work.** The working tree has uncommitted figure-alternation
  work (B14 law edit, `BriefingFigureLayoutPolicy.swift`,
  `BriefingLensContentViews.swift`, `BriefingFigureSequenceTests.swift`). This
  plan makes that code obsolete.

## 3. Design spec (A3)

All values come from existing tokens. No new colors.

| Element | Spec |
|---|---|
| Card | `surfaceSecondary`, radius `CornerRadius.card` (24), 1pt `borderSubtle` stroke. `ShadowStyle.card` in light mode, no shadow in dark mode. Horizontal margin `Spacing.appHorizontalMargin` (20). Card gap 20. |
| Hero | Full card width, aspect ratio 16:15, `.fill`, center crop. `CachedAsyncImage` with an explicit `targetSize` of card width × scale. The 200px thumbnail is only a blurred placeholder. |
| Fade | Bottom gradient: RGB(20,16,10) at 0.86 → 0.55 at 30% → 0 at 62%, the same in both modes. |
| Kicker | Publisher, uppercased, `editorialMeta`, tracking 1.6, cream at 80%, one line. |
| Title | `appSerif(size: 23, relativeTo: .title2, weight: .semibold)`, `#fffaf0`, `ShadowStyle.overlayText`, max 3 lines. 18pt side inset, 16pt from the bottom. |
| Length chip | Top-left, 14pt inset, 30pt capsule, `.glassEffect` (pill material). Articles: doc icon + `10 min read`. Podcasts: waveform icon + `1 hr 6 min`. Display only (see D4). Hidden when length is unknown. Placed as an overlay sibling of the hero button, not inside its label. |
| Body | Passage text through `BriefingPassageView`, `readerBody`, `readerBodyText`, 16pt top and 18pt side padding. Keeps links, selection, and Dig Deeper. |
| Footer | 1pt `outlineVariant` rule. Type icon + kicker date (`AUG 25`, year when not current). The date only, because length is on the chip. Trailing: Mark read (`checkmark.circle` / `.fill`) and Save (`KnowledgeSaveIcon`), 44pt hit areas, `onSurfaceSecondary`. |
| Read state | The card fades to 0.72 opacity (existing segment behavior), and the check fills. |
| Press | `EditorialCardButtonStyle` on the hero/title button. |
| Identity | Keep `accessibilityIdentifier("briefing.segment.<id>")` on the card root. |

Variants:

- **Image fails to load:** the card starts with the kicker and the title in
  `brandPrimary` ink on the card surface, and the length moves into the
  footer kicker. No placeholder art.
- **Accessibility text sizes:** the title moves below the image in ink, and
  the image becomes 16:9.
- **Regular width (iPad):** single column, cards at most 640pt wide and
  centered.

Interaction:

- **Hero and title:** a single button that opens the source.
- **Footer:** only the leading metadata opens the source. Mark read and Save
  are separate buttons.
- **Body:** passage links open the source, and selecting text keeps Dig
  Deeper.
- **Long press:** pressing the hero, title, or footer shows the source menu
  (Add to Knowledge, Chat, Create Deck; B18).
- **Mark read:** goes through `markSegmentSeen`, with a light haptic. It is
  one-way.
- **Save:** Add to Knowledge (idempotent), with a success haptic. The saved
  state is held for the session (D3).

Accessibility:

- The header is one element with `.isHeader` and `.isLink` traits. Its label
  is title, publisher, kind and length, published date, and Briefed label.
- Mark read, Save, Chat, and Create Deck are also named accessibility actions.

## 4. Composition approach

Render cards **from the existing one-source segments**. Source keys, read
marking, paging, versioning, snapshots, first-run progress, and narration stay
as they are.

Make the text card-length **at composition time**, through a dedicated
schema:

- **Schema.** `longform` and `audio` get a markdown-only schema like News:
  `briefing_card_markdown_v1 { markdown }`. The model can no longer emit
  figures or pullquotes, so no paid retries are spent rejecting them.
- **Prompt.** At most six sentences: the central argument or finding first,
  with the source linked on a descriptive phrase; then the strongest
  evidence, counterpoint, or why it matters. Target 60–100 words. Keep the
  existing rules: never restate the title or publication, no em dashes, link
  the source.
- **Validation.**
  - A sentence cap of at most 6, which renders as at most two paragraphs
    through the existing three-sentence split.
  - A generous hard word cap, about 150, with its own rejection code. Set the
    final value from the Phase 2 eval.
  - Link coverage stays required.
  - No paragraph-count check, because the normalizer discards raw breaks.

A client line clamp was rejected because it cuts through cited sentences.
Using `source.summary`/`keyPoints` was rejected because it bypasses
composition and loses links and Dig Deeper.

## 5. Decisions

| # | Decision | Resolution | Status |
|---|---|---|---|
| D1 | Source of card text | Composer, dedicated markdown schema, sentence cap, generous word cap (§4). | Accepted |
| D2 | Existing segments | Text drains naturally; older cards are longer until read. A one-off migration strips figure and pullquote blocks from active and degraded segments and bumps the version for affected users (B7). No LLM cost, no Full rebuild. Add a `newsly-admin` Full command only if measurement shows long legacy cards linger. Such a command would also recompose News and reset documents to the top. | Accepted |
| D3 | Saved state for Save | Session-local, keyed by content id. Seeded from the app's existing saved-to-Knowledge mutation events where reachable. Pending state is tracked per source, not through the shared `isBusy`. A server flag was rejected: every Knowledge writer would have to bump the Briefing version, which refetches every loaded lens and rejects continuation pages already in flight. | Accepted |
| D4 | Play on podcast cards | The chip is display only in v1. There is no original-episode player, and lens chapters are short generated adaptations, so "Play · 1 hr 6 min" would mislabel what plays. Options: (a) no per-card audio, Listen stays lens-level; (b) add a footer "Listen" control that plays this source's generated chapter, labelled with the chapter's own estimate; (c) build original-episode playback, a separate initiative. **Owner chose (a), 2026-11-20.** | Locked |
| D5 | Article chip | Display only. | Accepted |
| D6 | Mark read | One-way. Amend B11 to allow an explicit mark. | Accepted |
| D7 | Image framing | Center crop. The artwork prompt already reserves the central subject and a lower overlay-safe area. Spot-check only; existing images need explicit regeneration to benefit from any future prompt change. | Accepted |
| D8 | News lens | Unchanged. | Accepted |
| D9 | Uncommitted figure-alternation work | Obsolete under this plan. **Owner chose to drop it when Phase 1 starts, 2026-11-20.** | Locked |

## 6. Phases

Each phase ships by itself and leaves the product working.

### Phase 1 — iOS cards on existing data

Ships cards with today's passages (longer than the target until Phase 2).

- **Model** `Views/Briefing/BriefingSourceCardModel.swift`: a pure value built
  from a segment plus its sources.
  - hero and thumbnail URLs (`ServerImageURL.resolve`)
  - kicker (`BriefingSegmentHeadline.publisherName`), title, chip label and
    kind
  - footer date (`BriefingSegmentHeadline.shortDate`)
  - indexes of passage blocks only, read state, accessibility label
  - nil for any segment that is not one content source
- **View** `Views/Briefing/BriefingSourceCardView.swift`: §3, including the
  load-failure and accessibility-size variants.
- **Page:** `BriefingLensPageView` renders the card for article and podcast
  segments inside the existing read-frame measurement, so B11 measurement is
  unchanged.
- **Delete now:**
  - `BriefingSegmentView` serves only News after this phase, and News never
    has figures. Delete `BriefingFloatingFigurePassage`, `BriefingFigureView`,
    `BriefingPullquoteView`, `BriefingFigureLayoutPolicy`, the figure
    sequence, and `BriefingSegmentHeadlineView` (keep the
    `BriefingSegmentHeadline` value, which the card uses).
  - Delete their tests.
  - Swift enum cases stay decodable until Phase 2's migration.
- **Save:** per-source pending and saved state (D3).
- **Tests** (`newslyTests/BriefingSourceCardModelTests.swift`):
  - article and podcast labels
  - unknown length hides the chip
  - figure and pullquote blocks are excluded
  - publisher tagline stripping
  - read state
  - accessibility label
  - per-source save state
- **E2E visual state:** article, podcast, image load failure, read, a
  three-line title, accessibility size.
  - Update the Maestro dark baseline for `briefing-articles.png` and add a
    podcasts baseline.
  - Check light mode and accessibility size manually in Simulator.
  - Re-run the flows that address `briefing.segment.<id>`
    (`long_form_mark_read`, `briefing_article_podcast`,
    `visual_main_screens`).
- **UI test:** tap-to-open, the long-press menu, Mark read.

### Phase 2 — Card-length composition and block migration

- **Before starting:** check API logs for legacy unscoped narration requests.
  If old app versions still use them, accept that their Listen shortens and
  note it in the log.
- **Schema and prompt:** add the card schema and prompt for `longform` and
  `audio` (§4).
- **Validation:** the sentence cap, plus a word cap with a new rejection
  code.
- **Delete** everything that loses its producer:
  - `BriefingCompositionLayout`, the `Figure` and `Pullquote` variants,
    `suggested_quotes`
  - the `InvalidFigure`, `InvalidPullquote`, and `InvalidSuggestedQuotes`
    rejections
  - the normalizer's figure and pullquote branches, `backfill_figures`,
    `canonicalize_figure_placement`
  - `max_figures_deep`, the `BRIEFING_MAX_FIGURES_DEEP` config, and its
    fingerprint input
- **Bump `BRIEFING_COMPOSITION_PROMPT_VERSION`** (cooldown fingerprint only).
- **Migration:** strip figure and pullquote blocks from `briefing_segments`
  where the status is active or degraded, then bump `briefing_states.version`
  for affected users. Cover it with isolated PostgreSQL tests: blocks removed,
  passages untouched, version bumped once per affected user, unaffected users
  unchanged.
- **Contract:** remove `Figure` and `Pullquote` from `BriefingBlockType` and
  the related fields, then regenerate contracts. This is safe for old clients,
  which never receive those cases.
- **Fixtures:** update the E2E fixture pullquote block
  (`newsly-db/src/e2e_fixtures.rs`).
- **Tests:** provider schema validation, sentence cap, word cap, link
  coverage, normalize tests. Remove the figure tests.
- **Eval:** run the composer on about 20 real unread articles and podcasts
  (local DB after `scripts/sync_production_state.sh`). Record the word and
  sentence distribution, link placement, and any restated titles, and set the
  hard word cap from the result.
- **Check Listen:** confirm that Document-style chapter duration estimates
  are still reasonable with shorter `narration_text`.

### Phase 3 — Per-card audio (not scheduled; D4 chose option a)

Kept for reference if per-card audio is revisited.

- `BriefingNarrationController.playSource(contentId:lensKey:)`: prepares the
  lens program, then plays the chapter whose `sourceContentId` matches.
- Shows preparing and playing state on the card control.
- Ends playback after that chapter instead of continuing to the next card.
- If the source is missing from the prepared program, shows a toast. This can
  only be known after preparation.
- Tests: chapter lookup, missing source, reuse of an active session, switching
  between cards.

### Phase 4 — Polish

- Motion: cards fade in on first appearance (respect Reduce Motion), the
  check uses `.symbolEffect(.replace)`, and haptics match the old
  `LongFormCard`.
- Instruments pass on a 30-card lens: no main-thread decode, steady scroll on
  iPhone 17 Pro. Add hero prefetching only if the profile shows visible pop-in.
- Spot-check about 10 recent images in the 16:15 crop.

## 7. Documentation updates

- `docs/laws/briefing.md`:
  - **B5:** Articles and Podcasts metadata lives in the card. The publisher is
    on the image, length is on the chip, and the publication date is in the
    footer. The one-line truncation rule now applies to the kicker and footer.
  - **B11:** Add an explicit Mark read.
  - **B14:** Article and podcast passages are card-length summaries: at most
    six sentences, central idea first. No figures or pullquotes. Remove the
    floating-image alternation rule, the "substantive treatment" wording that
    conflicts with card length, and the headline clause (the card header
    replaces it).
  - **B18:** Long-pressing a card offers the source actions, and Save adds to
    Knowledge.
- `docs/architecture.md`: card rendering ownership. Record that per-card
  saved state is session-local, and why.
- `docs/log.md`: one entry per phase.

## 8. Validation

- **Rust (Phase 2):**
  - `cargo fmt` and warning-denied Clippy for `newsly-providers`,
    `newsly-worker`, `newsly-contracts`, `newsly-db`, `newsly-api`
  - focused tests, isolated PostgreSQL migration tests, contract drift check
- **iOS (Phases 1, 3, 4):** focused unit tests, the card E2E visual state,
  Maestro dark baselines, manual checks in light mode and accessibility size,
  and the briefing UI tests.
- **Runtime:**
  - Run the local API on synced production state.
  - Scroll both lenses until cards mark read.
  - Long-press, Save, and refresh; confirm read cards retire.
  - After Phase 2, confirm that new segments are card-length and that a
    migrated user's lens loads without figure blocks.
- **Review:** Opus 5.5 implementation review before handing off Phases 1 and 2.

## 9. Review findings (Opus 5.5 planning review, 2026-11-20)

All material findings were verified against the code and folded into the plan
above:

- The dedicated card schema replaces prompt-plus-rejection.
- A sentence cap replaces the paragraph cap, because the normalizer collapses
  whitespace.
- The word cap is generous and set after the eval, so publication doesn't
  stall on cooldown.
- The closed block enum means one stray figure fails a lens. A migration
  replaces the minimum-app-version removal condition.
- No Full rebuild producer exists, and a prompt version bump doesn't
  recompose. D2 is now drain plus migration.
- The audio claim was corrected: the legacy unscoped narration path and
  chapter duration estimates read `narration_text`.
- A server saved flag would make every Knowledge writer bump the version, so
  D3 is session-local. The shared `isBusy` drops concurrent saves, so pending
  state is per source.
- The Play chip label didn't match what plays, so D4 needs an owner decision.
- Client figure code dies in Phase 1, so it is deleted there.
- Thumbnails are 200px, so they are placeholders only.
- Missing images are rare, so the variant is tested through load failure.
- The `briefing.segment.<id>` identifier is kept for Maestro.
- The framing prompt is already done.
- B14's "substantive treatment" wording conflicts with card length.
- The footer duplicated the length. It now shows the date only.
- Tap targets: the chip is a sibling overlay and the footer tap is scoped to
  its leading metadata.

## 10. Risks

- **Model length drift:** bounded by the sentence cap and word cap. The eval
  sets the threshold.
- **Long legacy cards linger** for unread backlog. Measure after Phase 2 and
  add an admin Full command only if needed.
- **Old app versions:** after Phase 2 they show short passages under their
  existing headline. Their legacy Listen path speaks the shorter text.
- **Session-local saved state** resets on relaunch. Tapping Save again is
  idempotent and harmless.

## 11. As built (2026-11-20)

These are the differences from the plan above.

- **No separate image-failure variant.** A missing or failed image shows the
  `surfaceContainer` placeholder under the same fade and cream title. Content
  needs generated artwork to be admitted to Briefing, so this case is rare, and
  the dark fade keeps the title readable.
- **Title limit.** Up to four lines over the image, not three. The user's
  reader text size makes three lines truncate common titles.
- **Read styling.** A read card keeps the overlaid title fully opaque,
  desaturates the hero, and fades only the summary and footer. Fading the whole
  card washed out the cream title.
- **Save naming.** The card control is labelled "Save to Knowledge". "Add to
  Knowledge" stays the long-press menu item, so assistive technology never
  sees two identical controls.
- **Legacy block fields.** The public block keeps `image_url`,
  `thumbnail_url`, `caption`, `placement`, `alignment`, and `text`, always
  null. Installed iOS builds decode them as required keys. Remove them once
  those builds are unsupported. Only the `figure` and `pullquote` enum cases
  were removed.
- **Deploy overlap.** Online deploys run `migrate` while the previous
  release's workers still publish, so the API now decodes stored blocks as
  passages only and drops any other block type. Remove that guard once a
  follow-up migration has re-stripped blocks after this release's workers
  replace the old ones.
- **Prompt.** The card prompt also forbids markdown emphasis and quoted
  titles. It requires the linked phrase to read as part of its sentence and
  keeps most sentences under 25 words.
- **Eval.** 12 recent local items went through `briefing_compose_canary` with
  approximated context. All 12 were accepted on the first attempt, at 65–100
  words (median 86) and at most four sentences. After tightening the prompt,
  a rerun of six of them landed at 74–110 words. The hard cap stays at 150.
- **Snapshots.** `BriefingSnapshot.currentSchemaVersion` went to 4, so
  snapshots that may hold figure blocks miss once instead of failing to
  decode.
- **Review fixes.** The Opus implementation review led to these changes:
  - The hero's tap area is clipped to the card.
  - Hero images decode at a 960pt target.
  - The migration locks state rows first.
  - Saved marks are owned by `AuthenticatedSession`, so they never cross accounts.
  - The API guard drops only `figure` and `pullquote`.
  - The legacy `placement` and `alignment` fields are plain optional strings; their enums are gone.
- **Release order.** Deploy the server before shipping the iOS build. New
  builds reject responses that still contain figure blocks, and the migration
  plus the read guard ensure a deployed server never sends them.
- **Recomposing live cards (2026-11-21).** Card and News prompt versions are
  separate. After a card prompt change, normal sweeps replace stale live cards,
  6 per refresh, keeping each card's position. `newsly-admin tasks
  briefing-recompose` previews the backlog and can start the sweeps
  immediately. See law B9.
