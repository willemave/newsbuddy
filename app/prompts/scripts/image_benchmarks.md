---
id: scripts/image_benchmarks
description: Sectioned prompts and prompt fragments used by image benchmark scripts.
used_by:
  fluxdev_runware_negative: scripts/benchmark_fluxdev_prompt_study.py
  fluxdev_runware_negative_description: "Shared negative prompt for the Runware FLUX.1 dev image prompt-study script."
  fluxdev_long_gemini: scripts/benchmark_fluxdev_prompt_study.py:build_variant_prompt
  fluxdev_long_gemini_description: "Long Gemini-style image prompt variant for the Runware FLUX.1 dev prompt study."
  fluxdev_long_gemini_narrative: scripts/benchmark_fluxdev_prompt_study.py:build_variant_prompt
  fluxdev_long_gemini_narrative_description: "Narrative-forward Gemini-style image prompt variant for the Runware FLUX.1 dev prompt study."
  fluxdev_long_gemini_process: scripts/benchmark_fluxdev_prompt_study.py:build_variant_prompt
  fluxdev_long_gemini_process_description: "Process-chain image prompt variant for the Runware FLUX.1 dev prompt study."
  fluxdev_long_gemini_airy: scripts/benchmark_fluxdev_prompt_study.py:build_variant_prompt
  fluxdev_long_gemini_airy_description: "Airy Gemini-style image prompt variant for the Runware FLUX.1 dev prompt study."
  fluxdev_long_gemini_object_system: scripts/benchmark_fluxdev_prompt_study.py:build_variant_prompt
  fluxdev_long_gemini_object_system_description: "Object-system image prompt variant for the Runware FLUX.1 dev prompt study."
  fluxdev_long_gemini_story_card: scripts/benchmark_fluxdev_prompt_study.py:build_variant_prompt
  fluxdev_long_gemini_story_card_description: "Premium story-card image prompt variant for the Runware FLUX.1 dev prompt study."
  infographic_runware_negative: scripts/benchmark_infographic_model_options.py
  infographic_runware_negative_description: "Shared negative prompt for additional infographic image model benchmark runs."
  infographic_compact_gemini_user: scripts/benchmark_infographic_model_options.py:build_compact_gemini_prompt
  infographic_compact_gemini_user_description: "Compact Gemini-style prompt for additional infographic model benchmark runs."
  infographic_full_plus_ideogram_suffix: scripts/benchmark_infographic_model_options.py:build_prompt
  infographic_full_plus_ideogram_suffix_description: "Extra instruction appended to full infographic prompts when benchmarking Ideogram-style providers."
  infographic_variant_wrapper: scripts/benchmark_infographic_model_options.py:build_variant_prompt
  infographic_variant_wrapper_description: "Wrapper appended to infographic benchmark prompts when testing provider prompt variants."
  infographic_variant_process_chain: scripts/benchmark_infographic_model_options.py:main
  infographic_variant_process_chain_description: "Process-chain variant suffix for additional infographic model benchmark runs."
  infographic_variant_artifact_network: scripts/benchmark_infographic_model_options.py:main
  infographic_variant_artifact_network_description: "Artifact-network variant suffix for additional infographic model benchmark runs."
prompt_type: sectioned_prompt
---
## Fluxdev Runware Negative
<!-- prompt-section: fluxdev_runware_negative -->
readable text, labels, logos, watermarks, screenshots, interface, dashboard, phone screen, laptop, monitor
<!-- /prompt-section -->

## Fluxdev Long Gemini
<!-- prompt-section: fluxdev_long_gemini -->
Create an infographic that describes the article.

Style requirements:
- Modern, clean editorial illustration style
- Subtle, muted color palette with good contrast
- Conceptual representation of the theme
- Suitable for a news app
- Do not use text, letters, labels, captions, logos, or watermarks
- The description below is context only and must not appear as rendered words in the image
- 16:9 aspect ratio optimized for mobile display

Description: $story_title

