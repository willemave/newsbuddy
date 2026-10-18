> **RAW ADVISORY REVIEW — NOT VERIFIED AS WRITTEN.** Read [the verification summary](oracle-verified.md) before relying on this report. In particular, the claim that lens embeddings are created only after a user admits items is false: finalization and the scheduler already warm embeddings independently. Related coverage-bias and mandatory-new-prewarming cost claims are rejected. Other qualifications, confirmed findings and exact modelUsage proof are in the verification summary.

# Independent review: low-cost Newsly filtering plan

**Short version:** the direction is sound (explicit labels, unread is not a negative, shadow first, no Python authority, a learned head before any LLM). But it puts the filter in the wrong place, misses free signals the system already computes, and assumes vector coverage that doesn't exist. The highest-value seam is the per-user Briefing admission step, not reordering what's already shown and not a shared pre-extraction gate.

## 0. What this review is based on

- I read checkout HEAD `48a21d7e` (Oct 11) plus 50 uncommitted files. Uncommitted work that matters here:
  - Firecrawl credit deferral (`newsly-db/migrations/20261014000000_firecrawl_credit.sql`, `news_item/credit_deferral_tests.rs`)
  - `news_item/handler.rs`
  - Briefing law B18 (long-press source actions), added in `docs/laws/briefing.md:45`
- The files I cite for scrape, feed, Briefing preparation, visibility and contracts match HEAD (no uncommitted changes to them).
- I ran no production queries, so everything below describes the code, not deployed behaviour. Production environment overrides (`BRIEFING_*`, per-config `limit`) are unknown.

## 1. Claims checked, with corrections

| Claim | Verdict |
|---|---|
| Feed/Reddit default 10, range 1–100 | ✔ `newsly-worker/src/scrape.rs:483-489`, `newsly-providers/src/scraping.rs:642` |
| **Correction:** feed truncation is worse than "may stop at first known URL" | Feeds run in `StopAtKnown` mode against the user's own known URLs (`scrape.rs:213,474`; `newsly-db/src/content_misc.rs:895`). The loop stops at the first known entry and caps at 10 (`scraping/feed.rs:69-77`). If more than 10 items arrive between runs, the extras are **lost for good**: the next run stops at the newest one already seen. Only the user-triggered "download more"/backfill path uses `SkipKnown` (`feed_backfill.rs:120`). |
| HN top 15 | ✔ `scraping.rs:241` |
| Reddit `/new` | ✔ `scraping.rs:638`. Its score and comment counts (`:793`) are snapshots taken near zero, so they're nearly useless as popularity features. Pulling hot/top instead is a free rules-level change. |
| Missing from notes | Brutalist takes 25 per topic over 24h (`scraping.rs:520`); arXiv and HF Papers are separate sources (`scraping/papers.rs`). |
| Briefing admission 40 | ✔ `newsly-db/src/briefing_refresh/preparation.rs:21`. **Correction:** this is a drain rate, not a filter (see §2.1). |
| Lens page 12, max news lenses 10, news window 4 | ✔ `newsly-api/src/briefing.rs:169`; `briefing_refresh/config.rs:28,25`. Also `event_similarity=0.78` at `:41`. |
| Embedding input is title + summary + key points; default `qwen3-embedding-8b` | ✔ `newsly-db/src/briefing_refresh.rs:129-140`, `newsly-providers/src/news_item.rs:25` |
| **Correction:** "ready representative news have embeddings" | Embeddings are created **lazily**, only for news that some user's Briefing has admitted to pending (`newsly-worker/src/briefing_refresh/handler.rs:162`, `warm_news.rs:227-266`). They're stored once per item and shared (`news_lens_embeddings.rs:63`), as JSON. So the cached vectors are a **sample biased by the current newest-first policy**. Articles and podcasts never get them (they use fixed lenses, `briefing_refresh.rs:12`). |
| Analytics enum has only `Opened` | ✔ `newsly-contracts/src/mutations.rs:36-39`. **New problem:** see §2.6. |

## 2. Design critique

### 2.1 Filter placement: add a middle stage and treat it as the main one

