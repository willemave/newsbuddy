# News category reclustering: production-sample replay

Date: 2026-09-27. Base checkout: detached `776317d2`.
Status: completed offline experiment; no production behavior changed.
[Implementation plan](2026-09-27-nightly-news-category-reclustering-plan.md).

## Decision

Advance **nightly, warm-start spherical clustering over a 14-day window** into
shadow evaluation. Reuse the previous category centers; retain ordinary arrival
routing between nightly runs. Do not adopt the current fitter unchanged: reject
unsupported tiny categories, constrain semantic drift before retaining a name,
and validate against the actual user-visible routing path.

This is a promising computational approach, not proof of better human category
labels or a measured improvement over deployed Newsly. The incremental control is
a disclosed approximation, and the sample contains public source cohorts rather
than historical user feeds.

## Dataset and temporal interpretation

The existing local database had 3,623 news rows concentrated in two ingestion
batches, which was insufficient for a continuous replay. Instead of replacing it,
we copied a bounded production sample locally through read-only Rust operator
queries. No production or local database state was changed.

- Selected 1,445 current canonical, ready, public/global news representatives,
  at most 50 per source/week, deterministically ordered by a salted hash of ID.
- Accepted **1,436** after checking the exact encoder input hash. Excluded nine
  rows whose current title/summary/key-points text no longer matched its vector.
- Sources: Hacker News 400, Techmeme 376, Memeorandum 196, Mediagazer 138,
  Brutalist 196, FinURLs 130. These are strata, not population weights.
- Cached `qwen/qwen3-embedding-8b`, encoder v1, 4,096 dimensions. Exported existing
  vectors as float32; no new embedding requests. Checked dimensions, finite/nonzero
  values, unique IDs and complete, nontruncated operator responses.
- Selection interval: August 3 through September 26 inclusive in UTC. Accepted
  counts by ISO week: 77, 100, 100, 102, 204, 276, 297, 280.
- Replay availability is `max(ingested_at, processed_at)`. The final checkpoint
  sees **1,405** of the accepted rows; 31 arrive after that cutoff and never enter
  any fit or score. The final 14-day fitting window contains 582 stories.
- No account identifiers, authentication records, private submissions or user
  read history were exported.

This is a **final-text counterfactual corpus replay**. Current cached vectors may
have been backfilled or overwritten after a story originally arrived. Current
canonical representatives also omit stories merged or deleted subsequently.
Exact hash agreement establishes compatibility with current text, not historic
availability of that text/vector. Earlier cached coverage is disproportionately
technical; later source diversity is both a useful stress test and a sampling
confounder. Nothing here reconstructs the exact deployed August state.

## Experimental design

Replay starts at 03:00 America/Los_Angeles on August 10, after an initial week of
input. Weekly cutoffs: August 10, 17, 24, 31; September 7, 14, 21, 26. The last step
is five days. A second experiment runs every local night across the same period,
48 cutoffs. These dates do not exercise DST; timezone correctness needs separate
scheduler tests in the implementation plan.

Three source cohorts: all accepted sources; technology (HN + Techmeme, 776 rows);
and general (the other four sources, 660 rows). Each receives 15 weekly variants
and six nightly variants: **63 trajectories and 1,224 checkpoint evaluations**.

Default fitting parameters: 14 days, at most 1,500 fitting items, target 10 centers,
20 Lloyd iterations, unit-normalized vectors, deterministic seeded farthest-point
initialization. Warm runs start from prior occupied centers. Empty centers are
removed; the target count need not equal occupied categories. The basic spherical
arms do not enforce minimum group size, an observed limitation discussed below.

The weekly matrix compares frozen initial centers; a production-inspired
incremental surrogate; warm 7/14/28-day windows; seven-day decay within a 14-day
window; 6/10/14 center targets; a 0.50 assignment-confidence gate; a simple greedy
novelty algorithm; and five independent cold-start seeds. Nightly runs compare
frozen, incremental, warm7, warm14, warm14+decay, and warm14+confidence gate.

