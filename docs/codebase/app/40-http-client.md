# app/http_client/

Source folder: `app/http_client`

## Purpose
Low-level synchronous HTTP helper used where URL processing needs consistent headers, timeouts, redirects, streamed downloads, and targeted retry behavior.

## Runtime behavior
- `RobustHttpClient` wraps `httpx` GET/HEAD calls with default browser-like headers and timeout handling.
- Supports response streaming for large downloads.
- Handles redirects and a narrow hostname-mismatch retry path.
- Broader HTTP error classification lives in `app/services/http.py`; this package is intentionally lower level.

## Important files
| File | Purpose |
|---|---|
| `app/http_client/robust_http_client.py` | `RobustHttpClient` implementation. |
| `app/http_client/__init__.py` | Package marker. |

## Integration points
- `app/processing_strategies/registry.py` shares one client instance across URL processor strategies.
- `app/services/gateways/http_gateway.py` provides a gateway abstraction for workflow code that should not depend on concrete client details.
