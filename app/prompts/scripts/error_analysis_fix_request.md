---
id: scripts/error_analysis_fix_request
description: Closing instruction block for the legacy error-log analysis script's LLM debugging prompt.
used_by: scripts/analyze_errors.py:generate_debug_prompt
prompt_type: script_prompt_fragment
---
## Fix Request
Please analyze these errors and:
1. Identify the root cause(s) of each error category
2. Suggest code fixes with specific file paths and line numbers
3. Recommend error handling improvements
4. Identify any pattern in failing URLs/content that might need special handling
5. Suggest retry strategies or fallback mechanisms where appropriate

**Key Questions to Answer:**
- Are these transient errors (network timeouts) or code bugs?
- Should we add fallback extraction methods?
- Do we need better timeout/retry configuration?
- Are certain sources consistently problematic?

