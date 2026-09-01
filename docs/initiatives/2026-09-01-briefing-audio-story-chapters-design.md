# Briefing Audio Story Chapters Design

## Goal

Make Briefing audio feel like a purpose-written listening experience instead of a spoken copy of
the rendered Briefing. Articles and podcasts become individually titled chapters, News becomes one
combined highlight program, and playback continues through device lock with system Now Playing and
pause controls.

This design replaces the chapter-planning decisions in
`2026-07-19-briefing-audio-chapters-design.md` where they conflict. It keeps the existing durable
`audio_episodes` rows, authenticated stream endpoint, generation queue, chapter-scoped read
completion, and partial-manifest playback.

## Product Decisions

- Generate the spoken script on demand when the user requests Listen. Do not make routine Briefing
  refresh wait for or pay for audio-specific composition.
- Spoken scripts use canonical source titles, long summaries, key points, publication/show names,
  and other bounded source metadata already available to the detailed reading experience. They are
  grounded adaptations, not readings of the visible Briefing passage and not retrieval from the
  public web at playback time.
- Each active Article source is one chapter. Each active Podcast source is one chapter. A chapter is
  titled with the exact article or podcast title; the show/publication appears as secondary
  metadata when available.
- Short Article and Podcast chapters are acceptable. Do not combine documents merely to approach a
  duration target, and do not split one document across chapters.
- News is one combined program across every active News lens. Its chapters remain bounded listening
  chunks, but the script model selects the highest-signal events and synthesizes related coverage
  instead of reading every lens passage or mentioning every source.
- Preserve the current Briefing narration voice presentation in the first implementation. Changing
  hosts, voices, or sound design is independent work.
- The active chapter is the lock-screen media item. System metadata and queue position change at
  every chapter boundary.

## User Experience

### Articles and Podcasts

The Articles and Podcasts tier headers retain the compact Listen entry point. Starting playback
creates a manifest for the tier's current unread edition. The chapter row and chapter sheet show:

- exact document title as the primary label;
- publication or podcast show as a secondary label when present;
- duration and ready/preparing/failed state;
- selected and playing state.

The compact label becomes `Chapter N of M · <truncated document title>` where space permits. The
full title remains available in the sheet and through accessibility.

### News

The top-level News tier owns one Listen action rather than each semantic News lens owning an
independent audio program. Starting it creates one manifest from all active News lenses in their
display order and current Briefing version.

The script for each News chapter is a short editorial rundown: lead with the most consequential
events, combine related sources, retain concrete names/numbers/stakes, and omit low-signal items.
Source omission from narration does not remove the source from the written Briefing or alter its
eligibility. News chapter labels may remain numbered in this iteration because the user-facing
requirement for source-derived titles applies to Articles and Podcasts.

### Lock Screen and Control Center

When a chapter starts, iOS publishes:

- title: article/podcast title, or `News Briefing · Chapter N`;
- album/collection: `Articles`, `Podcasts`, or `News Briefing`;
- artist/subtitle: publication/show for long-form, or `Newsly` for News;
- artwork: source image/thumbnail when available, otherwise Newsly artwork;
- duration, elapsed time, playback rate, queue index, and queue count.

The system play, pause, toggle, seek-position, previous-chapter, and next-chapter commands use the
same state transitions as the in-app controls. Commands that cannot succeed are disabled rather
than displayed as inert controls. Locking the device does not pause playback, discard the manifest,
or prevent automatic chapter advance.

## Public Contract

Add an explicit scope as the canonical request identity while retaining the released optional lens
key for installed clients:

```text
BriefingNarrationScope
  article_tier
  podcast_tier
  news_program

BriefingNarrationRequest
  scope: BriefingNarrationScope?        exactly one of scope or lens_key
  lens_key: String?                     compatibility only
```

Do not represent the combined News program with a magic lens key on the wire. The scope is a closed
enum in the Rust contract and generated Swift model. The server also accepts the released
`{ "lens_key": ... }` request shape, maps it to the existing lens-scoped behavior, and rejects
requests containing both or neither forms. Remove the compatibility field only after the
minimum supported app version and route telemetry show that installed clients no longer send it.

Keep the existing plural create and group-status routes:

- `POST /api/briefing/narrations`
- `GET /api/briefing/narrations/{episode_group_id}`

Extend manifest and chapter presentation only with metadata the player cannot derive from the
ordered manifest:

