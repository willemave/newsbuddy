# Briefing taxonomy: hard-capped news desks with LLM planning

Status: proposed 2026-07-05, revised after local DB clustering sweep. The original
embedding-only merge-on-pressure design is rejected: on the synced local DB it
collapsed 37 active news lenses into one giant catch-all bucket plus singletons.
The revised design uses a richer async LLM taxonomy-planner call to choose the
right level of abstraction, then uses embeddings for deterministic routing and
sanity checks.

Builds on the working-tree lens-naming changes (OpenRouter naming + strict failure)
in `app/services/briefing/lenses.py`.

## Problem

News lenses only ever grow. Two creation paths run with no global budget and no
awareness of each other:

1. **Topic-slug bypass** (`assign_pending_lenses`, lenses.py:139-151): any news source
   carrying an aggregator `topic_slug` unconditionally creates (or resurrects) a
   `news-{topic_slug}` lens — no cap, no similarity check, and **no centroid**, so later
   semantic matching against it relies on title+deck text only.
2. **Semantic clusters** (`_assign_by_semantic_categories`): unmatched sources that form
   a cohesive cluster (≥ `briefing_new_lens_min_items`, pairwise ≥ 0.62) become a new
   LLM-named lens. Small clusters get packed into franken-lenses.

The only shrink mechanism is `retire_idle_lenses` (7 idle days, only for lenses with
zero active segments and zero pending). Nothing generalizes near-duplicates or
over-specific lenses ("AI models", "Artificial Intelligence", `news-ai`) into a
stable reader-facing section. Lens count is unbounded; the iOS lens-pill row and the
masthead degrade past ~10.

Local testing against the refreshed DB showed that embeddings alone are not enough for
the coalescing decision:

- Current user 1 state: 37 active news lenses with stored centroids and 187 active
  news-source mass.
- Repeated closest-centroid merging converged to one 28-lens / 158-source catch-all
  bucket plus nine singleton lenses.
- Source-level streaming clustering showed the same failure mode: one 179-source
  bucket plus singletons.
- Complete-link merging was less bad, but still produced mechanical clusters instead
  of stable editorial categories.
- A richer LLM taxonomy prompt produced useful stable desks, for example "AI in
  Practice", "Science, Biology & Health", "Hardware & Infrastructure", and "Tech
  Economy & Funding". The richer run took about 232s / 17.7k tokens, so it belongs on
  the LLM worker path and should run only on taxonomy pressure, not per item.

Additional debt found while reading:

- `_running_mean` is `(a+b)/2` — not a mean but exponential smoothing with α=0.5. A
  single new article moves a lens centroid halfway.
- Two different embedding models can write to the same `centroid` column:
  `_assign_by_centroid` (legacy, default-off) uses `news_embedding_model`; the semantic
  path uses `briefing_category_embedding_model`. Nothing records which model produced a
  stored centroid, so a model switch silently corrupts similarity math.

## Goals

- **Hard cap** of 10 active news desks per user (`briefing_max_news_lenses`, default
  10, counting `misc`, excluding the fixed `podcasts`/`articles` lenses).
- Produce generalized, reader-facing desks that are stable over weeks but can drift
  when the user's unread news mix changes.
- Let the LLM own abstraction and boundary choices; use embeddings for routing,
  centroid maintenance, and validation.
- Run the richer taxonomy planner asynchronously and rarely: overflow, sustained misc
  pressure, too many unassigned candidate clusters, or manual/full refresh after a
  stale taxonomy interval.
- Preserve content continuity: segments and pending sources are repointed to surviving
  desk lenses; old over-specific lenses become `status="merged"` instead of being
  deleted.
- Keep the iOS/API contract unchanged: index + version remain authoritative, and the
  client sees at most 2 fixed + 10 news pills.

## Non-goals

- No foreground taxonomy LLM call on every incoming item.
- No fixed global taxonomy. Categories are per-user and prompt-stabilized, not a hard
  product-wide enum.
