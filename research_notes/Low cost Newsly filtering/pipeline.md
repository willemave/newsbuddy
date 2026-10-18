# Current Newsly pipeline and low-cost filtering seams

## What exactly is selected and ordered today?

### Takeaway
The current local checkout does not implement a single global "top 10 newest" ranker. It has per-source discovery caps, chronological user feeds, and later bounded Briefing admissions; semantic categories organize coverage rather than learn relevance. These findings describe source code, not independently verified production behavior.

### Cited Findings
- Feed and Reddit target limits default to 10, configurable 1–100. RSS traverses source document order and stops once the selected-item cap is filled; it does not explicitly sort entries by publication time. It may stop at the first known URL. — [Target construction](/Users/willem/Development/news_app/rust/crates/newsly-worker/src/scrape.rs:460), [feed loop](/Users/willem/Development/news_app/rust/crates/newsly-providers/src/scraping/feed.rs:64)
- Reddit fetches `/new.json` using that limit, so it truly uses newest source candidates. Hacker News instead fetches its upstream `topstories.json` and takes 15 IDs; clustered RSS aggregators take 25; SciURLs/FinURLs take up to 100. — [Reddit](/Users/willem/Development/news_app/rust/crates/newsly-providers/src/scraping.rs:630), [HN](/Users/willem/Development/news_app/rust/crates/newsly-providers/src/scraping.rs:230), [other aggregators](/Users/willem/Development/news_app/rust/crates/newsly-providers/src/scraping.rs:164)
- News API defaults to 25 items (max 100), paging reverse chronological publication date, then processed/ingested/created fallback and ID. This is a page size, not a permanent top-k selection. — [API](/Users/willem/Development/news_app/rust/crates/newsly-api/src/content_read.rs:107), [query](/Users/willem/Development/news_app/rust/crates/newsly-db/src/content_read.rs:404)
- Content feed similarly sorts publication date / processed / created descending and filters ready, non-skipped accessible items. — [content feed](/Users/willem/Development/news_app/rust/crates/newsly-db/src/content_feeds.rs:179)
- Briefing normal news admission is a bounded batch of 40, balanced by platform/topic using row_number and then newest arrival; initial onboarding freezes its bounded cohort. Full rebuild has no such batch cap. — [batch constant](/Users/willem/Development/news_app/rust/crates/newsly-db/src/briefing_refresh/preparation.rs:21), [admission SQL](/Users/willem/Development/news_app/rust/crates/newsly-db/src/briefing_refresh/preparation.rs:265)
- Briefing lens API page size is at most/default 12 segments, ordered segment created_at DESC and ID DESC. Max news lenses=10 is another unrelated cap, and normal news composition window default is 4 events. — [lens route](/Users/willem/Development/news_app/rust/crates/newsly-api/src/briefing.rs:169), [lens query](/Users/willem/Development/news_app/rust/crates/newsly-db/src/briefing.rs:483), [worker settings](/Users/willem/Development/news_app/rust/crates/newsly-worker/src/briefing_refresh/config.rs:24)
- Global news visibility follows a user's active aggregator subscriptions and topic gates; user-owned news is separately scoped. — [visibility](/Users/willem/Development/news_app/rust/crates/newsly-db/src/news_visibility.rs:3)

### Inferences
- Increasing a page size alone cannot fix relevance. Ranking only an already truncated source pool cannot recover good unseen candidates.
- Broadening cheap metadata discovery should be a separate budget from downstream expensive admission; preserve actual source ordering and observed timestamps for evaluation.

### Gaps
- Production configuration overrides, actual source volume, user-facing current screen, and real cost distribution were not queried in this read-only local-code subtask.

## What models, reusable features and feedback already exist?

### Takeaway
Newsly already pays for rich news embeddings after summary/enrichment and caches them. They are an excellent low-incremental-cost basis for an offline personalized ranking experiment, but cannot save work already performed upstream.