The notes describe two stages: reorder what's shown, then later a metadata gate before extraction. The code points to a third seam that matters more.

**How admission works today:**
- `seed_news_pending` (`preparation.rs:315-345`) admits 40 items per refresh from *all* unread, visible, ready news.
- There's no age limit: visibility is just subscription plus topic (`news_visibility.rs:9-19`; `aggregator_config_admits` in `migrations/20261002000000_aggregator_visibility.sql:64-78`).
- Order is round-robin across (platform, topic), newest first within each.
- Anything not admitted stays eligible on paper but is always pushed out by newer items.

That *is* the "top-N newest" behaviour the user is unhappy with, in policy form.

**Why this is the right place to rank:**
- It runs per user, after the shared extraction and summary work, and before the per-user paid steps: lens embedding, `briefing.compose_window` composition, and narration.
- Ranking here changes both what the user reads and per-user spend, without ever suppressing shared ingestion for other users.

**Why "reorder what's shown" is weak:**
- Segments are already composed, often as multi-source roundups.
- Lens pages are ordered by segment `created_at DESC` with fixed-version paging (B8).
- Reordering composed segments works against B5/B8 and saves nothing.

**Recommendation:** swap Stage 1 from "presentation ranking" to "shadow-scored admission priority at `seed_news_pending`". Keep the shared pre-extraction gate as a later stage, and apply it to long-form only (§2.4).

### 2.2 The Briefing laws need an explicit change, even for ranking

- B6 (`docs/laws/briefing.md:17`) only guarantees coverage for sources already *admitted*.
- B2 (`:7`) defines eligibility.
- Admission priority with no aging rule is just a hidden filter that works by starvation. The current policy already starves old items without saying so.

Any change should add a behavioural law along these lines:
- Admission is prioritised within a stated reading budget.
- Unadmitted eligible sources stay reachable in an All view and age out on a declared rule.
- Some admission slots are reserved for exploration and low-confidence items.
- Missing features get a neutral prior, not a low score. Otherwise "never hard-reject sparse unknowns" quietly becomes "never admit them".

**Existing All view:** the chronological news list (`newsly-api/src/content_read.rs:107+`) already serves this purpose. Check that it's still reachable in the current app shell; memory says the Long/Fast/More shell is due to be removed.

**Law/code mismatch:** B2 says skipped *news* is ineligible, but `visible_news_sql` (`briefing_refresh/sources.rs:149-160`) only checks `status='ready'`. I found no DB filter on the news summary's `classification`.

**Onboarding:** the first-edition cohort freezes using the same ordering (`preparation.rs:270-287`). Keep onboarding on the current policy, since a new user has no labels (cold start).

### 2.3 Free signals the notes miss (use these before training anything)

- **LLM quality flag already paid for.** Every summarised news item gets `to_read`/`skip` ("duplicative, empty, promotional, low-signal") from the summary prompt (`newsly-providers/src/news_item.rs:30-36`; parsed at `newsly-worker/src/news_item/input.rs:108-117`). Briefing ignores it. Applying it is the cheapest baseline there is, and it's a ready-made feature.
  - The long-form `contents.classification` filter exists (`content_feeds.rs:195`, `preparation.rs:366`), but processing inserts `'to_read'` (`content/repository.rs:753`). The only `skip` writer I found is X sync (`x_sync.rs:709`).
- **Popularity:** HN score and comment counts at scrape time (`scraping.rs:297`).
- **Coverage breadth:** how many sources share a `representative_news_item_id`, and event-group size (`sources.rs:630`).
- **Topic and history:** semantic lens assignment as a topic feature, plus the user's per-lens read rate.

### 2.4 News and long-form are different problems. Don't merge them into one "pre-extraction gate"

**News:**
- The corpus is global. Extraction (Firecrawl at $0.004/credit per `docs/log.md:3233`) and summaries are shared.
- One user's preference must never gate shared enrichment. The only per-user saving is downstream (§2.1).
- With roughly one user, "aggregate demand" is moot today, but the ownership rule should still be written down.

