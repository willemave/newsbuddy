# Unified LLM Task Workflows Design

## Status

Reviewed design, ready for implementation planning. This document does not authorize migration,
production repair, deployment, or removal of compatibility state.

## Goal

Use `llm_tasks` as the single execution record for every LLM or agent run. Keep
`learning_decks` as the stable user-facing product, retire `learning_deck_runs`, and make the shared
agent runtime reliable enough that source waits, sandbox/tool problems, missing artifacts, and
worker failures always produce a correct, diagnosable outcome.

The design must prevent both July 2026 production failures:

1. A Learning Deck consumed all queue retries while its source was processing, leaving the queue
   task failed but the deck and LLM task permanently `preparing`.
2. A Learning Deck agent returned without writing `output/index.html`; the run failed with a raw
   missing-path error and lost the sandbox/tool evidence needed to explain the failure.

## Review Outcome

The first draft was directionally correct but too elaborate. This revision removes complexity from
the hot path:

- Use a small task-kind dispatcher, not a new generic workflow framework.
- Add one queue outcome (`defer`) rather than a dependency/DAG subsystem.
- Use one versioned sandbox template and the existing lazy per-user sandbox reuse.
- Expose one five-tool agent surface with typed results.
- Run web search directly on the host; do not route normal agent searches through a VM helper,
  task JWT, and callback endpoint.
- Run artifact checks automatically after the model returns; do not expose validation as another
  model tool.
- Probe full sandbox capabilities once when a sandbox is created or recreated, not before every
  task.
- Do not introduce another lease system. The queue already heartbeats active task leases; retain
  and test it for long agent runs.
- Do not dual-write a legacy run mirror. Use an additive migration, backfill, compatible reads, and
  a short-lived cutover flag.
- Keep the existing mobile API shape so the backend and iOS app need not deploy simultaneously.

## Invariants

These rules define correctness:

1. Every agent or LLM attempt has exactly one canonical `llm_tasks` row.
2. A `processing_tasks` row controls delivery only. Its retry/lease state is not product state.
3. A Learning Deck's current attempt is `learning_decks.latest_task_id`.
4. A failed rerun never replaces the previously published artifact or
   `latest_successful_task_id`.
5. An active asynchronous LLM task has one active queue delivery unless it is being created or
   finalized in the same transaction.
6. Waiting for a prerequisite never consumes an execution retry.
7. A model completion response is not success. Required files must exist and validate.
8. Every terminal failure records a typed stage, message, sandbox identity when applicable, and
   bounded internal log.
9. Sandbox reuse is an optimization, never a correctness requirement.

## Non-Goals

- Do not merge `processing_tasks` into `llm_tasks`.
- Do not move latency-sensitive chat turns onto the background queue.
- Do not require every LLM task to use a sandbox.
- Do not build a DAG/workflow engine.
- Do not expose Newsly mutations through arbitrary VM shell calls.
- Do not publish a deterministic low-quality fallback when deck output is invalid.
- Do not drop legacy schema in the first production deployment.

## Target Model

```text
learning_decks
    stable source, sharing state, and currently published artifact
    latest_task_id --------------------------+
    latest_successful_task_id ---------------|----+
                                              |    |
llm_tasks <-----------------------------------+----+
    one row per execution attempt
    task_kind = learning_deck
    subject_id = learning_decks.id
    parent_task_id = originating Share Action, when applicable
    input_json = source snapshot, interests, deck id
    output_json = generated title and publication result
    artifact_manifest = stored output
    status/history/model/sandbox/log/error = canonical execution evidence
        |
        +-> processing_tasks(run_llm_task)
            availability, lease, deferral, and delivery retries
```

### `llm_tasks` additions

Add:

- `subject_id INTEGER NULL`
- `parent_task_id INTEGER NULL REFERENCES llm_tasks(id) ON DELETE SET NULL`

`task_kind` defines the subject type, so a separate `subject_type` column is unnecessary. For a
Learning Deck task, `subject_id` is always a `learning_decks.id`. Other task kinds may leave it null
until they have a stable aggregate to reference.

Add indexes:

- `(user_id, task_kind, subject_id, created_at)`
- `parent_task_id`
- a partial unique index preserving the current rule of one active Learning Deck task per user:

```sql
UNIQUE (user_id)
WHERE task_kind = 'learning_deck'
  AND status IN ('queued', 'preparing', 'running', 'applying')
```

