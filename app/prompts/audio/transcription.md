---
id: audio/transcription
description: Sectioned transcription prompts selected by audio filename context.
used_by:
  default: app/services/openai_llm.py:OpenAITranscriptionService._get_transcription_prompt
  default_description: "GPT-Transcribe context for generic podcast audio files."
  interview: app/services/openai_llm.py:OpenAITranscriptionService._get_transcription_prompt
  interview_description: "GPT-Transcribe context for interview-style podcast audio files."
  tech: app/services/openai_llm.py:OpenAITranscriptionService._get_transcription_prompt
  tech_description: "GPT-Transcribe context for technology or AI podcast audio files."
  news: app/services/openai_llm.py:OpenAITranscriptionService._get_transcription_prompt
  news_description: "GPT-Transcribe context for news podcast audio files."
  bg2: app/services/openai_llm.py:OpenAITranscriptionService._get_transcription_prompt
  bg2_description: "GPT-Transcribe context for BG2 podcast audio files."
  voice_dictation: app/services/openai_llm.py:OpenAITranscriptionService.transcribe_audio_from_buffer
  voice_dictation_description: "GPT-Transcribe context for short voice dictation uploads."
  continuation_suffix: app/services/openai_llm.py:transcribe_audio
  continuation_suffix_description: "Suffix appended to transcription prompts for non-initial audio chunks."
prompt_type: sectioned_prompt
---
## Default
<!-- prompt-section: default -->
This recording is a podcast episode that may mention speakers by name.
<!-- /prompt-section -->

## Interview
<!-- prompt-section: interview -->
This recording is a podcast interview with multiple speakers.
<!-- /prompt-section -->

## Tech
<!-- prompt-section: tech -->
This recording is a technology podcast discussing AI, software, and technology products.
<!-- /prompt-section -->

## News
<!-- prompt-section: news -->
This recording is a news podcast that may mention people, organizations, and places.
<!-- /prompt-section -->

## BG2
<!-- prompt-section: bg2 -->
This is the BG2 podcast with Bill Gurley and Brad Gerstner discussing technology, venture capital, and market trends.
<!-- /prompt-section -->

## Continuation Suffix
<!-- prompt-section: continuation_suffix -->
This is a continuation of the previous segment.
<!-- /prompt-section -->

## Voice Dictation
<!-- prompt-section: voice_dictation -->
This recording is a short voice dictation that may contain names, numbers, URLs, or specialized terms.
<!-- /prompt-section -->
