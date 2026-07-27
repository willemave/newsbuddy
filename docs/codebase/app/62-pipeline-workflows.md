# app/pipeline/workflows/

Source folder: `app/pipeline/workflows`

## Purpose
Workflow helpers for the multi-step URL-analysis handler.

## Runtime behavior
- `analyze_url_workflow.py` defines protocols and flow helpers for feed subscription, tweet resolution, URL analysis, instruction link fanout, and instruction payload cleanup.
- Content lifecycle transitions are owned directly by `app/services/content_lifecycle.py`; source
  extraction and summarization orchestration live in `app/pipeline/worker.py`.

## Important files
| File | Purpose |
|---|---|
| `analyze_url_workflow.py` | `TweetResolutionFlowProtocol`, feed subscription flow, URL analysis flow, and instruction fanout support. |
| `__init__.py` | Package marker. |

## Integration points
- `app/pipeline/handlers/analyze_url.py` uses these helpers to keep handler logic readable.
- The workflows depend on service abstractions and queue gateways rather than directly owning persistence.
