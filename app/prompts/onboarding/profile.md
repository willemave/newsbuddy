---
id: onboarding/profile
description: Sectioned prompts for onboarding profile extraction.
used_by:
  system: app/services/onboarding.py:build_onboarding_profile
  system_description: "System prompt for building a concise onboarding profile from user interests and web snippets."
  user: app/services/onboarding.py:_format_profile_prompt
  user_description: "User prompt template that injects onboarding form fields and web results for profile generation."
prompt_type: sectioned_prompt
---
## System
<!-- prompt-section: system -->
You are building a short onboarding profile for a user. Use the provided interests and web snippets to infer a concise profile summary and 3-6 topical interests. Do not invent interests that contradict the user-provided topics. Return structured output only.
<!-- /prompt-section -->

## User
<!-- prompt-section: user -->
first_name: $first_name
interest_topics: $interest_topics

web_results:
$web_results
<!-- /prompt-section -->
