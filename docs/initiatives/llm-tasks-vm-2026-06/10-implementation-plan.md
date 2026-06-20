# LLM Tasks VM Runtime Implementation Plan

## Goal

Create one shared agent execution substrate for ShareSheet actions, Learning Decks, and chat-adjacent workflows.

The substrate should:

- track every agent run in a generic `llm_tasks` table;
- reuse one VM namespace per user, with per-task work folders and a shared user folder;
- expose a small stable LLM tool surface;
- support host-mediated Newsly action callbacks with workflow approval policy;
- keep product mutations in Newsly services, not inside VM shell commands;
- let ShareSheet modes, presentation generation, and chat workflows share runtime code while keeping domain tables as product state.

## Non-Goals

- Do not replace `processing_tasks`; the existing queue remains the async worker scheduler.
- Do not remove Learning Deck domain tables in this pass.
- Do not let VM bash call internal Newsly mutation APIs directly.
- Do not expose arbitrary shell execution to normal chat by default.
- Do not make the mobile client understand raw agent tool calls.

## Existing Seams

- ShareSheet modes are currently client flags sent to `/api/content/submit` or `/api/learning/decks`.
- URL analysis already accepts instruction, crawl-links, feed-subscribe, and share-and-chat intent.
- Learning Decks already run a VM-centered agent with bash, file tools, and Exa search.
- Chat already has a sandbox abstraction for personal markdown access, but it is read-only and separate from Learning Decks.
- `processing_tasks` already routes background work by task type and queue partition.

## Data Model

Add `llm_tasks` as the generic execution ledger.

Core columns:

- `id`
- `user_id`
- `task_kind`
- `mode`
- `workflow_key`
- `workflow_version`
- `workflow_state`
- `status`
- `approval_policy`
- `allowed_actions`
- `tool_policy`
- `vm_namespace`
- `sandbox_provider`
- `sandbox_id`
- `workspace_path`
- `shared_workspace_path`
- `prompt_pack`
- `input_json`
- `output_json`
- `artifact_manifest`
- `agent_log_object_key`
- `model_provider`
- `model_name`
- `usage_json`
- `error_type`
- `error_message`
- `status_history`
- timestamps

Add `llm_task_actions` for host-mediated product actions requested by the LLM.

Core columns:

- `id`
- `llm_task_id`
- `action_name`
- `action_status`
- `approval_required`
- `action_input`
- `action_result`
- `rationale`
- `idempotency_key`
- `approved_by_user_id`
- `created_at`
- `approved_at`
- `started_at`
- `completed_at`
- `error_message`

Domain objects can reference `llm_tasks` when useful. For example, `learning_deck_runs` should gain `llm_task_id` once Learning Deck generation is adapted.

## VM Workspace Model

Use a reusable VM namespace per user:

```text
/workspace/newsly/users/<user_id>/shared/
/workspace/newsly/tasks/<llm_task_id>/
  input/
  output/
  scratch/
```

The first implementation can create one sandbox session per task while preserving this path layout. A later provider adapter can reuse live E2B sandboxes for the same `vm_namespace`.

## Stable Tool Surface

All VM-backed agents should use the same public tool names:

```text
execute_bash(command, timeout_seconds?)
write_file(path, text)
read_file(path, max_bytes?)
list_files(path = ".")
```

Tool policy controls limits and permissions. It should not create a broad catalog of mode-specific
tools. Web search is exposed inside bash as the VM helper command:

```text
newsly-web-search --query "..." --limit 5 --format json
```

The helper calls a host-mediated endpoint with a task-scoped JWT. Exa keys stay in the host app, and
the VM can still chain searches with `jq`, Python, `curl`, and HTML processing.

## Host Callback Actions

Newsly product mutations are exposed as typed workflow actions handled by the host, not as VM tools:

```text
request_action(action_name, input, rationale)
propose_action(action_name, input, rationale)
finish_workflow(summary, result)
```

The workflow engine decides whether a requested action is executed immediately, recorded for approval, rejected, or treated as a dry run.

Examples of host actions:

- `subscribe_to_feed`
- `save_to_knowledge`
- `mark_read`
- `enqueue_chat`
- `create_learning_deck`
- `add_content`
- `add_links`

Each action must be validated against:

- task owner;
- workflow key and mode;
- allowed actions;
- approval policy;
- idempotency key;
- action-specific input schema.

## Workflow State Machine

Initial generic states:

```text
queued
preparing
running
awaiting_approval
applying
completed
failed
cancelled
```

Action states:

```text
proposed
awaiting_approval
approved
applying
applied
rejected
failed
cancelled
```

Approval policy is supplied by the client or chosen by the host default:

```json
{
  "default": "approval_required",
  "overrides": {
    "discover_feed": "auto_apply",
    "subscribe_to_feed": "approval_required"
  }
}
```