### Cited Findings
- The default lens embedding is `openrouter:qwen/qwen3-embedding-8b`. — [default](/Users/willem/Development/news_app/rust/crates/newsly-domain/src/lib.rs:38)
- Embedding input is title plus generated summary plus key points; cache lookup requires exact input hash/model/encoder version and valid dimensions. Dimensions are learned from provider response and checked stable; no fixed dimension is assumed here. — [input](/Users/willem/Development/news_app/rust/crates/newsly-db/src/briefing_refresh.rs:129), [cache](/Users/willem/Development/news_app/rust/crates/newsly-db/src/news_lens_embeddings.rs:24), [dimension guard](/Users/willem/Development/news_app/rust/crates/newsly-worker/src/briefing_refresh/warm_news.rs:148)
- Warmup is eager as well as demand-driven: news finalization queues PrepareNewsLens before Briefing fanout, and the scheduler fills missing/stale recent-global vectors in batches up to 128. This counters a claim that only already-admitted items can have vectors. Actual coverage still needs measurement. — [finalization](/Users/willem/Development/news_app/rust/crates/newsly-worker/src/news_item/repository.rs:394), [scheduler](/Users/willem/Development/news_app/rust/crates/newsly-scheduler/src/repository/fanout.rs:114)
- A general quality classification (`to_read` vs `skip` for duplicative/empty/promotional/low-signal) is already generated and stored, but News-list/Briefing eligibility queries do not apply it. Test it as a zero-new-inference baseline, not as personalized truth. — [rubric](/Users/willem/Development/news_app/rust/crates/newsly-providers/src/news_item.rs:30), [stored flag](/Users/willem/Development/news_app/rust/crates/newsly-worker/src/news_item/input.rs:452), [News query](/Users/willem/Development/news_app/rust/crates/newsly-db/src/content_read.rs:380), [Briefing query](/Users/willem/Development/news_app/rust/crates/newsly-db/src/briefing_refresh/sources.rs:155)
- Cache source loader accepts only ready representative news rows, and warm preparation embeds only when exact matching cache is absent. — [source](/Users/willem/Development/news_app/rust/crates/newsly-db/src/news_lens_embeddings.rs:68), [preparation](/Users/willem/Development/news_app/rust/crates/newsly-worker/src/briefing_refresh/warm_news.rs:102)
- News relation matching has a different embedding stage, after short-form summary generation. Exact relations can bypass that stage; relation vectors should not be conflated with preference labels or topic lens vectors. — [summary then relation](/Users/willem/Development/news_app/rust/crates/newsly-worker/src/news_item/handler.rs:405), [relation embedding](/Users/willem/Development/news_app/rust/crates/newsly-worker/src/news_item/handler.rs:527)
- A separate current-batch Briefing event grouping uses embedding dot product against event centroids plus overlapping title tokens; its default threshold is 0.78. This is a composition grouping heuristic, not a durable user novelty truth. — [event grouping](/Users/willem/Development/news_app/rust/crates/newsly-worker/src/briefing_refresh/planning.rs:231), [threshold](/Users/willem/Development/news_app/rust/crates/newsly-worker/src/briefing_refresh/config.rs:41)
- Semantic lens matching compares story vector to lens centroids, assigns above configured category similarity, and otherwise discovers new topic clusters or misc. It is category routing, not user preference estimation. — [semantic assignment](/Users/willem/Development/news_app/rust/crates/newsly-worker/src/briefing_refresh/semantic_lenses.rs:128)
- Feed metadata already includes title, source/domain, feed name, author, dates, full RSS body or description if available, tags, word count, and podcast duration. — [metadata construction](/Users/willem/Development/news_app/rust/crates/newsly-providers/src/scraping/feed.rs:94)
- News open tracking also has a current-code ID-type mismatch: the app submits the News ID, while the generic interaction repository validates only a contents ID. Historical incidence is unknown, so audit opens before treating them as labels. — [iOS caller](/Users/willem/Development/news_app/client/newsly/newsly/ViewModels/ContentDetailViewModel.swift:421), [repository](/Users/willem/Development/news_app/rust/crates/newsly-db/src/interactions.rs:66)
- Existing structured analytics interaction enum exposes only `Opened`; generic `user_feedback` is free-form product feedback with app/device fields. These are not keep/skip preference labels. — [interaction contract](/Users/willem/Development/news_app/rust/crates/newsly-contracts/src/mutations.rs:36), [feedback persistence](/Users/willem/Development/news_app/rust/crates/newsly-db/src/interactions.rs:6)
- Read state is not a strong preference label: Briefing marks a segment read after it passes above the readable viewport and marks its entire source batch read. — [Briefing laws B11/B12](/Users/willem/Development/news_app/docs/laws/briefing.md:27)