The incremental surrogate uses bounded-weight center updates, a category cap and
weak forced assignment. It does not reproduce production's frozen-within-batch
matching, every threshold, label embeddings, retry timing or actual routing calls.
Its zero movement follows immutable existing assignments; it is not a quality
result. Its batch cadence also changes outcomes. Frozen centers are a diagnostic
control, especially weak for a cohort whose initial nonempty batch is tiny.

### What is measured

The primary diagnostic scores each newly arriving story against centers from the
**previous** checkpoint, before the story enters fitting. Higher best-center cosine,
higher separation from the runner-up, and fewer weak matches indicate geometric
routing fit. Cosine is not an accuracy percentage or human relevance judgment.

Late-period comparisons use the **same 806 arrivals after September 7 at 10:00 UTC
through September 26 at 10:00 UTC**, in both cadences. Arrival metrics are weighted
by arrival count. Shared-story movement is the mean per update among stories that
remain in both fitting windows. ARI measures partition agreement independent of
category IDs. IDs use greedy overlap matching with a 0.70 centroid-similarity guard;
this is an estimate, not the proposed optimal production lineage matcher.

All old sources remain in the output as retained assignments, including stories
outside the fitting window. Low-confidence/noise items have null category keys,
representing required reachable mixed coverage. Actual unread/read coverage,
roundup composition, narration and generated category names are not simulated.

## Main results

Nightly runs, all-source cohort, identical late-period arrivals:

| Variant | Next-arrival cosine | Weak matches below .45 | Shared stories moved per night | Mixed after fitting |
|---|---:|---:|---:|---:|
| Frozen initial categories | .471 | 40.6% | 0.0% | 0.0% |
| Incremental surrogate | .529 | 16.1% | 0.0% | 0.0% |
| **Warm 14 days** | **.553** | **9.9%** | **1.7%** | **0.0%** |
| Warm 7 days | .543 | 11.9% | 1.9% | 0.0% |
| Warm 14 days, 7-day decay | .547 | 10.9% | 1.6% | 0.0% |
| Warm 14 days, .50 gate | .553 | 9.9% | 2.1% | 16.2% |

Warm14 reduced weak matches by 6.2 percentage points versus the nightly surrogate
and improved mean cosine by .023. The .50 gate only withholds assignments after
fitting: it cannot independently improve the fitted centers or next-arrival fit.
Its 16.2% mixed share is too large to adopt blindly.

Weekly runs over the same late arrival period:

| Variant | Next-arrival cosine | Weak matches | Shared stories moved per update |
|---|---:|---:|---:|
| Incremental surrogate | .534 | 15.9% | 0.0% |
| Warm 14 days | .539 | 11.8% | 7.2% |
| Warm 7 days | .536 | 13.5% | 2.3% |
| Warm 28 days | .540 | 11.5% | 10.5% |
| Warm 14 days, 7-day decay | .545 | 11.3% | 15.9% |
| Warm 14 days, 6 centers | .535 | 13.0% | 7.6% |
| Warm 14 days, 14 centers | .548 | 10.4% | 15.2% |
| Cold 14 days, five seeds | .551–.554 | 9.2–10.7% | 30.7–38.1% |
| Greedy novelty variant | .465 | 48.8% | 20.6% |

- **Cold restarts:** slightly better weekly arrival fit, but much greater movement.
  This is a poor default for recognizable categories. The five seeds expose
  initialization sensitivity; they are not confidence intervals.
- **Longer window:** 28 days adds work with little mixed-source fit improvement.
  It is a sensitivity arm using backfilled vectors, not evidence of a historically
  available 28-day cache. Technology-only weekly fit did benefit more (.566 vs .556).
- **Decay:** helps weekly mixed-source fit, but doubles weekly movement. It loses
  to unweighted warm14 nightly on the mixed-source cohort. Keep it as a shadow
  challenger, not the default.
