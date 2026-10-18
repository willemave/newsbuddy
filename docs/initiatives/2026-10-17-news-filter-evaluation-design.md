# News filter text evaluation with Jev

Status: corpus and experiment frozen; local harness built; 2,000 primary references and 200 repeat judgments completed, October 17, 2026. The user subsequently selected OpenRouter Jev 1.14; all 8,000 decisions and the 16-request smoke are complete; the frozen development selection chose structured text at threshold 0.50. See the separate [execution record](2026-10-17-news-filter-evaluation-run.md); no model winner is assumed.

## Question and scope

Which representation of the same user's interests best preserves relevant News while excluding irrelevant News? Measure the decision made from the actual saved summary, rather than whether an evaluator independently likes the article. This study informs the News-only filter described in [the product design](2026-10-16-summary-preference-filter-design.md). It does not change live admission, subscriptions, summarization, or published Briefing passages.

The original plan selected direct Jev `jev-1.13.0`; no inference calls were made on that route. The user's October 17 revision selects [OpenRouter `typesafe/jev-1.14`](https://openrouter.ai/typesafe/jev-1.14), whose endpoint metadata identifies revision `typesafe/jev-1.14-20261014`. Preserve the original run and freeze new manifests with the selected route and model. Four filter representations are compared on identical item/preference pairs, with the same source projection, evaluator instructions, Choice options, and decision thresholds. Use local Claude Code with explicit `claude-opus-5-5` for independent reference judgments. The completed judgments are reused with source provenance checks; OpenRouter is authorized for Jev inference only, not additional judge calls.

The result is evidence about a balanced, deliberately varied test population and synthetic interest profiles. It is not a measurement of real users' satisfaction or the precision of their actual feeds. User feedback is a separate, valuable validation layer. No personal labeling exercise or fine-tuning is required to use the proposed product.

## Corpus: 1,000 production News items

Use read-only Rust operator exports from live production, then process the versioned export locally in `python/evals`. Python never connects to the production database. Export only public canonical News content and required provenance; exclude private bookmarks, user memberships, onboarding text, chats, API keys, and full article bodies. Store raw exports and model outputs under ignored `test-results/news-filter-eval-2026-10-17/`.

Inventory on October 17 found nine aggregator keys. The quota design is:

| Aggregator | Target | Reason |
|---|---:|---|
| Brutalist | 200 | General news; balance its sports, business, politics, and science topics where available |
| arXiv | 150 | Scientific/technical papers; spread available categories |
| Hacker News | 100 | Software and technical discussion; cannot dominate the benchmark |
| Memeorandum | 130 | Politics and public affairs |
| Techmeme | 100 | Technology industry |
| Hugging Face papers | 90 | AI research |
| FinURLs | 90 | Finance and business |
| Mediagazer | 90 | Media and journalism |
| SciURLs | 50 | Explicitly older science challenge slice |
| **Total** | **1,000** | **950 recent items plus 50 older science items** |

Select recent items by ingestion time in the preceding 30 days; do not imply that ingestion establishes publication freshness. In the frozen sample, 424 actual publication dates are absent and remain null. SciURLs has no ready canonical items in that window in the inspected inventory; its available ready items are older. Preserve exact dates and report those 50 separately. A successful scraper health check is not evidence of fresh content. Never quietly replace a missing stratum with Hacker News or label older material recent.

Draw deterministically using seed `news-filter-2026-10-17`. Balance publishers within aggregator/topic cells and cap an ordinary publisher at 50 items where feasible. Document an explicit exception for repository hosts such as arxiv.org, which serve distinct papers from many authors across arXiv and Hugging Face. Repository balance is not publisher diversity. Keep all aggregator provenance for duplicate stories, but assign each canonical story to one sampling stratum so it counts once.

Require a readable factual summary and canonical representative identity. Remove exact duplicate URLs, identical normalized summaries, and duplicate canonical IDs. Group near-duplicate coverage conservatively by normalized title/event similarity and inspect cross-source clusters. Split these story groups, not individual judgments, into 40% development and 60% held-out test, stratified as closely as group sizes permit. Keep every profile/variant for a story in the same split. Record residual event duplication as a limitation if reliable grouping is unavailable. Freeze the export hash, sample, split, and pair assignments before running judges or Jev.