```text
BriefingNarrationResponse
  scope                         article_tier, podcast_tier, or news_program

AudioEpisodeResponse
  title                         exact chapter/media title
  subtitle                      optional publication/show
  artwork_url                   optional authenticated or public image URL
```

The client derives collection title, zero-based queue index, and queue count from `scope` and the
ordered `chapters` array. If an existing field already carries one of the added meanings during
implementation, use the canonical field rather than creating an alias. Regenerate OpenAPI and both
Swift clients; do not hand-edit generated artifacts.

The old singular `/api/briefing/narration` route and installed lens-key client contract remain
unchanged for compatibility. New app code sends only scoped requests to the plural endpoint.

## Source and Chapter Planning

Planning runs inside a short transaction and produces owned, immutable chapter inputs before queue
work begins.

### Article and Podcast scopes

1. Load active/degraded segments for every lens in the requested tier.
2. Revalidate ownership, unread eligibility, and canonical source keys.
3. Because Briefing law B4 gives each Article/Podcast segment one source, create one plan per source
   in the existing tier/lens/segment order.
4. Load the canonical source projection used by Briefing composition: exact title, source/show,
   long summary, key points, bounded context, image metadata, and source key.
5. Store that projection in the episode's source snapshot. Do not store provider SDK types, raw
   article bodies, or full podcast transcripts in the episode row.

### News scope

1. Load active/degraded segments across all active `news` lenses using lens position, then segment
   recency, as the stable input order.
2. Resolve their eligible sources to the same canonical title, summary, key-point, lens, and event
   metadata projections used by Briefing.
3. Pack bounded source windows for script generation. Use the existing duration target as a budget,
   not a promise; the resulting script is authoritative for final duration.
4. Preserve event groups so multiple sources describing one event are considered together.
5. Instruct the script model to curate highlights across the window. Unlike written Briefing
   coverage validation, narration is not required to mention or link every input source.

Read-on-finish ownership remains the planned source-key set for that chapter. For News, this means
finishing a curated chapter marks its complete planned window read even if the spoken script omitted
a low-signal source. The chapter sheet and tests must make this batching behavior explicit.

## Durable Audio Generation

Each chapter remains one normal `audio_episodes` row sharing an `episode_group_id`. Preparation no
longer writes the visible `narration_text` into `script` or `script_text`. Instead it stores a
versioned audio-source snapshot and leaves script fields empty so the existing audio worker performs
audio-specific script generation before TTS.

Add a `briefing_narration` branch to the audio script prompt:

- for Article/Podcast: tell one coherent, compact story from one document, using its thesis,
  strongest details, stakes, and takeaway without reciting the on-screen summary;
- for News: select and synthesize the highest-signal events across the supplied lenses, avoid a
  lens-by-lens roll call, and use only supplied summaries, key points, and metadata;
- preserve exact supplied titles and attribution;
- forbid invented facts, external research, stage directions, markdown, and claims of exhaustive
  coverage.

The group hash covers scope, current Briefing version, ordered source keys, normalized source
snapshots, chapter windows, and narration prompt version. The chapter input hash additionally covers
its index. Repeating the same request reuses rows and completed audio; any source or prompt change
creates a new immutable group.

Chapter 1 still gates `playable`. Later chapters generate in order and may complete while earlier
audio plays. A failed later chapter leaves completed chapters playable and is retried through the
existing idempotent request path.

## iOS Playback Ownership

Promote the narration queue from view-local behavior to an authenticated-session playback owner.
The owner retains the manifest, scope, selected chapter, preparation task, and automatic-advance
intent independently of whether `BriefingView` is currently visible. Views observe and command this
owner; they do not own background playback callbacks.

Keep `NarrationPlaybackService` responsible for the `AVPlayer`, progress, saved position, and audio
session. Add a MediaPlayer adapter responsible for Now Playing publication and remote-command
registration so system integration remains injectable and testable.

At playback start:

1. Configure `AVAudioSession` with the playback category and activate it only when playback begins.
2. Enable the app's Audio/AirPlay/Picture in Picture background mode.
3. Start the authenticated `AVURLAsset` stream for the selected chapter.
4. Publish complete Now Playing metadata and enable commands valid for the current queue position.
5. Update elapsed time and effective playback rate on play, pause, seek, rate change, and periodic
   progress ticks.

