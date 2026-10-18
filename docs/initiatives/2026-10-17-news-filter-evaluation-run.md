# News filter evaluation: execution record

October 17, 2026. Completed the 1,000-story / 8,000-decision ablation using [Jev 1.14 through OpenRouter](https://openrouter.ai/typesafe/jev-1.14). The original 2,000 primary Opus reference judgments and 200-pair consistency audit were reused unchanged. No product integration or deployment was performed.

The adapter uses OpenRouter's Decisions endpoint, restricts routing to TypeSafe with fallbacks disabled, and verifies the returned dated revision `typesafe/jev-1.14-20261014`, provider, unique generation ID, tokens, and reported cost. The [API reference](https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-request) documents the provider routing field. No direct TypeSafe key or agreement is needed for this route.

## Result and recommendation

**Jev is cheap enough for this use case; there is no evidence here that we need to train a custom model first.** Completed 8,000 decisions on the frozen 1,000-story corpus, plus 16 smoke calls and five protocol retries. OpenRouter reported **$0.254117976 total**, with no unknown-billing attempts. The main run's median request latency was 185 ms, p95 278 ms, maximum 2.35 s. These are API-request timings from this client, not end-to-end product latency or a service-level guarantee. Local Claude subscription grading is separate from the Jev charge.

The development rule selected **structured JSON text at a 0.50 exclusion threshold**: hide only when `choice=no_match` and its probability is at least 0.50. Development admission precision was 83.4%, recall 97.6%, and macro-profile precision 86.0%. This is the frozen research winner; no choice was made using the held-out results.

On the **570 recent held-out stories / 1,140 preference pairs**, three reference-uncertain pairs are kept outside binary metrics. Among the remaining 1,137 pairs, the selected filter:

- Kept **412/424 relevant pairs (97.2% recall)**; hid 12.
- Removed **641/713 irrelevant pairs (89.9%)**; admitted 72.
- Made admitted content **85.1% relevant**, versus 37.3% when admitting everything.
- Made exclusions **98.2% correct**; excluded 57.4% of binary-labelled pairs overall.
- Had story-cluster bootstrap 95% intervals of **82.0–88.2% admission precision**, **95.4–98.6% relevance recall**, and **97.0–99.1% exclusion precision**. These quantify sampling uncertainty against Opus, not human truth or production calibration.

All formats at the selected 0.50 threshold, descriptively on the same recent held-out pairs:

| Filter text | Admission precision | Relevant kept | Irrelevant removed | Relevant hidden |
|---|---:|---:|---:|---:|
| Prose | 85.1% | 97.2% | 89.9% | 12/424 |
| Bullets | 83.8% | 97.6% | 88.8% | 10/424 |
| Structured | 85.1% | 97.2% | 89.9% | 12/424 |
| Checklist | 81.1% | 97.9% | 86.4% | 9/424 |

**Structured text did not clearly beat ordinary prose.** Their aggregate counts are identical, although the individual decisions differ. The paired 95% interval for the precision difference is −1.53 to +1.54 percentage points. Structured versus bullets also includes zero (−0.18 to +2.76 points). Structured exceeded checklist precision by an estimated 4.1 points (interval +1.61 to +6.31 points) at this threshold. Keep the selection unchanged; do not turn descriptive test comparisons into a new winner.

The original product design's conservative **0.90 threshold** has a different tradeoff:

| Filter text | Admission precision | Relevant kept | Irrelevant removed | Relevant hidden |
|---|---:|---:|---:|---:|
| Prose | 67.8% | 99.8% | 71.8% | 1/424 |
| Bullets | 68.2% | 100.0% | 72.2% | 0/424 |
| Structured | 68.3% | 100.0% | 72.4% | 0/424 |
| Checklist | 63.2% | 100.0% | 65.4% | 0/424 |

For the first product trial, retain that already-proposed conservative threshold and fail-open behavior while the user reviews examples and we run a naturally arriving News shadow test. Do not silently adopt the research winner's more aggressive threshold. At 0.90, structured text removed 516/713 irrelevant pairs with no observed misses among 424 relevant pairs; that finite sample does not guarantee zero misses. Prose missed one. These were predeclared threshold curves, not a newly tuned threshold.

**Filter editing should stay plain-language.** Store the user's onboarding wording as the editable, versioned News filter. Make included interests and exclusions explicit, with “any interest matches; exclusions take precedence” when that is the intended rule. Structured fields are a reasonable model-input representation when those clauses already exist explicitly, but this ablation does not justify adding an LLM compiler or forcing users to edit JSON. Avoid a verbose decision checklist by default. Settings remains one prominent News-filter row; saved Articles and Podcasts remain outside filtering.

The 30 older held-out SciURLs stories / 60 pairs remain separate: structured 0.50 kept 20/21 relevant pairs, removed 37/39 irrelevant pairs, and achieved 90.9% admission precision. Small denominators make this descriptive only.

## Where the benchmark is weak

At the selected 0.50 threshold, climate/energy kept 8/10 recent test positives and sports 26/28. Neither domain had enough development positives for the preregistered per-profile gate. Brutalist supplied seven of the 12 false exclusions and kept 79/86 relevant pairs. Those weaknesses are obscured by the aggregate 97.2% recall.

Review of Opus’s recorded rationales for the twelve false exclusions suggests boundary ambiguity: energy infrastructure inside war coverage, fantasy-sports analysis versus excluded betting content, government institutions inside technology/business stories, and theoretical ML research without obvious AI language. These are interpretations of disagreements, not adjudicated human errors. Climate has only 15 positives in the entire corpus; add independently frozen positive-rich coverage before claiming broad domain reliability. Science/health and sports also have sparse development support.

Software/security admitted 27 relevant and 20 irrelevant recent test pairs (57.4% precision), and business admitted 68 relevant and 28 irrelevant (70.8%). Future work should inspect these false admissions and clarify scope against fresh examples, without rewriting this run's labels or retuning on its test set. Only four of all 2,000 Opus references were insufficient-evidence cases, so uncertainty handling remains largely untested.

The user can start with the existing blinded 48-case review packet and six separate repeat-disagreement cases below. No training labels are required to use zero-shot Jev; an evaluation still needs reference judgments and a small human check to establish whether those judgments match the desired meaning.

## Runtime reliability

All 8,000 jobs eventually completed in **8,005 main attempts**. Five non-argmax Choice responses remained billed failures (0.0625% of first attempts); each succeeded on its one retry. One additional probability-sum rejection was recovered from its original response without another request. The reported cost includes every attempt and smoke call; no budget was reset. There are zero exhausted jobs.

The selected structured format's first-attempt fail-open sensitivity has exactly the same headline recent test precision/recall as the final successful-response analysis; two first responses in that test slice were unavailable. Prose's first-attempt recall is 97.4% versus 97.2% after retry. Full sensitivity, per-attempt provenance, cost and final validation-source hashes are in `run-v2-openrouter/execution-summary.json`; the generation billing spot-check is in the smoke directory. Raw responses and original failures remain in the journal.

## Frozen experiment

- 1,000 unique public canonical production News items, exported read-only through
  the Rust operator at 2026-10-17 17:49:26 UTC.
- Nine aggregators and 377 publisher hostnames. Hacker News contributes 100 items.
  The source allocation is Brutalist 200, arXiv 150, Memeorandum 130, Hacker News
  100, Techmeme 100, HF Papers 90, FinURLs 90, Mediagazer 90, and SciURLs 50.
- 950 items were ingested within 30 days. The 50 older SciURLs items are a separate
  challenge slice. Ingestion time does not prove publication freshness.
- Eight synthetic interest profiles; two different profiles per item; four
  equivalent renderings: prose, bullets, structured fields, and checklist.
- 2,000 item/preference pairs and 8,000 Jev jobs; 400 development stories and 600
  held-out stories. Selection maximizes macro-profile admission precision subject
  to the preregistered 95% recall gates, using recent development data only.
- Frozen sample SHA-256:
  `209513e194eb7f5dbc07ac546792de191e640f1f8c854b0da9e79638529eeb2e`.
- Frozen experiment manifest:
  `f270b07e1fda75944c9c94503709e33be9ddb6b91ed2ad12e74ad336213c542b`.

These quotas and profile pairings deliberately enrich the test set. Its
precision numbers will describe this benchmark against Opus reference judgments,
not the prevalence or quality of a real user's feed. Semantic duplicates across
different coverage of an event may remain despite canonical and lexical grouping.

The primary Opus reference contains **745 matches, 1,251 nonmatches, and four
insufficient-evidence judgments**. Relevant-pair support is uneven:

| Profile | All reference positives | Recent development positives |
|---|---:|---:|
| AI systems | 236 | 93 |
| Software/security | 49 | 22 |
| Science/health | 58 | 12 |
| Climate/energy | 15 | 2 |
| Economy/business | 116 | 45 |
| Public affairs | 133 | 55 |
| Media/culture | 92 | 41 |
| Sports | 46 | 18 |

Climate/energy, science/health, and sports fall below the preregistered 20-positive
recent-development minimum. Their recall remains descriptive and cannot support a
confident domain-specific claim. Source variety alone does not guarantee enough
relevant examples. A later version should deliberately enrich positive examples in
these domains before making deployment claims; this run's frozen cases and
selection rule are not being changed after grading.

## Execution and checks

The separate unsampled Opus smoke passed. Reference generation used the local
Claude subscription CLI with explicit `claude-opus-5-5`, tools disabled, at most two
concurrent calls, and a persisted 64-call main allowance plus one smoke call. There
is no paid judge API fallback. The main run used **61 CLI invocations**: 54 for the
primary references and seven for the five accepted repeat batches plus two retries.
Including the one smoke invocation, **62 of the allowed 65 grading invocations**
were used. The three independent review consultations were separate.

The 200-pair fresh-session repeat audit agreed on **194/200 judgments (97%)**.
Three changed from nonmatch to match and three from match to nonmatch. This measures
judge consistency, not reference truth or Jev performance. Both original and repeat
labels remain intact. All 50 primary and five audit batches passed raw-response
verification; no judge calls remain active.

Strict quote checking initially rejected both inserted ellipses and curly/straight
quote substitutions. Before any Jev calls, validator v2 was adopted: only four
curly-quote characters and whitespace are normalized; changed words and inserted
ellipses still fail. One whole stored batch was explicitly revalidated and accepted
without another call, preserving its original failure, raw response, exact attempt
index, raw hash, and validator version. The prompt, rubric, manifest, and all 55
main/audit batch hashes stayed unchanged. Independent Opus review verified the
correction and found no material blockers in the hardened implementation.

Every accepted reference is checked against its saved raw response and its completed
or promoted attempt. Older completed attempts did not record raw hashes at the time;
the provenance report states that limitation rather than backdating hashes. Future
completions record raw hashes; future recovery records include timestamps.

Original local validation: **42 focused tests passed; Ruff and MyPy passed.** Three separate
read-only Opus consultations covered design, implementation, and follow-up review.
Returned model usage named only `claude-opus-5-5` in each consultation. Production
was not mutated; no commits, pushes, deployment, or application integration ran.

## Human review and Jev handoff

The ignored artifact directory is `test-results/news-filter-eval-2026-10-17/`.
Its `run-v1/human-audit-48.csv` and JSONL contain 40 random audit cases plus eight
extra cases for profile coverage, with model labels hidden. Instructions are in
`run-v1/HUMAN_REVIEW.md`. Human labels remain a separate sensitivity layer, split
by review cohort; they never silently replace the original reference or selection.
No human annotations have been submitted. These labels evaluate the written
synthetic interests; a personal onboarding profile needs a separately frozen run.
The separate `run-v1/human-repeat-disagreements.csv` and JSONL add six blinded
targeted cases where the two Opus judgments differed. Their error rates must not be
pooled with the random audit. This yields 54 worksheet rows across both packets.

Jev allowance: 16 smoke attempts/$0.01, then at most 8,384 main attempts/$4.99.
The total is capped at 8,400 attempts and $5 inference spend. The frozen main
requests reserve $2.1427 before retries under the conservative byte-based accounting;
this is not a provider invoice. The smoke confirmed input-only billing at $0.042 per million tokens. Purchases or subscriptions are separate from the inference allowance.

## OpenRouter execution

The original `run-v1` and `smoke-v1` remain intact. Schema-v2 manifests in `run-v2-openrouter` and `smoke-v2-openrouter` preserve the exact pairs, filter text, questions, split, and job order. Only provider/model transport changes. A hashed reference-source descriptor binds the original manifest, labels, judge checkpoints, prompts, and raw responses; no reference labels were regenerated. Human worksheets still belong to the original manifest.

- Main manifest: `f8a6a6d913e9bc6f1a29eb55c5f7e0b5479c389827a8e3a73565740dd7d1fd31`.
- Smoke manifest: `e8767cdd1243e1f71d55c679dc5f2a577b93140953ff896e62a81bdb364836d8`.
- Smoke: 16/16 decisions, 12,806 input tokens, reported cost **$0.000537852**. One generation was independently verified through OpenRouter's generation endpoint. Latencies ranged 0.161–0.365 seconds.
- Main attempt 30 returned cent-rounded probabilities totaling 0.99. The validator now permits totals 0.99–1.01 only when all three values are on the two-decimal grid; values remain unchanged for thresholding. This is an observed API deviation consistent with rounding, not a documented vendor guarantee. An append-only revalidation event accepted the exact saved response without another request, retaining the original failure and raw hash.
- Main attempt 121 selected `no_match` despite probabilities `match=0.49`, `no_match=0.48`, `insufficient_evidence=0.03`. This violates the documented Choice contract. It remains a failed decision, with its bill and response preserved. A narrow acknowledged retry and bounded automatic retries for the same known-billed failure do not change the choice or probabilities. Unknown billing, model/provider changes, and unrelated schema failures still halt dispatch.

The abandoned direct signup stopped at TypeSafe's separate agreement page. No direct key was created. Resume and verification commands are in [FILTER_EVALS.md](../../python/evals/FILTER_EVALS.md).

OpenRouter migration review used the local Claude CLI. Its planning consultation reported both `claude-opus-5-5` and `claude-haiku-5-5`; it was not exclusively Opus. The implementation review and focused recovery review reported only `claude-opus-5-5`. Verified corrections include exact dated model/provider/cost checks, source-reference hashes, preservation of original human-label identity, bounded choice-mismatch retries, and original-response recovery. Review invocations are separate from the 62 reference-grading invocations.

The final Opus-only follow-up verified the recovery fixes and identified one rare failure-reporting defect: an unsafe choice-mismatch response could halt correctly but make its journal unreadable. The provider now records such unsafe cases as halted invalid responses; mocked duplicate-generation and over-reservation tests verify the journal remains readable. The reporting helper refuses to compute held-out sensitivity unless scoring is complete and a selection record exists. These changes affect failure handling, not model outputs, selection thresholds, or reference labels.

Final local validation: **112 focused tests passed; Ruff and MyPy passed** (13 source files in the full MyPy pass, with the final provider change separately checked). The final two provider regression cases cover the rare unsafe mismatch paths identified by Opus. No production mutations, commits, pushes, or releases occurred.