The frozen sample meets all quotas, has 377 publisher domains (including 238 arxiv.org repository items), and its largest ordinary publisher is MarketWatch with 33 items. These counts describe observed hostnames, not verified independent editorial organizations. The manifest also hashes the extraction SQL and sampling implementation. The sample appears predominantly English; report that limitation and do not claim multilingual coverage from a non-Latin character heuristic. The manifest records eligible inventory, sampling rules, actual counts and shortfalls, exact export time, source hashes, canonical IDs, publisher/topic/aggregator distribution, language coverage, dates, group IDs, and split. This corpus evaluates only already-ingested, successfully summarized News; it cannot measure discovery or summarization failures.

## Preference cases and four structures

Use eight explicit synthetic profiles in [profiles.v1.json](../../python/evals/datasets/news_filter/profiles.v1.json): AI systems; software/security; science/health; climate/energy; economy/business; public affairs; media/culture; sports. Each has two positive interest clauses and two explicit exclusions. Their common semantics are “any one interest is sufficient; exclusions take precedence.” They span both broad topics and narrower distinctions such as research versus launch marketing or policy versus campaign polling.

Each of the 1,000 items gets two different profiles: one chosen by a frozen topic/aggregator affinity rule, and one assigned by a deterministic rotation balanced within each aggregator. A fixed climate/energy keyword rule takes priority over aggregator affinity so that profile is not left with only accidental matches. The affinity rule enriches plausible matches and difficult adjacent cases; it is not a relevance label. This yields **2,000 item/profile pairs**. Record pairing role and profile counts before calling models. Profiles with fewer than 20 development reference-positive pairs are descriptive only and do not support a confident per-profile recall claim. Do not choose pairs based on Jev's output or retrospectively discard easy or embarrassing cases.

Render exactly the same clauses into four variants:

1. **Natural prose:** first-person onboarding-style sentences.
2. **Include/exclude bullets:** labeled lists, followed by the shared precedence rule.
3. **Structured fields:** JSON-formatted filter text with interests, exclusions, and the same rule.
4. **Decision checklist:** test exclusions first, then any included interest.

All four are supplied as text in the same `state.filter` field; this does not separately test changing the API field to an object or splitting a policy into multiple questions. All variants use the same underlying clauses; none gets extra examples, new facts, or a stricter definition of interest. Their different lengths are part of the treatment: report token cost and latency as well as quality. A separate future length-matched control could distinguish structure from verbosity if the first result warrants it. Do not claim this initial comparison isolates punctuation alone.

The judge sees the canonical intent specification, not any of these rendered variants. Before running the full set, inspect all eight specifications and renderings for semantic equivalence. The user's real onboarding wording can form a separately versioned additional profile once supplied; it must not silently change a frozen experiment. Use the user's labels as the authority for that person's preferences, without claiming their answers represent the synthetic users.

## Input and decision contract

Jev and the judge see the same bounded title, factual summary, and key points. No legacy `to_read`/`skip`, raw article, scraped HTML, comments, stored relevance scores, or externally fetched knowledge. Publisher/aggregator metadata is retained for analysis but is not included as extra inference evidence in this first ablation. Record a separate exact inference-projection version and hash for these three fields. The corpus summary hash, which also binds URL/date provenance, is retained separately and does not describe the narrower inference input. Separate the preference from untrusted source text; instructions inside a summary never override the evaluator's task.

Use a single Choice question with `match`, `no_match`, and `insufficient_evidence`. A clearly unrelated topic is `no_match`, not uncertainty. Missing evidence for a material qualifier such as original research is uncertainty. The model must not fill in missing facts from its memory. Uncertainty is a legitimate answer and must not be forced into a binary reference label. Settle one important boundary identically for Jev and Opus: “For an exclusion phrased X without Y, apply it when the supplied evidence describes X and contains no substantive Y. Judge this supplied representation, not unseen full text.” This specifically evaluates what the summary presents; it does not prove that the full article lacks Y. A separate claim such as whether a study is original still requires supporting evidence. Supply the same Choice definitions and this rule to both models.

