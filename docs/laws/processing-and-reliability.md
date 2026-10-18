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

Terminal item failures attributed to a configured source are visible in that source's user-facing status. Access gates such as paywalls and sign-in requirements are distinguished from other processing failures without exposing internal provider diagnostics.

Missing expected source checks, sustained queue growth, stalled ready work, historical intake, blocked artwork, and alert delivery state are observable. Alert delivery survives restarts, suppresses unchanged notifications, and reports recovery; external delivery is at-least-once. Intentional incident cancellations retain an explicit reason and preserve canonical content and real user history. Schema deployment does not authorize unbounded historical provider work.

P16. Every sandbox-backed LLM task attempt receives fresh isolated compute. No product workflow resumes, snapshots, pools, or shares a user sandbox, and normal completion kills the sandbox.

P17. Knowledge is read from canonical host storage through typed, user-authorized tools. The system maintains no per-user sandbox mirror, corpus revision, index, or sync queue.

P18. Copying Knowledge into a task workspace reauthorizes every reference before reading object storage, performs bounded source reads, enforces per-item and aggregate bounds, and atomically publishes a task-local manifest with checksums for the copied files. A sandbox receives no Newsly callback token.

P19. Once sandbox creation is dispatched, the host observes it through an ID or a definitive provider failure. It records the active sandbox ID before honoring cancellation, durably marks cleanup before killing it, and clears the exact ID only after a successful or not-found kill. Retries reap recorded sandboxes before creating new compute; stale attempts still cannot publish without their exact queue and task leases.

P20. Short-form processing reuses durable summaries for definitive duplicates before paid provider work, retains independent summarization for uncertain semantic matches, checkpoints a new summary before later enrichment can fail, invalidates that checkpoint when its substantive source changes, retains it across polling-only clocks and engagement counters, bounds provider input, and generates optional enrichment only for unsuppressed representatives.

P21. Discussion comment collection and summary publication have independent cadences. A first usable summary runs when an active eligible user has admitted, cited, or opened the item. Later changes coalesce by materiality and time while an unread admitted or citing user still needs them and the item is less than 24 hours old. Opening an item does not keep merge refreshes alive. Lost demand or claim ownership prevents new paid attempts and preserves the fetched comments and existing summary.

P22. Long external work never holds a database transaction, checked-out connection, or persistence-backed handle. Workers commit a short preparation transaction, execute from an owned immutable snapshot, and finalize through a fresh transaction that revalidates ownership and lifecycle state.

P23. Each route group, task type, and state writer has one active runtime owner. A worker claims only work matching its runtime; renew and finalize require the exact durable owner and executor-version stamps carried by that claim. Any authority change requires an acknowledged write barrier and compare-and-set promotion, and rollback affects only new work rather than silently reassigning an in-flight attempt.

P24. Production document extraction is a versioned, bounded, database-free Python boundary. It revalidates the public network for the initial URL, redirects, and browser requests, accepts no arbitrary crawler configuration, and fails closed without its private service credential. Its runtime receives no application database configuration and returns only typed extraction, delegation, fallback, or failure results. Rust alone owns durable state, retries, Firecrawl credentials and calls, usage persistence, and downstream enqueueing.

Firecrawl credit exhaustion is a shared recoverable prerequisite, not a terminal
article failure. Paid fallback pauses durably across workers while free extraction
continues. Deferred work retains its retry budget and article coverage; one bounded
queued request checks recovery at a time, and stale recovery results cannot clear a
newer pause. Exhaustion and recovery alerts survive process restarts. New schema or
credit replenishment does not authorize replaying historical terminal work.

P25. Generated long-form artwork is derived from one immutable summary fingerprint. Provider and image-transform work runs without a database connection and writes only attempt-scoped staging files; the exact live lease must revalidate the current summary and lifecycle before publishing pointers to an immutable image and thumbnail pair, UTC cache version, and usage record. A failed or superseded attempt cannot overwrite published artwork. Unpublished artifacts are durably registered for bounded cleanup after a grace period, and referenced artifacts are retained. News rows never enter this artwork path.

P26. Discussion summaries and news key points expose their persisted character limits to generation and bounded validation correction. Invalid output never publishes. Exhausted discussion validation does not restart the same durable task; transient provider failures remain retryable, and later scheduled refreshes remain possible.

Briefing has one bounded correction budget with actionable validation feedback. Exhausted unchanged input cannot immediately restart through a new queue job; materially changed input or generator/parser version is eligible, and unchanged input has a bounded later recovery time. The previous usable edition remains readable.

P27. Usage reports distinguish unknown cost from a known zero charge. Any aggregate containing unpriced records has an unknown total and separately reports its priced subtotal, public list-price and provider estimate portions, and unpriced record count. Missing historical prices are never invented.

New usage may carry a public list-price estimate only when its billable unit, exact model or resource size, and applicable service tier are known. Reports identify estimated portions separately from provider-reported charges. A known absence of an external provider charge is a zero charge; self-hosted infrastructure and fixed subscription fees remain separate. Mixed resource units must not be presented as one homogeneous quantity. A running E2B sandbox remains visible with unknown final cost; confirmed cleanup records its measured lifetime once, while a missing sandbox with an unknown stop time stays unpriced. Repeated cleanup or product finalization must not double-count one sandbox.

