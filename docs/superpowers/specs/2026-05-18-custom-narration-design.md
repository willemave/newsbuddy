# Custom Multi-Source Narration Design

## Goal

Add a Long Read action that lets the user select multiple eligible articles and podcasts, then creates one combined podcast-style narration. The script must be generated from the full selected source text/transcripts as one cohesive prompt, then sent to ElevenLabs speech synthesis as dialogue turns. Generated narrations should be discoverable and replayable from the Knowledge tab.

## Product Decisions

- Entry point: top of `LongFormView`, near the existing Long Read header/actions.
- Picker: compact SwiftUI sheet opened from `Create narration`.
- Eligible items: current Long Read articles/podcasts plus saved Knowledge articles/podcasts.
- Output: one combined episode for all selected sources.
- Generation UX: the button and sheet show a generating state after submission. The user can leave while the backend task runs.
- Playback: reuse `NarrationPlaybackService` and `AudioEpisodeService` streaming behavior.
- Knowledge storage: show generated custom narrations in the Knowledge tab as audio artifacts, not as fake saved articles.

## Backend Design

Reuse `audio_episodes` and add a new kind:

- `custom_narration`

Add a create endpoint under the existing content audio surface:

- `POST /api/content/audio-episodes/custom-narrations`

Request body:

```json
{
  "content_ids": [123, 456, 789],
  "title": "Optional user-facing title"
}
```

Response remains `AudioEpisodeResponse`, extended with optional custom-narration metadata:

- `source_content_ids: list[int]`
- `source_count: int`
- `source_titles: list[str]`

Implementation seams:

- Extend `app/models/api/audio_episodes.py` with the new kind and request DTO.
- Add `create_custom_narration_episode(...)` in `app/services/audio_episodes.py`.
- Add a list query for user custom narrations:
  - `GET /api/content/audio-episodes/custom-narrations`
- Reuse `commit_audio_episode_delivery(...)`, `generate_audio_episode(...)`, stream endpoints, and the `generate_audio_episode` queue task.
- Store selected content IDs, titles, content types, summaries, and full source text/transcript in `source_snapshot`.
- Keep `source_content_id = null` for multi-source episodes and derive `source_content_ids` from `source_snapshot`.
- Avoid a migration unless implementation finds a hard need for indexed multi-source IDs.

Validation:

- Require 1 or more content IDs.
- Cap selected count conservatively in the API, for example 8-12 sources, even though the script model can take large context.
- Fetch each content with `get_visible_content(...)` for the current user.
- Allow only `article` and `podcast`.
- Require `content_bodies` text for each item.
- Preserve input order from the picker.

Script generation:

- Use the repo's dialogue model spec: `openrouter:deepseek/deepseek-v4-flash`.
- Add a separate custom narration prompt path instead of reusing `CONTENT_COUNCIL_DISCUSSION_KIND`.
- Do not use `_excerpt_longform_source_text(...)` for this path.
- Build one prompt with all selected full source texts/transcripts, source metadata, and summaries.
- Generate one structured script with dialogue turns.
- Let the script use the natural number and length of turns required by the selected sources; provider-bound TTS chunking is handled after script generation.
- Continue recording model usage with `feature="audio_episode_script"` and metadata kind `custom_narration`.

Audio generation:

- Reuse ElevenLabs dialogue TTS via `synthesize_dialogue_mp3(...)`.
- Generate and cache the final MP3 before playback; provider-sized synthesis chunks are stitched
  internally.
- Do not chunk script-generation text input by source.
- Continue caching the final MP3 on the episode row.

## iOS Design

Long Read:

- Add `Create narration` near the top of `LongFormView`.
- Button states:
  - idle: `Create narration`
  - picker open: unchanged
  - submitting/generating: progress indicator plus `Creating narration`
  - completed/ready: optional toast or transition to playback
  - failed: inline error or alert
- Add a compact `CustomNarrationPickerSheet`.
- Sheet loads two sources:
  - current `LongContentListViewModel.currentItems()`
  - saved Knowledge items via `ContentService.fetchKnowledgeLibrary(...)`
- Merge and de-dupe by content ID.
- Filter to article/podcast and prefer body-available items if that field is available on the summary model.
- Let the user select multiple rows and submit.
- Call `AudioEpisodeService.createCustomNarrationEpisode(contentIds:title:delivery:)`.
- Store the created episode in local state so the button can reflect generation status.

Knowledge tab:

- Add a `Narrations` row/section near the existing Library area.
- Load recent custom narration episodes from the new backend list endpoint.
- Show title, source count, duration/status, and a play control.
- Tapping a completed narration starts streaming through `NarrationPlaybackService`.
- Tapping an in-progress narration polls/fetches status or shows `Generating`.
- Keep Saved articles and custom narrations visually distinct.

Client models/services:

- Extend `AudioEpisode` in `Models/Narration.swift` with `kind`, `sourceContentIds`, `sourceCount`, and `sourceTitles`.
- Add endpoints to `APIEndpoints.swift`.
- Add create/list methods to `AudioEpisodeService.swift`.
- Keep playback target as `NarrationTarget.audioEpisode(id)`.

## Tests And Verification

Backend tests:

- Service test creates a `custom_narration` episode from two visible article/podcast rows and stores full body text in `source_snapshot`.
- Service test rejects unsupported content types.
- Service test rejects missing body/transcript text.
- Router test verifies create endpoint returns `AudioEpisodeResponse` and enqueues `generate_audio_episode` for background delivery.
- Script prompt test verifies custom narration does not call `_excerpt_longform_source_text(...)`.
- List endpoint test returns only the current user's custom narrations.

iOS checks:

- Xcode build for `client/newsly`.
- If feasible, add focused Swift tests for selection merge/de-dupe logic.
- Simulator smoke:
  - open Long Read
  - tap `Create narration`
  - select two items
  - create narration
  - verify generating state
  - verify Knowledge tab shows the generated narration
  - verify completed narration can stream/play

Python checks:

- `ruff check app/services/audio_episodes.py app/routers/api/audio_episodes.py app/models/api/audio_episodes.py tests/services/test_audio_episodes.py tests/routers/test_audio_episodes.py`
- `pytest tests/services/test_audio_episodes.py tests/routers/test_audio_episodes.py -v`

## Implementation Sequence

1. Extend backend API contracts for `custom_narration` and source metadata.
2. Add backend create/list endpoints.
3. Add `create_custom_narration_episode(...)` and custom prompt generation in `audio_episodes.py`.
4. Add backend tests for creation, validation, prompt input, and listing.
5. Extend iOS `AudioEpisode`, `APIEndpoints`, and `AudioEpisodeService`.
6. Build the Long Read `Create narration` button, picker sheet, selection merge, and generating state.
7. Add the Knowledge tab `Narrations` section/list and playback wiring.
8. Run focused backend checks and an iOS build.
9. Smoke-test in Simulator with local seeded article/podcast rows.

## Risks

- Very large source selections can produce high latency and cost even with a 1M-context model, so the API needs a selection cap and clear UX.
- Existing `AudioEpisodeScript` limits are too short for multi-source narrations; the implementation should either make limits kind-aware or introduce a custom script schema variant.
- Saved Knowledge items may include articles without body text; the picker can hide obvious unsupported items, but the backend must remain authoritative.
- The existing Knowledge tab is currently chat plus saved-content oriented, so narrations should be a separate lightweight section rather than folded into the saved-article list.
