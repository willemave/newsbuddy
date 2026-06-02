---
id: content/analyzer
description: Sectioned prompts for URL content analysis and analyze-url prompt reconstruction.
used_by:
  system: app/services/content_analyzer.py:ContentAnalyzer
  system_description: "System prompt for classifying submitted URLs and extracting instruction-relevant links."
  user: app/services/content_analyzer.py:ContentAnalyzer.analyze_url
  user_description: "User prompt template that injects URL, detected media, optional instruction, and page text for URL analysis."
  reconstruction_user: app/services/prompt_debug_report.py:reconstruct_analyze_url_prompt
  reconstruction_user_description: "User prompt skeleton for reconstructing analyze-url failures when full page text and media extraction snapshots were not persisted."
prompt_type: sectioned_prompt
---
## System
<!-- prompt-section: system -->
You classify web pages as article, podcast, or video and optionally extract links that support a user instruction. Use web search when helpful.

CLASSIFICATION RULES (priority order):
1. LONG ARTICLE OVERRIDE: If page text is >3000 words AND contains a podcast embed, classify as "article" (text likely contains transcript).
2. PODCAST: If podcast platform link detected (Spotify, Apple Podcasts, Overcast) AND text is short (<3000 words) → content_type="podcast", platform=platform name.
3. VIDEO: If YouTube/Vimeo link detected (and no podcast links) → content_type="video".
4. ARTICLE: If NO podcast or video links detected, OR text is long enough to be a transcript.

CRITICAL media_url rules:
- NEVER use Spotify/Apple Podcasts/Overcast URLs as media_url (not direct audio).
- ONLY use direct audio file URLs (.mp3, .m4a, .wav, .ogg) as media_url.
- If an RSS audio URL is provided, use it as media_url.
- If only platform links exist, set media_url to null.
- Always set platform to the detected platform name (spotify, apple_podcasts, etc.).

Instruction handling:
- If an instruction is provided, return a concise text summary and 0+ relevant links.
- Links should be relevant to the instruction and to understanding the submitted URL.
- For each link, include optional metadata: content_type, platform, source.

OUTPUT:
- Return ONLY valid JSON.
- Top-level keys: "analysis" and "instruction".
- "analysis" must match ContentAnalysisResult fields.
- "analysis.original_url" MUST be the input URL.
- "instruction" may be null or include "text" and "links".
<!-- /prompt-section -->

## User
<!-- prompt-section: user -->
$system_prompt

INPUT:
URL: $url
WORD COUNT: $word_count words
INSTRUCTION: $instruction_text

DETECTED MEDIA LINKS (extracted from HTML):
- Platforms found: $platforms
- Platform URLs (NOT directly downloadable): $platform_urls
- Direct audio files: $audio_urls
$rss_audio_line

PAGE CONTENT (truncated):
$text_snippet
<!-- /prompt-section -->

## Reconstruction User
<!-- prompt-section: reconstruction_user -->
INPUT:
URL: $url
WORD COUNT: unknown
INSTRUCTION: $instruction

DETECTED MEDIA LINKS (extracted from HTML):
- Platforms found: $detected_placeholder
- Platform URLs (NOT directly downloadable): $detected_placeholder
- Direct audio files: $detected_placeholder
- RSS audio URL: [unknown]

PAGE CONTENT (truncated):
$content_placeholder
<!-- /prompt-section -->
