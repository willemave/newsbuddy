# Parallel News processing with guarded grouping publication

Status: proposed design, 2026-10-18. Design only; no runtime implementation, migration, concurrency increase, or deployment. Reviewed with explicit Claude Opus 5.5; material findings checked against the local source. Runtime measurements remain rollout gates.

## Decision

Keep `process_news_item` as the durable task. Run expensive preparation concurrently, but serialize the short transaction that validates the grouping decision and makes a story ready. The finalizer always runs the existing deterministic matcher against freshly queried candidates using validated cached embeddings. An advisory preflight decision is never publication authority. Missing prepared inputs preserve completed work and cause a bounded refresh; they never permit stale publication.

Separate article extraction from processing so extraction latency does not occupy a processing slot. Initially validate two processing slots, then evaluate higher concurrency using burst latency, provider usage, database contention, and extraction resource measurements. This document addresses the grouping correctness prerequisite, not a production capacity change.

The guarantee is equivalence, for the prepared inputs, to an allowed serial order of the current matching algorithm within a visibility/owner scope. It does not promise perfect semantic deduplication, change matching thresholds, or merge all preexisting duplicates. Representative identity can depend on commit order, as it already depends on arrival order; existing representative identities remain stable according to the current reconciliation rules.

## Evidence and current behavior

The October 17 checkup window had 666 extraction and 666 processing tasks. One shared worker spent 2.92 hours executing in 24 hours. A burst of 103 articles arrived in eight seconds and completed over 29.9 minutes. Article enqueue through processing completion was 12.1 minutes median and 26.6 minutes p95. These are the prior investigation's measured results, not performance promises for this design.

Current local implementation targets:

- `newsly-worker/src/news_item/handler.rs`: summary generation/checkpoint, candidate lookup, remote embeddings, local matching, optional relevant-link generation.
- `newsly-worker/src/news_item/repository.rs`: candidate discovery, source/candidate validation, ready-state publication, representative reconciliation, cluster recomputation and downstream fan-out.
- `newsly-worker/src/news_item/repository/checkpoint.rs`: existing exact-source summary checkpoint.
- `newsly-domain/src/news_relations.rs`: canonical embedding texts and deterministic relation decisions. Cluster aggregation is local; there is no remote merge-summary call in this path.
- `newsly-queue`: exact-lease checkpoint and finalization capabilities, including a wall-clock lease recheck before commit.

The current finalizer checks only previously loaded candidates. Two different articles can both see no ready candidate and each publish a representative. Their individual task leases do not coordinate that decision. Merely putting a mutex around the existing finalizer leaves both stale decisions intact. Also, `prepare_processing` currently demotes every item to `processing`, including an existing ready representative; `reconcile_relation` moves accepted candidate clusters but only updates the incoming item itself. Concurrent reprocessing therefore requires preserving published eligibility and correctly moving the incoming representative's existing members.

## Processing protocol

1. **Prepare outside the grouping guard.** Read an owned source snapshot, reuse or generate its summary, and checkpoint it under the exact task lease. Precompute its canonical title/content/provenance embeddings, including article-title variants used by candidate matching. An unlocked candidate preflight warms missing candidate vectors and determines whether relevant-link preparation is currently needed. There is no DB connection held during provider work.
2. **Persist reusable preparation.** Store validated embeddings under their exact model/encoder/text identities and source ownership. Record provider observations independently of product publication. Checkpoint the optional relevant-link result, including a completed-empty or failed-optional outcome, against its input identity. Prepared output remains private.
3. **Begin fenced finalization and try the grouping guard.** Use a transaction-scoped PostgreSQL advisory lock for the candidate visibility/owner scope. Use nonblocking acquisition; a busy guard causes a short, jittered deferral rather than waiting with a task row locked. No provider call, file read, or embedding generation occurs inside this transaction.
4. **Decide against current reality.** Revalidate ownership, source input and lifecycle. Query and prefilter the current ready candidates, with deterministic ranking/ties and the current lookback cutoff. Load exact cached vectors and run `related_representatives` in the transaction. Remove the old precomputed `accepted_ids` authority and old-list-only `candidate_fingerprints_match`; `ProcessMutation` becomes prepared evidence, not a publication decision. A freshly discovered exact match can proceed through the existing no-vector exact path.
5. **Missing input means prepare, not publish.** If the current decision requires a missing vector, roll back product work, return an explicit preparation-needed outcome, and let the next attempt fetch missing inputs outside the guard. The preflight on retry searches current candidates again. Aggregate provenance and newly combined title variants can create texts that no source precomputed; never replace them with a different embedding or skip the candidate. If a fresh decision needs relevant-link selection that preflight skipped, defer to perform that optional step outside the guard; its normal completed-empty/failure policy still applies. A newly redundant link result is not attached to the wrong representative.
6. **Publish atomically.** Lock affected rows deterministically, persist the source summary, reconcile root/member relationships, recompute affected cluster aggregates, mark new content ready, enqueue required downstream work, and complete the task in one transaction. All candidate-affecting writers participate in the same guard, so the decision remains current through commit.
7. **Commit through the existing fence.** Preserve the final wall-clock lease compare-and-set. Lease expiry rolls back all product and grouping writes. Lock release follows commit, rollback, or connection loss automatically.

