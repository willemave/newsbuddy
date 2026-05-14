# Queue Congestion Plan

## Context

Newsly uses a PostgreSQL-backed `processing_tasks` table with queue partitions,
leases, retries, and active-task dedupe. The queue mechanics are adequate for
the next stage of load. The congestion risk is partition granularity: too many
latency-sensitive and bulk workloads share the same worker lanes.

This plan intentionally keeps the database-backed queue. It does not add a
separate fast lane for share-sheet URLs. Instead, it increases content
parallelism and splits the specific long-running/background workloads that can
block visible product flows.

## Target Shape

Queue partitions after this work:

| Queue | Purpose |
| --- | --- |
| `content` | Core content analysis, extraction, summarization, news normalization, scraping, discovery, reports |
| `discussion` | Long-form and short-form discussion fetch/summarization |
| `media` | Podcast and tweet-video download/transcription |
| `audio_episode` | On-demand generated audio episodes and TTS-heavy work |
| `image` | Generated long-form artwork |
| `onboarding` | Latency-sensitive onboarding discovery |
| `backfill` | Feed backfills after onboarding selections |
| `twitter` | X/bookmark integration sync |
| `chat` | Queued dig-deeper/share-and-chat work |

## Phase 1: Hygiene

- Route user-submission `ANALYZE_URL` task creation through the canonical queue
  service while preserving the existing merge behavior for active analyze tasks.
- Route queued dig-deeper task creation through the canonical queue service.
- Make queue maintenance spec-driven so existing pending/processing rows can be
  moved to their new queue partitions after task routing changes.
- Update architecture docs so the active media queue is not described as the old
  `transcribe` queue.

## Phase 2: Observability

- Keep queue-health visibility centered on oldest age by `queue_name/task_type`.
- Add processing-age visibility for in-flight tasks.
- Add queue activity readouts for enqueued/completed/failed counts inside the
  dashboard window.
- Keep this phase read-only. Do not add generalized backpressure yet.

## Phase 3: Content Parallelism

- Do not create a `user_content` or faster share URL queue.
- Increase default content worker parallelism from 2 to 4.
- Align local, bare-metal Supervisor, and Docker Supervisor workers to that
  default so load does not depend on one launcher path.
- Tune upward only from observed queue age and provider/DB limits.
- Watch `analyze_url`, `process_content`, `summarize`, and
  `process_news_item` separately so extra workers do not hide task-type
  hotspots.

## Phase 4: Discussion Queue

- Add a dedicated `discussion` queue.
- Move `fetch_discussion` and `fetch_news_item_discussion` there.
- Preserve discussion single-flight semantics: queued work, explicit refreshes,
  and scheduled catch-up must still claim/lease before network and LLM work.

## Phase 5: Media Split

- Keep podcast/tweet download and transcription in `media`.
- Move `generate_audio_episode` to `audio_episode`.
- This prevents a user-triggered generated episode from blocking podcast/tweet
  transcription, and prevents a media backlog from making generated audio feel
  broken.

## Phase 6: Onboarding And Feed Backfills

- Keep `onboarding_discover` in the latency-sensitive `onboarding` queue.
- Move `backfill_feeds` to `backfill`.
- Feed backfills begin only after onboarding selections are committed, then fill
  content progressively in the background.
- Bulk backfill scheduling can be refined later after queue-age data is visible.

## Phase 7: Dedupe

- Add or tighten dedupe without introducing backpressure:
  - `process_news_item`: payload/news-item identity.
  - `onboarding_discover`: run/user input identity.
  - `dig_deeper`: `user_id + content_id + initial_message_hash`.
  - `sync_integration`: `user_id + provider + trigger`.
  - `generate_insight_report`: keep the existing user/nightly dedupe key.
- Keep existing content, media, image, and discussion dedupe behavior.

## Deferred

- Generalized backpressure expansion.
- Background throttling by workload class.
- A dedicated share/manual-submission content queue.
- Replacing the DB-backed queue with an external broker.

## Rollout Order

1. Hygiene and docs.
2. Spec-driven queue maintenance for existing active rows.
3. Queue-health readouts.
4. Worker defaults, bare-metal/Docker Supervisor config, and content
   parallelism.
5. Discussion queue.
6. Audio episode queue.
7. Backfill queue.
8. Dedupe tightening and focused tests.

## Success Criteria

- Share/manual submissions benefit from more content parallelism without a
  separate queue.
- Discussion refresh waves no longer consume content workers.
- Generated audio episodes no longer block transcription.
- Feed backfills no longer block onboarding discovery.
- Operators can see oldest pending age, oldest processing age, and activity by
  queue/task type.