- **More categories:** improves geometric coverage while increasing fragmentation
  and movement. Six centers are cheaper but do not resolve the topic-count choice.
- **Novelty:** this particular greedy, cap-limited implementation sends 81.6% of
  the fitting set to mixed coverage. Reject this configuration; this does not
  establish that all density/novelty clustering approaches are unsuitable.

Daily and weekly churn percentages have different denominators and must not be
compared as cumulative movement. Comparing the nightly warm14 state at the three
late weekly endpoints gives 6.2%, 14.2%, and 12.3% shared-story movement (mean 10.9%),
versus 7.2% for weekly warm14. Nightly refresh offers smaller individual steps and
better arrival fit, but **not less total movement over a week** in this experiment.
The seven-day weekly arm usually has no shared fitting stories at all; its reported
movement is based on the final, overlapping five-day step and is not evidence of
superior stability.

Cohort sensitivity, nightly:

| Cohort | Incremental cosine / weak | Warm14 cosine / weak | Warm14 nightly movement |
|---|---:|---:|---:|
| All sources | .529 / 16.1% | .553 / 9.9% | 1.7% |
| Technology | .556 / 10.5% | .556 / 9.8% | 1.1% |
| General | .507 / 19.8% | .548 / 11.7% | 3.1% |

The main improvement comes from broader, changing content. A narrow technology
feed shows little cosine improvement. Do not assume every user's feed benefits
equally or force a changed partition every night.

## Week-by-week progression

All-source warm14, weekly cadence. Fit uses the prior week's centers; cohesion
is measured after fitting and is not an independent quality score.

| Cutoff UTC | Seen | Fitting set | Cohesion | Next-batch cosine | Shared movement | New IDs |
|---|---:|---:|---:|---:|---:|---:|
| 2026-08-10 | 82 | 82 | 0.626 | — | — | 10 |
| 2026-08-17 | 182 | 180 | 0.605 | 0.531 | 10.0% | 0 |
| 2026-08-24 | 281 | 199 | 0.596 | 0.552 | 2.0% | 2 |
| 2026-08-31 | 388 | 206 | 0.592 | 0.544 | 1.0% | 4 |
| 2026-09-07 | 599 | 318 | 0.559 | 0.482 | 22.4% | 1 |
| 2026-09-14 | 866 | 478 | 0.553 | 0.534 | 5.7% | 2 |
| 2026-09-21 | 1156 | 557 | 0.564 | 0.546 | 4.9% | 3 |
| 2026-09-26 | 1405 | 582 | 0.568 | 0.537 | 11.1% | 2 |

## What the categories actually did

These are interpretations of representative sampled headlines, not generated
category names or independent quality labels:

- Early centers covered coding/tools, AI-company funding, and technology policy.
  In the weekly replay, the arrival of broader sources coincided with 22.4%
  shared-story movement at September 7 and next-arrival fit falling to .482.
- By September 26 the nightly warm14 cohort had separate groups centered on
  coding agents (80 stories), media/political controversies (106), company funding
  and deals (151), NFL coverage (45), economic/market news (65), AI safety (88),
  and election polling (44).
- Representative coding headlines concerned coding-agent harness cost and CUDA
  optimization; NFL headlines covered Week 2 injuries and fantasy decisions;
  polling headlines covered the congressional ballot and Ohio races. These show
  recognizable new subject groups without re-embedding the archive.
- Three remaining categories contained one story each. In technology-only data,
  three large groups held 197 of 207 fitting stories, with six tiny groups sharing
  the other ten. A `.50` confidence gate does not fix this: singleton items can
  have perfect similarity to their own center.
- Gradual semantic drift can preserve an ID even as its meaning changes. The
  local continuity guard therefore cannot justify keeping a category's original
  name indefinitely. Naming/profile drift needs its own bounded decision.

These observations add a required minimum-support check, representative review,
and category-size diagnostics to the shadow phase. Minimum support should be
based on distinct events and persistence, not simply raw duplicate story count.
The improved supported-cluster policy is proposed follow-up, not a tested winner
in this report.

