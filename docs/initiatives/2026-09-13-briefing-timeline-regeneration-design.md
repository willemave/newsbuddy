# Preserve Briefing chronology through regeneration

Status: Proposal only. No runtime changes, migration, production repair, or regeneration authorized by this document.

## Recommendation

Keep a passage's reader-facing creation date stable when its stories are regenerated. Record generation time separately. Base the stable date on when those stories first appeared in this user's Briefing, not when a model last rewrote them.

Use the existing segment `created_at` and public `created_at` field for this logical timeline date. Add `generated_at` for the time the new segment was actually published. This is smaller than adding a second public timeline field and maintaining competing meanings for old and new clients. Document the distinction explicitly: `created_at` describes the reading timeline; it is no longer the physical row insertion time.

This refines the earlier conversational suggestion: publisher dates are not the right default. A months-old article newly added to Briefing should appear as newly introduced to the reader. Publisher dates remain source metadata.

## Evidence and limits

- `rust/crates/newsly-db/src/briefing_refresh/publication.rs`: full refresh compacts active rows, and `persist_segment` timestamps every replacement with the current clock. Ordinary compaction also uses this insertion path.
- `rust/crates/newsly-db/src/briefing_refresh/preparation.rs`: full mode includes eligible unread sources already represented by active segments. Sources awaiting preparation can publish in later successor sweeps.
- `rust/crates/newsly-db/src/briefing.rs`: lens order and pagination use `(created_at DESC, id DESC)`.
- `client/newsly/newsly/Views/Briefing/BriefingLensContentViews.swift`: date/time delimiters use cumulative four-hour distance between segment creation dates. The first segment has no delimiter.
- The investigation observed production AI & Society with 33 active segments created within 93 minutes; that entire list receives no delimiters under the current rule.
- A temporary native test with 60 segments preserved all 14 expected separators through real disk snapshot save/restore and view-model reactivation. This does not establish a visual reopen regression or the exact state of the user's device.

## Intended behavior

| Operation | Timeline behavior |
| --- | --- |
| Rewrite the same source set | Preserve its date. |
| Split a passage | Each output derives its date from the sources it actually covers. |
| Merge passages | Use the latest first-appearance date among the covered sources. |
| Mix existing and genuinely new sources | Use the new source's first-appearance time. |
| Move a source to another lens | Preserve its per-user first-appearance date. |
| Full refresh with delayed successor sweeps | Previously represented sources keep their dates even after their old rows become compacted. |
| Failed, cancelled, stale, or lease-lost publication | Do not assign dates or alter the visible timeline. |
| Return an old source to unread | Preserve its original first appearance; unread state does not make it a new story. |

The formula is `segment.created_at = max(first_briefed_at for each canonical source in the segment)`.

The maximum makes a passage containing genuinely new material appear at the newest relevant position. Using the minimum could bury new material beneath old stories. Dates belong to source coverage, so an LLM does not choose them.

There is an unavoidable limit: merging passages with different dates reduces several timeline positions to one; splitting may expose different constituent dates. Stable dates do not promise identical row order or delimiters after changed grouping. Equal dates retain the existing ID tie-breaker, so replacements can reorder within a tied date. No artificial time buckets will split one news event, preserving law B4.

This proposal preserves the current delimiter policy. Adding a first-row label or a delimiter on each calendar-day change is a separate UI decision and is not required to fix regeneration timestamps.

## Data ownership and publication

Add `briefing_source_timeline`, owned by `newsly-db`, keyed by `(user_id, source_kind, source_id)` with `first_briefed_at`. It is a durable per-user first-publication record, independent of active segment lifetime or lens assignment.

Why a small table: matching only active donor segments fails after full refresh retires them and successor sweeps publish later. Repeatedly reconstructing dates from archived JSON source membership makes historical rows a hidden runtime dependency. The explicit record also survives future segment-history retention changes.

Resolve canonical news representatives through the existing duplicate identity path. When two represented identities converge, retain the earliest first-appearance date across their aliases for that user. Reconciliation must merge these records atomically with identity changes; it must not accidentally assign a fresh date to the surviving representative. Account deletion and supported source-deletion/fixture-reset paths must include this table. Lens retirement alone must not delete it.

At final publication, within the existing exact-lease and version-fenced short transaction:

1. Validate publication, source ownership, and eligibility as today.
2. Resolve canonical covered source keys using Newsly-owned types.
3. Insert missing first-appearance records for successfully published sources, using one publication timestamp and conflict-safe insertion. Preserve existing records.
4. Derive each output date from its actual covered keys, then insert the segment with the stable `created_at` and current `generated_at`.
5. Commit source anchors, new segments, donor retirement, pending-source consumption, and version advancement together.

