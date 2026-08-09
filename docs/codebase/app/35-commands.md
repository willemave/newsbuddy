# app/commands/

Source folder: `app/commands`

## Purpose
Router-facing write/use-case entrypoints. Commands validate use-case intent, coordinate services/repositories, and return API-ready result objects without putting orchestration in routers.

## Runtime behavior
- User submissions and instruction ingestion create or reuse content rows and enqueue analysis/processing tasks.
- Read state and Knowledge actions mutate per-user state through services/repositories.
- Agent onboarding commands start and complete simplified onboarding flows.
- API-key and LLM-integration commands create, revoke, upsert, or delete user-managed credentials.
- Content actions cover news-to-article conversion, download-more-from-series,
  discussion refresh, tweet suggestions, and feedback submission.

## Important files
| File | Purpose |
|---|---|
| `submit_content.py`, `ingest_content.py` | User URL/instruction submission and queue handoff. |
| `convert_news_to_article.py` | Converts a short-form news item into long-form content and enqueues processing. |
| `mark_read.py` | Per-user read/unread actions. |
| `save_to_knowledge.py`, `remove_from_knowledge.py` | Knowledge save/remove use cases. |
| `start_agent_onboarding.py`, `complete_agent_onboarding.py` | Machine-oriented onboarding commands. |
| `create_api_key.py`, `revoke_api_key.py` | User API-key lifecycle. |
| `upsert_user_llm_integration.py`, `delete_user_llm_integration.py` | User-managed LLM provider credential lifecycle. |
| `download_more_from_series.py` | Feed/podcast backfill command. |
| `refresh_content_discussion.py` | Discussion refresh command. |
| `generate_tweet_suggestions.py` | Tweet suggestion generation use case. |
| `submit_feedback.py` | User feedback persistence. |

## Integration points
- Routers call commands after dependency resolution.
- Commands delegate DB details to repositories/services and enqueue async work through `app/services/queue.py` or queue gateways.