## Cost, performance and limits

The experiment made **zero embedding, naming or model-judge API calls: $0 external
API spend for the replay**. A separate authorized, read-only Claude Opus 5.5
architecture consultation used the locally authenticated CLI; it did not judge
the sample. New-story embeddings and occasional production names would still cost
money under the future design.

Mixed-source warm14 nightly replay took a median **67 ms**, maximum **219 ms**,
per late-period checkpoint on this local machine. The final window was 582 cached
4,096-dimensional vectors; experiments ran concurrently. These are rough local
feasibility timings, not a production capacity benchmark. They include in-memory
replay bookkeeping/metrics and exclude file parsing, database reads, naming,
queueing and publication. No cap stress benchmark or production memory profile
was performed.

The algorithm uses bounded item-center comparisons rather than an all-pairs
similarity matrix. Nightly work should skip unchanged/inactive users, cap the
fitting cohort and iterations, stagger starts, and have a global concurrency
budget. Naming should occur only for supported new/materially changed topics,
with caching and retry-inclusive request/token ceilings.

No human relevance labels, historical per-user subscriptions, read events,
composed-roundup behavior, real category-name continuity, power analysis or
production routing baseline were available. The next gate is at least two weeks
of per-user shadow candidates with actual source/compose timing and held-out
routing review. Do not ship based solely on this geometric result.

## Reproduction and validation

The reusable harness lives in `rust/crates/newsly-eval-driver/src/replay/` with
file-only preparation/reporting under `python/evals/`. Commands are in the
[eval README](../../python/evals/README.md#cached-vector-news-category-replay).

Local ignored artifacts under `test-results/news-lens-replay-20260927/` contain:
coverage queries, selection SQL and response, bounded export script, raw operator
responses, manifest, story metadata, six input/output pairs, aggregate comparison
JSON/Markdown, weekly-endpoint diagnostics, and the architecture review.

Source export SHA-256:
`ad75a33d1d17bba0c8dd3337323bf9268e8aedf1d10a2d933f8a8e72ee427f1d`.
Metadata SHA-256:
`075449fa1db92fc4991443a687b0a395dd5319f3bc7d803225201e19a335e186`.

Validation: Rust format and warning-denied Clippy; 12 focused Rust tests covering
future-data exclusion, deterministic replay, frozen initialization, fitted-center
retention, pre-update scoring, identity matching, caps/noise retention and ARI;
Python Ruff/MyPy and five focused integrity/time-boundary tests. Full release,
PostgreSQL and iOS gates are not applicable to this offline-only change and were
not run. No commit, push or deployment was performed.

## Production-core implementation replay

The implemented core was replayed on the same cached production sample. The fair recent-period comparison uses 19 nightly checkpoints, September 7–26, and the same 806 arrivals scored against the prior night's centers.

| Metric | Earlier warm 14-day variant | Implemented core |
|---|---:|---:|
| Held-out best cosine | 0.5527 | 0.5448 |
| Held-out weak matches below 0.45 | 9.93% | 12.90% |
| Top-two margin | 0.0817 | 0.0877 |
| Mean semantic categories | 9.63 | 4.79 |
| Shared-active assignment movement | 1.674% | 0.863% |
| Category births / deaths | 8 / 8 | 1 / 0 |
| Category-checkpoints with fewer than 3 events | 67 | 0 |
| Descriptive cohesion | 0.5744 | 0.5844 |

The core eliminates tiny categories and reduces churn, at the cost of broader categories and weaker held-out fit. Final general-news coverage is 12.03%; no retained story loses assignment. These are distinct metrics: higher cohesion alone does not establish better classification. Start with shadow maintenance and inspect proposed labels and general-news coverage before enabling publication.

Artifact: `test-results/news-lens-replay-20260927/production-core-recent-comparison.json` (ignored local output). This remains a retrospective final-text embedding replay, not a reconstruction of historical production state. No naming or embedding provider calls were made for this replay.
