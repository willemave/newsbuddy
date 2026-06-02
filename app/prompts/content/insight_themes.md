---
id: content/insight_themes
description: Sectioned prompts for extracting insight-report themes from saved knowledge.
used_by:
  system: app/services/insight_report.py:extract_themes
  system_description: "System prompt for grouping a reader's saved library into recurring insight-report themes."
  user: app/services/insight_report.py:extract_themes
  user_description: "User prompt template that injects saved library items for insight-report theme extraction."
prompt_type: sectioned_prompt
---
## System
<!-- prompt-section: system -->
You group a reader's saved articles into a small number of recurring themes. Prefer specific, noun-phrase themes over generic buckets. Each theme should cover multiple items from the library.
<!-- /prompt-section -->

## User
<!-- prompt-section: user -->
Identify $theme_count themes that best organize the following saved items. Return concise noun phrases suitable for a newsletter section heading.

$prompt_blocks
<!-- /prompt-section -->
