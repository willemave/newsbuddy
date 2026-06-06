# app/queries/

Source folder: `app/queries`

## Purpose
Router-facing read entrypoints and projection adapters. Queries compose repository/service calls and return DTO-ready data for API modules.

## Runtime behavior
- Content list/detail, search, body, narration, discussion, and recently-read queries back the mobile and CLI content surfaces.
- Knowledge, stats, queue health, job status, API-key, LLM-integration, and submission-status queries expose user-scoped operational state.
- News-item adapter queries bridge short-form `news_items` into content-like response shapes where the client expects shared presentation behavior.

## Important files
| File | Purpose |
|---|---|
| `list_content_cards.py`, `search_content_cards.py`, `get_content_detail.py`, `get_content_body.py` | Long-form content list/search/detail/body reads. |
| `get_content_discussion.py`, `get_news_item_discussion.py` | Discussion payload shaping. |
| `get_news_item_body.py`, `news_item_content_adapter.py` | Short-form news body/detail adapters. |
| `get_knowledge_library.py` | Saved Knowledge library reads. |
| `get_recently_read.py`, `get_stats.py` | Recently-read and unread/processing/long-form counts. |
| `get_job_status.py`, `queue_health.py` | Processing task status and queue health summaries. |
| `list_submission_statuses.py` | Submission and feed-subscription outcome status shaping. |
| `list_api_keys.py`, `list_user_llm_integrations.py` | User credential/integration list reads. |
| `search_external_results.py`, `search_mixed.py` | Machine-facing and mixed search surfaces. |
| `get_agent_onboarding_status.py` | Agent onboarding status reads. |
| `get_narration.py` | Narration/audio availability reads. |

## Integration points
- API routers call queries after auth/session dependency resolution.
- Query modules may use repositories, services, and DTO builders, but should avoid mutating state except where a legacy status endpoint explicitly performs cleanup.
