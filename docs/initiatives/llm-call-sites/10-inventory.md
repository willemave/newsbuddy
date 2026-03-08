# LLM call sites and replacement plan

Inventory of every LLM entrypoint and the intended pydantic-ai replacement. Model specs stay unchanged (e.g., `claude-*`, `gpt-*`, `gemini-*`).

## Summarization services
- `app/services/llm_summarization.py` → shared `ContentSummarizer` now handles routing + model resolution via `resolve_model`.
- Provider shims (`openai_llm.py`, `anthropic_llm.py`, `google_flash.py`) now subclass `ContentSummarizer` for compatibility.
- Callers:
  - `app/pipeline/worker.py` → uses `get_llm_service()` (shared summarizer) for article/news payloads.
  - `app/pipeline/sequential_task_processor.py` → uses `get_llm_service()` for summarize tasks.

## Chat / Deep Dive
- `app/services/chat_agent.py` → already pydantic-ai but streaming; refactor to use shared `llm_models.build_pydantic_model` and sync `run_sync` calls, plus updated initial suggestions prompt.
- `app/routers/api/chat.py` → drop NDJSON streaming endpoints; use sync helper that persists messages and returns DTOs.

## Tweet suggestions
- `app/services/tweet_suggestions.py` → already pydantic-ai with Google; swap direct `GoogleModel/Provider/ModelSettings` wiring for shared `llm_models.build_pydantic_model` and reuse prompt helpers.

## Tools
- `app/services/exa_client.py` used as pydantic-ai tool in `chat_agent`; keep tool function but ensure sync tool invocation works with `Agent.run_sync`.

## Planned shared modules
- `app/services/llm_models.py` → central model construction `build_pydantic_model(model_spec: str) -> tuple[Model | str, GoogleModelSettings | None]`.
- `app/services/llm_agents.py` → agent factories (`get_summarization_agent`, tweet generator, chat agent getter) that call `build_pydantic_model` and pull prompts from `llm_prompts`.
