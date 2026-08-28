"""Central model tier defaults for LLM-backed features."""

FAST_MODEL_SPEC = "cerebras:zai-glm-4.7"
OPENROUTER_DEEPSEEK_FLASH_MODEL_SPEC = "openrouter:deepseek/deepseek-v4-flash"

PDF_EXTRACTION_MODEL_NAME = "gpt-5.6-luna"
CHEAP_MODEL_SPEC = "openai:gpt-5.6-luna"

SMART_OPENAI_MODEL_NAME = "gpt-5.6-terra"
SMART_MODEL_SPEC = f"openai:{SMART_OPENAI_MODEL_NAME}"

SMART_ANTHROPIC_MODEL_NAME = "claude-opus-4-6"
SMART_ANTHROPIC_MODEL_SPEC = f"anthropic:{SMART_ANTHROPIC_MODEL_NAME}"

ARTICLE_PODCAST_SUMMARY_MODEL_NAME = "gpt-5.6-luna"
ARTICLE_PODCAST_SUMMARY_MODEL_SPEC = f"openai:{ARTICLE_PODCAST_SUMMARY_MODEL_NAME}"

DEEP_RESEARCH_MODEL_NAME = "o4-mini-deep-research-2025-06-26"
DEEP_RESEARCH_MODEL_SPEC = f"deep_research:{DEEP_RESEARCH_MODEL_NAME}"

IMAGE_GENERATION_MODEL_NAME = "gemini-3.1-flash-image-preview"
RUNWARE_INFOGRAPHIC_MODEL_SPEC = "bytedance:seedream@5.0-lite"
