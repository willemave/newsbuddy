# VM Execution Layer Plan

Date: 2026-08-23. Status: implemented locally; not committed or deployed. Supersedes the 2026-08-22 draft and the private-Volume variant from earlier on 2026-08-23.

Scope: E2B remains the execution runtime for chat agents and new-feed discovery. It is not used for routine configured-feed ingestion, Analyze URL page fetching, or podcast media downloads.

## 1. Decisions

- The host owns authentication, database state, queues, retries, credentials, corpus rendering, provider calls, and product mutations.
- The VM is a credential-free Linux computer for commands, file work, browser work, and isolated feed probes.
- A chat turn creates or resumes a VM only after the model invokes a VM tool.
- Every workflow uses the single code-owned `newsly-agent` template. The runtime revision hashes the Dockerfile, host hardening program, and corpus installer so any compatibility change rotates persisted compute automatically.
- Each user has one durable sandbox identity and, after the first successful hydration, one clean E2B snapshot identity. The snapshot contains the user corpus and workspace before user commands run.
- E2B Volumes are not part of the design. This team cannot use the private beta, and the runtime has no Volume fallback or configuration mode.
- Full-memory pause/resume is the normal warm path. A snapshot is the recovery path when a user's paused sandbox is gone; it is not taken after every turn.
- Feed discovery uses one durable system sandbox with full-memory pause/resume. It does not use snapshots because it has no user corpus to checkpoint and the canonical template is the faster clean start.

## 2. Checked assumptions

The implementation was checked against the installed `e2b` 2.35 and `e2b-code-interpreter` 2.9 SDKs and current E2B documentation.

- `Sandbox.connect` resumes a paused sandbox. `lifecycle.on_timeout.action=pause` with `keep_memory=true` preserves the running state and filesystem for a fast warm activation.
- `Sandbox.create_snapshot` captures filesystem and memory. `Sandbox.create(template=<snapshot_id>)` restores it, so a dead user sandbox can resume from a corpus checkpoint and receive only later revisions.
- E2B recommends templates for reusable clean environments because they start faster and consume fewer resources than snapshots. Newsly therefore snapshots user-specific runtime data only.
- Snapshot creation briefly pauses the source sandbox and drops open connections. Newsly reconnects before publishing the snapshot ID or returning the session.
- The template builder consumes `e2b.Dockerfile` through `Template.from_dockerfile`. The image pins its E2B base digest and Playwright 1.62.1.
- The live E2B account rejects Volume use. The obsolete `auto`/fallback split was removed instead of retaining an untestable branch.
- E2B's template finalizer grants its default `user` passwordless sudo after Dockerfile steps. A fresh canonical sandbox revokes both sudoers entries and group membership as root before capability probing, corpus installation, snapshot creation, or model work. Failure aborts and kills the sandbox.
- E2B's current network controls reject some selectors and do not block its provider-managed metadata endpoint. No Newsly or vendor credential is placed in the VM.

