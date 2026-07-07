# SpaceX IPO News Clustering Investigation Plan

**Date:** 2026-05-20
**Scope:** Production-backed investigation of why Fast Reads did not cluster related SpaceX IPO / S-1 / financial disclosure articles.

## Goal

Use a local restore of the production database to explain whether the SpaceX rows should have clustered, identify the exact matcher stage that kept them separate, and turn any confirmed miss into durable regression coverage or a narrow matcher fix.

## Constraints

- Treat production as read-only.
- Restore production into a separate local database such as `newsly_prod`; do not overwrite the normal local dev DB.
- Keep dump and analysis artifacts in ignored paths: `.local_dumps/`, `.tmp/`, or `outputs/`.
- Use `news_items` as the Fast Reads source of truth. Legacy `contents` rows are only supporting context.
- Do not commit, push, deploy, or patch production unless explicitly requested.

## Execution Steps

1. Copy production state locally with `scripts/sync_production_state.py --target-db newsly_prod --skip-assets --no-restart-server`.
2. Query the restored local snapshot through the target DB selected by that sync command.
3. Query SpaceX-related `news_items` around the visible feed window, expanding to adjacent ingestion windows if needed.
4. Export title, source, domain, URL, status, visibility, representative, cluster size, ingestion time, processed time, and cluster metadata for each candidate row.
5. Reconstruct the relation matcher decision path for the SpaceX rows:
   - exact story/item/external key matching
   - candidate visibility and owner filtering
   - 7-day lookback and max-candidate selection
   - title-token prefilter and candidate rank
   - title/content/provenance embedding scores
   - primary and secondary thresholds
   - lexical guard
   - optional reranker behavior if enabled
6. Label likely gold clusters manually so genuine adjacent SpaceX stories are not mistaken for duplicates.
7. Run threshold/reranker/candidate experiments only against confirmed misses.
8. Add or update a production-backed regression case when the expected behavior is clear.
9. Validate with focused tests and lint on touched files.

## Success Criteria

- A local production snapshot is restored and queryable.
- The SpaceX candidate set and current cluster assignments are exported.
- Each non-match has a concrete explanation tied to a matcher gate.
- Any durable product miss is covered by a regression case.
- If a code/config change is needed, it is narrow, validated, and documented with the before/after matcher evidence.

## Execution Notes

Artifacts from the 2026-05-20 investigation:

- Production dump: `.local_dumps/newsly_prod_20260521T015701Z.dump`
- Local restore target: `newsly_prod`
- Matcher replay: `.tmp/spacex_cluster_probe.json`
- Threshold eval: `.tmp/title_clustering_threshold_eval.clean.json`
- SpaceX eval ablations:
  - `.tmp/title_clustering_spacex_embedding_ablation.json`
  - `.tmp/title_clustering_spacex_reranker_sweep.json`
  - `.tmp/title_clustering_spacex_full_reranker_ablation.json`

The restored snapshot contained 6,743 `news_items`. The SpaceX IPO/S-1 candidate set from the screenshot window was:

| ID | Status | Current cluster | Title |
|---:|---|---:|---|
| 12870 | ready | 1 | SpaceX files for IPO, to list on Nasdaq under symbol SPCX |
| 12877 | ready | 1 | SpaceX files S-1 registration statement with SEC |
| 12878 | ready | 1 | SpaceX 2025 revenue $18.7B, up 33% YoY; loss $4.9B vs profit; capex $20.7B |
| 12882 | ready | 1 | Anthropic to pay SpaceX $1.25B/month through May 2029, expand deal to Colossus 2 |
| 12889 | ready | 1 | xAI had $6.4B operating loss on $3.2B revenue in 2025 per SpaceX IPO filing |
| 12891 | ready | 1 | xAI to spend $2.8B on turbines, including $2B on mobile gas turbines it's sued over |
| 12896 | ready | 1 | SpaceX filing reveals Starlink hits 10.3M subscribers |
| 12899 | processing | 1 | Filing: SpaceX set aside $530M for potential litigation losses... |

Production did not have `NEWS_LIST_*` overrides set, so the matcher used the defaults:

- embedding model: `Qwen/Qwen3-Embedding-0.6B`
- primary threshold: `0.85`
- secondary threshold: `0.75`
- lookback: `7` days
- max candidates: `150`
- reranker: disabled

## Findings

The SpaceX rows were not missed because of candidate-window filtering. Later rows found earlier SpaceX items in their candidate pools, and the lexical guard passed. They were also not exact-key matches because every row had a different story URL or external ID.

The decisive gate was the semantic threshold. The highest pairwise combined scores were:

| Pair | Combined score | Threshold result |
|---|---:|---|
| 12878 -> 12889, SpaceX revenue/loss vs xAI operating loss | `0.727` | below `0.75` secondary |
| 12877 -> 12870, SEC S-1 vs IPO filing | `0.697` | below `0.75` secondary |
| 12878 -> 12896, SpaceX revenue/loss vs Starlink subscribers | `0.626` | below `0.75` secondary |
| 12870 -> 12889, IPO filing vs xAI operating loss | `0.610` | below `0.75` secondary |

Lowering the global secondary threshold is not a clean fix. Existing curated eval results:

| Variant | Passed | Macro precision | Macro recall | Macro F1 |
|---|---:|---:|---:|---:|
| current `0.85/0.75` | 54 / 92 | 0.983 | 0.768 | 0.814 |
| lower secondary `0.72` | 63 / 92 | 0.974 | 0.858 | 0.878 |
| lower secondary `0.70` | 68 / 92 | 0.974 | 0.896 | 0.910 |

The lower-threshold variants improved recall, but introduced or retained known negative over-merges, including Apple Siri vs later chatbot-roadmap titles and MacBook Neo vs iPhone 17e titles. A simple global threshold change would therefore trade one SpaceX miss for broader false-positive risk.

## Recommendation

Do not patch the duplicate/relation matcher solely by lowering thresholds. These SpaceX rows are better described as an event/topic group around one filing than as exact duplicate articles. The durable product fix should be a separate event-grouping layer for Fast Reads, or a highly constrained filing-event relation rule if the desired behavior is to suppress all filing-derived disclosures under one representative.

If we choose the constrained relation-rule path, add a regression case for the SpaceX filing family first and run it against the existing negative cases before changing production behavior.

## Ablation Results

Two production-backed SpaceX eval cases were added:

- `batch_007_spacex_s1_disclosures`: broad positive family for SpaceX IPO / S-1-derived disclosure stories.
- `batch_007_negative_spacex_s1_vs_adjacent`: guardrail case so SpaceX IPO/S-1 stories do not absorb the Starship launch story or the earlier Cursor-acquisition-after-IPO story.

Full-suite results after adding those cases:

| Variant | Passed | Macro precision | Macro recall | Macro F1 |
|---|---:|---:|---:|---:|
| embedding current `0.85/0.75` | 54 / 94 | 0.984 | 0.754 | 0.801 |
| embedding secondary `0.72` | 63 / 94 | 0.974 | 0.842 | 0.864 |
| embedding secondary `0.70` | 68 / 94 | 0.967 | 0.880 | 0.894 |
| embedding secondary `0.68` | 75 / 94 | 0.967 | 0.915 | 0.918 |
| reranker enabled, threshold `0.45` | 81 / 94 | 0.979 | 0.947 | 0.947 |
| reranker enabled, threshold `0.15` | 84 / 94 | 0.979 | 0.968 | 0.964 |

SpaceX-specific results:

| Variant | Positive SpaceX F1 | Negative SpaceX F1 | Notes |
|---|---:|---:|---|
| embedding current | 0.133 | 0.286 | Creates small subclusters only. |
| embedding `0.70` / `0.68` | 0.133 | 0.222 | Still misses broad S-1 family and falsely absorbs Cursor story. |
| reranker `0.45` | 0.250 | 0.667 | Groups IPO/S-1/Starlink and revenue/xAI subclusters; keeps Cursor and Starship separate. |
| reranker `0.15` | 0.400 | 0.667 | Adds litigation story to IPO/S-1/Starlink subcluster; still does not group Anthropic or xAI turbines into the broad family. |

Reranking is a better general ablation than lowering embedding thresholds: it improves suite recall and F1 while preserving higher precision than aggressive threshold lowering. It also avoids the SpaceX Cursor false merge that appears with embedding-only thresholds at `0.70` and below.

However, reranking still does not fully solve the broad product expectation that all S-1-derived disclosure stories collapse into one representative. That behavior likely needs a separate event/filing grouping layer or a constrained filing-event rule, not only a pairwise duplicate matcher change.

## Reranker Rollout Notes

The reranker is already implemented behind settings and is currently disabled by default:

- `NEWS_LIST_RERANKER_ENABLED`
- `NEWS_LIST_RERANKER_MODEL`
- `NEWS_LIST_RERANKER_DEVICE`
- `NEWS_LIST_RERANKER_MAX_CANDIDATES`
- `NEWS_LIST_RERANKER_BATCH_SIZE`
- `NEWS_LIST_RERANKER_MAX_LENGTH`
- `NEWS_LIST_RERANKER_SIMILARITY_THRESHOLD`

Local timing on the two SpaceX eval cases, including model/cache checks in a fresh process:

| Mode | Wall time |
|---|---:|
| embedding only | 8.5s |
| reranker enabled, threshold `0.15` | 14.1s |

Recommended staged config to test in a worker environment:

```bash
NEWS_LIST_RERANKER_ENABLED=true
NEWS_LIST_RERANKER_SIMILARITY_THRESHOLD=0.15
NEWS_LIST_RERANKER_MAX_CANDIDATES=8
NEWS_LIST_RERANKER_BATCH_SIZE=4
NEWS_LIST_RERANKER_DEVICE=auto
```

Rollout cautions:

- Content workers now warm the reranker model at startup when `NEWS_LIST_RERANKER_ENABLED=true`; without that guard, the first clustering task pays model-load latency.
- Watch queue latency for `process_news_item`, memory pressure, and `news_relations` fallback logs.
- Keep embedding thresholds unchanged; threshold lowering caused SpaceX Cursor false merges and additional known negative over-merges.
- Treat reranker enablement as a quality improvement for pairwise relation matching, not as a full solution for event-level grouping.
