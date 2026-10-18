# Jev category routing experiment — 2026-10-17

The proposed split is viable enough for a shadow trial: a stronger model defines stable
categories, and Jev assigns incoming stories to those definitions. This experiment does
**not** establish higher quality or lower latency than Newsly's current embedding router.
Routine routing already uses cached embeddings and cosine similarity; Jev would add a
network inference call there. It can potentially improve semantic fit while avoiding a
generative call for every story. It does not generate new categories or write Briefing prose.

## Results

Ten frozen categories were generated from 60 development stories. We evaluated 120 separate
held-out stories, with 15 each from arXiv, Brutalist, FinURLs, Hacker News, HF Papers,
Mediagazer, Memeorandum and Techmeme. These stories were published September 17–October 17;
"recent" is the inherited corpus stratum, not a promise of same-day news. SciURLs' stale
science sample was excluded. All models received the same title, summary and key points,
without aggregator, topic or publisher metadata.

The reference distinguishes its preferred category from other defensible primary-topic
categories. Forty-seven of 120 judgments allow more than one category, so exact agreement
is the stricter and more useful headline alongside acceptable agreement.

| Classifier, all 120 stories | Exact preferred category | Within acceptable set |
|---|---:|---:|
| Jev: category title + deck | 108/120 (90.0%) | 118/120 (98.3%) |
| Jev: title + deck + routing rule | 108/120 (90.0%) | 117/120 (97.5%) |
| Separate Opus 5.5 classifier | 116/120 (96.7%) | 119/120 (99.2%) |

The richer definitions did not demonstrate an overall accuracy improvement over title+deck
(paired acceptable-set test: one gain, two losses; exact McNemar p=1). They did separate
confident from uncertain cases usefully in this small sample:

| Full definitions: fixed top-choice threshold | Named assignments | Exact / acceptable assignments | General-news remainder |
|---|---:|---:|---:|
| 0.70 | 96/120 (80.0%) | 92/96 / 96/96 | 24 |
| 0.85 | 83/120 (69.2%) | 82/83 / 83/83 | 37 |
| 0.90 | 77/120 (64.2%) | 77/77 / 77/77 | 43 |

The remaining stories stay in general news; they are not hidden. At 0.85, none of the 13
reference-first-choice `other` stories was given a named category. At 0.70 three were,
although those assignments were in the reference's acceptable set. The 83/83 acceptable
result has a Wilson 95% interval of approximately **95.6–100%**, not a guarantee of perfect
precision. All thresholds were frozen before calls; none is validated as a production setting.

The title+deck arm at 0.85 also assigned 83 stories, with 79 exact and 82 acceptable matches.
The richer arm's three unacceptable assignments without a threshold had probabilities of
0.40, 0.47 and 0.54. Examples were app distribution categorized as corporate business,
a prediction-market anomaly as macroeconomics, and a UK platform-regulation dispute as
crime/justice. These expose missing or overlapping taxonomy boundaries, not only model errors.

Both arms agreed with acceptable reference labels on all 30 stories with source topic labels.
On the 90 "unclassified" source-topic stories, acceptable agreement was 88/90 for title+deck
and 87/90 for full definitions. Those subgroup results are descriptive, with small samples.

## Cost, speed and reliability

- **296/296 Jev requests succeeded**, without retries or protocol failures.
- Reported OpenRouter Jev charge: **$0.018958128**, including the four-call smoke.
- Median request latency: **0.241 seconds**; p95: **0.998 seconds**; maximum: 1.834 seconds.
- Full definitions cost $0.009536814 for 120 requests: approximately **$0.0795 per 1,000
  article/category-set decisions**, at the observed token lengths and rate.
- Title+deck cost $0.005398974 for 120 decisions. Rich rules cost about 77% more input tokens.
- Six local CLI classifier/reference batches used explicit `claude-opus-5-5`; taxonomy
  generation used two additional CLI calls. These consume CLI account allowance and are
  **not included in the Jev charge**. Their CLI-reported cost estimates total $2.167552,
  not a verified incremental invoice. Independent planning and final oracle reviews are
  separate CLI usage.
- The three 40-story strong-classifier batches took 16.8–22.6 seconds each. This is not an
  apples-to-apples serving-speed comparison: CLI startup, batching, reference output and
  account routing differ from production API calls. No end-to-end onboarding speedup was measured.

Twenty-four same-order repeats produced no label changes. Reversing option order changed
one of 24 labels. This is evidence to keep category ordering stable and measure consistency
on a larger sample, not a calibrated estimate of position bias.

## Captured onboarding mistakes

A separate eight-story challenge used the two existing captured category definitions.
Jev rejected the college-life-skills article from college football and the Disney/Dude
Perfect partnership from geopolitical conflict, each with `other=1.00`. Its top choice
kept the other six stories in their expected category. One football result had only 0.61
membership probability, so a strict threshold would defer that relevant story too.

This challenge checks obvious article/category fit. It does not prove that Gaza, Ukraine
and Venezuelan mining belong in one coherent passage. Category membership and passage
composition coherence remain separate requirements. The challenge was not pooled with
the held-out metrics.

## What this changes in the recommendation

