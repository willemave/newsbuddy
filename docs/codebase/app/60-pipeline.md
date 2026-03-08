# app/pipeline/

Source folder: `app/pipeline`

## Purpose
Queue execution runtime: processor loop, task envelopes/results, dispatcher, checkout coordination, and the main content/podcast worker implementations.

## Runtime behavior
- Runs the sequential task processor that claims DB-backed tasks, dispatches handlers, applies retries, and records completion/failure state.
- Coordinates content checkout and worker context so multiple queue consumers can safely share the same task tables.
- Implements the long-form processing workers that fetch source material, select strategies, and hand off to summarization or downstream tasks.

## Inventory scope
- Direct file inventory for `app/pipeline`.

## Modules and files
| File | Key symbols | Notes |
|---|---|---|
| `app/pipeline/__init__.py` | n/a | Pipeline modules for content processing. |
| `app/pipeline/checkout.py` | `CheckoutManager`, `get_checkout_manager` | Types: `CheckoutManager`. Functions: `get_checkout_manager` |
| `app/pipeline/dispatcher.py` | `TaskDispatcher` | Dispatcher for routing tasks to handlers. |
| `app/pipeline/podcast_workers.py` | `PodcastDownloadWorker`, `PodcastTranscribeWorker`, `sanitize_filename`, `get_file_extension_from_url` | Types: `PodcastDownloadWorker`, `PodcastTranscribeWorker`. Functions: `sanitize_filename`, `get_file_extension_from_url` |
| `app/pipeline/sequential_task_processor.py` | `SequentialTaskProcessor` | Sequential task processor for robust, simple task processing. |
| `app/pipeline/task_context.py` | `TaskContext` | Shared dependencies for task handlers. |
| `app/pipeline/task_handler.py` | `TaskHandler`, `FunctionTaskHandler` | Handler protocol and adapters for task processing. |
| `app/pipeline/task_models.py` | `TaskEnvelope`, `TaskResult` | Task models for the sequential pipeline processor. |
| `app/pipeline/worker.py` | `ContentWorker`, `get_llm_service` | Types: `ContentWorker`. Functions: `get_llm_service` |
