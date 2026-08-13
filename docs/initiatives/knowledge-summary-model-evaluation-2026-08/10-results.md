# Knowledge Summary Model Evaluation

Date: 2026-08-13

## Decision

Use GPT-5.6 Luna as the leading replacement candidate for DeepSeek V4 Flash for Newsly long-form knowledge summaries.

Luna had the strongest pairwise result, was favored independently by both judges, finished positive on all ten test sources, and cost 13% less than DeepSeek in the candidate-generation pass. This is strong evidence for a larger validation run, not a production rollout decision by itself.

## Results

The relative score ranges from -1 to +1. The pairwise index maps -1 to 0, a tie to 50, and +1 to 100. Candidate cost is the total measured generation cost for the ten summaries; it excludes evaluator spend.

| Model | Relative score | Pairwise index | 95% CI | Sources +/0/- | Zero verdicts | Candidate cost | Cost vs. DeepSeek |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| GPT-5.6 Luna | +0.755 | 87.75 | +0.679 to +0.832 | 10/0/0 | 11 | $0.0229 | 0.87x |
| GLM 5.2 | +0.490 | 74.48 | +0.213 to +0.730 | 8/0/2 | 13 | $0.1535 | 5.83x |
| MiniMax M3 | +0.427 | 71.32 | +0.254 to +0.608 | 10/0/0 | 14 | $0.0888 | 3.37x |
| Kimi K2.6 | +0.389 | 69.42 | +0.131 to +0.640 | 7/0/3 | 23 | $0.6106 | 23.19x |
| MiMo V2.5 Pro | +0.340 | 66.98 | +0.130 to +0.558 | 7/0/3 | 19 | $0.0906 | 3.44x |
| DeepSeek V4 Flash | 0.000 | 50.00 | reference | 0/10/0 | — | $0.0263 | 1.00x |

All five challenger confidence intervals were above zero in this ten-source sample. The interval is widest for GLM, Kimi, and MiMo, so their exact ordering is less certain than Luna's lead.

## Method

- Inputs: the five most recently completed favorited articles and five most recently completed favorited podcasts for the test account. Source titles, URLs, text, and per-source judgments are intentionally excluded from this public repository.
- Candidates: the same 60 production-schema outputs from the initial comparison—six models on ten sources. No candidate was regenerated for the pairwise pass.
- Baseline: DeepSeek V4 Flash.
- Judges: GPT-5.6 Sol and Claude Opus 5, with anonymous A/B side assignment randomized independently for every source, challenger, and judge.
- Volume: five challengers x ten sources x two judges, producing 100 final pair judgments and 400 editorial criterion decisions.
- Outcomes: +1 when the challenger better satisfied a criterion, 0 when the result was materially equivalent or genuinely unclear, and -1 when DeepSeek better satisfied it.
- Weights: 35% factual fidelity, 25% salient coverage, 20% reader usefulness, 10% clarity and structure, 7% deterministic exact-quote support, and 3% deterministic first-pass schema compliance.
- Uncertainty: 95% confidence intervals from 10,000 paired bootstrap resamples over the ten source-level weighted outcomes.
- Human labels: none. This was a dual-model judge evaluation, not a human preference study.

## Judge Audit

- The judges exactly agreed on 134 of 200 paired editorial decisions (67.0%).
- They chose directly opposing directions on 10 of 200 decisions (5.0%).
- Cohen's kappa was 0.361.
- There were 80 explicit zero verdicts among 400 editorial decisions. Opus 5 used zero more frequently than Sol.
- Luna's judge-specific relative scores were +0.875 from Sol and +0.635 from Opus 5.
- A/B placement was exactly balanced across the full run: 200 criterion decisions with the challenger as A and 200 as B. The challenger mean was +0.465 as A and +0.575 as B, indicating a possible second-position effect. No post-hoc score correction was applied.

## Sensitivity and Limits

The earlier pointwise evaluation ranked Luna, GLM, Kimi, MiniMax, MiMo, then DeepSeek. The pairwise evaluation swapped MiniMax and Kimi but left Luna first by a wide margin.

The corpus is deliberately small and recent, and all sources came from one user's favorites. The two judges are model-based and not independent human labels. Provider retries and failed calls did not always return usage, so checkpoint-derived judge cost is a lower bound and must not be used as billing reconciliation.

The detailed self-contained HTML report and raw evaluation workspace remain local-only because they contain recent-favorite metadata, source excerpts, candidate-level judgments, and operational checkpoints.