### `learning_decks` additions

Add concrete foreign keys:

- `latest_task_id INTEGER NULL REFERENCES llm_tasks(id) ON DELETE SET NULL`
- `latest_successful_task_id INTEGER NULL REFERENCES llm_tasks(id) ON DELETE SET NULL`

Keep the published artifact columns on `learning_decks`. They are the artifact currently served by
viewer/share routes. Task artifacts remain in `llm_tasks.artifact_manifest` as attempt history.

The old run pointers and `learning_deck_runs` table remain read-only during compatibility and are
removed after cutover verification.

## API Compatibility

Keep the existing Learning Deck wire shape during the migration:

- `latest_run` remains the user-facing name for the latest generation attempt.
- `latest_successful_run_id` is populated from `latest_successful_task_id`.
- `LearningDeckRunResponse` remains an API DTO, but it is projected from an `LlmTask`.

Projection:

- `id` <- `llm_tasks.id`
- `interests_prompt` <- `input_json.interests_prompt`
- `timeline` <- `status_history`
- `error_message` <- `error_message`
- timestamps <- LLM task timestamps
- client status <- workflow phase mapping below

Existing iOS code uses this shape for presentation and rerun context, not a run-specific endpoint,
so the compatible projection avoids a coordinated client release. A later API version can rename
the DTO, but that is outside this reliability work.

## State Model

Keep shared task status coarse:

```text
queued -> preparing -> running -> applying -> completed
                                \-> failed
any active state ----------------> cancelled
```

Use `workflow_state` for the Learning Deck phase:

```text
queued
waiting_for_source
sandbox_preflight
generating
validating
repairing_artifacts
publishing
completed
failed
cancelled
```

Client mapping:

| Workflow state | Learning Deck status |
| --- | --- |
| `queued` | `queued` |
| `waiting_for_source`, `sandbox_preflight` | `preparing` |
| `generating`, `repairing_artifacts` | `generating` |
| `validating` | `validating` |
| `publishing` | `publishing` |
| terminal states | corresponding terminal status |

All transitions use `set_llm_task_status`. No second backend row stores mutable execution status.

## Simple Dispatcher

Extend the existing `RunLlmTaskHandler` with a small explicit map:

```python
LLM_TASK_EXECUTORS = {
    LlmTaskKind.SHARE_ACTION: run_share_action_task,
    LlmTaskKind.LEARNING_DECK: run_learning_deck_task,
}
```

Each executor validates its supported mode and `workflow_key`. Share Actions continue using the
existing `ShareActionWorkflowSpec`; the one Learning Deck workflow uses explicit constants. There
is no new registry class, inheritance hierarchy, or generic artifact framework.

Interactive chat continues through its current request/turn runtime while using `LlmTask` as its
canonical execution ledger. Background queue delivery is not required merely for uniformity.

## Queue Deferral

Replace the current success/retryable combination with one explicit disposition:

```text
complete  work succeeded
retry     execution failed transiently; increment retry_count
defer     prerequisite is not ready; preserve retry_count
fail      terminal execution failure
```

`TaskResult.defer(reason, delay_seconds)` returns the processing row to `pending`, updates
`available_at`, clears the lease, preserves `retry_count`, and leaves `error_message` empty.

Learning Deck source policy:

- If source content is incomplete but active, set `workflow_state=waiting_for_source` and defer.
- Derive the delay from task age without storing another counter: short waits poll quickly, then
  back off to a five-minute cap.
- Record the initial wait and material source-state changes, not an identical history event for
  every poll.
- If the source completes, continue on the next dequeue.
- If source processing fails, fail immediately with `source_processing_failed`.
- If it is still active two hours after `llm_tasks.created_at`, fail with `source_wait_timeout`.
- Later deferrals never move that deadline.

The existing worker lease heartbeat continues running while an executor is active. Add an
integration test proving a run longer than `worker_timeout_seconds` retains its lease; do not add a
second heartbeat or task-specific lease subsystem. Expose lease loss from the existing heartbeat
context and require a final ownership check before artifact promotion. A worker that no longer owns
the queue row may finish collecting diagnostics, but it must not publish.

## Sandbox Runtime

### One capable, versioned template

Use one pinned E2B template for Newsly agent tasks rather than relying on an unspecified provider
default. The image should include:

- bash and core Unix tools;
- Python 3;
- Node.js/npm for optional deck authoring;
- git, curl, and jq;
- a supported headless Chromium/Playwright installation;
- fonts needed by the deck renderer;
- a small `newsly-sandbox-probe --json` command reporting template and capability versions.

No task installs system packages, Node, Chromium, or Playwright at runtime.

Include the template revision in sandbox lease metadata and the process-local cache key. Changing
the configured template must not reuse a sandbox created from an older revision.

Production config diagnostics currently prove that E2B and its API key are configured, but do not
show the template revision or capabilities. Add safe diagnostics for provider, template identifier,
template revision, internet mode, and last canary result; never expose credentials.

### Lazy reuse and bounded preflight

Keep the current lazy per-user namespace reuse and task-specific workspaces:

```text
/tmp/newsly/users/<user_id>/shared/
/tmp/newsly/tasks/<llm_task_id>/
```

On a fresh or recreated sandbox:

1. Run `newsly-sandbox-probe --json` once.
2. Verify the required template revision and core capabilities.
3. Cache that manifest with the sandbox handle.

For every task:

1. Create the task workspace.
2. Write the source inputs.
3. Verify the input manifest in one bounded check.
4. Persist provider, sandbox id, reuse state, template revision, and capability manifest before the
   model call.

Do not repeat expensive binary/browser probes on a healthy reused sandbox. When an operation reports
that the sandbox disappeared, evict it, create a fresh sandbox, rerun full preflight, and restart the
task bootstrap.

## Agent Tool Surface

Expose exactly five tools to a sandboxed Learning Deck agent:

```text
execute_bash(command, timeout_seconds?)
read_file(path, max_bytes?)
write_file(path, text)
list_files(path = ".")
web_search(query, num_results = 5)
```

Rules:

- Remove the deprecated duplicate `bash` alias from new workflows.
- Run `web_search` directly on the Newsly host with the existing policy and telemetry. Do not make
  the model invoke a VM CLI that calls back to Newsly with a task token.
- Keep the VM search helper only for a workflow that explicitly needs shell scripts to search; the
  Learning Deck workflow does not.
- Return typed results, not prose that the model must parse:

```text
execute_bash -> ok, exit_code, stdout, stderr, truncated, duration_ms
read_file    -> ok, text, error_code
write_file   -> ok, path, bytes_written
list_files   -> ok, files
web_search   -> ok, results
```

- Missing files return `ok=false`; they are not disguised as successful text containing
  "File not found."
- Bound output size and command timeout centrally.
- Log tool name, duration, result status, and bounded metadata. Do not log task tokens or entire
  large file bodies.
- Compile the effective tool policy once at task start and verify that the required five tools are
  available before invoking the model.

This removes several VM writes, a chmod call, a callback HTTP request, token creation, and an extra
failure surface from the normal deck path.

## Artifact Contract

The required outputs remain:

```text
output/index.html
output/source-notes.md
```

After the model returns, orchestration—not the model—does the following:

1. List and store the output manifest.
2. Verify required files exist and fit size limits.
3. Validate Reveal structure, allowed external dependencies, local asset references, and source
   notes.
4. Run the versioned template's deck checker to catch browser/runtime errors and obvious overflow.
5. If validation fails, set `workflow_state=repairing_artifacts` and run one focused repair turn in
   the same sandbox with the exact JSON validation report.
6. Revalidate once.
7. Publish only after validation passes.
8. Otherwise fail with `artifact_contract_failed`.

Do not expose `newsly-deck-check` as another model tool and do not ask the model to decide whether its
own output passed. The agent prompt should focus on writing the two outputs; the runtime owns
verification.

## Diagnostic Boundary

Wrap sandbox acquisition, bootstrap, model execution, artifact reads, validation, repair, and
publication in one error boundary. Every error records:

- failure stage and typed error code;
- sandbox provider, id, reuse state, and template revision;
- capability and effective-tool manifest;
- bounded agent/tool events;
- output file manifest;
- model completion summary;
- repair result, when attempted.

Persist the internal log before sandbox release, including failures after the model returns. Redact
secrets. Model output may be a small structured completion summary, but filesystem state remains the
source of truth.

## Creation, Idempotency, and Publication

Creation transaction:

1. Resolve or create the stable `LearningDeck`.
2. If the same deck already has an active task, return that deck/task target.
3. Otherwise enforce the existing single-active-deck-task-per-user rule.
4. Create an `LlmTask(task_kind=learning_deck, subject_id=deck.id)`.
5. Set `parent_task_id` when a Share Action created it.
6. Set `learning_decks.latest_task_id`.
7. Enqueue `processing_tasks(task_type=run_llm_task, payload={llm_task_id, user_id})`.
8. Commit together.

Successful publication transaction:

1. Store validated artifacts under a task-specific prefix.
2. Copy published artifact pointers onto `learning_decks`.
3. Set `latest_task_id` and `latest_successful_task_id`.
4. Mark the LLM task completed with `output_json` and `artifact_manifest`.
5. Commit atomically.

If publication or a rerun fails, the previous successful pointers remain unchanged.

The executor is idempotent:

- A queue redelivery of a completed LLM task returns `complete` without rerunning the model.
- A redelivery of a deferred task reevaluates only the prerequisite.
- A redelivery after worker loss first validates any complete staged output. It publishes valid
  output without another model call; otherwise it resets partial output and starts one clean attempt.
- Publication checks the task-specific artifact keys before writing or promoting them again.

## Migration and Cutover

### Phase 0: reproduce first

- Add a failing test where source readiness takes longer than the current retry budget.
- Add a failing test where the agent returns without writing either required file.
- Change fake sandboxes so reads never manufacture files the agent did not write.
- Add a long-running task test proving the existing lease heartbeat prevents duplicate checkout.

### Phase 1: additive foundation

- Add `subject_id`, `parent_task_id`, and Learning Deck task pointers.
- Backfill them from existing `learning_deck_runs.llm_task_id` links.
- Add the active Learning Deck LLM-task index.
- Add the `defer` queue disposition.
- Extend `RunLlmTaskHandler` with the explicit Learning Deck executor.
- Add LLM-task-to-Learning-Deck API projection helpers.

The old execution path remains enabled during this deployment. Reads prefer `latest_task_id` and
fall back to the linked legacy run for pre-backfill data.

### Phase 2: reliability and cutover

- Ship the versioned sandbox template and safe config diagnostics.
- Simplify the tool surface and switch Learning Deck search to the direct host tool.
- Add cached sandbox preflight, automatic validation, one repair turn, and complete diagnostics.
- Enable the new Learning Deck executor behind a short-lived server flag.
- New attempts create only an LLM task; there is no legacy run mirror.
- Run focused canaries, then enable the path for normal traffic.

Rollback means disabling the flag in the same compatible release, not redeploying an older binary
that cannot read the new task pointers.

### Phase 3: remove legacy state

Proceed only after the backfill is complete, canaries pass, and no stranded active task exists:

- Remove `GenerateLearningDeckHandler`, `LearningDeckRunPayload`, and
  `TaskType.GENERATE_LEARNING_DECK` after old queue rows are terminal or migrated.
- Remove fallback reads and the temporary cutover flag.
- Remove the `LearningDeckRun` ORM model and table in a later migration.
- Keep the compatible API DTO/client enum as projections for this API version.
- Regenerate OpenAPI, Swift, and Go contracts and update architecture/codebase docs.

### Phase 4: repair the observed tasks

- Run the doctor report.
- Requeue the TPU task now that its source is complete.
- Rerun JEPA through the artifact-contract path.
- Verify DB state, queue state, stored artifacts, viewer URLs, and iOS presentation.
- Observe success, latency, repair rate, and stranded-task metrics before dropping compatibility
  code.

## Operations

Keep the operator surface small:

```text
admin llm-tasks doctor [--task-id ID] [--workflow KEY]
admin fix retry-llm-task --task-id ID          # dry-run unless --apply --yes
admin llm-tasks canary --workflow learning_deck.presentation.v1
```

`doctor` reports:

- active LLM task without active queue delivery;
- terminal queue task with active LLM state;
- completed task without published deck pointers;
- invalid task/deck ownership;
- task beyond source or execution deadline;
- missing stored artifacts.

The canary uses a fixed small source and the real configured model, sandbox template, tools, object
storage, and viewer path. It is labeled and excluded from the user Learning timeline.

Record stage timings:

- queue wait from `processing_tasks.created_at` to `started_at`, by queue and task type;
- sandbox acquire/bootstrap;
- model execution;
- each tool call;
- validation and repair;
- publication;
- total end-to-end duration.

