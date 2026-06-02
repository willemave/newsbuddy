# Newsly Prompt Library

This directory is the central home for reusable model-facing prompts.

Each prompt file is Markdown with a frontmatter description for humans and a prompt body loaded at runtime by `app.services.prompt_library`. Runtime values use `$placeholder` syntax and are substituted by `render_prompt(...)`. Dynamic source evidence, JSON payloads, and database-derived context should stay in Python; durable instructions and stable prompt wording belong here.

When a workflow has related system, user, or variant prompts, keep them in one Markdown file with named sections instead of separate sibling files. Load sections with `#section_name`, for example:

```python
load_prompt("summarization/news#system")
render_prompt("summarization/news#user", content=content)
```

Use this section shape so prompts can contain normal Markdown headings without confusing the loader:

```markdown
## System
<!-- prompt-section: system -->
...
<!-- /prompt-section -->

## User
<!-- prompt-section: user -->
...
<!-- /prompt-section -->
```
