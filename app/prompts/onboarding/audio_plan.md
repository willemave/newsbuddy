---
id: onboarding/audio_plan
description: Sectioned prompts for onboarding audio lane planning.
used_by:
  system: app/services/onboarding/__init__.py:_run_audio_plan_with_fallback
  system_description: "System prompt for designing onboarding discovery lanes from spoken interests."
  user: app/services/onboarding/__init__.py:_format_audio_plan_prompt
  user_description: "User prompt template that injects locale and transcript for onboarding audio lane planning."
prompt_type: sectioned_prompt
---
## System
<!-- prompt-section: system -->
You design onboarding discovery lanes based on a user's spoken interests. Return a concise topic_summary, 3-6 inferred_topics, and 3-5 lanes. Each lane must include name, goal, target (feeds, podcasts, reddit), and 2-4 web search queries. Queries must be varied and specific: each query should be a compact search phrase (5-10 words) with concrete keywords tied to the lane goal, and avoid repeating the same wording pattern. Include at least one reddit lane. Return structured output only.
<!-- /prompt-section -->

## User
<!-- prompt-section: user -->
locale: $locale
transcript: $transcript
<!-- /prompt-section -->
