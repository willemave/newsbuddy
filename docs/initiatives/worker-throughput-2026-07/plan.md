# Worker Throughput Initiative — Implementation Plan

Status: approved for implementation (2026-07-25)
Branch: `feature/worker-throughput`

## Goal

Increase task throughput of the queue workers without adding processes. Today every
worker is a single-threaded sequential loop; production runs 14 worker processes
(4 × `content` + 1 × each of the other 10 queues) at ~725 MB RSS each (measured:
importing `app.pipeline.sequential_task_processor` alone costs 725 MB, of which
~230 MB is torch+whisper pulled in transitively). Target end state:

- **One process per queue** (keep per-queue isolation, supervisord restart granularity,
  and watchdog semantics exactly as they are).
- **N claim-loop threads inside each process**, configurable per queue.
- **No torch/whisper import** outside the `media` queue process.
- **Embeddings via OpenRouter API** instead of a local Qwen3-Embedding-0.6B warmed
  in every content worker.
- **faster-whisper** replacing `openai-whisper` for local transcription.
  Transcription stays local — hosted speech-to-text is too expensive at our volume,
  so the API move applies to embeddings only.

Decisions already made (do not relitigate):

- Keep the Postgres-backed queue (`app/services/queue.py`). No external broker.
- Keep one worker process per queue; concurrency comes from threads inside the process.
  Do NOT build multi-queue dequeue / queue consolidation.
- Threads, not asyncio. Handlers are synchronous and several call `asyncio.run()`
  internally; an async rewrite is out of scope.
- Thread-count defaults should be "scale ready": content=6, media=3,
  audio_episode=1, all other queues=4 (see Phase 2). All overridable via settings/env.
- Transcription stays local (faster-whisper). Do not propose a hosted STT provider.

## Why threads are safe here

- The queue layer is already concurrency-safe: claims use `FOR UPDATE SKIP LOCKED`
  (`QueueService.dequeue`), tasks hold leases renewed by a per-task heartbeat thread
  (`SequentialTaskProcessor._lease_heartbeat`), and expired leases are reclaimable.
  Nothing about correctness assumes one task at a time per process.
- The workload is dominated by network I/O (HTTP scraping, LLM APIs, image/TTS APIs)
  and subprocesses (ffmpeg, yt-dlp), which release the GIL.
- Handlers that call `asyncio.run()` internally are fine on worker threads
  (`asyncio.run` creates a fresh loop per call; none of these threads have a running loop).
- CPU-bound local work (Whisper) is explicitly gated to single-flight (Phase 3).

## Phase 1 — Memory quick wins (lazy imports + API embeddings)

### 1a. Lazy-import torch/whisper out of the shared import graph

Current offenders (all module-level imports that end up in every worker process):

- `app/services/whisper_local.py:4-5` — `import torch`, `import whisper`.
- `app/pipeline/podcast_workers.py:27` — imports `whisper_local` at module level;
  `podcast_workers` is imported by `app/pipeline/worker.py`, which is imported by
  `sequential_task_processor.py`.
- `app/services/audio_pipeline.py:11` — same module-level `whisper_local` import.
- `app/services/news_reranker.py:9-10` — module-level `import torch`;
  `sequential_task_processor.py:51` imports `warm_news_reranker_model` from it.

Changes:

- In `whisper_local.py`, move `torch`/`whisper` imports inside the class methods that
  need them (`_get_device`, `_load_model`, `cleanup_service`). (This file is largely
  replaced in Phase 3 anyway; do the minimal lazy-import here so Phase 1 ships
  independently.)
- In `podcast_workers.py` and `audio_pipeline.py`, move
  `from app.services.whisper_local import get_whisper_local_service` into the
  functions/methods that call it (e.g. `PodcastTranscribeWorker._get_transcription_service`).
- In `news_reranker.py`, move `import torch` / `torch.nn.functional` inside the
  functions that use them so importing the module (for `warm_news_reranker_model`)
  does not load torch. Warm function must stay a no-op when
  `news_list_reranker_enabled` is false (it is false by default).
