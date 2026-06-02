---
id: scripts/notes_request_codex_plan
description: Codex prompt used by the Apple Notes poller for unapproved notes that need a written plan only.
used_by: scripts/poll_notes_requests.py:build_codex_prompt
prompt_type: script_user
---
Use $$$skill_name at $skill_path. Process the Apple Notes request in folder $folder_repr with note id $note_id_repr and current title $note_name_repr. Follow the skill workflow exactly. Keep the note marked with ⚙️ while working. Ask a user question only if blocked or the request is genuinely ambiguous. Write a comprehensive plan with these sections: Problem Summary, Relevant Files and Code Paths, Implementation Steps, Verification Plan, Risks and Open Questions. Write that plan back into the note via $notes_helper. Do not implement the change yet. Stop after the plan is written back to the note and wait for the title to gain 👍 approval.

