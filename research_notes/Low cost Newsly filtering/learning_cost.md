# Learning preferences and measuring filtering cost

## What feedback and evaluation infrastructure does Newsly already have?

### Takeaway
Newsly has useful behavioral clues and an existing offline evaluation home, but the current typed API does not express item-level negative preferences or exposure probabilities. A small explicit preference dataset is the first missing ingredient; reads alone are not a reliable training target.

### Cited Findings
- The typed `ContentInteractionType` contains only `Opened`; interaction requests include content ID, timestamp, surface and arbitrary context. The DB persists these idempotently in `analytics_interactions`. The iOS detail view sends an opened interaction with content type and `was_read_when_loaded`. This is real item-linked analytics, so it would be incorrect to say there is no feedback infrastructure. [Contract](/Users/willem/Development/news_app/rust/crates/newsly-contracts/src/mutations.rs:38), [Persistence](/Users/willem/Development/news_app/rust/crates/newsly-db/src/interactions.rs:64), [Client](/Users/willem/Development/news_app/client/newsly/newsly/ViewModels/ContentDetailViewModel.swift:421).
- `user_feedback` is a general message and app/device context form, not an item preference annotation. [Feedback fields](/Users/willem/Development/news_app/rust/crates/newsly-db/src/interactions.rs:7).
- Knowledge saves are `(user, content)` state with timestamps. Removing a save deletes it. Content reads are upserted state; marking unread deletes the read row. These tables are not complete historical behavioral event logs. [Read/save implementations](/Users/willem/Development/news_app/rust/crates/newsly-db/src/content_actions.rs:16).
- Briefing marks source keys read after its visual tracker says a segment was seen; it also supports marking an entire lens read. A read acknowledgement therefore does not prove approval, careful reading, or independent opening of each underlying source. [View-model read behavior](/Users/willem/Development/news_app/client/newsly/newsly/ViewModels/BriefingViewModel.swift:306), [Viewport tracker integration](/Users/willem/Development/news_app/client/newsly/newsly/Views/Briefing/BriefingLensContentViews.swift:489).
- `python/evals` already owns versioned offline datasets and local/hosted encoder experiments, while Rust exports source rows and owns production policy. The cached-vector replay can run without embedding or model-judge requests, but its current datasets/metrics concern relation/category grouping, not personal relevance. Current backfilled vectors also do not reconstruct historical deployed state. [Eval README](/Users/willem/Development/news_app/python/evals/README.md:1).
- The existing pipeline harness evaluates summarization and Briefing publication/synthesis with seeded category membership and fixed-vector embedding mocks. It explicitly does not evaluate embedding quality or URL extraction/transcription. Its candidate and judge calls are real, and the documentation states dollar cost is unavailable: do not repurpose it as a supposedly free preference benchmark. [Pipeline evaluation boundaries](/Users/willem/Development/news_app/python/evals/PIPELINE_EVALS.md:1).
- Production short-form embeddings currently use a hosted adapter; local SentenceTransformers and experimental rerankers are intentionally offline. [Architecture](/Users/willem/Development/news_app/docs/architecture.md:404).

### Inferences
- Treat saves as possible positive seed examples and opens as weak interest clues. Keep all unexposed/unread content unlabeled, rather than turning it into negative training examples. Even an open can express curiosity followed by disappointment.
- The first learning model can be a logistic/linear head trained offline over existing eligible cached embeddings plus deliberately chosen simple features. That yields local decision inference without requiring a generative model server. Existing summary-derived vectors are suitable only for downstream ranking; an admission model needs features available before processing.
- A future append-only item preference stream should distinguish explicit user intent from reading state: worth attention, less like this, already knew, repetitive, low quality, not now. Exposure should record slate ID, candidate ID, displayed rank, policy/model version and any randomized selection probability. This is a proposed extension, not an existing facility.

### Gaps
- No live user rows, production counts or current spend were inspected. Existing label counts, signal coverage and distributions are unknown.
- No current hardware capacity was verified. Repository references to local experiments are not evidence that a production CPU/GPU has spare memory or throughput.

## What is the cheapest useful experiment, and how should it be evaluated?

### Takeaway
Start with a manageable personal labeling pilot, compare two daily slates, and test two separate decisions: which raw candidates deserve processing and which processed stories deserve attention. Early success should mean visibly better selection with preserved useful-story recall, not merely a high classifier accuracy number.