- Verification: `uv run python -c "import sys; import app.pipeline.sequential_task_processor; assert 'torch' not in sys.modules"`
  must pass. Add a small test under `tests/` that asserts torch is not in
  `sys.modules` after importing the processor module, so this can't regress.

Expected effect: ~230 MB RSS saved in every process except `media`.

### 1b. Embeddings via OpenRouter

`app/services/news_embeddings.py` already supports API embeddings: model specs with
the `openrouter:` prefix route through `encode_texts_with_embedding_model` →
OpenRouter HTTP, no local model. Call sites (`app/services/news_relations.py`) encode
query + candidates together per call, so there is no persisted-vector compatibility
concern — but VERIFY that nothing stores embeddings across model changes
(grep for persisted vectors / pgvector usage) before switching.

Changes:

- `app/core/settings.py`: switch `news_embedding_model` default from
  `"Qwen/Qwen3-Embedding-0.6B"` to an `openrouter:`-prefixed spec. Check OpenRouter's
  catalog for the current best small embedding model (e.g. a Qwen3-embedding hosted
  variant or `openai/text-embedding-3-small`); pick one with reasonable dims/cost and
  note the choice in this doc. Keep the local path working when a non-prefixed model
  is configured.

  DECIDED (implemented): `openrouter:qwen/qwen3-embedding-8b` — the same family as the
  local `Qwen/Qwen3-Embedding-0.6B` default it replaces, and already the model
  `scripts/compare_news_embedding_models.py` benchmarks the local path against. Any
  value without the `openrouter:` prefix still loads locally.
- `warm_news_embedding_model()` must become a no-op for `openrouter:` specs (there is
  nothing to warm), and `SequentialTaskProcessor.__init__` should skip warming
  accordingly. Consider flipping `news_list_warm_embeddings` default to False since
  the default model no longer needs warming.
- The reranker stays as-is (disabled by default, local torch path only loads lazily now).

Expected effect: ~1–2.5 GB × 4 saved (content workers no longer each hold the 0.6B model).

### Phase 1 gates

- `ruff check` on touched files.
- `pytest` for `tests/` modules covering podcast workers, audio pipeline,
  news_embeddings/news_relations, plus the new no-torch-import test.
- Measure and record RSS before/after:
  `uv run python -c "import resource, app.pipeline.sequential_task_processor as _; print(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1e6)"`.

## Phase 2 — Threaded per-queue workers

### Design

New `ThreadedTaskProcessor` (suggested location: `app/pipeline/threaded_task_processor.py`)
that owns N inner claim-loops for ONE queue:

- **Main thread**: installs SIGINT/SIGTERM handlers (signal handlers must only be
  installed in the main thread), creates the shared services, spawns N worker threads,
  then waits. On shutdown: set a shared stop flag, wake all idle waiters, join threads
  with a timeout, log per-thread processed counts.
- **Worker threads**: each runs the existing sequential loop logic
  (claim → process → finalize → idle-wait). Refactor `SequentialTaskProcessor` so the
  loop body is reusable without installing signals — e.g. add an
  `install_signal_handlers: bool = True` parameter to `run()`, or extract the loop
  into a method the threaded wrapper drives. Prefer the smallest refactor that keeps
  `SequentialTaskProcessor` working standalone (it stays the `--threads 1` path and
  the `run_single_task` test path).
- **Worker identity**: today `worker_id = f"{queue}-processor-{worker_slot}"`. Extend to
  `f"{queue}-processor-{worker_slot}-t{thread_index}"` so `locked_by`, lease heartbeats,
  and log lines distinguish threads. Check nothing parses worker_id format
  (grep `locked_by` and watchdog code in `scripts/watchdog_queue_recovery.py`).
