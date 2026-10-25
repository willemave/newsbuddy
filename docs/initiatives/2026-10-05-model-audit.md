# Newsly model audit — 2026-10-05

Configuration and production-usage audit, observed around 20:06 UTC (13:06 Pacific).
This is an inventory and compatibility review; no model-quality benchmark or paid provider canary was run.

## Sol switch

The shared new-chat and missing-model default is now `openai:gpt-6.1-sol` locally.
It covers ordinary and assistant quick chats, onboarding follow-up chats, and
chat sessions materialized by share actions. Request model hints and persisted
explicit selections remain authoritative. Existing 5.6 Sol sessions keep their
model and their accurate iOS label. Start a new chat to use the upgraded default
after the backend update is deployed; the current provider choice is disabled
in an existing chat's model menu. Native menus/new-session labels
now show GPT-6.1 Sol, and stored GPT-6 Sol and GPT-5.6 Sol have distinct labels.

The offline pipeline judge default and current Sol evaluation examples now use
`gpt-6.1-sol`; the repository coding-agent preference was updated as well.
Historical evaluation results, compatibility fixtures and applied migrations
were preserved. This change is uncommitted and has not been deployed.

## Active source assignments

All paths below are relative to the repository root. Empty environment overrides
fall back to the source default; persisted or request-specific selections may
choose another model.

| Workload | Default / current source assignment | Override or owner |
| --- | --- | --- |
| New OpenAI chats and missing-model fallback | GPT-6.1 Sol | `rust/crates/newsly-db/src/chat.rs`, `chat_tasks.rs`; explicit session/model hint; no chat-model env lookup |
| Summarization | GPT-6 Luna | `SUMMARIZATION_MODEL`; `newsly-providers/src/summarization.rs` |
| Content analysis | GPT-6 Luna | `CONTENT_ANALYSIS_MODEL`; `content_analysis.rs` |
| News enrichment and relevant links | GPT-6 Luna | `NEWS_PROCESSING_MODEL`, `NEWS_LINK_SELECTION_MODEL`; `news_item.rs` |
| Briefing composition, dig and lens/category naming | GPT-6 Luna | `BRIEFING_MODEL`, `BRIEFING_CATEGORY_NAMING_MODEL`; `briefing_composition.rs`, `briefing_dig.rs` |
| Onboarding discovery | GPT-6 Luna | `onboarding_model.rs`; fixed low effort, priority tier, storage disabled |
| Learning Deck generation | Claude Sonnet 5.5 (since 2026-10-22) | `LEARNING_DECK_MODEL`; `newsly-worker/src/learning_deck/agent.rs` |
| Share-action execution | GPT-6 Luna | `LLM_TASK_MODEL`; `newsly-worker/src/share_actions/agent.rs` |
| Tweet suggestions and discussion summaries | GPT-6 Luna | `TWEET_SUGGESTION_MODEL`, `DISCUSSION_SUMMARY_MODEL`; `content_misc.rs` |
| Narration script generation | GPT-6 Luna | `AUDIO_EPISODE_MODEL`; `newsly-worker/src/config.rs` |
| Narration synthesis | ElevenLabs `eleven_flash_v2_5` | `ELEVENLABS_NARRATION_TTS_MODEL`; worker config |
| Audio transcription | OpenAI `gpt-transcribe` | Fixed in `openai_transcription.rs` |
| Images | Runware `bytedance:seedream@5.0-lite` | `INFOGRAPHIC_GENERATION_PROVIDER`, `INFOGRAPHIC_GENERATION_MODEL`, `INFOGRAPHIC_GENERATION_FALLBACK_MODEL`; `image_generation.rs` |
| Google image option | `gemini-3.1-flash-image-preview` | `IMAGE_GENERATION_MODEL`, `IMAGE_GENERATION_FALLBACK_MODEL`; `image_generation.rs` |
| News and Briefing embeddings | OpenRouter `qwen/qwen3-embedding-8b` | `NEWS_EMBEDDING_MODEL`, `BRIEFING_CATEGORY_EMBEDDING_MODEL`; domain default and provider/scheduler config |
| Optional Claude chat | `claude-opus-4-6` | API `chat.rs` model selection |
| Optional Claude tweet generation | `claude-sonnet-4-5` | `TWEET_SUGGESTION_ANTHROPIC_MODEL`; `content_misc.rs` |
| Optional OpenRouter chat | `deepseek/deepseek-v4-flash` | API `chat.rs` model selection |
| Deep Research legacy backend option | `o4-mini-deep-research-2025-06-26` | API/chat worker; hidden from new iOS provider selection |

Provider paths in the table are under `rust/crates/newsly-providers/src/` unless
otherwise specified. Specialized models and non-Sol choices were not changed.

## Live production evidence

Both `newsly-workers` and active `newsly-api-blue` use image
`081a0c6df9f238b84a30ffd979712fd469311fa0`.
The source switch above is newer local work, not production state.

Read-only evidence was collected through the container's Rust `newsly-admin`
usage summaries and bounded database queries. Model-specific runtime environment
inspection returned no overrides for routine text; it confirmed Runware Seedream
5.0 Lite images. Model names with and without `openai:` are combined below.

