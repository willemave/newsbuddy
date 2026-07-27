# app/pipeline/

Source folder: `app/pipeline`

## Purpose
DB-backed task execution runtime: task specs, task envelopes/results, dispatcher, queue processor loop, and worker implementations for content and podcast media.

## Runtime behavior
- `task_specs.py` is the canonical task-type contract for queue, payload, dedupe, lazy handler import, and shared-summarizer requirements. It assigns work to queues such as `content`, `media`, `discussion`, `image`, `onboarding`, `backfill`, `twitter`, `chat`, `audio_episode`, and `llm`.
- `SequentialTaskProcessor` claims queue rows through `QueueService`, validates payloads, dispatches handlers, uses Postgres `LISTEN` when available, falls back to polling, and applies retry/failure results.
- `QueueService` in `app/services/queue.py` owns dequeue/finalization, leases, retry buckets, and queue mismatch checks. Enqueue/dedupe, retention, and read-only metrics live in `queue_enqueue.py`, `queue_retention.py`, and `queue_metrics.py` behind the same public service API.
- Content processing still runs through `worker.py`; workflow modules are small adapters around newer service/lifecycle helpers.

## Important files
| File | Purpose |
|---|---|
| `dispatcher.py` | Maps `TaskType` values to concrete handlers. |
| `sequential_task_processor.py` | Worker loop, queue waiting, task execution, retry/failure handling, and handler construction. |
| `task_context.py` | Shared dependencies passed to handlers. |
| `task_handler.py` | Handler protocol. |
| `task_models.py` | `TaskEnvelope` and `TaskResult` models. |
| `task_specs.py` | Canonical payload, queue, dedupe, handler-import, and shared-dependency metadata. |
| `worker.py` | Main content processing worker and strategy orchestration. |
| `podcast_workers.py` | Podcast media download/transcription worker helpers. |
| `tweet_video_metadata.py` | Tweet-video metadata helpers used by media tasks. |

## Integration points
- Enqueue callers live in commands, services, scrapers, and queue gateways.
- Queue rows are persisted by the `ProcessingTask` ORM model.
- Docker and scripts start one or more processors per queue.