### Inferences
- Start with local offline fitting of a regularized linear/logistic personalized head on existing cached vectors, plus transparent recency/source/length features. Serving a dot product in Rust needs no GPU or separate Python authority.
- For a pre-extraction cost gate, use the information present at discovery. Train/evaluate its model with metadata-only snapshots; training on generated summaries leaks information unavailable at decision time.
- Existing rich news vectors cover neither raw undiscovered items nor all Article/Podcast items; claims of universal reuse would be incorrect.
- Explicit keep / less like this / irrelevant topic / already know / duplicate / not now labels are more useful than silently treating unread as rejection; an occasional pairwise choice can express a limited reading budget.

### Gaps
- No measured label dataset size, behavioral quality, held-out lift, local hardware throughput, or fixed vector dimensions were established. These require data inspection/benchmarking before choosing final model or deployment shape.

## Where should a low-cost implementation fit and what must remain true?

### Takeaway
Use two stages with separate goals: first a reversible ranking experiment on existing data; later, if validated, a metadata-first admission gate before extraction and summaries. Keep personalized decisions user-scoped and retain uncertain/deferred material.

### Cited Findings
- Scrape finalization persists content/news and queues ProcessContent or EnrichNewsItemArticle plus discussions. This is the cost boundary a future admission decision must precede, while preserving durable queue finalization. — [fanout](/Users/willem/Development/news_app/rust/crates/newsly-worker/src/scrape.rs:757), [downstream queue](/Users/willem/Development/news_app/rust/crates/newsly-worker/src/scrape.rs:895)
- News enters Briefing only after ready status, so changing Briefing admission can reduce composition or reading load but does not by itself reduce upstream extraction/summary cost. — [eligible news](/Users/willem/Development/news_app/rust/crates/newsly-db/src/briefing_refresh/sources.rs:149)
- Every admitted eligible source must remain reachable exactly once; pending backlog must remain bounded per run without silently marking deferred items read. Existing categories, citations, read identities, and narration are preserved during routing changes. — [B6](/Users/willem/Development/news_app/docs/laws/briefing.md:17), [B15/B17](/Users/willem/Development/news_app/docs/laws/briefing.md:35)
- Runtime authority is Rust, PostgreSQL and its durable queue. Python is bounded extraction or offline evals; provider work occurs outside transactions and publication is fenced. — [architecture](/Users/willem/Development/news_app/docs/architecture.md:24), [dependency direction](/Users/willem/Development/news_app/docs/architecture.md:112)

### Inferences
- Proposed first slice: capture a broad candidate snapshot, add explicit user labels, fit/evaluate baseline models offline, record shadow decisions with feature/model/policy versions, and compare same-day/event-held-out relevance at the same reading budget. Do not deploy a generative classifier without beating a cheap baseline.
- Any Recommended query must rank eligible candidate rows before SQL LIMIT/keyset paging, not rerank each already-returned page of 25. Scores and preference/model versions need a stable per-user feed snapshot or rank-generation token for cursor consistency, so changes mid-scroll cannot duplicate/drop items. Keep the first experiment offline; eventual missing/stale score behavior should preserve usable chronological reading, with no synchronous model/provider call in the read path. Current date-based cursor SQL is not directly reusable for a dynamic score order.
- A Recommended / All accessible-items view is more reversible than destructive filtering. Ranking-only initially delivers user-value while keeping existing full coverage laws explicit; a permanent admission exclusion changes behavior/laws and requires deliberate definition.
- A global aggregator corpus serves multiple users; never let one user's dislike suppress shared ingestion for other subscribers. Cheap collection and expensive shared enrichment require a separate aggregate demand/budget policy.
- Ordinary RSS uses StopAtKnown (scrape.rs:474), while backfill uses SkipKnown (feed_backfill.rs:120). Persisting rejected candidates into the same known-URL set could strand discovery; retain separate candidate identity, admission state, and cursor semantics. Widening cheap discovery at the same processing budget improves selection opportunity without promising spend reduction.
- Rank before per-source truncation; use per-source or topic diversity budgets and a small exploration allocation to avoid burying new interests. Sparse metadata should get abstain/defer, not a confident hard rejection.
- Identity deduplication, same-event grouping, topical similarity, and user novelty are four different concepts; do not infer "already know" merely from an embedding cosine threshold.
- Avoid a new database-owning Python model service. A Rust numerical head is simplest; a later local inference provider can return scores while Rust retains policy, snapshots, durable intent, leases, and publication authority.

### Gaps
- Final UX preference (prioritize vs hide; separate News vs Articles/Podcasts; chosen reading budget) still matters. Research does not authorize product implementation, production mutations or paid model eval batches.
