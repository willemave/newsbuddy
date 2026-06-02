---
id: admin/error_analysis_fix_request
description: Closing instruction block for generated admin error-analysis prompts.
used_by: app/admin_web/logs.py:_an_generate_llm_prompt
prompt_type: admin_prompt_fragment
---
Please propose fixes with:
1. Critical fixes first (showstoppers), with code diffs.
2. Root-cause analysis for top categories and patterns.
3. Specific code changes (file paths, line ranges, before/after snippets).
4. Preventive measures (validation, retries, backoff, monitoring, alerts).
5. Testing strategy (unit/integration), plus quick verification steps.