- No forced eviction of unread content-bearing lenses.
- No periodic full re-clustering of every unread source on every refresh.

## Design

The user-visible active news lenses become **taxonomy desks**. The old over-specific
news lenses are inputs to the planner and are merged into those desks. Future items are
assigned directly to active desks when confidence is high; low-confidence groups wait
for the planner or fall to `misc` when stale.

### 1. Weighted, model-tagged centroids

Schema (alembic migration on `briefing_lenses`):

- `centroid_weight INT NOT NULL DEFAULT 0` — items absorbed, capped.
- `centroid_model VARCHAR(120) NULL` — model spec that produced the centroid.
- `routing_rule TEXT NULL` — short taxonomy assignment rule generated for active news
  desks. Used in embedding text and planner prompts.

Update rule replacing `_running_mean`:

```
w' = min(w + 1, briefing_centroid_max_weight)   # default 32
centroid' = (centroid * w + v) / (w + 1)
```

Capping the weight gives exponential forgetting with an effective window of ~32 items:
a desk tracks what its story is *now*. Every place that writes a centroid also writes
`centroid_model = settings.briefing_category_embedding_model`.

Read rule: a stored centroid is usable only when `centroid_model` matches the current
setting; otherwise treat as absent. Existing rows migrate with `centroid_model = NULL`,
so legacy centroids self-heal lazily.

### 2. Taxonomy planner

Add a richer planner prompt, for example `app/prompts/briefing/taxonomy.md`, with a
strict JSON output:

```json
{
  "categories": [
    {
      "key": "ai-in-practice",
      "title": "AI in Practice",
      "deck": "Real-world applications, benchmarks, and trade-offs of AI tools.",
      "routing_rule": "Use for practical deployment, evaluation, and user experience of AI systems.",
      "include_lens_keys": ["news-ai-coding-agents-in-practice"],
      "include_candidate_ids": ["candidate:3"]
    }
  ],
  "operating_model": "Preserve prior desk keys unless the underlying subject meaningfully changed."
}
```

Inputs:

- Current active news desks, including key/title/deck/routing rule/source counts.
- Existing over-cap active news lenses that need merging.
- Candidate clusters formed from unassigned pending news sources.
- Representative source titles/summaries/key points per lens or candidate cluster
  (rich prompt is acceptable; keep bounded by `briefing_taxonomy_sources_per_lens`).
- Previous taxonomy metadata, when present, with an explicit instruction to preserve
  stable keys and only rename/split/merge when evidence changed.

Planner requirements:

- Return 8-10 categories.
- Assign every input lens key and candidate id exactly once.
- Prefer reader-facing desks over specific companies, tools, model names, or one-day
  events.
- Avoid one giant "Technology" catch-all.
- Keep category titles short enough for mobile pills.
- Preserve prior category keys unless the category boundary changed materially.

Validation before applying:

- Every input lens key and candidate id appears exactly once; no extra keys.
- Category count ≤ `briefing_max_news_lenses`.
- No category exceeds a soft source-share ceiling (for example 55%) unless the planner
  explicitly returns a valid reason; otherwise retry/repair once.
- Required fields pass Pydantic validation and normalized keys are unique.
- Optional embedding sanity check: compute each category's member centroid spread and
  log weak/coarse categories for observability. Do not use this check alone to reject
  a semantically sensible editorial desk.

### 3. Applying a taxonomy

Apply the validated taxonomy in one transaction:

- For each planned category, choose a surviving desk lens:
  - prefer an existing active lens with the same key;
  - else prefer an included lens whose normalized key/title already matches the planned
    category;
  - else create a new `BriefingLens` with the planned key/title/deck/routing rule.
- Repoint `briefing_segments.lens_id` from included loser lenses to the surviving desk.
- Repoint `briefing_pending_sources.lens_key` from loser keys and included candidate
  ids to the surviving desk key.
