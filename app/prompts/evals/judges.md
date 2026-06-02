---
id: evals/judges
description: Sectioned judge prompts used by summary and assistant evaluation jobs.
used_by:
  title_judge_user: app/services/summary_eval.py:build_title_judge_prompt
  title_judge_user_description: "Judge prompt for grading generated summary titles against reference and known-bad titles."
  assistant_trace_judge_user: app/services/assistant_eval.py:build_generic_judge_prompt
  assistant_trace_judge_user_description: "Judge prompt for grading whether an assistant execution trace satisfied an expected outcome."
prompt_type: sectioned_prompt
---
## Title Judge User
<!-- prompt-section: title_judge_user -->
You are grading a generated title for a summary-generation eval.

Decide whether the generated title is grounded, specific, and materially better than the known bad titles.
The generated title does not need to match the reference titles exactly, but it should be comparably informative and faithful.

Content type: $content_type
Prompt type: $prompt_type
Source title hint: $source_title
Existing title: $existing_title
Known bad titles:
$bad_titles

Reference good titles:
$reference_titles

Evaluation criteria:
$evaluation_criteria

Source evidence:
$input_text

Generated title:
$generated_title

Full generated payload:
$payload_json

Grade on these dimensions:
- Specificity and informativeness
- Faithfulness to the evidence
- Whether it avoids vague reaction-text or placeholder framing
- Whether it captures the real takeaway or tension

Fail the title if it stays generic, mirrors the bad titles, or misses the story.
<!-- /prompt-section -->

## Assistant Trace Judge User
<!-- prompt-section: assistant_trace_judge_user -->
You are grading whether an assistant satisfied an expected outcome.

Expected outcome:
$expected_outcome

Observed execution trace:
$trace_json

Decide whether the assistant satisfied the expected outcome.
Consider the final assistant response and the actions reflected in the trace.
Return passed=true only when the overall behavior matches the expected outcome.
If the trace shows the wrong target, a missing action, or inconsistent behavior, fail it.
<!-- /prompt-section -->
