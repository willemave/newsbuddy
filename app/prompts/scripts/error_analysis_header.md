---
id: scripts/error_analysis_header
description: Header template for the legacy error-log analysis script's LLM debugging prompt.
used_by: scripts/analyze_errors.py:generate_debug_prompt
prompt_type: script_prompt_fragment
---
# Error Analysis and Fix Request
Generated: $generated
Total Errors: $total_errors
Errored Content Items: $errored_content_count