- **Shared LISTEN connection**: today each processor opens its own psycopg LISTEN
  connection (`_ensure_queue_listener`). With N threads that would be N connections
  per process. Refactor to one listener per process: a small
  `QueueNotificationListener` that owns the psycopg connection and a
  `threading.Condition`; idle worker threads wait on the condition with a timeout
  (preserving the current poll-fallback cadence: 0.1 s startup phase, 1 s active,
  5 s backed-off), and the listener thread notifies all waiters on any NOTIFY.
  Keep the existing graceful fallback to pure polling when psycopg/LISTEN is
  unavailable.
- **QueueService thread safety**: `QueueService._retry_bucket_cursor` and
  `_retry_bucket_cache` are plain dicts mutated per dequeue — either give each thread
  its own `QueueService` instance (simplest; they share nothing else that matters) or
  add a `threading.Lock` around cursor/cache access. Choose one and document it.
  The lease-heartbeat contextmanager is already per-task and thread-safe.
- **Whisper single-flight (media)**: process-wide `threading.Semaphore(1)` around the
  transcription call in the media handlers (`process_podcast_media` /
  `transcribe` / `transcribe_tweet_video` paths). With media threads=3, download +
  ffmpeg-normalize of the next episodes overlap the current transcription — that
  pipelining is the media throughput win; concurrent Whisper runs are not.
- **crawl4ai**: `_ReusableCrawlerManager` in
  `app/processing_strategies/html_strategy.py` holds an RLock across each crawl, so
  HTML extraction is serialized per process even with 6 content threads. LEAVE IT
  for this initiative (one shared browser is the memory-friendly behavior; LLM-bound
  tasks still parallelize). Note it as a known cap; a follow-up can allow bounded
  page concurrency on the shared browser if extraction wait-time dominates.

### Configuration

- `app/core/settings.py`: add per-queue thread counts with env overrides, e.g.
  `worker_threads: dict[str, int]` or explicit fields (`worker_threads_content`, …)
  following existing settings conventions. Defaults:
  - `content`: 6
  - `media`: 3 (with Semaphore(1) on transcription)
  - `audio_episode`: 1
  - all other queues (`image`, `onboarding`, `backfill`, `discussion`, `twitter`,
    `chat`, `learning`, `llm`): 4
  - Why `audio_episode` is 1: an episode task already fans out its chunks across
    `elevenlabs_audio_episode_tts_max_workers` (`app/core/settings.py:354`, default 4)
    threads inside the task. Giving the queue 4 claim threads would stack a pool on a
    pool — up to 16 concurrent ElevenLabs calls — for no real gain, since episodes are
    TTS-bound and the existing pool already saturates that. One claim thread keeps
    concurrent TTS calls at today's 4. If episode latency (not throughput) is the
    problem, tune `elevenlabs_audio_episode_tts_max_workers` rather than the queue
    thread count; do not raise both.
- `scripts/run_workers.py`: add `--threads N` (default: per-queue setting). Wire to
  `ThreadedTaskProcessor`; `--threads 1` must behave exactly like today.
- `docker/run-worker.sh`: pass through an optional threads env
  (e.g. `WORKER_THREADS_<QUEUE>` or a generic `--threads "${WORKER_THREADS:-}"`).

### DB connection budget (must-do, easy to miss)

- `app/core/db.py` sizes the pool from `database_pool_size` / `database_max_overflow`.
  Per worker process the requirement is roughly:
  `threads (each holds a session during task processing) + heartbeat threads (short
  bursts) + listener (separate psycopg conn, not pooled) + margin`.
  Set per-process pool to `threads + 4` with overflow `threads` (computed in
  `run_workers.py` from the resolved thread count, unless explicitly configured).
- Total across 11 worker processes with the defaults above ≈ 42 steady threads plus
  bursts — check Postgres `max_connections` (default 100) also serves the API server
  and scheduler. Verify prod's setting; if it's at 100, either raise it in the
  Postgres config used by the Docker runtime or size pools more conservatively.
  Record the decision in this doc.

  DECIDED (implemented): the Docker runtime was on the stock `max_connections=100`.
  Worker steady state is ~42 claim threads + 11 listener connections, and each claim
  thread's lease heartbeat can briefly hold a second session, so the worst case sits
  right at the old ceiling before the API server is counted. `docker/run-postgres.sh`
  now starts Postgres with `max_connections=${POSTGRES_MAX_CONNECTIONS:-200}`.

