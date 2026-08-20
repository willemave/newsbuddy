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

P10. Paid or external work has time and size bounds. Durable success and resumable provider identities are reused instead of starting duplicate work.

P11. Queue notifications, client caches, and progress indicators reconcile with durable state.

P12. Public APIs and generated clients change together, while active external contracts keep compatibility until their stated removal condition.

P13. New feed discovery and validation run in bounded isolation. Accepted sources use size-limited application downloaders whose redirects stay on the public network, and downloaded data cannot execute work on the application host.

P14. VM-backed work uses one task workspace, rejects paths outside it, and never exposes provider paths in user-facing failures.

P15. Operator repairs are scoped and auditable. A liveness check alone never proves that workers, queues, and providers are healthy.
