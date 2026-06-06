# app/prompts/

Source folder: `app/prompts`

## Purpose
Markdown prompt library for LLM-backed backend features, evals, image generation, scripts, and diagnostics.

## Runtime behavior
- Prompt files are grouped by feature and loaded by prompt-library/service helpers instead of embedding large prompt strings in Python modules.
- Script prompts support local report generation, image benchmarks, Notes request processing, title clustering, and error-analysis workflows.
- The `README.md` documents the prompt-library convention and should be updated when new prompt groups are added.

## Important folders
| Path | Purpose |
|---|---|
| `admin/` | Admin diagnostics and fix-request prompts. |
| `audio/` | Audio episode and transcription prompts. |
| `chat/` | Article, contextual assistant, council, and dig-deeper chat prompts. |
| `content/` | Analyzer, insight report, interesting links, and news-pipeline prompts. |
| `discovery/` | Feed/discovery candidate, direction, and lane prompts. |
| `evals/` | Judge and variant prompts for eval tooling. |
| `feeds/` | Feed classification prompts. |
| `images/` | Infographic and thumbnail generation prompts. |
| `learning_decks/` | Learning Deck agent prompt. |
| `onboarding/` | Audio-plan, fast-discover, profile, and voice-parse prompts. |
| `processing/` | Processing-specific extraction prompts. |
| `scripts/` | Prompt templates consumed by local scripts. |
| `summarization/` | Discussion, editorial, interleaved, long-form, news, and structured summary prompts. |
| `tweets/` | Tweet generation and style prompts. |

## Integration points
- Service helpers such as `app/services/prompt_library.py`, `app/services/llm_prompts.py`, and feature-specific services load these files.
- Prompt changes often need eval or fixture updates because they change production LLM behavior.
