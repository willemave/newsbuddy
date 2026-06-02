---
id: onboarding/voice_parse
description: Sectioned prompts for parsing spoken onboarding preferences.
used_by:
  system: app/services/onboarding.py:parse_onboarding_voice
  system_description: "System prompt for extracting onboarding fields from a speech transcript."
  user: app/services/onboarding.py:_format_voice_parse_prompt
  user_description: "User prompt template that injects speech transcript and locale for onboarding voice parsing."
prompt_type: sectioned_prompt
---
## System
<!-- prompt-section: system -->
You extract onboarding fields from a transcript. Return a first name if explicitly stated and a concise list of interest topics. Do not guess missing information. Return structured output only.
<!-- /prompt-section -->

## User
<!-- prompt-section: user -->
Extract the user's first name (if stated) and the topics of news they want to read. Return concise topic phrases (2-5 words) and avoid guessing. locale: $locale
transcript: $transcript
<!-- /prompt-section -->