- Mark loser lenses `status = "merged"` and `retired_at = now`.
- Update the winner title/deck/routing rule from the planner output.
- Recompute winner centroid as a weighted mean of included lens centroids plus included
  candidate source vectors; cap `centroid_weight`.
- Increment the briefing state version through the existing `mutated` path so clients
  refetch.

`misc` counts toward the cap but is not an input to planner merge candidates. It stays a
floor for stale or low-context sources.

### 4. Normal assignment between planner runs

`assign_pending_lenses` remains cheap in the steady state:

1. Content sources still route to fixed `articles` / `podcasts`.
2. Topic slug no longer auto-creates `news-{topic_slug}`. If an active desk with that
   exact key exists, assign to it; otherwise the source enters the semantic pool.
3. Embed pending news sources and active desk profiles (`title + deck + routing_rule`,
   preferring a valid model-tagged centroid).
4. Assign to the best desk at `briefing_category_similarity` (0.55) and update its
   weighted centroid.
5. Sources below assignment confidence accumulate as candidate clusters.
6. If candidate clusters reach planner pressure, run/enqueue the taxonomy planner.
7. If a small low-context group is stale, route to `misc`.

### 5. Planner triggers

Run the richer planner only when it can improve the taxonomy:

- Active news lens count exceeds `briefing_max_news_lenses`.
- Unassigned candidate clusters reach `briefing_taxonomy_candidate_cluster_limit`.
- `misc` holds enough recent news sources to suggest a missing desk.
- Manual/full refresh and the previous taxonomy is older than
  `briefing_taxonomy_min_interval_seconds`.
- Explicit admin/debug command for dry-run or repair.

If the planner fails, keep assignment safe:

- Retry/repair once for schema or coverage errors.
- Fall back to complete-link deterministic coalescing only for emergency overflow
  restoration, because local testing showed it is less likely to create a giant bucket
  than closest-centroid merging.
- Otherwise leave low-confidence sources pending or route stale stragglers to `misc`.

## Settings

```python
briefing_max_news_lenses: int = Field(default=10, ge=3, le=30)
briefing_category_absorb_similarity: float = Field(default=0.45, ge=0.0, le=1.0)
briefing_centroid_max_weight: int = Field(default=32, ge=4, le=500)
briefing_taxonomy_planner_enabled: bool = True
briefing_taxonomy_model: str | None = None  # defaults to briefing_model
briefing_taxonomy_sources_per_lens: int = Field(default=7, ge=1, le=12)
briefing_taxonomy_min_interval_seconds: int = Field(default=21_600, ge=900, le=604_800)
briefing_taxonomy_candidate_cluster_limit: int = Field(default=3, ge=1, le=20)
briefing_taxonomy_llm_timeout_seconds: int = Field(default=300, ge=30, le=600)
```

Threshold invariant for steady assignment remains: `absorb 0.45 < assign 0.55 <
cluster 0.62`.

## Touched surfaces

| Surface | Change |
|---|---|
| `migrations/alembic/versions/` | add `centroid_weight`, `centroid_model`, `routing_rule` |
| `app/models/db/briefing.py` | new columns above |
| `app/core/settings.py` | planner and centroid settings |
| `app/prompts/briefing/taxonomy.md` | rich taxonomy-planner prompt |
| `app/services/briefing/lenses.py` | slug-bypass removal, weighted centroid assignment, planner trigger integration |
| `app/services/briefing/taxonomy.py` | planner input collection, LLM call, validation, apply transaction |
| `app/services/briefing/refresh.py` | optional status counts / task result telemetry |
| `tests/services/briefing/` | focused planner, validation, application, assignment tests |
| iOS client / API models | none — index + version already drive the UI |

Not affected, verified: read marks are source-key based; narration and dig resolve
lenses by key at request time (a merged key 404s once, client refetches after the
version bump); segments carry no denormalized lens key.

## Phases

1. **Centroid foundation** — migration, weighted update, model tagging, delete
   `_running_mean`. No visible behavior change.