At a chapter boundary, the session owner records chapter completion without blocking advance,
selects the next ready chapter or polls its durable manifest state, replaces the player item, and
atomically replaces Now Playing metadata. At final completion or explicit stop it clears Now Playing
state and deactivates the audio session with notification to other audio apps.

Handle audio interruptions and route changes as explicit player events. An interruption pauses and
updates system state; resume only when the system indicates it is appropriate. Headphone or route
loss must not accidentally continue through the speaker.

## State, Failures, and Compatibility

- Backgrounding pauses only UI-owned Briefing refresh/paging work. It does not cancel an active
  playback queue, manifest observation needed for the next chapter, or authenticated media stream.
- Authentication failure on a later stream stops at the chapter boundary with a recoverable state;
  it never loops requests in the background.
- If source artwork cannot be fetched, publish fallback artwork without blocking audio.
- If a chapter script fails, preserve the public bounded error message and allow the same manifest
  request to retry the failed episode.
- Existing completed chapter groups keep their immutable scripts and remain playable. New behavior
  is selected by a new narration prompt version and group hash, so no destructive backfill is
  required.
- Custom Narration, council discussions, single-item discussions, and Fast News Digest retain their
  existing generation semantics. They may reuse the iOS Now Playing adapter later but are outside
  this Briefing product change.

## Behavioral Law Update

Implementation must add a Briefing law stating that Article and Podcast narration chapters map
one-to-one to eligible documents; News narration is one combined curated program; spoken scripts
remain grounded in the chapter's canonical sources but need not duplicate visible prose or mention
every News source; and read-on-finish remains chapter-source scoped.

## Validation

### Rust

- Contract tests for all three scopes, closed-enum rejection, generated Swift drift, and legacy
  singular-route compatibility.
- PostgreSQL tests for tier-wide source loading, one-document chapters, combined News ordering,
  event-group preservation, group/input hash stability, immutable reuse, ownership, read
  revalidation, and retry after partial failure.
- Provider prompt/structured-output tests proving Article/Podcast scripts receive long summaries and
  key points, News can omit low-signal sources, exact attribution is preserved, and unsupported
  facts are rejected by fixtures/evals.
- Queue/worker tests proving empty script fields invoke the audio-script model exactly once per
  chapter before TTS and exact-lease finalization still fences stale work.
- Focused model canaries for an article, a podcast, and a multi-lens News program. Review grounding,
  spoken naturalness, title fidelity, selection quality, word count, and completed-audio latency.

### iOS

- Unit tests for app-scoped queue retention, chapter metadata mapping, automatic advance while the
  Briefing view is absent, command enablement, play/pause/toggle, seeking, next/previous bounds,
  playback-rate metadata, interruption handling, route loss, stop cleanup, and stale callback
  fencing.
- Service tests that authenticated stream headers survive player-item replacement.
- Build the native app and run focused Briefing narration/playback tests.
- Device validation, not Simulator-only proof: start a Podcast chapter, lock the device, confirm its
  exact title and artwork, let audio continue, pause/resume from the Lock Screen, seek, cross a
  chapter boundary, and verify the next title. Repeat with the combined News program and with
  Bluetooth/headphone route loss.
- Verify read marks after natural completion and verify pause, seek, skip, and interruption do not
  mark a chapter read.

## Implementation Slices

1. Introduce scoped contracts and canonical chapter-source projections; implement one-document
   Article/Podcast planning and combined News planning with repository tests.
2. Route Briefing chapters through versioned on-demand script generation; add prompt tests, worker
   tests, and model canaries.
3. Expose source-derived chapter metadata and update the compact row/chapter sheet.
4. Promote queue ownership, add background audio capability, Now Playing metadata, and lock-screen
   commands with focused Swift tests.
5. Run native device validation across lock, interruption, route change, automatic advance, and
   chapter-scoped read completion; then update `docs/laws/briefing.md` with the verified behavior.

Each slice must leave the existing released playback path functional. No implementation slice
authorizes commit, push, deployment, or Apple distribution.

## Out of Scope

- HLS or playback before a chapter MP3 is complete.
- Raw full-text or full-transcript narration.
- Generated News chapter titles.
- New voices, music, sound effects, or a full-screen global player redesign.
- Persisting playback position across process termination or device reboot.
- Changing written Briefing coverage, lens naming, or source eligibility.
