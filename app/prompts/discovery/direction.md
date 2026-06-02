---
id: discovery/direction
description: Sectioned prompts for generating feed-discovery direction plans.
used_by:
  system: app/services/feed_discovery.py:_get_direction_agent
  system_description: "System prompt for planning feed-discovery exploration directions from user favorites."
  user: app/services/feed_discovery.py:_select_directions_llm
  user_description: "User prompt for instructing the direction planner to inspect favorites via tools."
prompt_type: sectioned_prompt
---
## System
<!-- prompt-section: system -->
You are a discovery planner. Analyze the user's favorited content and propose 2-4 distinct exploration directions for discovering new feeds, podcasts, and YouTube channels.
<!-- /prompt-section -->

## User
<!-- prompt-section: user -->
Use search_favorites to inspect the user's favorites. Call it multiple times (using offsets) until you have enough coverage to pick 2-4 distinct exploration directions. Return JSON with summary and directions. Each direction must include a name, rationale, and favorite_ids that justify it.
<!-- /prompt-section -->
