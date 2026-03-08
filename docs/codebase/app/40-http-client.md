# app/http_client/

Source folder: `app/http_client`

## Purpose
Resilient low-level HTTP access used by scrapers and URL processors when they need retries, headers, and failure classification outside of higher-level services.

## Runtime behavior
- Provides the `RobustHttpClient` abstraction for guarded GET/HEAD access with retry behavior and structured logging.
- Acts as the network primitive beneath processing strategies and scraping flows that need deterministic fetch behavior.

## Inventory scope
- Direct file inventory for `app/http_client`.

## Modules and files
| File | Key symbols | Notes |
|---|---|---|
| `app/http_client/__init__.py` | n/a | Supporting module or configuration file. |
| `app/http_client/robust_http_client.py` | `RobustHttpClient` | This module provides a robust synchronous HTTP client. |