Benchmark-specific art direction:
- Use one dominant visual metaphor or one coherent scene, not a collage.
- Choose a single focal subject that communicates the story instantly at thumbnail size.
- Compose for a 16:9 editorial card with strong negative space and clear foreground/background separation.
- Keep the image bold, graphic, and readable on mobile.
- Prefer simplified shapes, restrained detail, and deliberate lighting over photo-busy realism.
- No text, captions, UI chrome, newspaper layout, screenshots, logos, or watermarks.
- Avoid generic stock-photo business scenes and multiple unrelated subjects competing for attention.
- Use a refined editorial palette with 2 to 4 dominant colors.

Story title: $story_title
Key facts to encode visually:
$facts_4

Output goal:
Create a premium editorial illustration for Newsly that feels distinctive, modern, and immediately legible.
<!-- /prompt-section -->

## Fluxdev Long Gemini Narrative
<!-- prompt-section: fluxdev_long_gemini_narrative -->
Create an infographic that describes the article.

Style requirements:
- Modern, clean editorial illustration style
- Subtle, muted color palette with good contrast
- Conceptual representation of the theme
- Suitable for a news app
- Do not use text, letters, labels, captions, logos, or watermarks
- The description below is context only and must not appear as rendered words in the image
- 16:9 aspect ratio optimized for mobile display

Description: $story_title

Benchmark-specific art direction:
- Use one dominant visual metaphor or one coherent scene, not a collage.
- Choose a single focal subject that communicates the story instantly at thumbnail size.
- Compose for a 16:9 editorial card with strong negative space and clear foreground/background separation.
- Keep the image bold, graphic, and readable on mobile.
- Prefer simplified shapes, restrained detail, and deliberate lighting over photo-busy realism.
- Avoid generic stock-photo business scenes and multiple unrelated subjects competing for attention.
- Use a refined editorial palette with 2 to 4 dominant colors.

Story title: $story_title
Editorial narrative: $narrative_compact
Key facts to encode visually:
$facts_3

Output goal:
Create a premium editorial illustration for Newsly that feels distinctive, modern, and immediately legible.
<!-- /prompt-section -->

## Fluxdev Long Gemini Process
<!-- prompt-section: fluxdev_long_gemini_process -->
Create an infographic that describes the article through image alone.

Style requirements:
- Modern, clean editorial illustration style
- Subtle, muted color palette with good contrast
- Conceptual but concrete enough to explain the article at a glance
- Strong negative space and one clear focal subject
- Do not use text, letters, labels, captions, logos, screenshots, or watermarks
- 16:9 aspect ratio optimized for mobile display

Description: $story_title

Benchmark-specific art direction:
- Make the image feel like the existing Gemini baseline: airy, graphic, calm, and polished.
- Use 3 to 5 related editorial objects rather than many small scattered symbols.
- Organize the objects into a readable process chain or visual progression.
- Prefer books, envelopes, stacks, packages, tokens, sketch tools, shelves, and symbolic rewards.
- Avoid interfaces, dashboards, screens, and literal documents.

Story title: $story_title
Editorial narrative: $narrative_compact
Key facts to encode visually:
$facts_3

Output goal:
Create a premium editorial illustration for Newsly that is visually explanatory, highly legible on mobile, and stylistically close to the Gemini baseline.
<!-- /prompt-section -->

## Fluxdev Long Gemini Airy
<!-- prompt-section: fluxdev_long_gemini_airy -->
Create an infographic that describes the article.

Style requirements:
- Modern, clean editorial illustration style
- Subtle, muted color palette with good contrast
- Suitable for a news app
- Do not use text, letters, labels, captions, logos, or watermarks
- The description below is context only and must not appear as rendered words in the image
- 16:9 aspect ratio optimized for mobile display

Description: $story_title

Benchmark-specific art direction:
- Match the Gemini baseline's airy, uncluttered feel.
- Use fewer, larger objects instead of many small icons.
- Keep broad negative space around the focal subject.
- One coherent scene or tableau, never a collage.
- Calm editorial lighting, clean edges, restrained detail.
- Bold mobile readability over realism.
- No UI chrome, screens, dashboards, or literal document pages.
- Prefer books, envelopes, packages, sketch tools, shelves, and symbolic rewards.