**Long-form feeds:**
- Each subscription is per user. Every new item triggers `ProcessContent` (extraction, summary, image generation via `summarization/fanout.rs:124-157`, and one Briefing segment per source).
- RSS body or description is often present at discovery (`scraping/feed.rs:111-120`), so a metadata gate has real features here, unlike news titles.
- This is where a gate before `ProcessContent` can actually save money.
- **Rule:** only gate items where no subscribed member's policy admits them. Deferred items get a visible deferred state, not silent loss.
- This changes processing and first-run progress semantics, so it needs its own law edits: `docs/laws/processing-and-reliability.md`, plus B15's "skipped items" in `briefing.md`.

### 2.5 Data availability and selection bias

- Cached vectors exist only for items that were admitted (§1), so they can't rank the pool that was never admitted.
- **For the offline pilot:** encode title + summary for the whole labelled sample with a free local encoder (MiniLM in `python/evals`), and compare against hosted vectors where they exist. That answers "do we need hosted vectors at all?" without changing the architecture.
- **For production shadow scoring:** someone has to pre-warm lens embeddings for every ready, visible news item. That's an explicit new cost, measurable under the `news.prepare_lens_embedding` usage operation. Approve it deliberately; don't let it happen by accident.
- **Dimensions:** `qwen3-embedding-8b` returns 4096 dimensions by default. The notes' "1,536" is only an example. With 4096 dimensions and around 300 labels, a logistic head will overfit unless it's strongly regularised or projected down. Always report it next to a cosine-to-positive-centroid baseline.

### 2.6 Feedback signals are worse than the notes say

- **Open tracking for news looks broken:**
  - For news, `ContentDetailViewModel` loads via `fetchNewsItemDetail` (`client/newsly/newsly/ViewModels/ContentDetailViewModel.swift:199-200`).
  - It then sends `trackContentOpened(contentId: fetched.id…)` (`:225-229`, `:421-431`).
  - The server checks that ID against the **`contents`** table only (`newsly-db/src/interactions.rs:64-75`).
  - So news opens are either rejected, or recorded against an unrelated content row with the same number. `context_data.content_type` may allow repair later.
  - This is likely but not confirmed: check the production rows before using opens as a signal at all.
- **Read state is unreliable for three reasons:** scroll-triggered reads (B11/B12), whole-lens mark-read (B12), and finished audio chapters marking their whole source window read (B16).
- **Saves:** saving a news source saves its linked *article* (uncommitted B18). Any join back to news must go through that mapping.
- **There is no negative signal anywhere.** The uncommitted B18 long-press menu is the natural future home for "Less like this" / "Already knew", recorded per source key.
- **For the pilot, skip app UX entirely.** Label offline from an operator export in a throwaway local tool. No contract or client changes until labels prove they're worth it.

### 2.7 Duplicate, same event, topic and novelty are separate mechanisms

The code already has three of these:

| Concept | Where it lives | Notes |
|---|---|---|
| **Identity / same story** | `representative_news_item_id`, from the exact relation key (`news_item/input.rs:219`) or relation-stage embedding matches | Relation vectors aren't stored, so this can't be reproduced offline from embeddings |
| **Same event** | Briefing groups by cosine ≥ 0.78 on lens vectors (`config.rs:41`) | Only within a composition window |
| **Topic** | Lens centroids, threshold 0.55 (`config.rs:29`) | Category routing, not preference |
| **Novelty for this user** | Doesn't exist | |

- "Already knew" is about the user's history, not the item. Model it as a feature: maximum similarity to the user's recently composed or read sources.
- For evaluation splits, group items by representative ID plus event cluster so the same story can't land in both train and test.

### 2.8 Ownership, latency and complexity

- **Ownership:** Rust keeps policy and scoring; training stays offline in `python/evals`; the model ships as a versioned JSON artifact (coefficients, scaling, feature order, embedding model and encoder version). No ONNX or sidecar until a learned head fails.
- **Latency:** scoring a few hundred JSON vectors per refresh is negligible.
- **Model shortlist:** SetFit, ModernBERT and Qwen are fine to list but out of scope. Unpinned "benchmark later" work tends to grow.
- **Cost evidence already exists.** Usage is recorded per operation (`summarize_short_form`, `embed_relations`, `prepare_lens_embedding`, `briefing.compose_window.observed`, `summarization.llm_summarization`, `firecrawl_scrape`). Query that before setting any savings target. The notes' "build a stage ledger" step is mostly already done.

