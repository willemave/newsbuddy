# Task-Scoped Agent Sandboxes

Date: 2026-09-01
Status: Implemented locally; not deployed

## Decision

Newsly will replace persistent per-user agent VMs with fresh, task-scoped E2B sandboxes.
Sandboxes are disposable compute, not a second user-data store. A sandbox receives only the
durable inputs for its current task plus Knowledge items explicitly selected through host-owned
tools. It is destroyed after success, failure, or cancellation.

The cutover is one coordinated replacement. There is no dual runtime, compatibility fallback,
or multi-release migration period. Brief maintenance downtime is acceptable while old writers
and APIs stop, persisted E2B resources are deleted, the destructive migration runs, and the new
runtime starts.

## Why the current design should be retired

The persistent design couples every VM-backed task to a replicated filesystem:

- event fanout renders canonical Newsly data into `agent_data_files` and a host mirror;
- acquisition compares corpus revisions, builds a full or delta archive, uploads it, installs it,
  and verifies a remote manifest;
- per-user sandbox and snapshot identities require durable namespace leases, recovery snapshots,
  template rotation, reconciliation, and account-deletion cleanup;
- Learning Deck and Share Action work pays that acquisition cost before the agent can use the
  explicit source already stored in its task workspace;
- a corpus-size or synchronization defect can prevent unrelated artifact generation before a
  model request starts.

The live production shape on 2026-09-01 was about 30,600 active mirrored files and 346 MB of raw
documents for two users. The preceding seven days produced 1,132 `sync_agent_data` tasks and 622
`index_agent_data` tasks, while the preceding 30 days contained 52 recorded agent attempts. The
replica lifecycle therefore creates substantially more durable work than the agents it serves.

## Goals

1. Make every sandbox reconstructible from one durable task and canonical host data.
2. Remove corpus hydration, revision tracking, snapshots, and per-user VM ownership.
3. Preserve isolated shell, filesystem, browser, and hostile-feed execution.
4. Give every agent bounded, user-authorized access to Knowledge without placing credentials in
   the sandbox.
5. Make retries start cleanly and make queue/task state the only publication authority.
6. Delete the obsolete runtime, contracts, configuration, schema, workers, mirror data, and laws
   in the same cutover.

## Non-goals

- Persist arbitrary workspace files across tasks or chat turns.
- Mount or synchronize the whole inbox, News, chats, Briefings, or Knowledge into a sandbox.
- Give a sandbox a callback token, database access, Newsly credential, or vendor credential.
- Add E2B Volumes, another durable filesystem, a self-hosted VM platform, or an empty-sandbox pool.
- Preserve the persistent VM path as a fallback.

## Target architecture

```text
processing_tasks exact lease
          |
          v
immutable task/source snapshot ------ host Knowledge tools
          |                                  |
          v                                  v
fresh canonical E2B sandbox <----- selected bounded documents
          |
          v
task workspace: inputs, scratch files, generated output
          |
          v
host validation and exact-lease finalization
          |
          +----> durable artifact/log/result
          `----> kill sandbox
