# Aggregator Corpus, Demand-Sensitive Cost, and First-Run Tier Progress

**Date:** 2026-09-02
**Status:** Proposed. Validated against the current Rust checkout and two local databases.
**Scope:** aggregator refresh cadence, per-item enrichment gating, onboarding attach path,
first-run Briefing progress for the fixed Articles and Podcasts lenses.

## What was validated

The earlier proposal assumed Newsly needed to build a shared, pre-processed aggregator corpus.
It already has one. The corrections below drive the rest of this design.

| Earlier claim | What the code does today |
| --- | --- |
| "Stop treating aggregator ingestion as synchronous onboarding work" | The scheduler already enqueues one `scrape` task for `sources: ["all"]` every 15 minutes. All seven aggregators are fetched, extracted, summarized, and embedded once, globally, whether or not anyone subscribes. Onboarding adds a second targeted scrape of the same sources to drive the source chips. |
| "New users only trigger lens construction" | Already true for the corpus. Onboarding completion enqueues `briefing_refresh` first, and refresh preparation seeds every visible, ready, unread global news item into the user's pending set. The slow part is what follows: embedding every pending item per user, clustering, naming up to ten lenses, then composing only the first window per lens per task. |
| "Build a durable run-to-item table for tier progress" | Not needed. Content rows carry `status` (`new`, `processing`, `awaiting_image`, `completed`, `failed`, `skipped`) and news rows carry `status` (`new`, `processing`, `ready`, `failed`). Tier progress is a count over rows the user can already see. |
| "Articles and Podcasts benefit from the aggregator corpus" | They do not. Aggregators feed the news tier only. Articles and Podcasts are the fixed lenses fed by the user's own feed subscriptions through `backfill_feeds` (two items per feed) and, for podcasts, transcription. |
| "$1.07 for 724 items" | Snapshot taken mid-import. The completed import was 1,202 items and 962 summarizations. The per-item rate was right; the volume was not. No LLM row carries `cost_usd`, so every dollar figure comes from external price sheets. |
| "Instrument seven days of production intake" | The production-synced copy shows no aggregator items after 2026-07-21 and one news summarization in the last 14 days. Production intake must be confirmed running before cadence tuning means anything. |

## Cost model

Prices used: GPT-5.6 Luna at $0.20 input and $1.20 output per million tokens (effective
2026-07-30), Qwen3-Embedding-8B on OpenRouter at $0.01 per million, Firecrawl at $0.00083 per
page on the Standard plan. Token volumes are from the local `newsly_dev` import (2026-09-02 to
2026-09-03) and the production-synced `newsly` copy (June 2026 for steady-state rates).

### Per new unique story (article pipeline)

| Step | Cost per processed item | Note |
| --- | --- | --- |
| Summarize | $0.00083 | 2,343 input and 303 output tokens on average |
| Relevant-link selection | $0.00034 | runs on 67% of items (skipped when clustered) |
| Firecrawl fallback | $0.00028 | 33% of items fall back; 66% for FinURLs |
| Relation embedding | $0.00002 | |
| **Total** | **$0.00147** | matches the earlier per-item rate |

### Per Hacker News item (discussion enrichment)

June 2026: 3,292 discussion summaries and 5,732 merge refreshes for 2,518 HN items, 68.0M input
and 33.7M output tokens. At Luna prices that is $54 per month, or **$0.021 per HN item, roughly
15 times the article pipeline cost**. The earlier model omitted this line entirely. Merge
refreshes (about 3.5 per item) are the larger half.

### Steady-state daily intake at the current 15-minute cadence

| Source | New items per day | Basis |
| --- | --- | --- |
| Brutalist Report | ~435 | two consecutive local fetches, 24-hour window across four topics |
| Hacker News | 84 | June 2026 production average (max 108) |
| Techmeme | 41 | June 2026 production average (max 68) |
| Memeorandum | ~35 | 25-item feed, estimated |
| Mediagazer | ~30 | 25-item feed, estimated |
| SciURLs | ~30 | 100-item page, one new item on the second local fetch; low confidence |
| FinURLs | ~25 | 21 new on the second local fetch |
| **Total** | **~680** | Brutalist is 64% of volume |

