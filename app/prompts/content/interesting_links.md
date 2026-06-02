---
id: content/interesting_links
description: Sectioned prompts for selecting relevant external links from article HTML.
used_by:
  system: app/services/interesting_external_links.py:select_interesting_external_links
  system_description: "System prompt for selecting high-signal outbound links from deterministic article candidates."
  user: app/services/interesting_external_links.py:_build_selection_prompt
  user_description: "User prompt template that injects article metadata and outbound-link candidates."
prompt_type: sectioned_prompt
---
## System
<!-- prompt-section: system -->
Select useful outbound links from an article.

Return only links that help a reader understand, verify, or continue from the article:
- primary sources, papers, datasets, documentation, tools, source repositories,
  company/product pages, or important related context
- exclude navigation, homepages, share links, login/signup/subscribe pages, ads,
  generic social follow links, and weak citations
- choose from the provided candidates only; never invent a URL
- prefer fewer high-signal links over a long list
<!-- /prompt-section -->

## User
<!-- prompt-section: user -->
Article title: $title
Article URL: $source_url

Candidate outbound links:
$candidate_payload

Select up to $max_selected_links links. Use concise titles and reasons.
<!-- /prompt-section -->
