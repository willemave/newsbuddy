---
id: discovery/lane
description: Sectioned prompts for planning feed-discovery search lanes.
used_by:
  system: app/services/feed_discovery.py:_plan_lanes_llm
  system_description: "System prompt for turning discovery directions into targeted search lanes."
  user: app/services/feed_discovery.py:_plan_lanes_llm
  user_description: "User prompt template that injects direction-plan JSON for lane planning."
prompt_type: sectioned_prompt
---
## System
<!-- prompt-section: system -->
You design discovery lanes with targeted search queries. Create 3-6 lanes across feeds, podcasts, and YouTube. Each lane includes 2-4 concrete queries.
<!-- /prompt-section -->

## User
<!-- prompt-section: user -->
Use the directions below to craft lanes. Mix in smallweb and Substack where relevant. Include at least one YouTube-focused lane if any direction suggests it. Include at least two podcast-focused lanes and ensure some queries mention podcast RSS feeds. Prefer generic queries like 'podcast', 'podcast RSS', or 'RSS feed' that can surface both single episodes and full podcast feeds. Avoid platform brand names except Apple Podcasts is allowed when it helps surface show pages we can resolve to RSS.

Directions: $direction_plan_json
<!-- /prompt-section -->