### Cited Findings
- Chronological validation prevents training on future data and evaluating on the past. Scikit-learn's `TimeSeriesSplit` assumes equally spaced samples for comparable fold durations, so irregular news arrivals warrant explicit calendar windows instead of blindly splitting equal row counts. [TimeSeriesSplit documentation](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html).
- Grouped validation prevents overlapping groups between training and testing. Event/syndication groups are the relevant news leakage unit; standard GroupKFold alone does not enforce time order. [GroupKFold documentation](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.GroupKFold.html).
- Choosing a probability threshold is a separate decision from fitting the score model. Scikit-learn explicitly warns against training and threshold tuning on the same data. [Decision threshold guide](https://scikit-learn.org/stable/modules/classification_threshold.html).
- NDCG discounts lower positions and normalizes against the ideal ranking; it accepts graded relevance and a top-k cutoff. [NDCG documentation](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.ndcg_score.html).
- Vowpal Wabbit's news-personalization example samples an action from a probability distribution and carries the chosen action's probability into learning. This is useful evidence for logging randomized exploration correctly; the tutorial is a simulation, not proof it would outperform a simple Newsly ranker. [Official contextual-bandit tutorial](https://vowpalwabbit.org/docs/vowpal_wabbit/python/latest/tutorials/python_Simulating_a_news_personalization_scenario_using_Contextual_Bandits.html).

### Inferences
Proposed first experiment, with sample sizes and timings explicitly heuristic:

1. Assemble about **200–300 examples**, covering several days, sources, topics, content lengths, and near-duplicate events. Include 100–150 raw title/snippet cards and 100–150 already-processed story cards. These sizes start a learning curve; they are not a claim of statistical sufficiency or expected accuracy. If possible, include raw candidates beyond the current per-source cap through a metadata-only prospective collection; previously discarded candidates cannot be reconstructed from admitted rows alone.
2. For raw candidates ask: **worth processing / maybe or insufficient information / skip**. For processed cards ask: **worth my attention today / maybe or later / skip**. Optional reasons: wrong topic, shallow/repetitive, already knew, weak evidence, too promotional, timing, too long. Preserve “not enough information” separately from a negative. A title-only model cannot learn content quality that is invisible in its input.
3. Begin with 30 cards to refine label meanings; then label in several short sessions. An illustrative 15 seconds per card is 50–75 minutes for 200–300 cards, excluding reading full stories. Actual effort should be measured after the first 30. Keep the interaction to one decision plus an optional reason, with a full-text reveal recorded separately.
4. Reserve approximately 20% as untouched future/calendar holdout and use an intermediate validation window for thresholds. Keep the same event, canonical article and syndications together. For groups crossing a boundary, purge conflicting training rows or omit the group from that evaluation; do not move future observations into earlier training. Fit preprocessing, preference features, and calibration only from each training window. If the data spans too few days or contains too few positive events, collect more before any hard filtering.
5. Compare recency/source-cap baseline, simple diversity/duplicate rules, a frozen-embedding linear model, and only then a trained encoder or local decision LLM challenger. Evaluate models on identical examples and stage-appropriate inputs. The candidate set at raw admission and processed ranking is different and must remain explicit.
6. Show two anonymized daily slates side-by-side at the same reading budget, with randomized left/right ordering. Ask which is better and which omitted story should have made it. Keep this qualitative check even if offline metrics improve: the true objective is a useful day’s reading, not clicks.
7. For ranking, report useful stories in top 10, NDCG@10, duplicate-event count, source/topic concentration, and the worthwhile items omitted. For admission, report recall of worthwhile candidates, fraction of candidates avoided, uncertainty/abstention rate, and false-negative examples. Assess recall per source/content type where counts allow. Accuracy is misleading when most candidates are uninteresting.
8. Use high-recall admission: only clearly low-value candidates become reject/defer candidates; ambiguous or unseen-source candidates pass through initially. A desired recall such as 95% is a product target, not a justified claim after a few hundred labels. Report numerator/denominator and uncertainty, and inspect every missed high-value example during the pilot.
9. Run in shadow mode, then enable ranking first. Reserve, for example, one of ten slots for diverse/uncertain exploration and regularly sample rejected raw candidates for human audit. The 10% quota is a tunable starting assumption, not a research-prescribed optimum. Record probability if randomized; deterministic quotas do not magically create unbiased logged-policy evaluation. Avoid a full contextual-bandit stack before explicit labels and a useful baseline exist.

### Gaps
- User-specific meanings of important, interesting, actionable and repetitive need elicitation through the first small batch. A generic LLM's taste cannot substitute for the user's labels.
- Existing admitted-only datasets cannot establish recall over the original discovery universe. Evidence of saved/read items is affected by the present selection policy.
- There are no measured Newsly personal-relevance results yet. The proposed experiment would discover whether the local model is good enough.

## When does filtering actually save money, and when does self-hosting break even?

### Takeaway
Changing display order can improve the product while saving no upstream provider cost. A raw admission decision can save money only when it precedes avoidable processing, and when the avoided work was not still needed for another user or shared product path.

### Cited Findings
- Current processing reuses durable/exact-representative summaries before paid work and checkpoints summaries to avoid repeated paid work on relation retries. Therefore fetched item count is not paid-model-call count, and naive cost estimates based on “ten items per feed” will overstate some savings. [Processing architecture](/Users/willem/Development/news_app/docs/architecture.md:410).
- Current eval tooling can reuse cached vectors without new model calls, making an offline baseline feasible without a provider benchmark bill. This does not mean generating new raw-candidate embeddings is free. [Cached-vector replay](/Users/willem/Development/news_app/python/evals/README.md:48).

### Inferences
Use a measured stage ledger rather than assume per-item cost: discovered candidates → already-seen/cache-hit → admitted unique candidates → extraction/transcription → summary → embedding → Briefing composition. Record actual metered calls/tokens and failures/retries at each stage, stratified by article/podcast/news. Count avoidable processing over the union of interested users, not independently per-user rejections of shared content.

Let `N` be new unique monthly candidates, `r` the fraction of otherwise-necessary processing actually avoided, `c` the measured avoidable downstream dollars per such item, `f` the incremental filtering dollars per candidate, and `H` incremental monthly hosting/operations cost. **Net monthly saving = N × r × c − N × f − H**. Fixed development effort and user labeling time should be accounted for separately. If `r × c <= f`, scale will not rescue the model's variable economics.

Illustrative scenarios only, not current Newsly rates or volume:

| Unique candidates/month | Actual avoidable fraction | Avoided cost/item | Gross avoided processing | After $30/month incremental host, assuming negligible inference variable cost |
|---:|---:|---:|---:|---:|
| 30,000 | 50% | $0.001 | $15 | −$15 |
| 30,000 | 50% | $0.01 | $150 | $120 |
| 300,000 | 50% | $0.001 | $150 | $120 |

These are arithmetic sensitivities. At `c=$0.001`, `r=0.5`, `H=$30`, and negligible `f`, breakeven is 60,000 unique candidates/month; at `c=$0.01` it is 6,000. If a cloud or local decision adds nontrivial cost, substitute it into the denominator `N_break_even = H / (r*c-f)`. This compares an upstream filter against no filter. To compare self-hosted filtering against hosted filtering, use `N × (hosted_filter_cost − local_variable_cost) > incremental_local_host_cost`, plus quality/latency differences.

For replacing a ten-item cap with selection from a larger pool, the sensible first target may be **better selections at the same processing budget**, not lower spend. Processing a larger pool to obtain embeddings and then filtering can increase cost. A downstream classifier using already-produced embeddings can have tiny marginal decision work, but the embeddings and summaries remain part of total system cost.

The first experiment should require no new server or always-on local LLM: offline labels, cached eligible vectors, and a CPU-sized learned head. If an encoder or LLM later wins materially, measure wall time, peak memory, cold start and throughput on the intended host before choosing serving infrastructure. Training locally and serving remotely are independent deployment choices.

### Gaps
- All monetary values above are explicit hypothetical inputs; no current provider bill, candidate volume, machine capacity, or actual avoidable-cost estimate was gathered.
- Low raw-item relevance does not necessarily mean all its processing is avoidable, particularly with shared content and different user preferences.
- Model quality, maintenance effort and labeling cost may dominate an inexpensive API bill. There is not enough live evidence to justify hardware spending or promise a savings percentage.