### Process topology changes

- `docker/supervisord.worker-programs.conf`: `worker_content` drops
  `numprocs=4` → single process (worker-slot 1). All programs stay one-per-queue.
- `supervisor.conf` (host) mirrors the same change.
- `scripts/start_services.sh` / `scripts/dev.sh`: the `--content-workers 4`-style
  flags become thread counts passed to a single process per queue (keep flag names
  working if cheap, or rename with clear errors).
- `tests/scripts/test_supervisor_queue_config.py` derives expected worker coverage
  from `TaskQueue` — update its expectations for the new single-process-per-queue
  shape and keep it guarding config drift.
- `scripts/watchdog_queue_recovery.py`: verify it keys off queue/task state, not
  process count (expected: no change needed).

### Phase 2 gates

- Unit tests: threaded processor spawns N loops, drains a seeded queue faster than
  1 thread, clean shutdown on stop flag, no duplicate task execution under
  contention (existing SKIP LOCKED tests may already cover claim exclusivity —
  extend, don't duplicate).
- Concurrency smoke: seed ~50 fake fast tasks in a test DB, run the threaded
  processor with 6 threads, assert all complete exactly once.
- `ruff check` + full `pytest tests/` for queue/pipeline/scripts modules.
- Manual: `scripts/start_services.sh workers` locally, watch logs for lease
  conflicts, listener behavior, graceful Ctrl+C.

## Phase 3 — faster-whisper + media pipelining

- Replace `openai-whisper` with `faster-whisper` (CTranslate2) in
  `app/services/whisper_local.py`, preserving the public interface:
  `transcribe_audio(path: Path) -> tuple[str, str | None]` and
  `cleanup_service()`. Map `whisper_model_size` settings values (tiny/base/small/…)
  directly; use `compute_type="int8"` on CPU, `float16` on CUDA. Segment iterator →
  join text; language from `info.language`.
- Keep the singleton + lazy-load pattern and the Phase 2 single-flight semaphore.
- Dependencies (`pyproject.toml`): add `faster-whisper`; remove `openai-whisper` if
  nothing else imports `whisper`; torch stays only for the (disabled-by-default)
  local reranker/embedding fallback paths — do NOT remove torch in this initiative.
- MPS note: current code falls back MPS→CPU; faster-whisper is CPU/CUDA only, so the
  device logic simplifies to cuda-if-available-else-cpu (int8). Delete the MPS
  workaround paths.
- Tests: existing transcription tests updated; add a fixture-based test with a tiny
  audio file if one exists in `tests/` fixtures (check first — do not add large
  binaries).
- Gate: transcribe a short real audio file locally and compare output sanity vs the
  old path; record rough timing in the PR description.

## Rollout / rollback

- Ship phases as separate commits (or PRs) in this order; each phase is
  independently deployable.
- Rollback lever for Phase 2: set all `WORKER_THREADS_*` env values to 1 — behavior
  is then identical to today's sequential workers (topology change aside).
- After deploy: `uv run -m admin logs exceptions --limit 20`, queue stats via the
  admin CLI, and container RSS (`docker stats`) before/after for the memory claim.
- Watch for: Postgres connection exhaustion, ElevenLabs/LLM provider rate limits at
  higher concurrency, crawl4ai browser instability under serialized-but-busier use.

## Explicit non-goals

- No multi-queue worker processes, no queue consolidation.
- No asyncio rewrite of handlers.
- No external broker (Redis/RabbitMQ/etc.).
- No hosted transcription API — local faster-whisper only.
- No change to `elevenlabs_audio_episode_tts_max_workers` (stays 4, in-task).
- No crawl4ai concurrency changes (follow-up candidate).
- No torch removal from the repo.