Observed Briefing usage is recorded independently of successful edition publication, including rejected output and accepted sibling units. Re-recording an observed attempt does not double-count it. Provider failures without observable usage remain explicitly unknown; absent token counts and historical responses are not reconstructed as facts.

Firecrawl invocations are recorded before dispatch and observed usage is retained
independently of content rejection, cancellation, or product finalization. Missing
provider credit quantities and absent account-specific marginal prices remain
unknown; configured marginal top-up estimates are distinguished from invoices and
fixed subscription fees. Local failure counters keep diagnostic event units
separate from provider credit units, and contain no article text or raw exceptions.

Each observed agent model response, generated-image response, audio-episode script-model response and synthesis chunk is recorded when its complete provider response is observed, independently of later decoding, validation, sibling request, stitching, storage, lease, or product-finalization outcomes. Stable request identity makes repeated observation idempotent. Cache reads and writes use exclusive input-token categories, and long-context multipliers apply to the full qualifying request. Failed requests without a successful response are not assigned inferred usage.

Each successful transcription chunk is likewise recorded before response decoding or content finalization. Measured duration is priced per completed chunk; estimated duration remains visible but unpriced. A later malformed response, sibling failure, lease loss, or content mutation cannot erase an already-observed provider response.


P28. Aggregator ingestion is shared across users. News aggregator checks target 15 minutes with active subscribers and 60 minutes without them, with a small tick grace so fetch duration does not skip the next scheduled slot. Research-paper aggregators (arXiv daily listings and Hugging Face Daily Papers) retain hourly subscribed and two-hour unsubscribed checks; arXiv contributes each paper once, on its first announcement in its primary category, and a day without announcements is a successful empty check. The scheduler owns routine checks; onboarding attaches from the shared corpus. A newly demanded Brutalist topic triggers a shared fetch even when an older fetch is already running. Queue retries retain their backoff, and backlog can delay checks without losing due state. Successful empty checks, failures, new-item counts, item preparation, and readiness remain distinct. Subscribed news sources with no successful check for 45 minutes are observable as stale; research-paper sources use three hours.

Brutalist fetches topics selected by active subscribers and admits at most 12 new items per topic and source section in a rolling 24-hour window. Concurrent fetches cannot bypass the cap. Existing items continue receiving metadata updates at the cap, and cross-listed items remain visible through every selected topic. A failed demanded topic remains a source failure even when another topic yields items.

Paid Firecrawl fallback for global aggregator news requires a currently visible active audience. Free extraction remains available. Audience transitions and denied-fallback publication cannot lose a newly eligible story; bounded recent retries also cover new cross-listed visibility. A body-backed story never adopts a metadata-only sibling summary or representative; an affected cluster may require one fresh model summary.

Reusable news lens preparation is keyed by canonical story input, encoder version, model, and vector dimensions. Source changes and lease loss fence publication. Global background preparation is bounded, and foreground demand can promote existing work without duplicating it or erasing retry delays. Retry fairness remains effective across task priorities. Repeated unchanged terminal preparation failures do not immediately create fresh paid attempts. Exact input/model/version checkpoints also identify worker deadline failures. Unchanged failures have a six-hour recovery window; changed inputs or models can proceed immediately. Embedding preparation has independent worker capacity, and content-ingestion backpressure does not pause preparation of already-ready news.

P29. Routine OpenAI text generation defaults to GPT-6 Luna, including content analysis and summaries, news enrichment, Briefing, onboarding, Learning Decks, share-action agents, tweet suggestions, discussions, and narration scripts. Chat retains its separate larger-model default. Explicit model overrides and existing chat selections remain authoritative; transcription, speech, images, and embeddings retain their specialized models.

P30. Nightly category maintenance has at most one durable run per user and local date. It starts and publishes only within that date's local night window, honors timezone revisions and a minimum twelve-hour interval between publications, and skips missed windows rather than catching up during the day. Disabled accounts and unsettled onboarding are ineligible. Duplicate scheduling, expired leases, stale source snapshots, or timezone changes cannot publish.

Reclustering reuses exact input/model/version embeddings and does not trigger archive re-embedding. Naming has a bounded batch, a maximum of two reserved attempts per run, and a shared daily token reservation limit. A superseded execution cannot adopt a replacement execution's claim or reserve naming work. Cancellation stops new naming attempts; an already-started call may finish recording its observed result and usage without publishing. Observed results and usage are recorded independently of publication; retries can reuse an exact-input successful result. No usable fit, provider failure, or exhausted budget preserves the current edition.

P31. Personal News inference uses bounded per-user requests, concurrency, and deadlines with no database connection held during provider work. Only an exact live queue claim can make its results durable. Decisions are reusable only for the same user, preference revision, canonical source, evidence, and evaluator. Retries cannot overwrite a successful decision with an outage, and all known charges and unpriced attempted requests remain distinguishable in usage accounting.


P32. Concurrent short-form preparation cannot publish a relation decision against
stale candidates. All candidate-affecting writers coordinate at publication;
representative merges preserve complete membership within the same visibility
scope. Reprocessing keeps the last usable story available. Contention and missing
preparation have bounded, observable recovery without spending provider failure
retries, and unchanged paid preparation is reused.

A request waiting for a shared browser cannot close or recycle a browser owned by
another request. Admission waiting and owned execution share one total deadline;
only the request holding that browser may recycle it after failure or cancellation.