All four variants use the same surrounding question and option descriptions. Run each variant in a separate request to avoid cross-variant context. Shuffle job order deterministically across models' input variants, profiles, sources, and splits so one structure does not always run during a different load period. Preserve the requested and returned model ID. Unexpected routing stops the experiment rather than silently mixing results.

The product's provisional policy admits matches and uncertainty, and excludes only a winning `no_match` above a probability threshold. A probability of 0.90 is not a guarantee of 90% correctness. Evaluate both raw Choice quality and this effective admission policy.

## Independent reference judgments and human review

Run Opus through the local CLI with tools disabled, explicit model and effort, and validated structured output. The grading task is limited to the supplied data; it cannot browse, inspect Jev outputs, or delegate. Require an opaque exact pair ID, three-way label, a decisive clause (`interest_1`, `interest_2`, `exclusion_1`, `exclusion_2`, or `none`), a short justification, and an evidence excerpt where available. Hide aggregator, publisher, profile ID, pairing role, and split. Put an item's two pairs in different batches and record positions so neighbouring labels do not imply a mutually exclusive contrast. In this frozen run, a hash assigns each item's profiles to two halves; batches are shuffled within each half and the first half runs before the second. Pairing role is not tied to half, but execution order is not fully interleaved. Validate complete batch coverage, duplicate IDs, unknown IDs, and allowed labels before accepting a checkpoint. Inspect actual `modelUsage`; any unexpected model invalidates the batch pending investigation.

Grade each pair once, shared across all four ablations. Plan 50 batches of 40 pairs. Run contract smokes on unsampled candidates before freezing the main judge prompt. The main sample remains separate from that debugging exercise. Any later judge-rubric change creates a new reference version and requires regrading; do not adjust the rubric after seeing Jev disagreements and retain the old labels. Independently regrade a preselected random 10% of pairs (200 pairs, five more batches) in fresh sessions without revealing the first judgment. Agreement is a consistency check, not proof of truth. Keep disagreement and insufficient-evidence rates visible; do not silently let a majority of machine votes become human ground truth.

During reference generation, strict code-point quote checking rejected otherwise literal excerpts when Opus converted curly quotes to straight quotes. Reference validator v2 permits only that four-character typography mapping and whitespace normalization; words, case, punctuation, and ellipses remain significant. This parser correction does not change the frozen prompt, rubric, labels, batch identities, or Jev input, and occurred before any Jev calls. Apply it uniformly to cached and new references. Reaccepting a previously rejected stored batch requires explicit revalidation of the entire response, recorded with its attempt index, raw-response hash, and validator version; retain the original failure and response. No partial-batch salvage or silent replacement is allowed.

Prepare a human review packet with the preference, summary, source link, and three blank choices: matches / does not match / insufficient evidence. Hide Jev and Opus labels while the user reviews it. Start with 40–60 cases spanning profiles and aggregators, including a random audit plus ambiguous, machine-disagreement, and high-cost false-exclusion candidates. Keep random and targeted audit results separate because their error rates are not comparable. The user can correct their own interest profile before a new experiment version, or label whether the written synthetic rule is supported by a summary.

Keep headline metrics against the original Opus reference. Human labels enter a separate versioned adjudicated sensitivity layer; retain original judgments and distinguish random audit from targeted review. Only the random audit supports an adjusted error estimate; targeted cases are selected across all four variants equally and cannot be used to claim an unbiased improvement in headline metrics. Tell reviewers to judge the supplied summary only; source links are for provenance, not additional evidence. Optional corrections never train a model or silently alter the held-out set. The initial report must say “against Opus reference judgments” until adequate human validation exists. Items whose reference remains uncertain are reported separately, not counted as easy true negatives.

## Metrics and choosing a filter shape

Define the positive class as **relevant to the written preference**:

- Admission precision: relevant items among admitted items.
- Relevance recall: admitted relevant items among all relevant items.
- Exclusion precision: irrelevant items among excluded items; its complement is the harm of hiding useful stories.
- Irrelevant rejection rate: excluded irrelevant items among all irrelevant items.
- Raw three-way confusion, uncertainty, malformed-output, unavailable-call, and repeat-judgment disagreement rates.

