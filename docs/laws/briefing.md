# Briefing Laws

B1. Briefing is a per-user, continuously updated reading edition, not a static global feed.

B2. Eligible sources are completed, unread, non-skipped, user-visible articles, podcasts, and news.

B3. Briefing never reveals a source the authenticated user could not open directly.

B4. A source key is the canonical unit of coverage, citation, and read state.

B5. Article and podcast segments cover one source each; news segments may combine several sources into one compact roundup.

B6. Lens names and ordering are stable for a representation, and lens counts describe active unread source coverage rather than the raw backlog.

B7. Every eligible source admitted to an edition remains reachable exactly once after duplicate reconciliation; refresh and compaction never drop unread coverage.

B8. One global version identifies one user's complete visible Briefing representation; every visible mutation bumps it.

B9. Briefing validators are private and user-scoped; an unchanged representation may return `304 Not Modified` without becoming an error.

B10. Paging a lens at one fixed version yields the same ordered segments, sources, read flags, and summary as the complete lens.

B11. Refresh appends and coalesces work; a failed refresh leaves the last usable edition intact.

B12. Local snapshots are cold-start caches, never authority, and stale readable content remains visible during recoverable refresh failures.

B13. A segment becomes read only after it was visible and the reader passes its midpoint; initial offscreen geometry never marks it read.

B14. Reading a segment marks its full source-key batch once, and a segment retires only when all of its sources are read.

B15. Marking a lens read covers every active source in that lens, including canonical representatives of duplicates.

B16. Read-only styling changes preserve scroll position; replacing the ordered document resets the lens to the top without interrupting the old readable view mid-refresh.

B17. Every source link, figure, discussion, and citation resolves to a source owned by that segment; unknown references are repaired or rejected before publication.

B18. Pullquotes are editorial callouts, not attributed verbatim quotations.

B19. Dig Deeper uses the reader's selected passage and only user-visible supporting material.

B20. First-run progress is durable and incremental; one unavailable source cannot block later sources or remove categories already ready to read.