## 3. Changes to the staged plan

1. **Stage 0 (read-only):** 30 days of `newsly-admin` usage broken down by operation. Plus:
   - ready news per day by platform, and admitted news per day;
   - share of eligible news never admitted;
   - share of news with lens vectors;
   - share of news the LLM marked `skip`;
   - long-form items per day per feed, and how often the 10-item cap is hit.
2. **Stage 1 (offline):** label a sample, compare policies at a fixed admission budget (§4).
3. **Stage 2 (only if Stage 1 passes):**
   - Rust shadow scoring at `seed_news_pending`: record policy version, score, rank, and whether the item would have been admitted. Don't change the ordering yet.
   - Then switch the ordering, with exploration slots and aging, after updating the laws.
   - Pre-warming embeddings for all ready news is an explicit cost decision.
4. **Stage 3:** a long-form gate before `ProcessContent` for items no one else is subscribed to, only if Stage 0 shows long-form processing is a meaningful cost. Use metadata and RSS body only; never summary-derived features.
5. **Discovery:** before relying on "broad discovery ahead of caps", fix the feed-burst loss. Two options:
   - capture every new entry as cheap metadata and cap only processing; or
   - record when the cap is hit, to measure the loss.

   For Stage 1, a one-off operator snapshot of the feeds is enough. No durable candidate table yet.

## 4. Smallest useful experiment, metrics, go/no-go

**Sample:**
- Take a 10–14 day window of ready, visible news (and optionally completed long-form).
- Stratify by admitted vs never admitted, by platform, and by LLM skip flag.
- Label about 250 items with `worth reading / maybe / skip`, plus an optional reason (wrong topic, already knew, shallow/promo, duplicate).

**Policies to compare** at the real daily admission budget K:
- (a) current round-robin newest;
- (b) rules: apply LLM skip, HN score, topic subscriptions, one item per event;
- (c) logistic regression on local-encoder vectors plus metadata;
- (d) the same on hosted cached vectors, using only items that have them;
- (e) cosine-to-centroid baseline.

**Splits:**
- Train on the earlier days, test on the last 3–5 days.
- Group by representative ID and event.
- Bootstrap confidence intervals over days.

**Metrics:**
- share of "worth reading" in the top K, and NDCG@K;
- recall of "worth reading" within K, compared with the full set of eligible items;
- duplicate-event exposures, and source/topic concentration;
- list every high-value item that each policy left out;
- one blind side-by-side daily-slate comparison per test day.

**Go/no-go:**
- **Go for Stage 2** if (b) or (c) beats (a) by about 15 percentage points or more on held-out "worth reading" share at K, with the confidence interval excluding zero and recall not lower. The user must also prefer the new slate on most test days.
- **Ship rules and stop** if (b) gets 80% or more of (c)'s gain. Learned models are justified only by gains beyond rules.
- **Local vs hosted:** if (c) is within noise of (d), local vectors are enough for Stage 1, but changing production to local embeddings is still a separate architecture decision.
- **Not enough data yet** if there are fewer than about 40 positives in test, or fewer than 3 test days. Collect more rather than decide.

## 5. Strongest likely objections

1. **The problem may be volume, not ranking.** With one user and topics already chosen, a per-lens daily budget plus applying the LLM skip flag might fix most of it. That's exactly why (b) is the bar to beat.
2. **Admission ranking is filtering by starvation.** It's honest only with a law update, aging rules, an All view and exploration slots.
3. **Personal scoring raises cost first.** Pre-warming embeddings for all news costs money before any composition savings show up. Savings depend on what the usage ledger shows.
4. **Labels from one person are noisy and get tiring.** The plan has to survive about 10 labels per day, or rely on the coming long-press feedback.
5. **The existing behavioural data is contaminated:** news open tracking, bulk and audio reads, and lazy-vector bias. Any model trained on it before §2.6 is fixed learns the current policy, not the user's preferences.