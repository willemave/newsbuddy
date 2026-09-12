# Briefing Laws

B1. Briefing is a per-user, continuously updated reading edition.

Briefing and the Knowledge list share the same bottom-edge fade, kept low behind floating navigation so readable content above it stays clear.

B2. Eligible sources are completed, unread, non-skipped articles, podcasts, and news that the authenticated user can open directly.

B3. A source key is the canonical unit of coverage, citation, and read state. Briefing passage links retain their accent color and tap destination without an underline.

B4. Article and podcast segments cover one source each, while news segments may combine several sources into a compact roundup. News prose follows a concise newspaper-brief style: a single-event passage never exceeds 40 words, while a multi-event roundup targets 45-65 words and never exceeds 75. Linked descriptive noun phrases appear early and fit grammatically into their sentence without duplicating a predicate. Supporting prose selects useful context or material qualifications without repeating the link, substituting a feature inventory, or expanding merely to reach a target; thematic connections appear only when informative. A news segment is sized by the distinct events it covers, not by source count: sources covering one event always stay in the same segment, however many there are.

B5. Lens names and order remain stable within one representation, and counts describe active unread source coverage.

B6. Every admitted source remains reachable exactly once while eligible. Duplicate reconciliation, refresh, and compaction preserve that coverage.

B7. One user-scoped version identifies the complete visible Briefing. Every visible mutation changes it, and an unchanged private validator may return `304 Not Modified`.

B8. Paging at one fixed version returns the same ordered segments, sources, read flags, and summary as the complete lens.

B9. Refreshes may coalesce, but publication requires successful composition and unchanged version, source ownership, and eligibility. A manual refresh observes its exact durable task, so successful same-version completion terminates without waiting for a version-change deadline. Failed or stale work leaves the last usable edition intact.

B10. Server state remains authoritative. Local snapshots support cold starts, and recoverable failures, retries, or reopening preserve readable content while unfinished work resumes safely.

B11. A segment becomes read only after it was visible and its full rendered body passes above the readable viewport boundary. Initial offscreen geometry never marks it read.

B12. Reading a segment marks its full source batch once, and the segment retires when every source is read. Marking a lens read covers every active source and canonical duplicate representative. A successful read-mutation response is durable and visible to the next Briefing index read.

B13. Read-only styling preserves scroll position and cannot be reversed by an index response from before an accepted read mutation. Outside that reconciliation window, the latest server index remains authoritative even when its version is lower. Replacing the ordered document resets the lens to the top without interrupting the readable view during refresh.

B14. Links, figures, discussions, and citations resolve to sources owned by their segment, while invalid references are repaired or rejected before publication. Citation validation, coverage, rendering, and narration agree on actual Markdown links, including nested or escaped brackets and Unicode titles. Bare URI text, code, and images do not establish citation coverage. Article and podcast passages treat each source as a full work, use its available enriched context, and give its thesis, evidence or counterpoints, and significance substantive treatment. They identify each work by its title and its available publication or show name. Pullquotes are editorial callouts and never claim to be source quotations, while Dig Deeper uses the selected passage with user-visible support.

B15. First-run progress is durable and incremental. One unavailable source cannot block later sources or remove categories already ready to read. Healthy warm news attaches without another aggregator fetch or repeated matching story embeddings. The first news cohort is bounded before admission, remains stable across retries, and settles before remaining eligible news enters normal admission; deferred news is never silently marked read.

Enabled Articles and Podcasts lenses are available immediately, including while their feeds are still processing. Progress follows the run's admitted items, including reused content, and distinguishes discovery, preparation, readable publication, failure, and skipped items. A summary awaiting lens publication is still preparing. Empty tiers offer source management. Browsing a lens preserves ongoing progress, and refresh validators cannot hide progress changes. Terminal artwork failures and ineligible or archived items cannot keep a tier preparing forever. An abandoned first run expires after 24 hours without affecting its subscriptions, content, or normal processing. A completed aggregator check belongs to that run and does not regress when later polling discovers more news. Ready article and podcast publication never waits for missing news lens embeddings, and one failed story does not change healthy siblings' semantic assignment.

B16. Briefing audio is a grounded listening adaptation of the lens selected when Play is requested. Its complete eligible unread source set defines the program boundary, and playback never automatically continues into another lens. Article and podcast programs keep one titled source per chapter. News chapters may curate only the highest-signal details within that lens; naturally finishing a News chapter marks its complete planned source window read even when some sources were not spoken.