Policy values:

- `auto_apply`
- `approval_required`
- `dry_run`

The LLM never sets workflow state directly. It requests actions or finishes. The host records and validates transitions.

## Prompt Packs

Use mode-specific prompt files with one stable harness:

```text
app/prompts/llm_tasks/share_action.add_content.md
app/prompts/llm_tasks/share_action.add_links.md
app/prompts/llm_tasks/share_action.add_feed.md
app/prompts/llm_tasks/share_action.chat.md
app/prompts/llm_tasks/share_action.presentation.md
app/prompts/llm_tasks/share_action.bookmark_only.md
app/prompts/llm_tasks/chat.article.md
app/prompts/llm_tasks/chat.contextual_assistant.md
app/prompts/llm_tasks/learning_deck.presentation.md
```

Each prompt should include:

1. Goal for the mode.
2. Recommended tool-use sequence.
3. What not to do.
4. Required `output/result.json` schema.
5. Examples of good and bad outputs.
6. Host-side effect note: the VM does not mutate Newsly directly.

## Chat and Knowledge UI

The client should not render raw tool calls. It should render derived action events:

- running milestone;
- proposed action;
- awaiting approval;
- applied action;
- failed action.

These events are derived from `llm_task_actions` and can be displayed inline in Knowledge chat.

Approval endpoints should apply stored action input:

```text
POST /api/llm-tasks/{task_id}/actions/{action_id}/approve
POST /api/llm-tasks/{task_id}/actions/{action_id}/reject
```

The VM does not rerun just to apply an approval.

## Implementation Phases

### Phase 1: Generic Task Foundation

- Add LLM task enums/contracts.
- Add `llm_tasks` and `llm_task_actions` models.
- Add Alembic migration.
- Add service helpers for task creation, status transitions, status history, and action recording.
- Add focused tests for workflow policy and action status transitions.

### Phase 2: Shared VM Harness

- Introduce `AgentVmSession` and `AgentCommandResult`.
- Move Learning Deck sandbox code behind the generic interface.
- Add `AgentToolset` registration helpers for shell and files.
- Add host-mediated VM helper scripts such as `newsly-web-search`.
- Preserve existing Learning Deck behavior.

### Phase 3: Learning Deck Integration

- Create an `llm_tasks` row for each Learning Deck generation run.
- Add `learning_deck_runs.llm_task_id`.
- Store model, sandbox, log, and artifact metadata through the generic task where practical.
- Keep Learning Deck response contracts unchanged.

### Phase 4: Chat Runtime Integration

- Adapt the personal-library sandbox to the generic VM/session shape.
- Keep normal chat tool policy read-only.
- Record chat agent work through `llm_tasks` where a durable run is needed.
- Do not expose `execute_bash` to normal chat unless an explicit workflow enables it.

### Phase 5: Share Actions

- Add Share Action task API and queue handler.
- Implement prompt packs and output schemas.
- Start with `add_feed` and `add_links`.
- Apply host-side actions through existing feed/content services.
- Then move `add_content`, `bookmark_only`, `chat`, and `presentation`.

### Phase 6: Client Action Rendering

- Add an API model for chat-visible action events.
- Render proposed/applied/failed action cards in Knowledge chat.
- Add approval and rejection endpoints.

## VM Image Requirement

Pin the VM template and add a smoke test for:

- shell basics;
- `curl`/`wget`;
- Python HTML parsing;
- `trafilatura`;
- `feedparser`;
- Node/Playwright/Chromium;
- file write/read/list;
- output artifact validation.

Minimum image packages:

```text
bash coreutils findutils ripgrep grep sed awk jq tree file diffutils patch
tar unzip gzip xz curl wget openssl ca-certificates dnsutils iputils
python3 pip uv
requests httpx aiohttp beautifulsoup4 lxml html5lib selectolax trafilatura
readability-lxml feedparser markdownify bleach pydantic jinja2
node npm pnpm playwright chromium jsdom cheerio
tidy-html5 libxml2-utils imagemagick ffmpeg fontconfig
sqlite3 pypdf pdfplumber
```

Install Playwright browser dependencies during image build, not at runtime.

## Rollout Safety

- Keep old ShareSheet routes working until each mode is explicitly migrated.
- Keep Learning Deck API responses stable.
- Keep chat behavior stable while moving internals.
- Add feature flags before enabling VM Share Actions in production.
- Prefer host-side validation over trusting `output/result.json`.
- Store raw agent logs for admin/debug, not client UI.

## Validation

Run focused checks as phases land:

```bash
ruff check app/models app/services app/pipeline tests
pytest tests/services -v
pytest tests/pipeline -v
pytest tests/routers -v
```

For Learning Deck changes, also run the existing Learning Deck service and router tests. For Share Action changes, add mocked-VM integration tests that write `output/result.json` and assert host-side actions.
