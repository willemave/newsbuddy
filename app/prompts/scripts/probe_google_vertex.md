---
id: scripts/probe_google_vertex
description: Sectioned prompts for the Google Vertex routing probe script.
used_by:
  contents: scripts/probe_google_vertex_us_central1.py
  contents_description: "Minimal user prompt used by the Gemini routing probe to confirm text generation succeeds."
  system: scripts/probe_google_vertex_us_central1.py
  system_description: "Minimal system prompt used by the pydantic-ai Gemini routing probe."
prompt_type: sectioned_prompt
---
## Contents
<!-- prompt-section: contents -->
Reply with exactly OK
<!-- /prompt-section -->

## System
<!-- prompt-section: system -->
Return OK only.
<!-- /prompt-section -->
