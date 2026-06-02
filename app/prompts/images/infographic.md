---
id: images/infographic
description: Sectioned prompts for article, podcast, and insight-report infographic image generation.
used_by:
  article_user: app/services/image_generation.py:_build_infographic_prompt
  article_user_description: "Image prompt template for no-text 16:9 editorial illustrations for long-form content."
  insight_report_user: app/services/image_generation.py:_build_insight_report_infographic_prompt
  insight_report_user_description: "Image prompt template for no-text 16:9 editorial cover images for insight reports."
  runware_negative: app/services/image_generation.py:ImageGenerationService
  runware_negative_description: "Negative prompt sent to Runware for infographic generation to suppress text, UI, and document-like artifacts."
prompt_type: sectioned_prompt
---
## Article User
<!-- prompt-section: article_user -->
Create a premium no-text editorial illustration for Newsly.

Hard constraints:
- No readable text, letters, numbers, labels, captions, logos, or watermarks
- No poster layout, newspaper layout, document pages, magazine spreads, screenshots, dashboards, or UI chrome
- 16:9 aspect ratio optimized for mobile display
- One dominant visual metaphor or one coherent scene, never a collage
- One focal subject with strong negative space and clear foreground/background separation
- Bold, graphic, and immediately legible at thumbnail size
- Refined editorial palette with 2 to 4 dominant colors

Visual brief:
- Story context: $story_context
- Primary subject: $primary_subject
- Visual metaphor: $visual_metaphor
- Scene direction: $scene_direction
- Supporting cues: $supporting_cues

Output goal:
Create a distinctive editorial image that communicates the story instantly without rendering any words.
<!-- /prompt-section -->

## Insight Report User
<!-- prompt-section: insight_report_user -->
Create a premium no-text editorial cover illustration for a Newsly insight report — a personal weekly briefing synthesized from the reader's saved library.

Hard constraints:
- No readable text, letters, numbers, labels, captions, logos, or watermarks
- No poster layout, newspaper layout, document pages, magazine spreads, screenshots, dashboards, or UI chrome
- 16:9 aspect ratio optimized for mobile display
- One dominant visual metaphor or one coherent scene, never a collage
- One focal subject with strong negative space and clear foreground/background separation
- Bold, graphic, and immediately legible at thumbnail size
- Refined, slightly warmer editorial palette with 2 to 4 dominant colors

Visual brief:
- Story context: $story_context
- Primary subject: $primary_subject
- Visual metaphor: $visual_metaphor
- Scene direction: $scene_direction
- Supporting cues: $supporting_cues

Output goal:
Create a distinctive editorial cover image that reads as a synthesis across the reader's recurring themes, not as a single news article.
<!-- /prompt-section -->

## Runware Negative
<!-- prompt-section: runware_negative -->
readable text, words, letters, numbers, captions, labels, headlines, logos, watermarks, screenshots, website UI, app interface, chart axes, poster, document page, printed page, magazine spread, dashboard, phone screen, tablet screen, desktop monitor, laptop, computer, office workstation
<!-- /prompt-section -->
