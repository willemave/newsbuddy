---
id: chat/dig_deeper
description: Sectioned prompt for the default dig-deeper article chat handoff.
used_by:
  user: app/services/dig_deeper.py:build_dig_deeper_prompt
  user_description: "Default user prompt used to start a dig-deeper chat for a content item."
prompt_type: sectioned_prompt
---
## User
<!-- prompt-section: user -->
Dig deeper into the key points of $title. For each main point, explain reasoning, supporting evidence, and include a bit more detail explaining the point. Also pull out key ideas from the discussion context when available, and add more insights from the discussion, including notable agreements and disagreements. Keep answers concise and numbered.
<!-- /prompt-section -->
