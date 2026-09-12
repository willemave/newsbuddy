# Briefing audio startup plan

Status: timing instrumentation implemented locally; real-world measurements and
performance changes remain pending.

Review gap: a read-only Claude Fable consultation was attempted using the
repository-required skill. It returned no output after approximately seven
minutes and was stopped. The progressive-publication architecture needs that
independent review before it is treated as implementation-ready.

## Objective

Reduce the time between pressing Play and hearing the first spoken words, while
preserving the current narration quality, chapter order, selected-lens scope,
pause/resume, replay, and read tracking. Initially assume the reported delay is
first playback; measure resume separately.

## Current evidence

The current checkout follows this sequence:

1. Play requests an immutable narration edition for the selected lens.
2. The API queues every unfinished chapter at the default queue priority.
3. An audio worker generates a complete adapted script.
4. It synthesizes all speech turns, waits for every response, stitches multiple
   MP3s when needed, saves the file, and commits chapter completion.
5. iOS observes completion through polling every 1.5 seconds, then starts AVPlayer.

The app already starts after the first chapter completes; it does not wait for
the whole edition. Its player already uses `playImmediately` and disables
automatic waiting to minimize stalls. The existing `/stream` endpoint waits for
the completed file; it does not expose audio during synthesis.

Relevant owners:

- `client/newsly/newsly/ViewModels/BriefingNarrationController.swift`
- `client/newsly/newsly/Services/NarrationPlaybackService.swift`
- `rust/crates/newsly-api/src/briefing/narration.rs`
- `rust/crates/newsly-worker/src/audio_episode/handler.rs`
- `rust/crates/newsly-providers/src/audio_episode.rs`
- `rust/crates/newsly-api/src/audio_episodes.rs`

These are code observations, not evidence of the deployed bottleneck. Script
generation, synthesis, queue contention, and media delivery remain unmeasured.

## 1. Measure the complete first-play path

Correlate a playback intent with its request, episode group, chapter, and queue
attempt. Use monotonic timings within each process and explicit stage durations
across processes; do not infer latency from unsynchronized client/server clocks.

Capture tap, snapshot/request duration, enqueue-to-claim wait, script duration,
TTS-slot wait, TTS first-byte and full-response time, stitching, storage/finalize,
client readiness discovery, media authorization, and first advancing playback.
Existing AVPlayer readiness/progress logs are useful but begin after preparation.
Validate the playback metric against audible output on a device.

Compare fresh News, article, and podcast editions; completed edition reuse;
pause/resume; chapter transitions; and a busy audio queue. Report median and
tail latency with sample counts. Begin with existing logs and deterministic
fixtures; establish an authorized call count and cost ceiling before live
provider benchmarks. Verify effective runtime settings rather than assuming
configuration defaults are deployed.

## 2. Remove measured avoidable waits

- If queue wait matters, use the existing priority mechanism to favor the first
  requested chapter over later background chapters. Preserve fairness and test
  simultaneous users. Priority cannot interrupt an already-running episode;
  inspect worker capacity before promising a fix for that case.
- If readiness discovery matters, test a shorter bounded foreground polling
  interval with backoff. The current polling contributes up to roughly one
  interval plus request time; reducing it cannot eliminate generation latency.
- Confirm completed editions and paused players reuse their existing resources.
  Do not reuse audio across changed source snapshots or narration versions.
- If script generation dominates, benchmark prompt/input and model settings
  against the same groundedness, coverage, and listening-quality criteria.
- If stitching dominates, investigate avoiding unnecessary audio re-encoding
  while verifying audio continuity, format compatibility, seeking, and duration.

Choose changes from measurements. Each change must improve tap-to-audio without
increasing stalls, failed starts, duplicate generation, or unwanted provider cost.

## Reading the implemented timings

Filter API/worker logs for `Audio timing`. The request events carry `request_id`,
and the enqueue/commit events connect it to `episode_group_id` and the first
`audio_episode_id`. The `audio_episode_attempt` span carries `task_id`,
`audio_episode_id`, and `retry_count` through provider work and finalization.
Each `audio_tts_chunk` child span identifies its `chunk_index`.

| Stage | What the duration measures |
| --- | --- |
| `narration_transaction`, `narration_snapshot`, `narration_enqueue`, `narration_commit` | API preparation and durable queue insertion; commit also reports total request elapsed time |
| `queue_ready_to_claim` | Database claim time minus availability time, excluding scheduled retry backoff |
| `queue_creation_to_claim` | Task age at claim, including backoff and earlier attempts; not interchangeable with ready wait |
| `prepare`, `script_generation` | Worker preparation and complete validated script; script includes model, generated/cache/preauthored mode, and available usage counts |
| `tts_slot_wait`, `tts_response_headers`, `tts_response_body` | Per-chunk provider capacity wait, HTTP headers, and complete body receipt |
| `tts_requests`, `tts_stitch`, `tts_total` | All concurrent speech requests, assembly/re-encoding, and total speech stage |
| `file_storage`, `fenced_finalize`, `total_attempt` | File storage, finalization including commit, and complete worker attempt |

