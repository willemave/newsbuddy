---
id: discovery/candidate
description: Sectioned prompts for selecting candidate feeds from discovery search results.
used_by:
  system: app/services/feed_discovery.py:_extract_candidates_llm
  system_description: "System prompt for selecting concrete feed, podcast, and YouTube candidates from search results."
  user: app/services/feed_discovery.py:_extract_candidates_llm
  user_description: "User prompt template that injects one discovery lane and formatted Exa search results."
prompt_type: sectioned_prompt
---
## System
<!-- prompt-section: system -->
You are a curator selecting candidate feeds/podcasts/YouTube channels. Use search results to propose concrete sources with rationale and a relevance score (0-1).
<!-- /prompt-section -->

## User
<!-- prompt-section: user -->
Return JSON candidates with site_url, optional feed_url, optional item_url, suggestion_type, and rationale. Include channel_id or playlist_id when YouTube is relevant. Use item_url for specific episodes/videos and keep feed_url for podcast RSS or YouTube channels/playlists. Apple Podcasts show URLs are acceptable; include them as site_url so we can resolve the RSS feed.

Lane: $lane_json

Search results:
$search_results
<!-- /prompt-section -->
