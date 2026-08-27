# Briefing Laws

B1. Briefing is a per-user, continuously updated reading edition.

B2. Eligible sources are completed, unread, non-skipped articles, podcasts, and news that the authenticated user can open directly.

B3. A source key is the canonical unit of coverage, citation, and read state.

B4. Article and podcast segments cover one source each, while news segments may combine several sources into a compact roundup. A news segment is sized by the distinct events it covers, not by source count: sources covering one event always stay in the same segment, however many there are.

B5. Lens names and order remain stable within one representation, and counts describe active unread source coverage.

B6. Every admitted source remains reachable exactly once while eligible. Duplicate reconciliation, refresh, and compaction preserve that coverage.

B7. One user-scoped version identifies the complete visible Briefing. Every visible mutation changes it, and an unchanged private validator may return `304 Not Modified`.

B8. Paging at one fixed version returns the same ordered segments, sources, read flags, and summary as the complete lens.

B9. Refreshes may coalesce, but publication requires successful composition and unchanged version, source ownership, and eligibility. Failed or stale work leaves the last usable edition intact.

B10. Server state remains authoritative. Local snapshots support cold starts, and recoverable failures, retries, or reopening preserve readable content while unfinished work resumes safely.

B11. A segment becomes read only after it was visible and its full rendered body passes above the readable viewport boundary. Initial offscreen geometry never marks it read.

B12. Reading a segment marks its full source batch once, and the segment retires when every source is read. Marking a lens read covers every active source and canonical duplicate representative. A successful read-mutation response is durable and visible to the next Briefing index read.

B13. Read-only styling preserves scroll position and cannot be reversed by an index response from before an accepted read mutation. Outside that reconciliation window, the latest server index remains authoritative even when its version is lower. Replacing the ordered document resets the lens to the top without interrupting the readable view during refresh.

B14. Links, figures, discussions, and citations resolve to sources owned by their segment, while invalid references are repaired or rejected before publication. Article and podcast passages identify each work by its title and its available publication or show name. Pullquotes are editorial callouts and never claim to be source quotations, while Dig Deeper uses the selected passage with user-visible support.

B15. First-run progress is durable and incremental. One unavailable source cannot block later sources or remove categories already ready to read.