Story title: $story_title
Editorial narrative: $narrative_tight
Key facts to encode visually:
$facts_3

Output goal:
Create a premium, calm, polished editorial image that feels close to Gemini's visual tone and composition.
<!-- /prompt-section -->

## Fluxdev Long Gemini Object System
<!-- prompt-section: fluxdev_long_gemini_object_system -->
Create an infographic that describes the article.

Style requirements:
- Modern, clean editorial illustration style
- Subtle, muted color palette with good contrast
- Suitable for a news app
- Do not use text, letters, labels, captions, logos, or watermarks
- The description below is context only and must not appear as rendered words in the image
- 16:9 aspect ratio optimized for mobile display

Description: $story_title

Benchmark-specific art direction:
- Build a Gemini-like object system: one hero object plus 3 to 4 supporting objects.
- Make the relationships legible through grouping, scale, and spacing rather than arrows.
- Keep the composition information-dense but still open and breathable.
- Avoid visual noise and avoid many tiny decorative details.
- Prefer books, envelopes, stacks, packages, tokens, plinths, sketch tools, and shelves.
- No interfaces, dashboards, labels, screenshots, or logos.

Story title: $story_title
Editorial narrative: $narrative_tight
Key facts to encode visually:
$facts_4

Output goal:
Create a premium explanatory editorial illustration that feels organized, calm, and visually close to the Gemini baseline.
<!-- /prompt-section -->

## Fluxdev Long Gemini Story Card
<!-- prompt-section: fluxdev_long_gemini_story_card -->
Create an infographic that describes the article.

Style requirements:
- Modern, clean editorial illustration style
- Subtle, muted color palette with good contrast
- Conceptual representation of the theme
- Suitable for a news app
- Do not use text, letters, labels, captions, logos, or watermarks
- The description below is context only and must not appear as rendered words in the image
- 16:9 aspect ratio optimized for mobile display

Description: $story_title

Benchmark-specific art direction:
- Make it feel like a premium editorial story card.
- Use one dominant visual metaphor with a clear supporting object system.
- Strong foreground/background separation and broad negative space.
- Calm, polished, illustrative rather than photoreal.
- Avoid business-scene cliches and unrelated secondary subjects.
- Keep the image legible and elegant on mobile.
- No screenshots, UI, logos, labels, or visible words.

Story title: $story_title
Editorial narrative: $narrative_compact
Key facts to encode visually:
$facts_3

Output goal:
Create a polished Newsly card image that is visually explanatory and as close as possible to the existing Gemini production image.
<!-- /prompt-section -->

## Infographic Runware Negative
<!-- prompt-section: infographic_runware_negative -->
readable text, labels, logos, watermarks, screenshots, interface, dashboard, phone screen, laptop, monitor
<!-- /prompt-section -->

## Infographic Compact Gemini User
<!-- prompt-section: infographic_compact_gemini_user -->
No text. 16:9 editorial infographic. Explain the story through connected objects in a clear process chain with 3 to 5 major elements. No UI, screens, dashboards, labels, logos, or readable words.
Title: $title
Encode these facts visually:
$story_bits
Use clean hierarchy, strong negative space, and a premium editorial illustration style.
<!-- /prompt-section -->

## Infographic Full Plus Ideogram Suffix
<!-- prompt-section: infographic_full_plus_ideogram_suffix -->
Keep it crisp and diagrammatic, with clear object grouping and no fake typography.
<!-- /prompt-section -->

## Infographic Variant Wrapper
<!-- prompt-section: infographic_variant_wrapper -->
$base_prompt

Prompt variant:
$variant_suffix
<!-- /prompt-section -->

## Infographic Variant Process Chain
<!-- prompt-section: infographic_variant_process_chain -->
Emphasize a clean left-to-right explainer chain with 3 to 5 editorial objects, where each object visibly transforms into or causes the next.
<!-- /prompt-section -->

## Infographic Variant Artifact Network
<!-- prompt-section: infographic_variant_artifact_network -->
Emphasize an information-dense artifact network with one dominant central object and 3 to 4 supporting objects grouped around it, using connectors and spatial hierarchy instead of text.
<!-- /prompt-section -->
