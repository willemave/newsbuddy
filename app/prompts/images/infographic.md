---
id: images/infographic
description: Sectioned prompts for article and podcast infographic image generation.
used_by:
  article_user: app/services/image_generation.py:_build_infographic_prompt
  article_user_description: "Image prompt template for no-text 16:9 editorial illustrations for long-form content."
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
- Premium magazine image with tactile, materially believable surfaces
- Purposeful asymmetry, decisive frame fill, and clean negative space
- One surprising material or object derived directly from the story topic
- Refined topic-derived palette with 2 to 4 dominant colors; avoid default purple/cyan tech color schemes
- Avoid generic AI robots, glowing blue circuitry, corporate clip art, and familiar stock metaphors
- If the story centers on a named real person, do not invent or approximate their recognizable face; create a non-literal portrait through their craft, tools, materials, silhouette, or environment

Visual brief:
- Story context: $story_context
- Primary subject: $primary_subject
- Visual metaphor: $visual_metaphor
- Scene direction: $scene_direction
- Supporting cues: $supporting_cues

Output goal:
Create a distinctive editorial image that communicates the story instantly without rendering any words.
<!-- /prompt-section -->

## Runware Negative
<!-- prompt-section: runware_negative -->
readable text, words, letters, numbers, captions, labels, headlines, logos, watermarks, screenshots, website UI, app interface, chart axes, poster, document page, printed page, magazine spread, dashboard, phone screen, tablet screen, desktop monitor, laptop, computer, office workstation
<!-- /prompt-section -->