Performance requirements are structural rather than guessed thresholds:

- no per-task package installation;
- full capability probe only on fresh/recreated sandboxes;
- no VM-to-host callback for ordinary web search;
- one automatic validation pass;
- repair model call only after a failed validation;
- bounded tool outputs and logs.

Use canary measurements to set p95 latency alerts after a baseline exists.

The TPU incident included a roughly 20-minute `process_content` queue wait. Current production queue
health is clear, so this design does not guess at a worker-count change from one historical spike.
Alert on enqueue-to-start latency separately and adjust queue capacity only from sustained evidence;
deck deferral must not hide source-pipeline starvation.

## Hand-Computed Incident A: Source Wait

### Current broken state

```text
t=15:03 content 29894 = processing
         processing task 715193 retry_count=0
         LLM task 30 = queued
         deck run 6 = queued

t=15:03, 15:08, 15:13, 15:18
         source incomplete -> ordinary retry
         retry_count becomes 1, 2, 3, then maxes out

t=15:18 processing task = failed
         LLM task = preparing
         deck run = preparing
         ⚠ active product state has no runnable delivery

t=15:29 content = completed
         nothing wakes or reevaluates the deck
         UI remains Preparing
```

### Proposed state

```text
t=15:03 content = processing
         LLM task 30 = {
           task_kind: learning_deck,
           subject_id: 6,
           status: preparing,
           workflow_state: waiting_for_source
         }
         processing task = {
           task_type: run_llm_task,
           status: pending,
           retry_count: 0
         }

while source remains active
         defer same processing row with bounded backoff
         retry_count remains 0
         fixed deadline remains 17:03

t=15:29 content = completed
next dequeue
         sandbox bootstrap -> generating -> validating -> publishing
         LearningDeck.latest_task_id = 30
         LearningDeck.latest_successful_task_id = 30
         LLM task = completed
         processing task = completed
```

If the source is still active at the fixed deadline, the executor sets
`error_type=source_wait_timeout`, marks the LLM task failed, and returns terminal `fail`. The UI
cannot remain active without delivery.

## Hand-Computed Incident B: Missing Artifact

### Current broken state

```text
t=13:22 JEPA source incomplete -> ordinary retry
t=13:27 source complete -> agent starts
t=13:32 model returns completion text
         read output/index.html -> missing-path exception
         deck and LLM task fail through a generic path
         sandbox identity/output manifest/tool log are not preserved
         ⚠ the infrastructure cannot distinguish model omission from runtime failure
```

### Proposed state

```text
t=13:22 source incomplete
         LLM task = waiting_for_source
         queue delivery deferred; retry_count remains 0

t=13:27 source complete
         acquire versioned sandbox
         cached template probe or fresh full probe passes
         task workspace/input manifest passes
         effective five-tool policy persisted
         LLM task = generating

t=13:32 model returns
         automatic output manifest lacks output/index.html
         LLM task = repairing_artifacts
         one repair turn receives the exact JSON validation report

branch 1 repair succeeds
         validate -> publish -> LLM task and queue delivery complete

branch 2 repair fails
         LLM task = failed
         error_type = artifact_contract_failed
         sandbox/template/tool/output/log evidence is preserved
         processing task = failed
```

Both branches are correct. The runtime cannot claim success without files, lose the evidence, or
leave the UI spinning.

## Acceptance Matrix

| Case | Required outcome |
| --- | --- |
| Source processing exceeds three polls | Deferrals preserve retry count; task later runs or reaches fixed timeout |
| Source processing fails | Terminal `source_processing_failed` on LLM and queue task |
| Agent returns without files | One repair turn, then success or typed terminal failure |
| Required tool disabled | Fail before model call with `tool_policy_invalid` |
| Sandbox template is missing a capability | Fail fresh-sandbox preflight with capability report |
| Cached E2B sandbox disappeared | Evict, recreate lazily, rerun full probe and bootstrap |
| Agent exceeds initial queue lease | Existing heartbeat retains ownership; no duplicate execution |
| Lease renewal fails during generation | Losing worker cannot publish; recovered delivery resumes from validated staged output or restarts cleanly |
| Agent times out | Terminal `agent_timeout` with sandbox and bounded log preserved |
| Artifact remains invalid after repair | Terminal `artifact_contract_failed`; old deck stays published |
| Publication fails | Terminal `publication_failed`; no partial promotion |
| Same deck is requested twice | Return existing active task rather than failing the Share Action |
| Different deck requested while one is active | Preserve explicit conflict until concurrency is designed |
| Completed task is redelivered | Idempotently return complete without another model call |
| Active task has no delivery | Doctor reports it and explicit fix can requeue it |

