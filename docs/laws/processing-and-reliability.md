# Processing and Reliability Laws

P1. Durable database state is the authority for long-running work.

P2. Product state and the job that advances it are created together in one transaction.

P3. Every task type has one owning queue and a validated payload contract.

P4. Deduplication may share underlying work, but every authorized requester still receives explicit access to the result.

P5. User-owned work rejects inactive owners and payloads that claim a different owner.

P6. A worker may start paid provider work or publish partial or terminal state only while it owns the live lease and current attempt generation. Superseded work cannot finalize.

P7. Retryable failures use a bounded retry budget, terminal failures stop, prerequisite deferrals preserve that budget, and public status remains truthful.

P8. Cancellation or deletion prevents stale work from publishing later.

P9. Successful independent results remain durable when another item in the same batch fails.

P10. Paid provider work has time and size bounds. Durable success and resumable provider identities are reused instead of starting duplicate work.

P11. Queue notifications, client caches, and progress indicators reconcile with durable state.

P12. Public APIs and generated clients change together, while active external contracts keep compatibility until their stated removal condition.

P13. New feed discovery and validation run in bounded isolation with candidate-scoped egress and batched probes. Accepted RSS and Atom documents have no byte cap and download through the application client; page analysis and non-YouTube media remain size-limited. Every redirect stays on the public network, and downloaded data cannot execute work on the application host.

P14. VM-backed work writes only inside its task workspace, may read the owning user's credential-free corpus, rejects every other path, and never exposes provider paths in user-facing failures. A fresh canonical VM must lose the provider's default passwordless sudo grant before corpus or model access.

P15. Operator repairs are scoped and auditable. A liveness check alone never proves that workers, queues, and providers are healthy.

P16. Persistent VM ownership is durable and serialized across processes. Releasing a turn never kills or extends healthy idle compute; timeout pauses it with memory preserved for warm activation.

P17. The agent corpus is an event-maintained per-user mirror with bounded documents, checksummed files, an index, and a manifest written last. No committed corpus event may be absorbed by sync work that already rendered; backfill and reconciliation remain chunked and retryable.

P18. The host is authoritative for the agent corpus. Acquisition applies full or revision-delta hydration under the owning user lock, writes the manifest last, rejects remote revisions ahead of the host, and grants the VM no callback token.

P19. A user VM snapshot is a clean recovery checkpoint taken after canonical hydration and before user commands. Live pause/resume is the warm path; snapshot restore may apply later corpus revisions, and system/feed VMs with no user corpus use the canonical template instead.
