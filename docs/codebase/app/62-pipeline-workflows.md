# app/pipeline/workflows/

Source folder: `app/pipeline/workflows`

## Purpose
Workflow helpers for the multi-step URL-analysis handler.

## Runtime behavior
- `analyze_url_workflow.py` coordinates directly injected flow callables for feed subscription, tweet resolution, URL analysis, instruction link fanout, and instruction payload cleanup.
- Content lifecycle transitions are owned directly by `app/services/content_lifecycle.py`; source
  extraction and summarization orchestration live in `app/pipeline/worker.py`.

## Important files
| File | Purpose |
|---|---|
| `analyze_url_workflow.py` | Feed subscription, tweet resolution, URL analysis, and instruction fanout orchestration. |
| `__init__.py` | Package marker. |

## Integration points
- `app/pipeline/handlers/analyze_url.py` uses these helpers to keep handler logic readable.
- The workflow receives the five concrete flow methods directly and uses the queue gateway rather than owning persistence.