Show denominators, not just percentages. For binary quality metrics, keep reference-uncertain pairs outside the denominator and show their count and admission rate separately. Provider failures are not model uncertainty: show conditional model quality on completed responses, completion coverage, and the operational fail-open result including failures. Do not drop failures to inflate quality.

Compare against admitting everything. Report paired differences for the four structures at the fixed initial 0.90 threshold and development-only threshold sweeps of 0.50, 0.70, 0.80, 0.90, 0.95, and 0.98. Select only with complete reference coverage and completed Jev responses for the main comparison. On recent development pairs, require micro relevance recall of at least 95% and at least 95% recall in every profile with 20 or more reference-positive pairs. These are point-estimate research constraints, not lower-confidence-bound guarantees. Among qualifying candidates maximize macro-profile admission precision on completed responses. Exact ties prefer prose, then bullets, structured fields, and checklist, then the higher threshold. If none qualifies, report no qualifying winner. Report exclusion precision, uncertainty intervals, and support counts as additional evidence; do not claim safety from a handful of excluded examples. Partial runs can produce coverage diagnostics but cannot select a winner; unavailable calls are included only in the separately labeled operational fail-open analysis.

Write a hashed selection record bound to the manifest, development labels, and selection-rule version before computing or opening held-out results. The test set supplies the final estimate; do not retune using it. Report micro totals and macro averages by profile and aggregator, along with publisher/topic support where sufficient. Show the 950 recent and 50 stale-science slices separately. Bootstrap confidence intervals and paired differences at the story-group level so four variants and two profiles do not masquerade as independent articles. Sparse cells are descriptive, not ranking evidence.

For the selection objective, a present profile with no admitted items contributes zero precision rather than disappearing from the macro average. Descriptive tables may still show its ratio as undefined alongside its support. This prevents a candidate from improving its selection score merely by admitting nothing in a sparse profile.

The enrichment and quotas change prevalence, so these precision values are benchmark-specific. Do not convert aggregator inventory weights into an alleged real-user feed precision: actual user profile mix and source eligibility are unknown. A later shadow run on naturally arriving News is needed for deployment precision, latency, and volume estimates. The newest-ten feed heuristic is not a comparable classifier on this retrospective stratified set; evaluate it separately through a time-ordered product replay if needed.

A strong result should identify not only a mean winner but failure patterns: negation, explicit exclusions, adjacent topics, missing detail, short summaries, political horse-race distinctions, promotional language, and scientific claims. If structures tie within uncertainty, prefer the simpler representation with lower cost and clearer user editing. Do not automatically rewrite every user's text based on a small aggregate improvement.

## Execution, cost, and reproducibility

