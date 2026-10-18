# News filter corpus

The Jev filter-text experiment uses a frozen, public production-derived corpus.
Raw exports, summaries, judge labels, and model outputs belong under ignored
`test-results/`; only the collection method and synthetic tests are checked in.

## Export through the Rust operator

`export_candidates.sql` is a bounded SELECT template. Substitute the four named
placeholders with reviewed constants: `__PLATFORM__` from the nine keys below,
`__AS_OF_UTC__` as one fixed UTC snapshot time, `__WINDOW_DAYS__` as 30 (90 for
SciURLs), and `__SEED__` as `news-filter-2026-10-17`. Save each resulting SQL file
as `candidates-<platform>.sql`. Run the saved query through the Rust operator:

```sh
ssh news-app-server 'sudo docker exec newsly-workers /usr/local/bin/newsly-admin db query --output json --limit 600 --sql "<reviewed SELECT>"'
```

When automating the shell invocation, use a proper shell argument quoting function
(e.g. `shlex.quote` in a one-off operator invocation), not string concatenation
of untrusted values. Save each JSON response as
`candidates-<platform>.operator.json`. The operator enforces read-only transactions,
row/byte/time bounds, and redaction; never request `--unsafe-raw`. Its output must
have `ok=true` and `data.truncated=false`. The query exports at most 500 rows per
platform, no account data, full raw metadata, bodies, or classification labels.
Python evaluation code never connects to production PostgreSQL or SSH.

Create `export_snapshot.json` alongside the exports:

```json
{"requested_at_utc":"2026-10-17 17:49:26","seed":"news-filter-2026-10-17","public_global_only":true}
```

These metadata attestations accompany, and do not replace inspection of, the saved
SQL. Eligibility requires global visibility, no owner user, ready status, no
representative parent, and a nonblank factual summary. No user's subscriptions,
reading history, onboarding answers, or preference text are exported.

## Prepare a new frozen directory

```sh
uv run --project python/evals python python/evals/scripts/prepare_news_filter_corpus.py \
  --operator-export-dir test-results/news-filter-eval-2026-10-17 \
  --output-dir test-results/news-filter-eval-2026-10-17-reproduced \
  --seed news-filter-2026-10-17
```

The output directory must not exist. The script writes `sample.jsonl`, a separate
up-to-four-item `smoke-sample.jsonl`, and `corpus-manifest.json`. It never overwrites
a frozen experiment. Source export hashes, available SQL and legacy collection
script hashes, the current sampler hashes, sample hashes, and coverage counts are
recorded. Smoke items are outside all selected story groups and marked development;
they must not be counted in the 1,000-item benchmark. All variants and personas
must inherit each source's fixed split.

| Aggregator | Count | Topic allocation |
|---|---:|---|
| brutalist | 200 | 50 each sports, business, politics, science |
| arxiv | 150 | 30 each cs.LG, cs.CV, cs.AI, cs.CL, stat.ML |
| hackernews | 100 | Publisher-balanced |
| memeorandum | 130 | Publisher-balanced |
| techmeme | 100 | Publisher-balanced |
| hfpapers | 90 | Publisher-balanced |
| finurls | 90 | Publisher-balanced |
| mediagazer | 90 | Publisher-balanced |
| sciurls | 50 | Older science challenge, reported separately |

The first snapshot contains 950 recently **ingested** stories and 50 SciURLs
stories ingested September 1; successful current crawler checks did not establish
fresh SciURLs inventory. The recent label is not a claim of verified publication
date. `published_at` retains null; the legacy Briefing display-time fallback is
stored separately. SciURLs' challenge designation is fixed to this v1 study;
a future study with fresh inventory needs a reviewed slice definition/version.

Nonrepository publisher domains are capped at 50. `arxiv.org` is explicitly exempt
for arXiv and HF Papers because both required strata share this research repository.
Source taxonomy does not constitute a judge's relevance label. Source names remain
null to match the current Rust News factual projection; provenance stays separate.

The 400/600 development/test split uses seeded ordering within each topic cell.
Production representative IDs, exact canonical URL hashes, compact summaries,
normalized titles, and similar titles are grouped before selection. The frozen v1
similarity rule is token Jaccard >=0.80 with at least six tokens and
`abs((left_ingested-right_ingested).days) <= 3`. Differently worded reports of the
same event may remain; audit event leakage before claiming semantic event-disjoint
splits. A predominantly English corpus plus a non-Latin-character audit is not a
multilingual evaluation.

This balanced, publisher-enriched challenge set does **not** estimate deployment
prevalence. Persona-pair enrichment introduces another sampling distortion; simple
platform weights cannot recover real-world precision. Report conditional challenge
metrics and the older SciURLs slice separately.
