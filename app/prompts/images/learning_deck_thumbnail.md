---
id: images/learning_deck_thumbnail
description: Direct square editorial artwork prompts for Learning Deck list thumbnails.
used_by:
  user: app/services/image_generation.py:_build_learning_deck_thumbnail_prompt
  user_description: "Recraft prompt for a typographic, source-specific Learning Deck cover."
prompt_type: sectioned_prompt
---
## User
<!-- prompt-section: user -->
Create a square editorial cover tile for a Newsly Learning Deck.

This is a designed presentation cover, not an illustration or cinematic scene. It will be displayed
at 40 by 40 points, so the hierarchy and central mark must remain distinctive at that size.

Visual language:
- Strict Swiss editorial grid with generous negative space
- Off-white paper background, charcoal ink, and one restrained signal-color accent
- Crisp flat shapes, subtle print texture, and no gradients, glow, or 3D rendering
- One bold stenographic system mark derived from the deck context: a compact diagram, notation,
  sequence, branching path, relationship, or structural motif
- Keep that mark extremely simple: thick strokes, few elements, no tiny details

Typography:
- Render the deck title exactly once as: "$title"
- Use a crisp condensed sans serif with strong hierarchy
- You may add at most one short two-to-four-word uppercase descriptor derived from the context
- Do not add any other words, labels, captions, logos, or watermarks

Hard constraints:
- Square 1:1 composition with one dominant typographic hierarchy and one supporting mark
- No people, scenery, devices, screenshots, dashboards, UI chrome, stock-photo aesthetics,
  decorative collage, or multiple competing subjects
- Do not imitate a real brand or publication

Deck title: $title
Primary source: $source_title
User focus: $interests

Deck outline and source context:
$deck_context

Output goal:
Create a memorable, topic-specific miniature deck cover rather than a generic course icon.
<!-- /prompt-section -->