Read `succeeded` and `outcome` together. Successfully committing a failure or a
retry is not successful audio generation. Totals contain their child stages;
do not add overlapping durations or sum concurrent TTS requests to estimate
wall-clock time. Aborted work may lack an inner completion event; inspect the
attempt outcome and existing worker failure logs.

In iOS logs, `Briefing audio timing` provides a local trace UUID, group/episode
identity, `elapsedMs` since that trace began, and `operationMs` for a command,
poll, or authorization operation. Use the play trace's `player_requested`
milestone plus the matching episode's `Streaming narration first playback
progress` duration to estimate tap-to-progress. The latter includes media
authorization and player setup. Match the same playback attempt, since replay
reuses episode IDs. Read command/poll traces separately rather than summing
their cumulative elapsed times. Resume starts a new player timing interval.

The existing player progress timer samples every 0.5 seconds. This is an
approximation of playback onset, not physical audibility. Neither HTTP headers
nor the runtime's post-validation `TextDelta` event measure first generated
audio/token: the current script uses `model.complete`, and speech uses complete
response-body buffering. This instrumentation intentionally preserves both paths.

For a real baseline, capture a fresh Play, completed-episode reuse, resume, and
a chapter transition with the instrumented app/backend. The local fixture tests
validate behavior, not provider latency. No live provider benchmark or deployment
is included in the timing-only change.

## OpenAI streaming and provider comparison

OpenAI supports [streamed text generation](https://developers.openai.com/api/docs/guides/streaming-responses)
and [streamed text-to-speech](https://developers.openai.com/api/docs/guides/text-to-speech).
These are different stages: speech streaming consumes written input, while text
streaming could later let validated script units feed speech before the entire
script completes. Merely replacing ElevenLabs does not remove Newsly's current
full-chapter readiness gate.

Before choosing a replacement speech provider, compare identical scripts for
naturalness, pronunciation of names/numbers, first audible output, interruption
behavior, and actual cost. Preserve the current provider until listening and
timing evidence supports a switch. Agree on a bounded live benchmark separately.

## 3. Prototype progressive speech only if needed

If full speech synthesis remains a substantial wait, prototype playback while
speech is generated within the existing logical chapter. ElevenLabs supports
[HTTP speech streaming](https://elevenlabs.io/docs/api-reference/text-to-speech/stream).
This is a provider capability, not proof that Newsly's current transport or
AVPlayer path can consume it correctly.

Initially retain a complete validated script before synthesis. That preserves
the existing adaptation and avoids exposing unvalidated partial model output.
Publish ordered audio through the worker, with durable generation identity and
short lease-fenced updates. Keep authenticated delivery in the API and retain a
completed artifact for replay. Provider transport remains in the provider crate.

Before implementation, resolve the delivery format, player buffering/seek
behavior, partial-artifact lifecycle, and failure/retry semantics. A stream ending
early must not count as chapter completion. A retry must not silently splice a
new script or regenerated audio into an already-heard prefix. Older clients must
retain their completed-file path until the compatibility removal condition is met.

Prove one chapter end to end before extending to multi-chapter playback. Keep
script generation, first playable audio, and full generation as separate metrics.
Streaming speech alone cannot remove time spent generating the script.

## Deferred options

Preparing audio when a lens opens could hide startup time, but spends resources
on audio the user may never play and must revalidate current unread inputs at
Play. Consider it only with an explicit freshness/reuse policy and cost budget.
Shorter chapters or reading the visible summary would change the listening
experience; those are separate product decisions.

## Acceptance and validation

After baseline measurement, set separate numerical targets for fresh generation,
completed audio, resume, and chapter transitions. Do not promise a cold-start
duration based on provider inference figures alone.

Validate on an Xcode-built current app in Simulator and on a physical device,
including Wi-Fi and cellular, background/foreground, pause, stop, lens switching,
and a slow or interrupted connection. Add focused Rust/provider and iOS coverage
for changed behavior; queue or partial-publication changes also need isolated
PostgreSQL tests for dedupe, retries, cancellation, lease loss, and stale writes.
Any new wire states require contract drift checks and native-client validation.

The selected lens and immutable edition must remain stable. Natural completion
marks the logical chapter's planned sources read exactly as today; a partial
stream or interrupted playback must not do so. Update the audio law only if
approved intended behavior changes.
