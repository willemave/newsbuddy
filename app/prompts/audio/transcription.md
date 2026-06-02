---
id: audio/transcription
description: Sectioned transcription prompts selected by audio filename context.
used_by:
  default: app/services/openai_llm.py:OpenAITranscriptionService._get_transcription_prompt
  default_description: "Whisper transcription prompt for generic podcast audio files."
  interview: app/services/openai_llm.py:OpenAITranscriptionService._get_transcription_prompt
  interview_description: "Whisper transcription prompt for interview-style podcast audio files."
  tech: app/services/openai_llm.py:OpenAITranscriptionService._get_transcription_prompt
  tech_description: "Whisper transcription prompt for technology or AI podcast audio files."
  news: app/services/openai_llm.py:OpenAITranscriptionService._get_transcription_prompt
  news_description: "Whisper transcription prompt for news podcast audio files."
  bg2: app/services/openai_llm.py:OpenAITranscriptionService._get_transcription_prompt
  bg2_description: "Whisper transcription prompt for BG2 podcast audio files."
  continuation_suffix: app/services/openai_llm.py:transcribe_audio
  continuation_suffix_description: "Suffix appended to transcription prompts for non-initial audio chunks."
prompt_type: sectioned_prompt
---
## Default
<!-- prompt-section: default -->
This is a podcast episode. Please transcribe accurately, including speaker names when mentioned.
<!-- /prompt-section -->

## Interview
<!-- prompt-section: interview -->
This is a podcast interview. Please transcribe accurately, noting different speakers.
<!-- /prompt-section -->

## Tech
<!-- prompt-section: tech -->
This is a technology podcast discussing AI, software, and tech innovations. Include technical terms accurately.
<!-- /prompt-section -->

## News
<!-- prompt-section: news -->
This is a news podcast. Please transcribe accurately, including proper names and places.
<!-- /prompt-section -->

## BG2
<!-- prompt-section: bg2 -->
This is the BG2 podcast with Bill Gurley and Brad Gerstner discussing technology, venture capital, and market trends.
<!-- /prompt-section -->

## Continuation Suffix
<!-- prompt-section: continuation_suffix -->
This is a continuation of the previous segment.
<!-- /prompt-section -->