Main Jev run: **2,000 pairs × four structures = 8,000 requests**, plus at most 400 total smoke/retry/robustness attempts. At an illustrative 2,000 billed input tokens per call and the documented $0.042/million input rate, the main run is about $0.67. Actual inputs, retries, and tokenization determine the bill. Reserve a conservative request cost before dispatch, meter actual usage, and enforce a **$5 Jev inference stop limit** and **8,400 total-attempt ceiling**. Missing usage or unknown billing must stop further dispatch; account purchase/credit requirements are separate from this inference estimate. No unrequested subscription is authorized by these numbers. [Jev models and prices](https://docs.typesafe.ai/models), [API contract](https://docs.typesafe.ai/api).

For the frozen 8,000 requests, the harness's conservative byte-plus-overhead reservations total $2.1427 before retries. This is an offline reservation calculation, not measured token usage or a provider invoice. Billing assumptions still require confirmation in the account smoke.

Judge route: local Claude Code `claude-opus-5-5`, at most **65 grading invocations**, covering the 50 primary batches, five repeat batches, and bounded smoke/recovery. This consumes the user's Claude allowance; do not describe it as costless. No paid judge API fallback. Review consultations are separate and reported as such.

Start with an unsampled two-item/four-pair Jev contract smoke across all four structures (16 calls) and one Opus grading invocation. Reserve those budgets in separate smoke checkpoints, reserve $0.01 for its 16 Jev attempts, then limit the main run to $4.99, 8,384 Jev attempts, and 64 judge invocations, keeping aggregate ceilings at $5, 8,400, and 65. The two smoke items do not count toward the 1,000-item benchmark. Verify schema, exact model routing, usage, persistence, and resume behavior before bulk work. Keep concurrency bounded (initially four Jev calls, at most two judge CLI calls). Respect provider rate limits and bound retries; every retry consumes the attempt and cost budgets. A checkpoint resumes only if model, source, intent, structure, prompt/schema, option order, and hashes match. Preserve raw responses without credentials and append-only usage/attempt records. Interrupted or timed-out calls retain their budget reservation unless billing can be reconciled.

Store the API key server-side in a local ignored secret file or existing secret storage; never include it in committed fixtures, reports, logs, screenshots, or command-line arguments. Browser signup/account configuration is distinct from activating the production integration. Only public corpus summaries and the synthetic or explicitly supplied preference text go to Jev. No production state is mutated by the experiment.

## Deliverables and validation

Deliver the frozen corpus manifest, 1,000-item sample, 2,000-pair manifest, four preference renderings, reference labels, human review packet, Jev checkpointed responses, metric tables and paired intervals, and a report stating what actually ran. Keep raw production-derived artifacts ignored; commit-worthy artifacts are the method, harness, synthetic profile bank, tests, and aggregate report.

Test deterministic sampling/pairing, equivalent clause preservation, correct metric denominators, uncertainty/failure treatment, held-out selection discipline, mismatched checkpoint rejection, malformed provider/judge responses, concurrency-safe spend limits, retry accounting, and secret redaction. Run the isolated Python package's Ruff, MyPy, and focused pytest checks. Obtain the repository's independent Opus review for the consequential experiment design and substantial harness before handoff. Record verified findings and limitations, including any account/verification block that prevents actual Jev calls. Do not report a planned run as completed.

## Independent design review

A read-only `claude-opus-5-5` review completed with only that model in returned usage. Incorporated findings: separate inference/provenance hashes; climate affinity and within-aggregator controls; explicit missing-evidence semantics for conditional exclusions; blinded, separated judge batches and decisive-clause labels; unsampled smoke cases; frozen selection rules; method hashes; and separation of targeted human corrections from headline metrics. The reviewer identified potential same-event paraphrases across splits; lexical/production deduplication does not guarantee semantic event-disjointness, and bootstrap intervals may understate uncertainty from related-event coverage.

Implementation review and a follow-up also used only `claude-opus-5-5`. Verified fixes include append-only Jev cost accounting, explicit per-run caps, sanitized subscription-judge environment, auditable retry recovery, hidden held-out metrics during partial runs, and a defined selection penalty for profiles with no admissions. The follow-up independently checked the typography correction against the raw attempts: only the promoted response passes v2; earlier invented-ellipsis responses still fail. All 520 references accepted before that correction passed the original strict validator. No Jev output was available when the parser correction was made. Smoke and main caps remain separately enforced and must retain their documented aggregate allocation.

## OpenRouter protocol observations

The selected route returns three choice probabilities rounded to two decimals. After main attempt 30, response validator `choice-cent-rounding-v2` accepts totals 0.99 or 1.01 only on that cent grid, without normalizing them. The original failure and exact response remain in the append-only journal, with an explicit revalidation event.

After main attempt 121, the known-billed Choice mismatch policy permits up to the existing three total attempts per job when `choice` contradicts the highest probability. Model, provider, generation identity, remaining schema, usage, and reservation must all validate first. The malformed attempt is never a reference or successful model decision. Its raw response, cost and failure count remain available; failures after the retry limit prevent full-run winner selection. Report first-attempt fail-open sensitivity and retry frequency alongside final successful-response metrics so retry-conditioned quality is visible. This operational correction occurred before inspecting any quality metrics; frozen requests, references, thresholds and selection rule are unchanged. The vendor [Choice documentation](https://docs.typesafe.ai/primitives/choice) specifies a highest-probability choice.