2. **Taxonomy planner dry-run** — build planner prompt/service and admin or script
   dry-run against current local DB; no writes. Validate coverage and show proposed
   desks.
3. **Apply taxonomy** — transactional merge/repoint/update path, `status="merged"`,
   version bump, fallback behavior.
4. **Steady assignment** — slug auto-create removal, desk routing, candidate clusters,
   planner triggers, `misc` fallback.
5. **Tests + rollout** — see below.

Phase 1 ships alone. Phase 2 can ship behind a dry-run/admin-only path. Phases 3-4
deliver the user-visible capped desk behavior.

## Test plan

Use `tests/services/briefing/test_lenses.py` conventions where possible: fake naming or
planner functions, monkeypatched embeddings, and deterministic source fixtures.

- Weighted centroid math incl. weight cap; `centroid_model` mismatch → profile fallback
  then rewrite.
- Slug: existing active desk still exact-assigns; missing slug falls to semantic pool
  and does not auto-create; `status="merged"` lens is not resurrected.
- Planner validation: accepts full exact coverage; rejects missing keys, duplicate keys,
  unknown keys, over-cap categories, duplicate normalized keys, and missing required
  fields.
- Planner apply: seeded with 14 active news lenses, LLM output converges to ≤10 desks;
  segments and pending sources repoint; loser lenses become `merged`; state version
  bumps.
- Stable keys: previous active desk key is preserved when the planner keeps the same
  category; title/deck/routing rule update without key churn.
- Candidate clusters: unassigned pending sources can be included in planner output and
  assigned to the chosen desk.
- Steady routing: source above assignment threshold routes to best desk and updates the
  weighted centroid; below-threshold source remains pending until planner pressure or
  stale `misc`.
- Fallback: planner schema failure retries once; persistent failure leaves sources safe
  and does not partially apply; emergency deterministic complete-link fallback never
  touches fixed lenses or `misc`.
- LLM-less branch respects the cap and routes stale/low-context sources to `misc`.

## Rollout & verification

Feature is already gated to `briefing_enabled_user_ids=[1]`.

1. Run `scripts/sync_production_state.py`.
2. Run taxonomy dry-run locally and inspect proposed desks, coverage, token usage, and
   source distribution. Expect generalized stable desks, not one giant tech bucket.
3. Enable apply path locally and run a `full` briefing refresh.
4. Inspect `briefing_lenses`: active news lenses ≤10, old over-specific lenses marked
   `merged`, segment counts preserved under surviving desks.
5. Call the briefing index and lens endpoints; verify version bump and no client model
   changes.
6. Watch LLM usage and task duration. The richer prompt is acceptable, but it should be
   rare and worker-bound.

Rollback = revert; no destructive data change. Merged lenses keep rows, and segment
history is preserved through `lens_id` repointing.

## Rejected alternatives

- **Closest-centroid merge-on-pressure**: local DB sweep produced one huge catch-all
  bucket plus singleton leftovers. This does not create reader-facing categories.
- **Average-link or single-link embedding clustering**: also tended toward giant
  buckets.
- **Complete-link as primary taxonomy**: better balanced than centroid merging, but
  still mechanical and worse than LLM-planned desks. Keep only as emergency fallback.
- **Standalone hourly merge sweep**: adds a second mutation path and still cannot choose
  the right abstraction level without an editorial planner.
- **Fixed global taxonomy**: too rigid for a per-user unread briefing and loses the
  product value of emergent personalized desks.
- **Forced eviction of content-bearing lenses**: hides unread content the user may be
  mid-way through. Taxonomy apply repoints content instead.

## Follow-ups (out of scope)

- Delete `_assign_by_centroid` (default-off legacy path superseded by the semantic
  path; its `news_embedding_model` centroids are exactly the mixed-model hazard Phase 1
  guards against).
- Re-rank news lens `position` by unread volume on sweep so the busiest categories lead
  the pill row (client already sorts by position).
- Add an admin dry-run command that prints planner input size, proposed desks, coverage,
  token usage, and a before/after lens table.