| Observed model | Last 24 hours: calls | Last 7 days: calls |
| --- | ---: | ---: |
| GPT-6 Luna | 2,413 | 14,157 |
| GPT-5.6 Sol | 11 | 11 |
| GPT-6.1 Sol | 0 | 0 |
| Qwen3 Embedding 8B | 2,932 | 16,195 |
| GPT-Transcribe | 2 | 58 |
| Seedream 5.0 Lite | 5 | 38 |

All eleven recent Sol calls are recorded under `assistant_chat`, and all are
unpriced with `missing_applicable_model_rate`. Three nonarchived 5.6 Sol sessions
exist, all created in the last seven days. Other nonarchived session models
include older GPT, Claude and Gemini choices, with no newly created sessions in
those groups during that window. Existing sessions may still request those models;
this is retained state rather than evidence of recent traffic.

The last-24-hour known cost subtotal across vendors is **$1.63745789**, with
**12 unpriced records** (11 older Sol calls and one sandbox observation). The
last-seven-day known subtotal is **$13.80012133**, with **17 unpriced records**.
Neither subtotal is a complete total, and neither measures model quality.

Production environment still carries `ELEVENLABS_TTS_MODEL`,
`ELEVENLABS_STT_MODEL_ID`, and `VOICE_HAIKU_MODEL`. No current Rust/client reads
of those exact keys were found; they do not prove the active narration or
transcription model. The current Rust narration key is
`ELEVENLABS_NARRATION_TTS_MODEL` and audio transcription is hardcoded separately.

## Compatibility and pricing

[OpenAI's GPT-6.1 Sol model sheet](https://developers.openai.com/api/docs/models/gpt-6.1-sol)
confirms the exact model identifier and Responses API tool support. Supported
reasoning efforts are low, medium (default), high, xhigh and max; none and minimal
are unsupported. Newsly chat already uses Responses through
`rig_engine/openai_response.rs`, with no explicit reasoning effort or temperature
in its ordinary chat request. No transport change is required by this switch.

Standard rates per million tokens are input **$2**, cache read **$0.10**, cache
write **$2.50**, output **$10**. The existing
`20260930000000_resource_price_catalog.sql` contains those exact 6.1 rates,
long-context rates and observed tier multipliers. The matching 6.1 catalog row
is present in production, effective **2026-09-30 16:37:50 UTC**. No new pricing
migration or historical repricing was added. Exact provider-reported model names
still require matching catalog coverage; dated variants can remain unknown.

## Remaining audit findings

1. **Legacy Sol pricing:** no applicable GPT-5.6 Sol token rate is present live.
   Retained older sessions can continue producing unknown costs. Add a sourced,
   forward-only rate if continued legacy traffic needs pricing; do not estimate
   historical costs using 6.1 prices.
2. **Admin eval inventory drift:** `newsly-api/src/admin_evals.rs` aliases cheap
   to GPT-5.6 Luna, smart OpenAI to GPT-5.6 Terra, and smart Claude to Opus 4.6.
   Its default model set does not measure current production Luna or new Sol.
   A separate scoped update should align those aliases and their public metadata.
3. **Offline pipeline candidate drift:** the candidate default remains
   `openai:gpt-5.6-luna` in `python/evals/src/newsly_evals/pipeline/cli.py`.
   Pass `--model openai:gpt-6-luna` explicitly when evaluating current routine
   production work. The Sol judge default is updated by this change.
4. **Database raw default drift:** production `chat_sessions.llm_model` still
   defaults to `openai:gpt-5.4` at the database level. Current application insert
   paths explicitly bind their selected/shared model, so this does not override
   the new-chat switch. Consider a forward migration if raw insert defaults are
   intended to track the product; do not edit the historical baseline migration.
5. **Optional provider modernization:** Claude chat, Claude tweets, OpenRouter
   chat and Deep Research retain the source assignments above. These require
   separate provider availability/quality checks before changing their models.
6. **Installed-client labels:** older app builds still advertise GPT-5.6 Sol in
   the provider menu and show generic GPT for unknown 6.1 session models. The
   native app update is required for accurate new-model labels. Existing 5.6
   session labels remain correct and those sessions are not automatically upgraded.
7. **Live 6.1 verification:** no paid canary, model benchmark, deployment or
   existing-session migration was run. After release, verify a new chat and
   model-specific usage/pricing; backend and native-client updates are separate.

## Validation

- Rust format and diff checks: passed.
- Python eval package: Ruff and MyPy passed; 43 tests passed, 5 skipped.
- iOS Simulator: build and six `ChatModelProviderTests` passed, including current
  6.1 labels and retained legacy Sol labels.
- Rust: both focused API model-selection tests passed; `newsly-db` and
  `newsly-api` all-target warning-denied Clippy passed with `SQLX_OFFLINE=true`.
- Public contract drift check: passed; no generated contract changes required.
- Independent read-only Oracle Fable review completed; response `modelUsage`
  contains only `claude-opus-5-5`. No must-fix implementation findings. Verified
  its installed-client labeling and disabled same-provider-menu findings and
  incorporated them into this report. Log status finalized before handoff.
- No full release gate, paid provider benchmark/canary or deployment performed.