No external calls occur inside this transaction. Source anchors are never written merely because preparation began. Reject malformed/empty coverage rather than silently substituting the current date. Existing stale-publication guards remain authoritative. Include append, full refresh, ordinary compaction, and repair composition in the same persistence rule.

Keep `updated_at` for later row mutations, such as retirement. Use `generated_at` for generation recency and audit queries. Audit every existing `created_at` consumer before implementation: timeline ordering should retain it; operational generation-age or compaction-age decisions must explicitly choose the appropriate clock.

## Migration and existing data

1. Add `generated_at`, initially copied from existing `created_at`; those rows currently record their actual publication time.
2. Backfill per-user canonical source first appearance from the earliest retained segment membership, including compacted and retired rows. Use bounded set-based SQL/batches and inspect the query plan before a production run.
3. Derive stable dates for active/degraded segments using the formula above. Retain historical rows' original timestamps for audit; the source table becomes the timeline authority.
4. Advance each affected user's Briefing version once when visible dates/order change. Do not run a model or regenerate text for this repair.
5. Emit a read-only report before applying the data repair: affected users/segments, recoverable date ranges, absent or ambiguous historical membership, canonicalization conflicts, and expected timeline movements.

If history is missing, preserve the earliest verifiable appearance available. Do not infer an exact previous Briefing date from publisher time. Report the limitation: this prevents future drift but cannot guarantee recovery of already-lost chronology.

The backend cutover must prevent old workers from inserting unanchored rows during the backfill. Use an additive schema phase followed by a controlled worker/publication cutover and bounded final backfill. Specify the exact deployment ordering during implementation; do not rely on mixed worker versions interpreting `created_at` differently. Retain a rollback mapping of changed active dates. Rolling back code alone does not undo repaired timeline dates.

## API, paging, and iOS

Keep public `created_at`, its required-key behavior, and the existing ordering contract. No new required client field or Apple release is needed for the timeline correction. `generated_at` is initially internal/operator metadata.

Backfilled dates invalidate some existing cursor anchors. Current anchor-date mismatch maps to HTTP 400, whereas the client has a dedicated stale-cursor path for HTTP 409. Treat a valid old anchor whose date changed as `409 stale_cursor`, so the client marks the lens structurally stale and its next retry restarts from the first page. The current client does not automatically restart in this error branch; an installed app may briefly show its continuation retry affordance during the cutover. Do not reinterpret a stale cursor against the new order. Confirm the old-client recovery path with a real cached partial lens.

The version bump ensures existing cached complete lenses are replaced on revalidation. Snapshots may show the previous dates until that revalidation; their wire shape remains compatible and a schema bump is unnecessary. Verify cold start, warm return, offline fallback, ETag revalidation, and pagination while dates change. Preserve segment-exit read marking and existing generation/version guards when a visible document is replaced.

## Validation and delivery slices

1. **Schema and derivation:** migration fixtures with retained/missing history, canonical duplicates, per-user isolation, tied dates, merges, splits, old-plus-new coverage, and first successful publication. Verify account deletion and backfill query cost.
2. **Publication and reads:** isolated PostgreSQL tests for full refresh followed by delayed sweeps, compaction, retries, rollback, malformed coverage, cancellation/lease loss, stale versions, eligibility changes, and atomic anchor assignment. Check ordering and full-versus-paged parity after date repair.
3. **Client recovery:** old cursor returns recoverable stale status; existing contract drift checks pass; native cached complete/partial lenses revalidate. Capture screenshots before regeneration and after reopening with unchanged source sets spanning several days. Dates and delimiters must match, including after scrolling beyond the first page.
4. **Release and historical repair:** review the dry-run report and deployment sequence before separately authorized release/data repair. Prove affected dates, coverage, ordering, and version changes afterward. No paid composition eval is needed unless implementation also changes composition behavior.

Implementation updates should add the behavioral invariant to `docs/laws/briefing.md`, document ownership in `docs/architecture.md`, and record execution in `docs/log.md`.

## Alternatives considered

- **Carry one donor segment date:** smaller, but ambiguous for splits/merges and insufficient for delayed publication after a full refresh. Rejected as the canonical design.
- **Always use source publication date:** changes the product to publication chronology and can bury newly added older material. Not recommended for this request.
- **Add public `timeline_at` and preserve physical `created_at`:** cleaner naming, but requires client migration and a deliberate compatibility bridge for installed apps. Prefer stable existing wire semantics plus internal `generated_at` for this scoped fix.

## Review status

Repository evidence and the existing lifecycle tests informed this proposal. The required independent Fable review was attempted with read-only plan permissions, but Claude Code returned `Not logged in · Please run /login`. No other model was substituted. Migration/cutover, duplicate reconciliation, and cursor recovery remain unreviewed by Fable and should receive that review before implementation. No source code or production data was changed for this proposal.