A simple race becomes safe:

| Event | Worker A | Worker B |
|---|---|---|
| Parallel preparation | Prepares summary/vectors; preflight sees no candidate | Prepares summary/vectors; preflight sees no candidate |
| First publication | Fresh query finds none; publishes A | Preparing outside the guard |
| Next finalization | Already committed | Fresh query finds A; matcher joins A using cached vectors |
| If a vector is missing | — | Defers without publishing; prepares that vector, then reruns the fresh decision |

Unrelated tasks still prepare concurrently and their short publications serialize within their scope. There is no global revision that invalidates every in-flight decision after any publication, and no need to preserve an old query cutoff or candidate ordering across attempts.

### Reprocessing published groups

Keep an already-ready item's last committed representation eligible during reprocessing. The queue task conveys in-progress work; do not demote the published row to `processing`, `new`, or `failed` merely because replacement preparation is underway or fails. Fresh source/ownership checks still prevent publication of an obsolete replacement. This does not override deletion or an explicit visibility/lifecycle revocation.

If an existing representative joins another group, move the representative and **all its existing members** in the same guarded transaction. Resolve canonical roots, avoid self/ancestor cycles, and recompute affected aggregates. A member update must not leave the former root's aggregate stale. Preserve referenced news IDs and existing read/source provenance; no member may point at another member. Add explicit fixtures for these cases before allowing concurrent processing.

## Scope and writer discipline

Use precisely the same visibility/owner tuple as candidate discovery. All global candidates share a guard; each private owner has a separate guard. URL, publisher, topic and guessed cluster keys are insufficient: semantically related articles can have different values for all of them. Define stable, namespaced advisory keys; a key collision may only reduce concurrency, never broaden visibility.

Every operation that changes candidate eligibility, matching text, exact identity, representative membership or relevant source scope must participate. The implementation audit must include:

- All grouping finalization/reconciliation and lifecycle transitions that change candidate eligibility. Ordinary reprocessing must preserve published eligibility as specified above.
- Scrape persistence, which can update existing ready items or insert ready records.
- Re-enrichment and source changes affecting existing candidates.
- Candidate deletion, account deletion, scope changes, and operator repairs.
- Terminal state changes wherever they can alter candidate eligibility.

Polling metrics and unrelated discussion metadata need not invalidate relation decisions. Keep source and relation fingerprints deliberate. For multi-scope operations, acquire all required guards in a stable order before relation-affecting mutation; on contention roll back the whole attempted write, then retry outside the transaction. Avoid taking a blocking grouping lock after locking news rows. Integrate the ordering with existing ownership and aggregator-demand locks; use nonblocking acquisition so the new guard cannot add a blocking lock-order cycle. No concurrency increase is safe while an active writer bypasses this protocol.

Guarded candidate discovery uses a fresh READ COMMITTED statement snapshot after guard acquisition. Reusing a transaction-wide snapshot taken before acquiring the guard would defeat the protocol. Preserve the current 14-day lookback, 150-row query bound, effective candidate prefilter and matching thresholds. Guard every candidate-affecting write, not just accepted rows: a concurrent change to a rejected or previously undiscoverable candidate can change the correct result.

## Reuse, costs and bounded recovery

Reuse the existing source-fingerprinted summary checkpoint. Do not rerun summary generation solely because grouping changed. Add durable reuse of relation embeddings keyed by canonical text hash, provider/model, dimensions and encoder version. Use a feature-owned `news_relation_embeddings` table keyed by the originating news item plus provider/model, encoder version, dimensions and canonical text hash, with validated vectors and a foreign key cascading on item deletion. Scope every lookup through the authorized source item; do not add a cross-owner cache. Retire superseded entries through bounded maintenance. A representative change selects new hashes rather than overwriting the meaning of an old vector. The existing news-lens embedding table is not interchangeable: it embeds different text for a different decision.

Persist relevant-link preparation against its exact source/candidate-input and prompt/model identity if it would otherwise be regenerated on every grouping conflict. Cache metadata must not accidentally enter source fingerprints and invalidate itself. Preparation artifacts remain private until grouping publication succeeds.