### Monthly picture with all seven sources fully processed

| Line | Per month | Lever |
| --- | --- | --- |
| Article pipeline, six non-Brutalist sources | ~$11 | none needed |
| Article pipeline, Brutalist | ~$19 | topic gating and per-source caps |
| Hacker News discussion enrichment | ~$54 | demand gating (below) |
| Firecrawl subscription | $83 (yearly) or $99 | ~6,800 fallback pages per month exceeds the Hobby plan's 3,000 credits |
| Briefing composition, all users | ~$1.40 | 868 compose calls in 14 days of production |

Refresh cadence is not a meaningful dollar lever. Scraping is free; cost follows unique new
items, and a 25-item RSS feed polled hourly yields almost the same unique set as one polled every
15 minutes. Brutalist's 24-hour window makes its yield cadence-independent. The money is in
discussion enrichment, Brutalist volume, and the Firecrawl fixed cost.

## Design

### 1. Keep the corpus warm; gate enrichment by demand

Keep all seven aggregators scraped and article-processed globally. At ~$30 per month this is the
cheapest way to guarantee a new subscriber finds ready content, and deferring processing until
the first subscriber reintroduces the first-run wait this work is meant to remove.

Gate the expensive enrichment instead:

- **Discussion summaries** run only for items with at least one subscriber whose Briefing has
  admitted the item, or that a user has opened. Merge refreshes stop when the item is read by every
  subscriber, or after 24 hours. Expected saving: half or more of the $54 line.
- **Firecrawl fallback** runs only for items visible to at least one active subscriber. Unsubscribed
  sources keep the static extraction result or the aggregator title and excerpt. This is what makes
  the Firecrawl Hobby plan sufficient if Brutalist stays capped.
- **Brutalist** scrapes only topics with at least one subscriber (topics are already stored per
  subscription) and caps items per source section. With today's two subscribers, that alone removes
  most of its 435 items per day.

### 2. Refresh cadence

Two tiers, not four:

