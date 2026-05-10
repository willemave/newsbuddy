# app/admin_web/

Source folder: `app/admin_web`

## Purpose
Server-rendered admin web UI for operators. This is the only web UI surface in the backend; product UI remains iOS and machine-facing APIs remain under `app/routers/api`.

## Runtime behavior
- Aggregates all admin HTML routes in `app/admin_web/router.py` under `/admin`.
- Keeps admin login/logout in `app/admin_web/auth.py` while preserving `/auth/admin/*` URLs.
- Renders Jinja templates from `app/admin_web/templates/`.
- Serves admin CSS from `app/admin_web/static/` at `/admin/static`.
- Keeps generated content images separate at `/static/images`.

## Modules and files
| File | Key symbols | Notes |
|---|---|---|
| `app/admin_web/router.py` | `router` | Aggregates the admin web route modules. |
| `app/admin_web/auth.py` | `admin_login_page`, `admin_login`, `admin_logout` | Admin session routes. |
| `app/admin_web/dashboard.py` | `admin_dashboard` | Dashboard, queue, cost, and operational readouts. |
| `app/admin_web/logs.py` | `list_logs`, `view_log`, `download_log`, `errors_dashboard`, `reset_error_logs` | Log and error diagnostics. |
| `app/admin_web/usage.py` | `vendor_usage_dashboard`, `legacy_llm_usage_redirect` | Vendor and LLM usage dashboards. |
| `app/admin_web/evals.py` | `admin_eval_summaries_page`, `admin_eval_summaries_run` | Summary eval admin routes. |
| `app/admin_web/onboarding.py` | `onboarding_lane_preview_page`, `onboarding_lane_preview` | Onboarding lane preview routes. |
| `app/admin_web/api_keys.py` | `admin_api_keys_page`, `admin_api_keys_create`, `admin_api_keys_revoke` | User API key management. |
| `app/admin_web/feedback.py` | `admin_feedback_page` | User feedback review. |
| `app/admin_web/insight_reports.py` | `admin_insight_reports_page`, `admin_insight_reports_trigger` | Insight report admin routes. |
| `app/admin_web/formatting.py` | `format_user_label`, `format_user_label_with_id` | Shared admin display formatting helpers. |
| `app/admin_web/templates.py` | `templates`, `markdown_filter` | Package-local Jinja environment and filters. |
