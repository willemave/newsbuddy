# Admin Web Refactor Implementation Plan

Date: 2026-05-10

## Goal

Move the server-rendered admin site out of root-level `templates/` and `static/`, make admin web ownership explicit under `app/admin_web/`, and delete stale public web remnants now that Newsly's product UI is iOS-only.

The iOS and machine-facing APIs remain in place. The generated-content image URL space, `/static/images/...`, remains stable because API responses and the iOS client depend on it.

## Target Shape

```text
app/admin_web/
  __init__.py
  router.py
  templates.py
  auth.py
  dashboard.py
  logs.py
  usage.py
  evals.py
  onboarding.py
  api_keys.py
  feedback.py
  insight_reports.py
  templates/
  static/
    css/
```

## Implementation Checklist

- [x] Create this initiative plan and keep it updated during implementation.
- [x] Move admin templates from root `templates/` into `app/admin_web/templates/`.
- [x] Move admin CSS from root `static/css/` into `app/admin_web/static/css/`.
- [x] Delete stale public web templates and legacy article-reader JavaScript.
- [x] Preserve `/static/images/...` as generated-content image storage.
- [x] Mount admin assets from `/admin/static`.
- [x] Move admin login/logout out of the mobile auth router while keeping `/auth/admin/*` URLs stable.
- [x] Split admin web controllers into focused modules under `app/admin_web/`.
- [x] Update Tailwind/package config for the new asset paths and rebuild CSS.
- [x] Update scripts, ignores, and docs that assume root `templates/` or `static/`.
- [x] Update tests and monkeypatch targets for the new module ownership.
- [x] Run focused lint and route/template tests.

## Detailed Steps

1. **Package setup**
   - Add `app/admin_web/__init__.py`.
   - Add `app/admin_web/templates.py` with package-local `Jinja2Templates`.
   - Keep the markdown filter and `static_version` global.

2. **Template and asset relocation**
   - Move admin-live templates into `app/admin_web/templates/`.
   - Rename `base.html` to stay as the admin base template.
   - Delete `articles.html` and `detailed_article.html`.
   - Move CSS source/output to `app/admin_web/static/css/`.
   - Remove the base template include for `static/js/main.js`.
   - Point CSS links to `/admin/static/css/app.css`.

3. **Static mounts**
   - Keep `/static/images` mounted from `settings.images_base_dir`.
   - Mount `/admin/static` from `app/admin_web/static`.
   - Stop creating root `static/` at app import time.

4. **Controller split**
   - Move dashboard/cost/readout routes to `app/admin_web/dashboard.py`.
   - Move logs/errors routes to `app/admin_web/logs.py`.
   - Move vendor usage routes to `app/admin_web/usage.py`.
   - Move eval routes to `app/admin_web/evals.py`.
   - Move onboarding preview routes to `app/admin_web/onboarding.py`.
   - Move API key routes to `app/admin_web/api_keys.py`.
   - Move feedback routes to `app/admin_web/feedback.py`.
   - Move insight report routes to `app/admin_web/insight_reports.py`.
   - Move admin login/logout routes to `app/admin_web/auth.py` while keeping the same external URLs.
   - Aggregate admin routers in `app/admin_web/router.py`.

5. **Config, docs, and tests**
   - Update Tailwind paths in `package.json`, `tailwind.config.js`, and CSS `@source`.
   - Add a non-watch CSS build script.
   - Update OpenAPI export scripts that created root `static/`.
   - Update `.gitignore`; keep `.dockerignore`'s existing `static/images` exclusion because admin assets now live under `app/admin_web/static`.
   - Update docs for the new template/static locations.
   - Update tests importing or monkeypatching `app.routers.admin`, `app.routers.logs`, or `app.routers.auth` admin functions.

## Validation

```bash
npm run build:css
uv run ruff check app/admin_web app/main.py app/routers/auth.py scripts/export_openapi_schema.py scripts/export_agent_openapi_schema.py tests/test_main.py tests/routers/test_admin.py tests/routers/test_admin_dashboard_readouts.py tests/routers/test_admin_eval.py tests/routers/test_admin_onboarding_lane_preview.py tests/routers/test_admin_usage.py tests/routers/test_logs.py tests/routers/test_auth.py
uv run pytest tests/test_main.py tests/routers/test_content.py tests/routers/test_admin.py tests/routers/test_admin_dashboard_readouts.py tests/routers/test_admin_eval.py tests/routers/test_admin_onboarding_lane_preview.py tests/routers/test_admin_usage.py tests/routers/test_logs.py tests/routers/test_auth.py -v
```