| Source state | Cadence |
| --- | --- |
| At least one active subscriber | 15 minutes (today's schedule) |
| No active subscriber | 60 minutes |

Store `last_fetched_at` and `last_new_item_count` per aggregator key in a small
`aggregator_refresh_state` table. `build_source_plans` reads it and skips keys that are not due.
Hourly is the floor: a slower tier loses unique yield on Hacker News (15 stories per fetch) without
saving money, and it forces the "immediate priority refresh on first subscribe" complexity the
earlier draft needed. With hourly polling a first subscriber finds a corpus at most one hour old,
which is inside the 24-hour Briefing window.

### 3. New-user attach path

```
finish onboarding
  → persist subscriptions, seed inbox content, start first-edition run
  → mark aggregator source rows processed immediately from the global corpus
  → enqueue briefing_refresh (append, first-edition priority)
  → enqueue backfill_feeds for the user's own feeds
  → reddit only: targeted scrape
```

Changes from today:

- **Drop the onboarding aggregator scrape.** The scheduler owns aggregators. At completion, mark each
  aggregator source row `processed` with the count of ready items visible in the last 24 hours. The
  chips for aggregators complete instantly and truthfully.
- **Bound the first-edition seed.** The first refresh currently seeds every visible unread ready
  item, which with Brutalist can be several hundred. Seed the most recent 12 hours capped at 40 per
  platform for a run with an active first edition; the remainder arrives through the normal sweep.
  This shortens the embed-cluster-name-compose chain from minutes to under a minute of model time.
- **Reuse item embeddings.** The news pipeline already embeds each item with the same model the
  Briefing planner uses. Persist that vector once per item and model; lens planning for a new user
  then needs no embedding calls for warm items. The dollar saving is negligible; the latency saving
  is one external round trip per 32 items.
- **Re-enqueue on stale publication.** Discussion enrichment bumps the Briefing version for every
  user citing the item, and a version bump during composition discards the whole composed batch
  until the next sweep. When the finalizer reports `stale`, re-enqueue an append refresh a few
  seconds out, bounded to three attempts. Demand-gated discussions (section 1) also reduce how often
  this happens during first run.
- **Prioritize first-edition refreshes** on the `llm` queue so a new user's composition is not
  behind existing users' sweeps.

### 4. Articles and Podcasts during first run

The fixed lenses exist from the first refresh but the first-run strip disables their pills until
`segment_count > 0`, so a new user sees nothing for the tiers that take longest. Change the
first-run presentation for the two fixed lenses only:

- Show the Articles and Podcasts pills from the first index response, enabled.
- Tapping one before it has segments opens a tier progress page in the Welcome page's visual
  language: one headline, a chip row of the user's feeds for that tier, and one narration line.
- Segments render as they land above the progress block; the block disappears when the tier has
  no unfinished items.
- Copy uses counts, never stages or percentages: "Reading 18 articles. 5 ready." Failed items are
  folded into "We couldn't get to 2 of them." This keeps the existing non-goals (no queue, task,
  or model vocabulary; no percentages; no durations).

News lenses keep today's behavior: they are dynamic, so their pills appear only when readable.

### 5. Tier progress contract

Add `tiers` to `BriefingFirstRunProgress`:

```json
"tiers": {
  "articles": { "discovered": 18, "ready": 5, "processing": 11, "failed": 2 },
  "podcasts": { "discovered": 8, "ready": 8, "processing": 0, "failed": 0 },
  "news":     { "discovered": 96, "ready": 96, "processing": 0, "failed": 0 }
}
```

Derived at index time, no new table:

- `articles` and `podcasts`: contents joined through the user's `content_status` inbox membership
  for feed configs created by this run, grouped by content status. `ready` = `completed`,
  `processing` = `new`, `processing`, `awaiting_image`, `failed` = `failed` or `skipped`.
- `news`: the visibility query relaxed to all statuses, grouped by news status.

The composite ETag already folds in the run revision; tier counts change the revision through the
same per-source progress writes plus a content-status write hook on the summarization finalizer.

## Alternatives considered

- **Four cadence tiers with immediate refresh on first subscribe.** Rejected: no dollar saving over
  hourly, more state, and a stale-corpus edge case to patch.
- **Defer all processing for unsubscribed sources.** Rejected: saves at most ~$11 per month across
  six sources and reintroduces the first-run wait for the first subscriber.
- **`onboarding_first_edition_items` table.** Rejected: duplicates truth already in content and
  news status columns.

## Prerequisites and open questions

- Confirm production aggregator intake is running under the Rust scheduler. The synced copy shows
  none since late July; if the scheduler's backpressure gate or the deployment is the cause, that is
  the first fix.
- Confirm the production Firecrawl plan and whether anything else needs it. If the Hobby plan is
  enough after gating, the fixed cost drops from $83 to $16.
- SciURLs and FinURLs daily yield needs one week of observation; the estimates above are from two
  fetches.
- Record `cost_usd` on LLM rows from a configured price table so the next cost pass needs no
  external sheet. `vendor_usage_records.pricing_version` already exists for this.

## Implementation order

1. Cost gating: Brutalist topic gating and caps, Firecrawl fallback gating, discussion demand
   gating. Each is independent and measurable in `vendor_usage_records`.
2. Attach path: drop the onboarding aggregator scrape, bound the first-edition seed, stale
   re-enqueue, first-edition queue priority.
3. Cadence table and hourly tier for unsubscribed keys.
4. Tier progress contract and the Articles and Podcasts first-run pages.
5. Persisted item embeddings reused by lens planning.
