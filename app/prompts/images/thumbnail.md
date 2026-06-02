---
id: images/thumbnail
description: Sectioned prompts and style fragments for news thumbnail generation.
used_by:
  style_abstract: app/services/image_generation.py:_build_news_thumbnail_prompt
  style_abstract_description: "Style fragment for abstract or highly conceptual news thumbnail image prompts."
  style_stylized: app/services/image_generation.py:_build_news_thumbnail_prompt
  style_stylized_description: "Style fragment for moderately abstract news thumbnail image prompts."
  style_simple: app/services/image_generation.py:_build_news_thumbnail_prompt
  style_simple_description: "Style fragment for concrete, clean news thumbnail image prompts."
  news_user: app/services/image_generation.py:_build_news_thumbnail_prompt
  news_user_description: "Image prompt template for subtle square editorial thumbnails."
  script_user: scripts/generate_thumbnails.py:build_interesting_prompt
  script_user_description: "Image prompt template for the legacy thumbnail generation utility script."
prompt_type: sectioned_prompt
---
## Style Abstract
<!-- prompt-section: style_abstract -->
- Abstract, conceptual representation
- Simple geometric shapes
- Plenty of negative space
- Minimalist composition
<!-- /prompt-section -->

## Style Stylized
<!-- prompt-section: style_stylized -->
- Stylized, understated illustration
- Simple shapes and forms
- Subtle metaphorical imagery
- Balanced, calm composition
<!-- /prompt-section -->

## Style Simple
<!-- prompt-section: style_simple -->
- Clean, simple illustration style
- Recognizable subjects, minimal detail
- Quiet visual hierarchy
- Refined editorial aesthetic
<!-- /prompt-section -->

## News User
<!-- prompt-section: news_user -->
Create a subtle editorial thumbnail illustration.

CONTENT:
Title: $title
Summary: $overview
Key themes: $key_themes

VISUAL REQUIREMENTS:
$style_direction
- No text, logos, or watermarks
- Square 1:1 aspect ratio
- Muted, subtle color palette
- Soft contrast, understated aesthetic
- Clean and minimal$tension_instruction

MOOD: $mood

Create a refined, elegant thumbnail image.
<!-- /prompt-section -->

## Script User
<!-- prompt-section: script_user -->
Create a striking editorial thumbnail illustration.

CONTENT:
Title: $title
Summary: $overview
Key themes: $key_themes

VISUAL REQUIREMENTS:
$style_direction
- No text, logos, or watermarks
- Square 1:1 aspect ratio
- Muted, subtle color palette
- Soft contrast, understated aesthetic
- Clean and minimal$tension_instruction

MOOD: $mood

Create a refined, elegant thumbnail image.
<!-- /prompt-section -->
