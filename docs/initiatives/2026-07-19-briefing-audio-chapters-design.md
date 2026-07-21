# Briefing Audio Chapters Design

## Goal

Make long Briefing narrations playable much sooner by generating and playing them as a sequence of
durable chapters instead of waiting for one complete MP3. Preserve the compact Briefing player and
make chapter navigation available without turning the resting screen into a playlist.

## Product Decisions

- Target approximately five minutes of narration per chapter.
- Never split a `BriefingSegment`; an individual segment longer than five minutes is its own chapter.
- Preserve the written Briefing's newest-first segment order.
- Keep the player compact. Show `Chapter N of M` with previous and next actions, and open the full
  chapter list from that label.
- Use simple numbered chapter titles in the first version. Do not add another LLM call for naming.
- Mark only a chapter's sources read, and only after that chapter reaches the end of playback.

## Persistence Model

Keep one physical table. Each playable chapter is one normal `audio_episodes` row.

Add two nullable columns to `audio_episodes`:

- `episode_group_id`: the deterministic hash identifying one exact Briefing narration snapshot.
- `chapter_index`: the zero-based position inside that group.

The existing row fields continue to own chapter state:

- `status`, `started_at`, `completed_at`, and `error_message` for generation lifecycle;
- `script`, `script_text`, and `duration_seconds` for the pre-authored narration;
- `audio_storage_path` and `audio_content_type` for the playable MP3;
- `source_snapshot` for lens metadata, segment IDs, source keys, and read-on-finish source IDs;
- `input_hash` for exact chapter identity and generation deduplication.

Enforce one chapter at each position with a unique constraint over user, kind, group, and chapter
index. Existing non-chaptered audio rows keep both new columns null and retain their current behavior.

There is no parent row. Overall narration state is a derived manifest over rows sharing an
`episode_group_id`.

## Chapter Planning

Estimate each segment's duration with the existing 145-words-per-minute estimator. Starting with
the newest segment, greedily pack consecutive whole segments into the chapter whose total is closer
to the five-minute target. Once adding the next segment would make the current chapter farther from
five minutes, close the current chapter and begin the next one.

This is a target rather than a minimum or hard maximum. It avoids pathological short chapters while
respecting the stronger rule that a composed segment is never divided.

The group hash covers the prompt version, lens identity, ordered segment IDs, source keys, and exact
narration text. Repeating the same request reuses the same rows and completed MP3s. A changed
Briefing snapshot creates a new group and leaves the older immutable narration intact.

## Backend Flow

1. `POST /api/briefing/narrations` loads active/degraded segments newest first and plans chapters.
2. It creates or reuses one `audio_episodes` row per chapter and returns a manifest.
3. In background delivery, incomplete chapter rows are enqueued in chapter order through the
   existing `GENERATE_AUDIO_EPISODE` task and `audio_episode` queue.
4. Each worker task uses the existing pre-authored script, TTS generation, retry classification,
   file persistence, and stream endpoint.
5. `GET /api/briefing/narrations/{episode_group_id}` returns refreshed manifest state for the owner.
6. The manifest is playable when Chapter 1 is completed. It is completed when every chapter is
   completed. Chapter-level status remains authoritative for partial failures.

The manifest response contains:

- group ID, lens key, title, derived status, and `playable`;
- ordered chapter `AudioEpisodeResponse` values;
- total estimated duration derived from chapter durations.

If Chapter 1 fails, preparation fails normally. If a later chapter fails, already completed chapters
remain playable and the failed chapter can be retried by requesting the same narration again.

## iOS Flow

`BriefingViewModel` stores a narration manifest per lens instead of one episode. Preparation requests
the manifest and polls only until it becomes playable, rather than waiting for every chapter to
complete. Manifest refresh remains a service/view-model concern and treats cancellation as a normal
lifecycle outcome.

Playback still uses `NarrationPlaybackService` and an individual chapter's existing authenticated
stream URL. At completion it starts the existing read-on-finish recording and asks the Briefing flow
to advance without making the next chapter wait on that network call:

- if the next chapter is completed, play it immediately;
- if it is still generating, refresh the manifest until it is ready and show preparation state;
- if it failed, stop at the boundary and show a retryable error;
- after the final chapter, stop normally.

Manual previous, next, and chapter-list selection use the same chapter-playing path. Playback speed
remains shared. Progress and seeking remain chapter-local; the chapter label supplies the position
within the complete narration.

## Hybrid Player UI

While narration is preparing or active, the existing panel gains one compact row above the current
playback controls:

- previous chapter button;
- tappable `Chapter N of M · ~5 min` label;
- next chapter button.

The label presents an item-driven sheet containing all chapters. Each row shows its chapter number,
estimated duration, source count, and ready/preparing/failed state. Ready chapters are selectable;
selecting a pending chapter waits through the same manifest polling path. Accessibility labels expose
chapter position, duration, status, and navigation availability.

## Compatibility And Rollout

- Do not change generation or playback behavior for fast-news, council, custom, or single-item audio.
- Keep `POST /api/briefing/narration` returning the original full-length `AudioEpisodeResponse` so
  installed app versions remain compatible. Only the new plural endpoint returns a chapter manifest.
- Ignore legacy ungrouped Briefing narration rows when creating a chaptered manifest; they remain
  playable through existing episode URLs.
- Regenerate OpenAPI and Swift contract artifacts from the registered Pydantic models.
- No production data backfill is required because both new columns are nullable.

## Validation

- Unit-test five-minute packing, newest-first stability, oversized segments, and no segment splits.
- Service/router tests cover group reuse, chapter ordering, manifest status, enqueue order, ownership,
  partial failure, and chapter-scoped read-on-finish behavior.
- Pipeline tests confirm every chapter remains a standard audio-generation task.
- Swift tests cover preparation stopping at playable, manifest refresh, chapter selection, next/previous
  bounds, pending-next waiting, automatic advancement, and cancellation.
- Build the native iOS target and run focused Briefing narration tests.
- Add or update Maestro coverage for the compact chapter row and chapter sheet if the flow can use a
  deterministic completed-audio fixture.

## Out Of Scope

- HLS playlists or byte-level live streaming of an incomplete MP3.
- Splitting inside a composed Briefing segment.
- LLM-generated chapter names.
- Cross-snapshot reuse of a chapter whose surrounding grouping changed.
- A global lock-screen-style player redesign.