References: [sandbox persistence](https://docs.e2b.dev/sandbox/persistence), [snapshots](https://docs.e2b.dev/sandbox/snapshots), [template quickstart](https://docs.e2b.dev/template/quickstart), and [template user/workdir](https://docs.e2b.dev/template/user-and-workdir).

## 3. Goals

1. Give agents a general Linux execution layer without moving product authority into it.
2. Expose the user's readable Newsly corpus as ordinary files and a small JSONL index.
3. Make no-tool chat turns perform zero E2B work.
4. Resume active users quickly and avoid full corpus rebuilds when a sandbox disappears.
5. Keep feed discovery to one sandbox command per candidate batch instead of several file and command round-trips per URL.

## 4. Non-goals

- No Newsly-specific executable or mutation callback in the template.
- No product or provider credentials in VM environment variables.
- No VM replacement for PostgreSQL, queues, provider clients, or durable state.
- No E2B Volume dependency.
- No snapshot after each command or chat turn.
- No runtime migration to a self-hosted microVM platform.

## 5. User VM lifecycle

The user row stores the canonical runtime identities and revisions:

```
agent_vm_sandbox_id
agent_vm_template_revision
agent_vm_snapshot_id
agent_vm_snapshot_template_revision
agent_data_revision
```

Acquisition runs under a database row lock for the user and a process-local singleflight lock:

```
first VM tool in a turn
  |
  +-- current agent_vm_sandbox_id -> Sandbox.connect
  |      +-- running: reuse
  |      +-- paused: resume with memory
  |      `-- missing: clear only after confirmed not-found
  |
  +-- no live sandbox, compatible agent_vm_snapshot_id
  |      `-- Sandbox.create(template=agent_vm_snapshot_id)
  |             -> apply corpus revisions after snapshot
  |
  `-- no compatible snapshot
         `-- Sandbox.create(template=newsly-agent)
                -> revoke default-user sudo -> capability probe
                -> full corpus hydration -> clean snapshot -> reconnect

turn close -> release the local lease; do not kill or extend the sandbox
idle timeout -> E2B pauses with keep_memory=true
template change -> retire confirmed old sandbox/snapshot, then rebuild
user deletion -> kill sandbox and delete snapshot
```

The process cache is updated only after the database transaction commits. A failed commit cannot leave a usable in-process sandbox that the database does not own.

Persisted IDs are cleared only after deletion succeeds or E2B confirms the object is missing. A transient provider error rolls the transaction back and preserves the recoverable identity.

Snapshot creation is an availability optimization. If it fails, the newly hydrated canonical sandbox remains usable. If snapshot creation succeeds, reconnect must also succeed before Newsly stores the checkpoint.

## 6. Corpus and incremental synchronization

The host mirror remains authoritative. Event-driven `backfill` tasks project completed user-visible content, ready news, current Briefings, Knowledge state, and completed chats into bounded markdown documents. `agent_data_files` stores typed `(document_kind, document_key)` identity, path, stale paths, checksum, byte size, and index metadata.

The VM layout is:

```
/data/
  manifest.json
  index.jsonl
  knowledge/
  content/
  news/
  briefings/
  chats/
  workspace/
```

`/data/manifest.json` contains the corpus revision and is written last. `index.jsonl` contains document metadata for `jq`; bodies remain in markdown files. Documents are capped at 200 KB.

Corpus transfer happens during VM acquisition while the same user row lock is held:

- A new canonical sandbox or a missing/corrupt manifest receives one full archive.
- A compatible sandbox or snapshot receives only files and tombstones between its remote revision and the host revision.
- A remote revision greater than the host revision is treated as corruption. The sandbox or snapshot is discarded and rebuilt from host state.
- The installer checks archive paths, applies tombstones and replacements, writes the index atomically, and writes the manifest last.
- The archive is removed after installation.
- Corpus files and directories are owned by root and read-only to the model user. Only `/data/workspace` is owned by the model user and writable.
- The model user has no passwordless sudo after host preflight, so those ownership boundaries are effective rather than cosmetic.

The first clean user snapshot contains a root-owned manifest with its corpus revision. If the live sandbox disappears, restoring that snapshot reads the checkpoint from the manifest and applies only later revisions. The snapshot is intentionally not advanced after user work because recovery must not preserve an untrusted mutation of the corpus or manifest.

Backfill and reconciliation stay bounded. Chat projection bulk-loads messages for the selected sessions, documents encode and hash once, incremental ledger queries use typed identities, and no event can request an unbounded full-corpus scan. Identical pending sync events coalesce under a shared user lock; an event racing a processing sync creates one deduplicated successor, so committed state cannot be lost behind work that already rendered. Nightly reconciliation pages through the ledger and database rather than materializing every document in one worker.

## 7. One VM tool vocabulary

All VM-capable agents register the same five tools from one canonical name set:

| Tool | Contract |
|---|---|
| `execute_bash(command, timeout_seconds=60)` | Runs in `/data/workspace`; timeout is clamped to 1-300 seconds; stdout and stderr are bounded. |
| `read_file(path, max_bytes=...)` | Reads a workspace path or read-only corpus path with a byte cap. |
| `write_file(path, text)` | Writes below `/data/workspace` in one operation. |
| `edit_file(path, old_text, new_text, replace_all=false)` | Performs exact UTF-8 replacement below `/data/workspace`; one match is required unless replace-all is explicit. |
| `list_files(path=".")` | Lists bounded workspace or corpus results. |

VM progress is retry-fenced and separate from transcript text. Start/progress events are `running`; a failed event or non-zero command exit is `failed`; only successful completion is `completed`.

Host tools remain responsible for Exa search, saves, read state, ingestion, subscriptions, and every product mutation.

## 8. Chat context and request work

- No-tool chat does not acquire a sandbox.
- History is selected as newest complete user turns, including their assistant and tool sequence. Individual rows are never cut into invalid model history.
- The request budget reserves output tokens, tool schemas, system/context material, and the current prompt before allocating history.
- Historical tool results have a separate cap.
- Article/context loading, history loading, and key resolution overlap where their dependencies allow it.
- Model request count and VM command/output bounds remain explicit.

## 9. Feed discovery and host processing

Feed discovery uses the singleton `user:0` sandbox. It starts deny-by-default, temporarily allows only candidate hosts, performs a parallel curl batch in one command, parses bounded JSONL stdout, and resets the network policy before releasing the lease. A PostgreSQL advisory lock serializes network-policy changes across workers.

The system sandbox uses full-memory auto-pause for warm reuse. It has no corpus and no snapshot checkpoint. If it disappears, Newsly creates it from the canonical template.

Configured RSS/Atom/podcast ingestion runs through the hardened host HTTP client. Analyze URL and bounded non-YouTube media download also run on the host. The shared bounded-public transport performs public DNS validation, IP-pinned dispatch with correct Host/SNI, redirect validation, content-length checks, streaming bounds, and consistent error categorization for both in-memory and file sinks.

## 10. Reliability and fanout changes from review

- Capability manifests reject valid JSON that is not an object.
- Lifecycle cache ownership lives in `E2BSandboxPool`; session classes contain only command and file I/O.
- One revision-keyed capability cache replaces per-namespace duplicate state.
- Briefing ready-news fanout resolves visible users once, loads read state once, inserts pending rows in bulk, groups pending counts, and enqueues corpus-sync plus refresh requests in one queue call.
- Agent-data sync requests and Briefing refresh requests have one canonical builder each.
- Feed resolution requires the batch detector contract directly.
- Bounded public fetch and download share one security-sensitive redirect/streaming implementation.
- Stale personal-library routing, unused registration arguments, compatibility aliases, Volume modes, and full-corpus event paths are removed.

## 11. Validation evidence

Local and live validation on 2026-08-23:

- The migration integration test passed upgrade/downgrade/upgrade through head `20260823_01`; the local database reports that head.
- The 246-test lifecycle/data/chat/feed/Briefing/migration focus passed. The full Python suite completed with 2,835 passing and 40 skipped tests; its only failures were the two pre-existing, unrelated Learning Deck source assertions for portrait-chat state and hosted-control marker text.
- Repository Ruff lint, formatting for all 1,056 Python files, MyPy over 499 application modules, generated-contract checks, development and production Compose renders, template validation, focused high-confidence Vulture, and `git diff --check` passed. The full suite also covered the module-size and architecture guards.
- The canonical template built in 24 seconds as revision `newsly-agent-71aaec31f39836e1`. That revision now covers the Dockerfile, host hardening, and corpus installer rather than the image alone.
- A live user canary passed cold full hydration, direct-corpus write rejection, workspace write/edit, memory-preserving pause/resume with a live process, delta hydration and tombstones, clean-snapshot recovery with only later revisions, and sudo revocation. Cold full activation took 10.37 s; memory resume plus delta took 1.03 s; snapshot recovery plus delta took 3.27 s.
- A real no-tool chat took 2.26 s and performed zero VM acquisitions. The first forced `execute_bash` turn took 13.18 s, including 8.77 s of VM acquisition; after a real memory pause, the forced tool turn took 4.30 s, including 848 ms of VM acquisition.
- Full feed discovery independently found and validated `https://lucumr.pocoo.org/feed.atom` in both samples. Cold discovery took 18.21 s, including 9.07 s of VM acquisition; after a real memory pause it took 10.41 s, including 273 ms of VM acquisition.
- Every timing canary removed its sandbox, snapshot, temporary user, mirror, task data, and durable VM state. The local database finished with no canary users, no system VM state, and no persisted user VM IDs; E2B listed no matching live canary and no snapshots.

Production measurements remain post-deployment work: chat p50/p95 by no-tool versus VM-tool turns, warm activation p50/p95, E2B vCPU-hours, snapshot storage, corpus drift, and feed command count.