## Concrete Code Plan

Keep implementation commits narrow:

1. Regression tests for source wait, missing artifacts, and lease heartbeat.
2. Queue disposition enum and `defer` persistence.
3. Additive LLM task ownership schema, Learning Deck pointers, and backfill.
4. Small task-kind dispatcher and Learning Deck LLM-task executor.
5. Compatible API/submission projections from LLM tasks.
6. Versioned sandbox bootstrap and safe capability diagnostics.
7. Five typed tools, direct host web search, and removal of the deck `bash` alias/helper install.
8. Update the Learning Deck prompt to match the five tools and runtime-owned validation.
9. Automatic artifact validation, one repair turn, lease-ownership guard, and complete error logs.
10. Doctor, explicit retry fix, production canary, and cutover.
11. Later cleanup migration removing the dedicated task type, fallback path, ORM model, and table.

Primary backend seams:

- `app/models/db/llm_tasks.py`
- `app/models/db/learning_deck.py`
- `app/models/contracts.py`
- `app/services/llm_tasks.py`
- `app/pipeline/handlers/run_llm_task.py`
- `app/pipeline/task_models.py`
- `app/pipeline/sequential_task_processor.py`
- `app/services/queue.py`
- `app/services/agent_vm_sessions.py`
- `app/services/agent_toolset.py`
- `app/services/learning_decks.py`
- `app/services/learning_deck_sources.py`
- `app/services/learning_deck_generation.py`
- `app/services/learning_deck_agent.py`
- `app/services/learning_deck_artifacts.py`
- `app/prompts/learning_decks/agent.md`
- `app/queries/list_submission_statuses.py`
- Learning Deck routers, hosting/share/token gates, admin commands, and Alembic migrations

The Learning Deck VM helper install path remains only if another workflow proves it needs shell-level
search. Do not keep parallel direct and callback-based search paths for the same agent.

## Validation Gates

Before enabling the new path:

1. Ruff and focused backend tests for queue, LLM tasks, VM sessions, toolset, Share Actions,
   Learning Decks, submissions, routers, and admin commands.
2. Migration upgrade and backfill checks against a production-shaped database snapshot.
3. Contract generation/checks; existing Learning Deck wire compatibility must remain green.
4. Native iOS decoding/unit tests and Maestro Learning/deck visual scenarios.
5. Staging canary using the real E2B template, model, direct web tool, validation, storage, and
   viewer route.
6. Failure injection for missing output, stale sandbox, tool-policy mismatch, model timeout,
   publication failure, and queue redelivery.

After deployment:

1. Verify backfill counts before enabling the cutover flag.
2. Run `admin llm-tasks doctor` report-only.
3. Run one production canary and inspect stage timing/tool traces.
4. Enable normal Learning Deck traffic.
5. Rerun the TPU and JEPA decks.
6. Verify DB/queue invariants, artifact storage, viewer URLs, and iOS status.
7. Observe completion rate, p95 latency, repair rate, and stranded-task count before cleanup.
8. Verify content and LLM queue wait latency independently so a healthy deck executor cannot mask a
   starved prerequisite queue.

## Final Review

- One canonical execution row remains: `llm_tasks`.
- The stable product remains separate: `learning_decks`.
- The queue remains separate because leases and retries are delivery mechanics.
- The implementation adds one small dispatcher, not a workflow framework.
- Source waiting uses a fixed-deadline defer, not retries or a dependency engine.
- The queue's existing lease heartbeat is reused and tested rather than replaced.
- The sandbox is lazy, versioned, capable, and fully probed only when created.
- The agent gets five non-overlapping typed tools; normal web search has one host-side path.
- No per-task package installation or redundant callback/auth layer remains in the deck hot path.
- Validation is automatic, with one bounded repair model call only on failure.
- Diagnostics survive every failure stage.
- Migration is additive and reversible through a same-version flag without dual-write state.
- Both observed production incidents and adjacent failure cases land in legal terminal states.
