# app/

Source folder: `app`

## Purpose
Application root wiring for the FastAPI server, shared constants, and the Jinja environment bridge used by admin pages.

## Runtime behavior
- Bootstraps FastAPI with lifespan-based startup, request logging, validation handlers, static mounts, and router registration.
- Keeps runtime-wide constants such as worker ID generation and shared path helpers close to the app entrypoint.
- Binds the repo-level `templates/` directory into a reusable Jinja environment via `app/templates.py`.

## Inventory scope
- Direct file inventory for `app`.
- The generated table omits `.DS_Store` and other filesystem noise.
- This doc covers direct files in `app/`. Subpackages are documented separately.
- The empty `app/templates/` directory is not part of the active Jinja rendering path; admin templates live in the repo-level `templates/` directory.

## Modules and files
| File | Key symbols | Notes |
|---|---|---|
| `app/__init__.py` | n/a | Supporting module or configuration file. |
| `app/constants.py` | `generate_worker_id` | Application-wide constants and defaults. |
| `app/main.py` | `lifespan`, `validation_exception_handler`, `admin_auth_redirect_handler`, `log_requests`, `root_redirect`, `health_check` | Functions: `lifespan`, `validation_exception_handler`, `admin_auth_redirect_handler`, `log_requests`, `root_redirect`, `health_check` |
| `app/templates.py` | `markdown_filter` | Functions: `markdown_filter` |
