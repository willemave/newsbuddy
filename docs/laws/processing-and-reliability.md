# Processing and Reliability Laws

P1. Durable database state, not an in-memory worker or client spinner, is the authority for long-running work.

P2. Product state and the job that advances it are created in one transaction or not at all.

P3. Every task type has one owning queue and a validated payload contract.

P4. Deduplication may share underlying work, but every authorized requester still receives explicit access to the result.

P5. User-owned work rejects inactive owners and payloads that claim a different owner.

P6. A worker owns a task only through a live lease token; an expired or superseded worker cannot renew or finalize it.

P7. Retryable failure schedules a bounded retry, terminal failure stops after its budget, and both retain a truthful public status.

P8. Deferring work for an unmet prerequisite does not consume retry budget or masquerade as failure.

P9. Cancellation or deletion prevents later stale work from publishing a result.

P10. Successful partial results remain durable when another independent item in the batch fails.

P11. Paid or external work is bounded by deadlines and size limits and is not repeated after durable success.

P12. Queue notifications, client caches, and progress badges are hints; they always reconcile with durable state.

P13. Public API and generated client contracts change together, and active external contracts keep compatibility until their stated removal condition.

P14. Untrusted pages, feeds, files, and remote media are processed within bounded isolation and cannot execute arbitrary work on the application host.

P15. Operator repairs are explicit, scoped, and auditable; a liveness health check alone is never proof that workers, queues, and providers are healthy.
