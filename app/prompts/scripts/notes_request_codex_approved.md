---
id: scripts/notes_request_codex_approved
description: Codex prompt used by the Apple Notes poller for approved notes that should be planned, implemented, verified, and marked done.
used_by: scripts/poll_notes_requests.py:build_codex_prompt
prompt_type: script_user
---
Use $$$skill_name at $skill_path. Process the Apple Notes request in folder $folder_repr with note id $note_id_repr and current title $note_name_repr. Follow the skill workflow exactly. Keep the note marked with ⚙️ while working. Ask a user question only if blocked or the request is genuinely ambiguous. Write a comprehensive plan with these sections: Problem Summary, Relevant Files and Code Paths, Implementation Steps, Verification Plan, Risks and Open Questions. Write that plan back into the note via $notes_helper. The note is already approved with 👍, so after the plan is written or refreshed, implement the change in the current repository, run appropriate verification, and mark the note done with ✅ when complete.