```

PostgreSQL remains authoritative for users, content, Knowledge ownership, tasks, attempts,
transcripts, artifacts, and retries. E2B remains authoritative only for the live process and files
inside one disposable attempt.

## Sandbox lifecycle

### Creation

Learning Deck and agent-backed Share Action attempts create a fresh sandbox before writing task
inputs. Chat creates one lazily when the model first calls `execute_bash`, `write_file`,
`edit_file`, `read_file`, `list_files`, or `write_knowledge_items`. Host-only search and read tools
never acquire E2B.

Every sandbox:

- starts from the one canonical `newsly-agent` template;
- is secure, credential-free, and deny-by-default until a task grants bounded egress;
- has the provider's default sudo access revoked before task inputs are written;
- uses only a task workspace such as `/data/workspace/tasks/{llm_task_id}`;
- has no `/data` corpus, manifest, index, user-shared workspace, or snapshot;
- records its provider and sandbox ID on the current `llm_tasks` attempt after creation.

The template revision remains an application setting and an event/log field. It is not persisted
as user VM state. Capability probing may remain cached in one worker process by template revision.

### Use

One sandbox remains alive for the duration of one task attempt so the agent can iterate over files,
commands, browser validation, and one bounded artifact-repair pass. Host tool calls and VM commands
may interleave; they continue to use the same attempt sandbox.

File tools read and write only within the task workspace. `read_file` and `list_files` no longer
accept corpus paths. Generated paths returned to the model are workspace-relative.

### Completion and failure

On success, the host reads and validates the expected outputs, stores the artifact and agent log,
finalizes behind the exact queue lease, and kills the sandbox. On failure or cancellation, it
captures bounded diagnostics, marks the attempt truthfully, and kills the sandbox without
publishing output.

`llm_tasks.sandbox_id` is an attempt-scoped cleanup handle, not resumable product state. A reclaimed
or explicitly retried attempt first issues an idempotent kill for any recorded prior sandbox and
then creates a fresh sandbox. Provider timeout-based destruction is the orphan backstop, not the
normal cleanup path.

## Knowledge tools

All Newsly agents may use the same three host-owned Knowledge tools. These tool implementations run
inside the Rust worker. The sandbox never calls Newsly APIs.

### `search_knowledge`

Searches canonical saved content for the task owner using the existing PostgreSQL ranking policy.
It returns 1-10 results with:

- `reference`: `{ "kind": "content", "id": <positive integer> }`;
- title, source, canonical URL, and bounded snippet;
- read state and saved state.

It no longer returns `corpus_path` and does not join `agent_data_files`.

### `read_knowledge_item`

Reads one typed reference after rechecking that the task user is active and still owns the
Knowledge save. Input contains `reference`, optional `offset`, and optional `max_bytes`. The
default is 100,000 bytes and the hard maximum is 500,000 bytes. Output contains UTF-8 text,
checksum, returned byte range, `truncated`, and `next_offset` when more data remains.

The repository loads relational ownership and immutable body identity in a short transaction,
releases PostgreSQL, and reads file-backed body content afterward.

### `write_knowledge_items`

Writes selected Knowledge items into the current task workspace. The name means writing copies to
the sandbox; it never creates, edits, saves, or removes a Knowledge record.

Input contains 1-20 typed references and an optional workspace-relative directory below
`input/knowledge`. The host reauthorizes every item, reads each canonical body with a 200,000-byte
per-item cap, and enforces a 4,000,000-byte aggregate cap. It writes safe generated filenames and
`manifest.json`, which records each typed reference, title, source URL, checksum, byte count, and
whether the body was truncated. One unauthorized or invalid reference rejects the entire call, so
the workspace never receives a partial mixed-authority selection.

The tool returns workspace-relative paths and aggregate counts. Uploaded copies are ordinary task
inputs and may be edited by the model without affecting canonical Newsly data.

## Product flows

### Learning Deck

The worker prepares the source snapshot and source body exactly as today, creates a fresh sandbox,
and writes `input/source.txt`, `input/source-snapshot.json`, the user's interests, and the design
brief. The agent may search, read, or write additional Knowledge items. It generates the deck in
the task workspace; host artifact and browser validation remain mandatory. A failed retry cannot
replace an earlier successful artifact.

### Share Actions

An agent-backed Share Action creates a fresh task sandbox and writes only its explicit task input.
It receives the same Knowledge and web tools. Host-only actions and mutations remain outside E2B
and finalize behind their existing authorization and idempotency fences.

### Chat

Search and inline Knowledge reads remain VM-free. A shell/file request lazily creates a fresh
sandbox for that assistant turn. `write_knowledge_items` also triggers lazy creation because its
destination is the task workspace. The sandbox is killed when the turn closes; later turns start
clean. Durable history and explicit screen/content context provide continuity.

### Feed validation

The current `FeedValidator` is already task-scoped: it creates a short-lived sandbox, fetches one
candidate, and destroys the sandbox. Keep that model. Remove obsolete persistent-namespace wording
and metadata; do not add corpus or snapshot behavior.

## Persistence and schema

Retain on `llm_tasks`:

- `sandbox_provider` and `sandbox_id` as attempt-scoped diagnostics and cleanup identity;
- `workspace_path` as the canonical task-relative workspace;
- durable input, output, artifact, usage, status, and error fields.

Remove:

- `llm_tasks.vm_namespace` and `llm_tasks.shared_workspace_path`;
- `users.agent_vm_sandbox_id`, `agent_vm_template_revision`, `agent_vm_snapshot_id`,
  `agent_vm_snapshot_template_revision`, and `agent_data_revision`;
- `agent_data_files`, `agent_vm_namespace_leases`, and the unused legacy
  `agent_vm_system_state` table;
- indexes, constraints, grants, and ownership declarations that exist only for those fields.

Historical `processing_tasks` and `llm_tasks` rows keep their terminal audit text. The migration
does not rewrite completed task history merely because its former executor used a persistent VM.

## Code and runtime deletion inventory

Delete the complete retired path rather than leaving dormant compatibility code:

- `rust/crates/newsly-worker/src/agent_data/` and `src/bin/agent_data_worker.rs`;
- `rust/crates/newsly-worker/src/agent_vm/corpus.rs` and the persistent lifecycle implementation;
- the database agent-VM repository and exports;
- corpus archive/install code from `newsly-e2b` and `newsly-vm-bootstrap`;
- sandbox connect/resume/pause and snapshot create/delete/restore methods after removing the
  persistent lifecycle and account-deletion snapshot cleanup; retain only fresh create and kill;
- agent-data task payloads, queue registration, ownership manifest entries, scheduler reconcile,
  worker dispatch, tests, and fixtures;
- agent-data enqueue fanout from content, Knowledge, chat, Briefing, onboarding, Learning Deck, and
  Share Action finalization;
- the agent-data worker program from Docker, local services, release checks, and operational docs;
- `AGENT_DATA_*`, persistent `AGENT_VM_*`, mirror-root, snapshot, and namespace configuration;
- `/data/agent_user_data` mounts and derived mirror files;
- prompts and tool descriptions that advertise a readable `/data` corpus.

Replace the persistent `AgentVmLifecycle` with a small task-sandbox owner responsible only for
create, harden, capability probe, attempt registration, network reset, kill, and orphan-safe
cleanup. Feed validation may continue using its existing smaller ephemeral cleanup owner.

## One-cutover deployment

The implementation lands as one complete branch and one production deployment.

1. Build and validate the new image and destructive SQLx migration together.
2. Enter a brief maintenance window and stop both API slots, workers, and scheduler so no old
   process can enqueue agent-data work or read removed schema.
3. Use the current persisted IDs to kill every recorded user sandbox and delete every recorded
   snapshot. Fail the cutover if any E2B deletion returns an ambiguous result.
4. Delete pending or retryable agent-data maintenance tasks, remove their runtime-ownership rows,
   remove the derived host mirror, and apply the destructive migration.
5. Start the new API, workers, and scheduler, run health and task probes, then restore public
   traffic.

There is no code fallback after step 4. Rollback requires restoring the pre-cutover database backup
and prior application image together; the deleted mirror is derived and does not require backup.

## Failure semantics and security

- A Knowledge tool failure is a tool error; it does not silently fall back to stale VM files.
- Search/read authorization is checked on every call, and write authorization is checked for the
  entire selection before the first upload.
- A sandbox receives only explicit task inputs and selected Knowledge copies, reducing accidental
  exposure compared with a full user corpus.
- No sandbox operation holds a database transaction or checked-out connection.
- Lease loss cancels external work, kills the sandbox, and prevents publication.
- Every non-zero bootstrap or command exit persists bounded stderr before generic transport status.
- Cleanup failure is internally visible and retryable, but cannot convert a failed task into
  success or publish unvalidated output.

## Observability

Record structured timings and byte counts for:

- sandbox create, harden, capability probe, first command, validation, and kill;
- Knowledge search/read/write calls, authorized item count, bytes read, bytes uploaded, and
  truncation count;
- task duration split into preparation, sandbox execution, validation, and finalization;
- cleanup failures and orphan sandbox IDs.

Do not record document bodies, credentials, signed URLs, or raw provider access tokens.

## Validation

Focused tests must prove:

- `search_knowledge` and `read_knowledge_item` perform zero E2B calls;
- every agent receives the three Knowledge tools under its tool policy;
- `write_knowledge_items` lazily creates a chat sandbox, reuses an existing task sandbox, rejects
  mixed ownership atomically, enforces item/byte/path bounds, and writes a correct manifest;
- file tools cannot read outside the task workspace and no `/data` corpus path is accepted;
- Learning Deck and Share Action attempts never invoke corpus preparation or snapshot APIs;
- retry, cancellation, lease loss, worker interruption, network-reset failure, and kill failure
  cannot publish stale output;
- a task succeeds with a production-sized Knowledge library because sandbox startup is independent
  of library cardinality;
- feed validation remains isolated and always attempts sandbox cleanup.

Repository validation includes Rust formatting, warning-denied Clippy, affected unit and SQLx
integration tests, public-contract drift checks, architecture guardrails, Docker Compose rendering,
and `git diff --check`. The production-shaped smoke must exercise real E2B Learning Deck, Share
Action, chat search/read/write, cancellation, retry, artifact publication, and viewer access.

Post-cutover proof requires:

- no agent-data task types are registered or pending;
- no persistent VM IDs, snapshots, namespace leases, corpus tables, or mirror files remain;
- a fresh chat Knowledge search performs no sandbox operation;
- a real Learning Deck reaches model execution without corpus hydration;
- all containers and public/admin health probes are healthy.

## Documentation and laws

Implementation changes intended behavior, so update:

- `docs/architecture.md` to define E2B as task-scoped disposable compute;
- Chat laws CH13-CH15 and CH17 so Knowledge is host-served and file tools are task-workspace only;
- Processing laws P14 and P16-P19 to remove persistent ownership, corpus, hydration, and snapshots
  and replace them with task-scoped lifecycle and host-authorized Knowledge writes;
- operations, deployment, template, queue, and account-deletion documentation;
- `docs/log.md` with implementation decisions, validation, and cutover evidence.