Record paid embedding observations independently of product finalization with stable request/response identities, as summary usage already does. An obsolete preflight, crash or lost lease must not erase cost; replaying an observation must not duplicate it. A cache hit is not a new provider call. Two simultaneously discovered duplicate articles may still each need a summary before a reusable representative exists; this design does not promise cross-article single-flight billing.

Treat guard contention and missing prepared inputs as deferrals, not provider failures. Use bounded refresh work per attempt, jittered requeueing, and durable observation of first-ready time and repeated conflicts. Set an explicit overall conflict-age/deferral ceiling in implementation and test terminal settlement or operator-visible recovery; do not introduce unbounded deferral or an unsafe 'publish anyway' fallback. Choose the numeric ceiling from the existing task policy and the burst replay rather than add arbitrary new user-facing configuration.

## Alternatives

- **Higher processing priority:** improves median completion on a shared serial worker but does not make parallel grouping safe.
- **Single grouping worker after a parallel summary stage:** viable, but needs a new durable handoff/task contract and enforced singleton ownership. Candidate embedding calls still serialize that stage. Keep this as a fallback if measured optimistic conflicts are excessive, not an additional v1 execution path.
- **Per-story/URL locks:** insufficient for cross-URL semantic matches and bridge merges.
- **Provider work under the guard:** prohibited. Only cached-vector lookup and local matching run inside it; a cache miss takes the normal prepare/defer path outside the transaction.
- **Serializable isolation only for finalization:** does not repair a decision computed from an earlier transaction. Fresh matching is required; use the existing READ COMMITTED approach with an explicit writer guard.

## Implementation slices and rollout gates

1. Add the scope guard, fresh guarded matching, minimal vector storage, and published-group reprocessing fixes while still running one processing worker. Audit every relevant writer and add deterministic interleaving tests.
2. Make paid preparation reusable across contention and preserve embedding accounting across rejected publication. Keep schema/storage scoped to these artifacts rather than introducing a generic workflow engine.
3. Separate extraction capacity from processing; enable two processing workers only after all writer paths use the guard. Drain old workers during cutover: old code ignores the advisory protocol. Rollback must return to one processing worker before running old code.
4. Compare burst completion p50/p95, processing/extraction waits, conflicts per task, guard duration, DB waits, provider calls/cost per source and duplicate/representative integrity. Increase concurrency only when the measurements justify it. Keep existing ingestion backpressure initially.

Required isolated-PostgreSQL coverage:

- Two exact-duplicate items and two semantically matching, different-URL items both initially see no candidates: one canonical representative after completion.
- Two unrelated items can prepare concurrently and finish without needless conflict.
- Candidate appearance, removal, changed summary, changed exact key, changed representative and ranking-boundary movement affect the fresh decision, including changes to previously unselected candidates.
- Bridge merges preserve current conflict/detail rules, representative references and visibility boundaries; reprocessing keeps a published root visible and moves its full membership without chains/cycles.
- Source changes and deletion between preparation and publication cannot resurrect or publish stale content.
- Guard contention/crash, expired task lease and source-invalid checkpoints leave no partial product writes.
- Replayed grouping attempts reuse valid summary/embedding/link work; changed text/model/version cannot reuse invalid artifacts; usage persists exactly once per observed request.
- Old/new-worker cutover, deterministic lock ordering with scrape/account deletion, bounded conflict recovery and no database connection retained during provider work.

Proposed behavioral law: concurrent News processing must not create extra representatives solely from stale candidate observations; only a current, exact-lease grouping decision may publish. Published groups remain available during replacement preparation, root/member references stay canonical, and paid preparation remains reusable across deferrals. Update canonical laws and architecture when implementation changes behavior, not while this remains a proposal.

## Independent review and verified adjustments

Claude Opus 5.5 recommended fresh local matching under the guard instead of old-plan comparison. Verified `related_representatives` is pure local work and `recompute_cluster` does not call a provider. Adopted that simplification and verified the published-reprocessing/member-move hazards in `prepare_processing` and `reconcile_relation`.

Did not adopt the review's suggestion to guard only processing publication or leave ready-row scope changes as a documented gap: `persist_scraped_news` updates ready-row identity, scope, summaries and metadata, which can change discovery and matching even without changing ready status. These writes must coordinate. Chose nonblocking guard acquisition instead of the proposed five-second lock wait, avoiding held-connection waiters and savepoint-based timeout handling. Cache keys include provider, model, encoder version, dimensions and originating news item, rather than model/text alone; account and source deletion must remove their artifacts. The review's predicted embedding savings and suggested timing thresholds were unmeasured and are not promises in this design. Precomputing own-item vectors can increase calls for isolated or exact-match stories; measure that cost before expanding concurrency.
