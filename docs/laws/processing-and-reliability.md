# Processing and Reliability Laws

P1. Durable database state is the authority for long-running work.

P2. Product state and the job that advances it are created together in one transaction.

P3. Every task type has one owning queue and a validated payload contract.

P4. Deduplication may share underlying work, but every authorized requester still receives explicit access to the result.

P5. User-owned work rejects inactive owners and payloads that claim a different owner.

P6. A worker may start paid provider work or publish partial or terminal state only while it owns the live lease and current attempt generation. Superseded work cannot finalize.

P7. Retryable failures use a bounded retry budget, terminal failures stop, prerequisite deferrals preserve that budget, and public status remains truthful. Expired execution attempts consume that budget, bounded execution deadlines prevent endless lease renewal, and terminal queue failure settles the corresponding active product workflow. Source HTTP retry delays are respected, with positive jitter spreading recovery attempts.

P8. Cancellation or deletion prevents stale work from publishing later.

P9. Successful independent results remain durable when another item in the same batch fails. Each configured source or global aggregator has an independent retry boundary; a malformed entry cannot repeatedly hide later usable feed entries.

P10. Paid provider work has time and size bounds. Durable success and resumable provider identities are reused instead of starting duplicate work.

P11. Queue notifications, client caches, and progress indicators reconcile with durable state.

P12. Public APIs and generated clients change together, while active external contracts keep compatibility until their stated removal condition. Every response field promised by the contract is present on the wire, including explicit `null` for nullable values; request construction defaults do not weaken response guarantees, and server-owned timestamps use typed RFC 3339 UTC values rather than sentinel strings.

P13. New RSS and Atom candidates are validated through bounded public HTTP with DNS-pinned dispatch, public-network redirect revalidation, concurrent probes, strict time and size limits, and inert Rust parsing. Accepted RSS and Atom documents have no byte cap and use the same DNS-pinned, redirect-revalidated dispatch with a total download deadline; page analysis and non-YouTube media remain size-limited. Downloaded data cannot execute work on the application host. Browser, shell, or model-authored execution remains sandbox-only.

P14. Sandbox-backed work writes only inside its task workspace, rejects every other path, and never exposes provider paths in user-facing failures. Every fresh canonical sandbox must lose the provider's default passwordless sudo grant before model or tool access.

P15. Operator repairs are scoped and auditable. A liveness check alone never proves that workers, queues, and providers are healthy. Source failures, overdue work, and terminal queue/product mismatches are separately observable without automatically replaying terminal work.

P16. Every sandbox-backed LLM task attempt receives fresh isolated compute. No product workflow resumes, snapshots, pools, or shares a user sandbox, and normal completion kills the sandbox.

P17. Knowledge is read from canonical host storage through typed, user-authorized tools. The system maintains no per-user sandbox mirror, corpus revision, index, or sync queue.

P18. Copying Knowledge into a task workspace reauthorizes every reference before reading object storage, performs bounded source reads, enforces per-item and aggregate bounds, and atomically publishes a task-local manifest with checksums for the copied files. A sandbox receives no Newsly callback token.

P19. Once sandbox creation is dispatched, the host observes it through an ID or a definitive provider failure. It records the active sandbox ID before honoring cancellation, durably marks cleanup before killing it, and clears the exact ID only after a successful or not-found kill. Retries reap recorded sandboxes before creating new compute; stale attempts still cannot publish without their exact queue and task leases.

P20. Short-form processing reuses durable summaries for definitive duplicates before paid provider work, retains independent summarization for uncertain semantic matches, checkpoints a new summary before later enrichment can fail, invalidates that checkpoint when its source changes, bounds provider input, and generates optional enrichment only for unsuppressed representatives.

P21. Discussion comment collection and summary publication have independent cadences. The first usable summary is immediate; later input changes accumulate and coalesce by materiality and time, while stale changed summaries eventually refresh.

P22. Long external work never holds a database transaction, checked-out connection, or persistence-backed handle. Workers commit a short preparation transaction, execute from an owned immutable snapshot, and finalize through a fresh transaction that revalidates ownership and lifecycle state.

P23. Each route group, task type, and state writer has one active runtime owner. A worker claims only work matching its runtime; renew and finalize require the exact durable owner and executor-version stamps carried by that claim. Any authority change requires an acknowledged write barrier and compare-and-set promotion, and rollback affects only new work rather than silently reassigning an in-flight attempt.

P24. Production document extraction is a versioned, bounded, database-free Python boundary. It revalidates the public network for the initial URL, redirects, and browser requests, accepts no arbitrary crawler configuration, and fails closed without its private service credential. Its runtime receives no application database configuration and returns only typed extraction, delegation, fallback, or failure results. Rust alone owns durable state, retries, Firecrawl credentials and calls, usage persistence, and downstream enqueueing.

P25. Generated long-form artwork is derived from one immutable summary fingerprint. Provider and image-transform work runs without a database connection and writes only attempt-scoped staging files; the exact live lease must revalidate the current summary and lifecycle before publishing pointers to an immutable image and thumbnail pair, UTC cache version, and usage record. A failed or superseded attempt cannot overwrite published artwork. Unpublished artifacts are durably registered for bounded cleanup after a grace period, and referenced artifacts are retained. News rows never enter this artwork path.

P26. Discussion summaries and news key points expose their persisted character limits to generation and bounded validation correction. Invalid output never publishes. Exhausted discussion validation does not restart the same durable task; transient provider failures remain retryable, and later scheduled refreshes remain possible.

P27. Usage reports distinguish unknown cost from a known zero charge. Any aggregate containing unpriced calls has an unknown total and separately reports its known subtotal and unpriced call count. Missing historical prices are never invented.
