# app/services/gateways/

Source folder: `app/services/gateways`

## Purpose
Small dependency gateways that keep workflows and services from binding directly to concrete HTTP, LLM, queue, or object-storage implementations.

## Runtime behavior
- Gateway interfaces are used where orchestration needs a patchable boundary for tests or a provider-agnostic API.
- Object storage supports local filesystem and S3-compatible providers. S3-compatible mutations
  record usage synchronously through a dedicated short-lived database session; latency-sensitive
  reads and existence probes intentionally do not open database sessions, so the vendor ledger is
  not a complete storage request meter.
- Queue gateway wraps `QueueService` for handlers/workflows that need to enqueue or inspect tasks.

## Important files
| File | Purpose |
|---|---|
| `http_gateway.py` | HTTP access abstraction. |
| `llm_gateway.py` | LLM call abstraction. |
| `task_queue_gateway.py` | Queue enqueue/status abstraction over `QueueService`. |
| `object_storage_gateway.py` | Provider-agnostic text/binary object storage with local and S3-compatible implementations. |
| `__init__.py` | Package marker. |

## Integration points
- Content bodies, news article bodies, news item discussions, and Learning Deck artifacts use object storage.
- Pipeline workflows and handlers use the queue gateway to avoid direct queue-service coupling.
