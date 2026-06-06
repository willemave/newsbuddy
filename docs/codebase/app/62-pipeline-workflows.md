# app/pipeline/workflows/

Source folder: `app/pipeline/workflows`

## Purpose
Small workflow adapters for multi-step state transitions used by queue handlers.

## Runtime behavior
- `analyze_url_workflow.py` defines protocols and flow helpers for feed subscription, tweet resolution, URL analysis, instruction link fanout, and instruction payload cleanup.
- `content_processing_workflow.py` is a thin adapter over `app/services/content_lifecycle.py`; the main source extraction/summarization orchestration still lives in `app/pipeline/worker.py`.

## Important files
| File | Purpose |
|---|---|
| `analyze_url_workflow.py` | `TweetResolutionFlowProtocol`, feed subscription flow, URL analysis flow, and instruction fanout support. |
| `content_processing_workflow.py` | Content lifecycle transition helpers used by processing handlers. |
| `__init__.py` | Package marker. |

## Integration points
- `app/pipeline/handlers/analyze_url.py` uses these helpers to keep handler logic readable.
- The workflows depend on service abstractions and queue gateways rather than directly owning persistence.