1. Keep the stronger model on the slower category-definition path, writing title and deck. Treat the existing routing rule as optional: this run did not establish that it is necessary. Newsly's nightly
   naming already outputs `title`, `deck` and `routing_rule`; the first-run naming path
   currently outputs title/deck. No training or labeled dataset is needed to try Jev:
   it selects from the supplied definitions directly.
2. Test Jev in shadow on newly summarized stories using the user's current category set,
   alongside the existing centroid result. Compare exact fit, unacceptable placements,
   general-news rate, first-category timing and whole-passage coherence on real per-user
   categories. The current experiment's broad shared taxonomy is easier than that task.
3. If that paired test succeeds, route incrementally when stories arrive. Record the exact category-definition version and stable option order with decisions.
   If decisions are cached, bind them to the supplied story evidence and evaluator version.
   Reuse the existing outside-transaction calls and exact-lease/routing-generation fences.
   Only accepted members should update centroids. These are future integration guardrails,
   not behavior exercised by this offline test.
4. Keep uncertain stories reachable in general news. Do not speed up category renaming
   merely because routing is cheap; retain the existing continuity and novel-topic support
   rules. Category discovery and generative passage writing still need separate work.

A 0.85 threshold is a useful **next-test candidate**, not a selected deployment policy.
Calibrate on a separate development set and verify on new held-out personalized categories.
No production, database, scheduler, provider-default or category behavior was changed.

## Reproducibility and limits

Artifacts: `test-results/jev-categories-20261017/`.

- `development.json`, `heldout.json`: frozen corpus rows selected with a deterministic hash
  from the previous filtering experiment's development/test split. Explicit story-group
  overlap is zero; semantically related coverage may still cross that inherited split.
- `taxonomy.json`, `manifest.json`: frozen production-shaped definitions, request bodies,
  option order, scoring rules, fixed thresholds and source hashes. Allocation: 240 main,
  24 reversed-order, 24 same-order, 8 challenge calls = 296; cap 300 requests/$0.10.
- `run/attempts.jsonl`, `run/raw/`, `run/results.jsonl`: durable reservations, original
  responses, billed failures if any, provider/model IDs, probabilities and timing.
- `judge-*.raw.json`, `classifier-*.raw.json`: separate blinded reference and classifier
  calls. All verified model-usage keys are exactly `claude-opus-5-5`.
- `reference-evidence-corrections.json`: one auditable quote-only correction. The judge
  paraphrased "The organization" as "EFF". The exact source sentence was restored;
  no label, acceptable set or original raw output was changed.
- `metrics.json`, `score.py`, `prepare.py`, `prepare_jobs.py`, `run_labels.py`,
  `run_teacher.py`: local reproduction/scoring evidence. The original attempted local
  Codex call is retained: GPT-6.1 Sol was rejected as unsupported for this account;
  no paid OpenAI fallback was used.
- Reusable bounded runner: `python/evals/scripts/run_category_decisions.py`.
  Its 22 initial mock-HTTP tests passed before paid execution; final route-drift hardening
  passes 24 tests, Ruff/formatting and MyPy.

The reference saw full routing rules even when judging the title+deck arm, which gives the
full-definition arm a possible advantage. The same model family authored the taxonomy, supplied reference judgments and served as
the strong comparator. Their agreement has shared model bias; these are not human labels.
The sample is balanced by aggregator, not representative of deployment prevalence, and
has substantial AI research coverage. There is no paired current-centroid baseline, no
personalized taxonomy test, no measured taxonomy-discovery gain, and no production or
end-to-end latency proof. All of those limit a claim of "higher quality" despite the
promising confidence-gated results.

The recent sessions consulted were **Research Low-Cost Content Filtering** and
**Optimize onboarding first-run speed**. The former's completed 8,000-decision filter test
is recorded in `2026-10-17-news-filter-evaluation-run.md`; its 97.2% held-out relevant-item
retention is a keep/exclude result and should not be treated as category accuracy. The
latter supplied the real grouping failures and showed that passage-generation latency
and coherence are independent bottlenecks. Both sessions had active implementation work;
this experiment did not modify their implementation scope.

Independent Opus 5.5 planning and final reviews completed with model routing verified.
The final review independently reproduced the scores and reran all 22 initial runner tests.
Its cautions about lenient acceptable sets, unproven routing-rule value, confidence-dependent
coverage, same-family bias and missing production baseline are reflected above. A reusable-runner
hardening change makes model/provider drift halt further dispatch; the completed run had no
such responses and was not repeated. The initial taxonomy prompt/schema were overwritten
during shape conversion; they are now explicitly reconstructed from the preserved preparation
script and development rows, with `reconstructed` in their filenames. The draft model response
and final executed prompt/schema remain original saved artifacts.

All 296 original responses were independently revalidated against their frozen requests and
normalized results. `verified-artifact-hashes.json` records their hashes.
The public [Jev model/API documentation](https://openrouter.ai/typesafe/jev-1.14) was checked;
observed billing and latency above come from this experiment's saved provider responses.

Follow-up: the [October 18 embedding comparison](2026-10-18-jev-versus-embedding-routing.md)
adds same-taxonomy profile/centroid routing replays against these frozen Jev outputs.
It remains distinct from a live per-user production replay.
