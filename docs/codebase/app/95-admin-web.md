# app/admin_web/

Source folder: `app/admin_web`

## Purpose
Server-rendered Jinja admin UI for dashboards, eval tooling, logs/errors, API keys, feedback, onboarding previews, and vendor usage.

## Runtime behavior
- `router.py` mounts dashboard, evals, onboarding lane preview, API keys, feedback, logs/errors, and usage under `/admin`.
- Admin HTML auth is split into `auth.py` and mounted separately at `/auth/admin/login` and `/auth/admin/logout`.
- `templates.py` configures the Jinja environment, static asset versioning, and Markdown rendering filters for diagnostic pages.
- `/admin/llm-usage` redirects to `/admin/vendor-usage`; vendor usage supports provider/model/feature/user/date/limit filters.

## Important files
| File | Purpose |
|---|---|
| `router.py` | Admin router aggregation under `/admin`. |
| `auth.py` | Admin login/logout routes. |
| `dashboard.py` | Main admin dashboard. |
| `evals.py` | Summary/title eval pages. |
| `onboarding.py` | Onboarding lane preview. |
| `api_keys.py` | API key management. |
| `feedback.py` | User feedback listing. |
| `logs.py` | Log/error browser and reset utilities. |
| `usage.py` | Vendor usage/cost dashboard. |
| `formatting.py` | Display formatting helpers. |
| `templates.py` | Jinja environment setup. |
| `templates/` | `api_keys.html`, `base.html`, `dashboard.html`, `errors.html`, `eval_summaries.html`, `feedback.html`, `log_detail.html`, `login.html`, `logs_list.html`, `onboarding_lane_preview.html`, `vendor_usage_list.html`. |
| `static/css/` | Admin CSS (`app.css`, `styles.css`). |

## Integration points
- Admin routes use backend services/repositories directly and render HTML, not JSON DTOs.
- Production log/runtime inspection should generally go through the separate `admin` CLI unless the task is specifically about the web UI.
