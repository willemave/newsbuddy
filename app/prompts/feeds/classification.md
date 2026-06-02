---
id: feeds/classification
description: Sectioned prompts for classifying detected feed candidates.
used_by:
  system: app/services/feed_detection.py:classify_feed_type_with_llm
  system_description: "System prompt for classifying RSS and Atom feeds by URL and page metadata."
  user: app/services/feed_detection.py:_build_classification_prompt
  user_description: "User prompt template that injects feed URL and discovery page metadata for feed classification."
prompt_type: sectioned_prompt
---
## System
<!-- prompt-section: system -->
You classify RSS/Atom feeds by inspecting the feed URL and page metadata. Return structured output that matches the schema.
<!-- /prompt-section -->

## User
<!-- prompt-section: user -->
Classify this RSS/Atom feed based on the feed URL and the page it was found on.

Feed URL: $feed_url
Page URL: $page_url
$title_line
Classify as one of:
- "substack": Substack newsletter. Substack publications may use custom domains
  (e.g., chinatalk.media, stratechery.com) but are still Substack-powered.
  Look for substack.com in the feed URL, or indicators that this is a newsletter.
- "podcast_rss": Podcast feed with audio episodes. Look for podcast hosting platforms
  (anchor.fm, transistor.fm, libsyn, buzzsprout, simplecast, captivate, podbean, spreaker)
  or keywords like podcast/episode in the URL.
- "atom": Generic blog or news RSS feed that doesn't fit the above categories.

Return your classification with confidence score and brief reasoning.
<!-- /prompt-section -->
