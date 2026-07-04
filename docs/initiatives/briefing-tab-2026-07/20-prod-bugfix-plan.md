# Briefing prod bugfix plan — lens groupings + missing longform

Date: 2026-07-04. Debugged against prod (user 1, container `newsly:13e3fb57`).

## Bug A — Useless news groupings ("Soatok'S desk", "Wafer desk", …)

### Root cause (deployed code at `13e3fb57`)

1. **LLM lens naming is not wired.** Deployed `refresh.py` calls
   `assign_pending_lenses(db, user_id=..., settings=...)` without `naming_fn`, so every
   new lens is named by `_default_lens_name()`: "{first >3-char word of the first
   item's title} desk". "Soatok'S" is the Python `.title()` apostrophe artifact.
   Evidence: `admin briefing costs` shows **zero `briefing_lens_naming` calls ever**.
2. **No clustering runs at all.** Centroid assignment is off by default
   (`briefing_centroid_assignment_enabled=False`, commit "Skip briefing centroid
   embeddings by default"), and the semantic-clustering + LLM-naming implementation
   (+401 lines in `lenses.py`) exists only as uncommitted working-tree code — never
   deployed. Result: each sweep's batch of ≥4 unassigned news items becomes ONE new
   lens. 11 junk desks were created roughly hourly (18:55 → 02:51), matching the
   `briefing_sweep_seconds=3600` cadence.
3. Prod news items have **no `aggregator.topic`** (460/460 NULL in last 3 days), so the
   topic fast-path never fires; everything funnels into the default-name path.

### Fix

1. Finish and deploy the working-tree semantic clustering + LLM naming path, hardened:
   - Wrap the per-cluster `naming_fn(...)` call in try/except → fall back to
     `_default_lens_name` for that cluster and log, instead of failing the whole
     refresh. (DeepSeek Flash structured-output quirks are a known risk.)
   - Smoke-test `LensNameOutput` (min/max length constraints) against
     `openrouter:deepseek/deepseek-v4-flash` before deploying.
2. Fix `_default_lens_name` casing: keep the word's original casing (drop `.title()`),
   so even the fallback never emits "Soatok'S".
3. Tests: cluster-to-existing-lens assignment, new-cluster naming, naming-failure
   fallback, key uniqueness.

## Bug B — No podcasts, almost no articles

### Root cause

Both briefing entry points require `Content.classification == 'to_read'`:

- `list_unread_longform_sources` (bootstrap/full seed)
- `enqueue_content_for_briefing_if_ready` (incremental hook)

But prod content is overwhelmingly **unclassified** (`classification IS NULL`):

| type | completed+to_read | …of which read | completed+NULL | …of which unread |
|---|---|---|---|---|
| article | 247 | 247 (all) | 2596 | ~52 |
| podcast | 23 | 23 (all) | 740 | ~45 |

Every eligible (to_read) item was already read, so the podcasts lens **never composed a
single segment** and articles ran dry. The repo-wide visibility convention everywhere
else (content_feed_query, content_card/detail/stats repositories) is
`(classification != 'skip') | (classification IS NULL)` — the briefing is the outlier.

Aggravator: two `mode="full"` rebuilds ran (19:39 → compacted 7 segs; 01:02 → compacted
22 segs) and rebuilt only from the tiny eligible set (5 new segments). One full refresh
hung 20+ minutes (sequential compose, `briefing_compose_parallelism=1`) and was
operator-cancelled.

### Fix

1. Change both call sites to the repo convention:
   `(classification != 'skip') | (classification IS NULL)`.
2. Raise `briefing_compose_parallelism` for prod (1 → 4) so a full rebuild over the
   ~97 newly-eligible sources finishes in minutes, not 20+.
3. Tests: NULL classification is eligible; `skip` stays excluded; incremental hook
   enqueues NULL-classified completed content.

## Prod remediation sequence (after deploy)

1. Deploy the new image.
2. One-off: retire the 11 default-named `news-*` lenses (admin fix / SQL) so clean
   clusters form fresh — otherwise semantic assignment will keep pulling new items into
   junk-titled lenses via their title/deck profile text.
3. `admin briefing refresh --user-id 1 --full` (LLM on).
4. Verify: `admin briefing status --user-id 1` (podcasts/articles have active segments,
   news lens titles are sensible), `admin briefing costs` (`briefing_lens_naming` > 0),
   spot-check the app.

## Open questions / follow-ups

- Why is `classification` NULL for ~92% of content? If the classify step is meant to
  run for all inbox content, that is its own pipeline gap (separate initiative).
- Consider capping active news lenses per user and/or merging near-duplicate lenses
  during sweeps.
- `fetch_news_item_discussion` failed 1,315× in 24h (HTTP 404) — unrelated to the
  briefing but noisy in exception logs; worth a separate look.
