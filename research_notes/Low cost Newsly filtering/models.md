# Low-cost local models for Newsly filtering

Research checked 2026-10-16. This is a deployment shortlist and experiment proposal, not measured Newsly model performance. No weights were downloaded, training run, or paid inference performed.

## Which model should we try first?

### Takeaway
Prioritize three candidates: a learned linear scorer over existing embeddings; the Rust-servable 149M ModernBERT reranker; and Qwen3.5-2B only as an optional later challenger. Separately test a small local encoder on feed metadata for inexpensive admission; keep that stage conservative because a headline cannot establish article quality. The other models below document alternatives and tradeoffs rather than expanding the initial benchmark.

### Cited Findings

- Repository verification: the default existing embedding model is hosted `openrouter:qwen/qwen3-embedding-8b`; source text is title + summary + key points. Cache lookup requires news ID, model, exact input hash, encoder version and valid dimensions; source selection accepts ready representative news only. Dimensions are inferred and checked from responses, not fixed in this code. [Default model](/Users/willem/Development/news_app/rust/crates/newsly-domain/src/lib.rs:38), [Embedding text](/Users/willem/Development/news_app/rust/crates/newsly-db/src/briefing_refresh.rs:129), [Cache and source](/Users/willem/Development/news_app/rust/crates/newsly-db/src/news_lens_embeddings.rs:21), [Dimension checks](/Users/willem/Development/news_app/rust/crates/newsly-worker/src/briefing_refresh/warm_news.rs:148)

| Candidate | Verified properties | Appropriate experiment |
|---|---|---|
| Frozen embeddings + logistic regression | Scikit-learn provides linear classifiers with logistic loss and incremental `partial_fit`; production inference needs only learned coefficients and preprocessing. [SGDClassifier](https://scikit-learn.org/stable/modules/generated/sklearn.linear_model.SGDClassifier.html) | Personal value score from explicit user labels; reuse Newsly embeddings if their text/model provenance is suitable. |
| `sentence-transformers/all-MiniLM-L6-v2` + linear head | 22.7M parameters, 384 dimensions, Apache-2.0; short-paragraph encoder with default truncation at 256 word pieces. [Model card](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2) | Cheap metadata encoder, or separate title + short-summary encoder. Do not silently feed entire articles into this default. |
| SetFit on a small Sentence Transformer | Trains a Sentence Transformer body and classifier head; supports sklearn or differentiable heads. The frequently cited eight-examples-per-class result is from a sentiment dataset, not personalized news. [Project](https://github.com/huggingface/setfit) | Fine-tune only after a frozen-embedding baseline, using the same held-out labels. |
| `cross-encoder/ms-marco-MiniLM-L6-v2` | 22.7M parameters and Apache-2.0; trained for search query/passage relevance. Published throughput was measured on V100, so it is not a Mac/CPU latency estimate. [Model card](https://huggingface.co/cross-encoder/ms-marco-MiniLM-L6-v2) | Smallest reranker control; less naturally suited to a long preference rubric. |
| `Alibaba-NLP/gte-reranker-modernbert-base` | 149M parameters, English, 8,192-token maximum, Apache-2.0. [Model card](https://huggingface.co/Alibaba-NLP/gte-reranker-modernbert-base) | Conventional cross-encoder challenger with a simpler documented serving path. |
| `Qwen/Qwen3-Reranker-0.6B` | 0.6B parameters, 32K context, Apache-2.0, multilingual and instruction-aware. Its reference implementation normalizes final-token `yes`/`no` scores. [Model card](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B) | Profile/rubric → article relevance challenger, preferably limited to a shortlist. |
| `Qwen/Qwen3.5-0.8B` / `Qwen/Qwen3.5-2B` | Current official Apache-2.0 models exist; 0.8B is explicitly positioned for prototyping/task-specific fine-tuning. The 2B card supports text-only input and warns about thinking loops. [0.8B card](https://huggingface.co/Qwen/Qwen3.5-0.8B), [2B card](https://huggingface.co/Qwen/Qwen3.5-2B) | Non-thinking, constrained-label decision model; try prompted behavior first and LoRA only after labels accumulate. |

- Cross-encoders process each query/document pair, whereas reusable embeddings process each text once; this is why rerankers are commonly applied to a shortlist. Their search-relevance performance does not establish personal-value performance. [Sentence Transformers usage](https://www.sbert.net/docs/cross_encoder/usage/usage.html)
- A score between zero and one is not automatically calibrated confidence. Calibration must be checked on held-out data. [Scikit-learn calibration](https://scikit-learn.org/stable/modules/calibration.html)

### Inferences

- Recommended experiment order: (1) explicit interests + deterministic diversity/recency policy, (2) frozen embedding logistic scorer, (3) SetFit or ModernBERT, (4) Qwen 0.6B reranker, (5) Qwen3.5-2B prompted then fine-tuned. Run only enough challengers to answer a measured failure of the cheaper baseline.
- Two jobs need different inputs and loss priorities. Before extraction/summarization, use title, feed description, publisher, tags, date and missing-field indicators to decide whether processing is worth its cost. After processing, use summary/content embeddings and feedback to rank value. High recall matters more at admission; precision at the top of the delivered list matters more later.
- Metadata models should produce `admit`, `defer`, or `insufficient evidence`, with conservative thresholds and a small random audit stream. A confident topic match on a title does not prove quality or novelty. Save deferred candidates so they can be inspected/reconsidered.
- Separate topic relevance (matches interests), quality (evidence/depth/source practices), personal value (worth this user's attention), and novelty (adds something beyond recent coverage). User labels can teach personal value; semantic matching alone cannot. Novelty requires comparison to recent items/history, not a one-item classifier.
- A linear model is still a trainable decision model. It provides a sensible first answer to the user's desire to train something locally, without adopting generative decoding and a model server immediately.
- Reusing current embeddings means **local decision head with hosted embeddings**, not a fully local system. Already cached vectors avoid another embedding call, but newly processed items retain hosted embedding cost. Fully local admission requires a local encoder over metadata; replacing the existing rich embedding service is a separate evaluated change. The existing ready-item embeddings arrive too late to save extraction/summarization costs.

### Gaps

- No evidence establishes which model best predicts this user's preferences. Public retrieval or sentiment results are not a substitute for Newsly labels.
- The models above are a practical small-model shortlist, not a claim to enumerate every model released by the research date.
- Runtime configuration may override the repository default embedding model, and actual stored embedding coverage/dimensions were not queried from production. The verified code establishes the intended input and cache boundary.

## How much hardware and operational cost would this require?

### Takeaway
The linear model can run inside Rust on the existing server. Small encoders are plausible CPU workloads; 0.6B–2B models should be benchmarked locally before buying GPU hosting. Use the Mac for experiments and training, while keeping always-on product decisions on server infrastructure.

### Cited Findings

- `ort` is a Rust binding for ONNX Runtime; SetFit's ONNX tutorial demonstrates exported encoder inference followed by the classifier head. This is an available deployment building block, not proof any arbitrary export works unchanged. [Rust ort](https://docs.rs/ort/latest/ort/), [SetFit export tutorial](https://huggingface.co/docs/setfit/main/en/tutorials/onnx)
- Hugging Face Text Embeddings Inference lists ModernBERT reranking support and has a Rust implementation. Its documented Qwen3 embedding support must not be mistaken for documented Qwen3 reranker support. [TEI repository](https://github.com/huggingface/text-embeddings-inference)
- MLX LM provides Apple Silicon inference and low-rank/quantized fine-tuning. Current source includes Qwen3.5 model code; this is not validation of the exact conversion/training combination we would select. [MLX LM](https://github.com/ml-explore/mlx-lm), [Qwen3.5 implementation](https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/models/qwen3_5.py)
- llama.cpp provides server inference, constrained JSON output, and reranking endpoints. A 2026 issue documents wrong near-zero scores with certain Qwen-style GGUF rerankers missing the expected classification handling. Pin model conversion and server versions and compare reference outputs; generic endpoint availability is insufficient. [Server documentation](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md), [Issue 25447](https://github.com/ggml-org/llama.cpp/issues/25447)

### Inferences

The following are arithmetic planning estimates from parameter counts, not measured RAM or throughput. Decimal MB/GB; actual artifacts may contain extra tensors, quantization metadata, and unquantized layers. Runtime activations, context/cache, batch size, tokenizer and OS all add memory.

| Model scale | Weight arithmetic | Sensible initial test machine, not a minimum guarantee |
|---|---|---|
| Linear head | `(dimensions + 1) × 4 bytes` per binary head; 1,536 dimensions is about 6 KB | Existing Rust CPU process |
| 22.7M encoder | ~91 MB FP32; ~23 MB ideal INT8 weights | Existing CPU VPS, bounded batches; any modern development Mac |
| 149M reranker | ~596 MB FP32 or ~298 MB FP16 | CPU VPS with several GB free; test throughput with short summaries first |
| 0.6B reranker | ~1.2 GB FP16; ~0.3 GB ideal 4-bit weights | CPU batch processing or Apple Silicon; allow several GB beyond weights |
| 0.8B / 2B generative | ~1.6 / 4 GB FP16; ~0.4 / 1 GB ideal 4-bit weights | 16 GB Apple Silicon is a reasonable small-batch experimental target; training needs more headroom and must be measured |

- Local inference removes provider charges per decision, not compute, electricity, or maintenance cost. If existing server CPU headroom suffices, marginal cash cost may be small; a dedicated always-on GPU should not be the default.
- Keep title/summary inputs short and bounded (for example 256–512 tokens for the first benchmark), batch during ingestion, and cache by item representation hash + model version + preference version. A 32K maximum is not a reason to process 32K per item.
- For N items, per-item latency T milliseconds implies approximately `N × T / 1000` serial seconds. Example sensitivity only: 1,000 items at 10/100/1,000 ms cost 10/100/1,000 seconds. These are not predictions for any model.
- Export linear coefficients, intercept, scaling, feature order, embedding version and label schema into a small versioned artifact; implement its multiply-and-sigmoid in Rust. Use Python only in offline evaluation/training, with Rust parity tests on fixed vectors.
- For neural encoders, prefer ONNX inside a Rust adapter or a supported Rust TEI sidecar. For generative models, a C++ llama.cpp sidecar can remain a pure inference dependency called by Rust. Rust retains task, user state, policy, persistence and lease ownership. Do not introduce a Python production application/queue service.
- Mac-hosted experiments are useful; a sleeping/offline laptop should not become an availability dependency for an always-on server. MLX can train locally, then a validated exported model can serve elsewhere.

### Gaps

- No machine inventory or benchmark was run here. Exact VPS free RAM/CPU, Mac chip/memory, arrival volume, input lengths, p95 latency and queue capacity remain measurements to obtain.
- Qwen3.5 LoRA conversion to a chosen C++ runtime needs a real parity test. MLX's LoRA documentation explicitly limits its direct GGUF export path; do not promise one-command portability. [MLX LoRA documentation](https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/LORA.md)
- Dollar hosting estimates would require an actual capacity decision and current provider quotes. No dollar amount is asserted.

## What training and evaluation makes this useful rather than merely cheap?

### Takeaway
Invest first in a small, explicit, representative preference dataset and a replay evaluation. Train the cheapest model that wins on future-time items while retaining valuable stories; fine-tuning a generative model is a later option, not a prerequisite.

### Cited Findings

- SetFit is explicitly designed for few-shot sentence-transformer adaptation, but its own synthetic-data tutorial notes that benefits depend strongly on the dataset. [SetFit overview](https://huggingface.co/docs/setfit/main/index), [Synthetic-data tutorial](https://github.com/huggingface/setfit/blob/main/docs/source/en/tutorials/zero_shot.mdx)
- Cross-encoder training supports encoder and sequence-classification checkpoints and task-specific heads. [Training overview](https://sbert.net/docs/cross_encoder/training_overview.html)
- MLX LoRA supports local train/validation/test JSONL and completion-only loss via prompt masking. Its built-in language-model loss evaluation is not a personalized news ranking metric. [MLX LoRA guide](https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/LORA.md)

### Inferences

- Proposed labeling budget, not a scientific sufficiency claim: begin with 150–300 varied items spanning topics/sources, useful versus repetitive stories, and both obvious and ambiguous cases. Reserve a future-time batch before tuning. Then add 10–20 deliberate labels per day, oversampling disagreements while retaining randomly sampled items.
- Ask `worth reading`, `not for me`, `already covered`, and `uncertain`; allow a lightweight reason. Do not collapse `unread` into `disliked`. A click can be curiosity, accidental interaction, or dissatisfaction. Explicit labels should lead the first model.
- With few labels, fit a regularized linear head and compare to a centroid/cosine-interest baseline. Use learning curves at increasing label counts instead of announcing that a fixed number guarantees success. SetFit can be tested on the same labels; its encoder changes require separate embeddings/model versioning.
- A larger LoRA experiment becomes reasonable after hundreds to low thousands of consistent examples, if cheap models make important contextual mistakes. This is a planning range only. Train on compact decisions/reasons, not long synthetic essays, and retain human-labeled test data even if a stronger model supplies training suggestions.
- Evaluate admission recall of `worth reading` items and processing cost avoided separately from final ranking Precision@K/NDCG, duplicate exposure and topic/source diversity. Inspect missed valuable items explicitly, not only average metrics.
- Split by time and group duplicate stories across splits; otherwise syndication and repeated events can make the experiment appear effective without learning preferences. Report source/topic slices, insufficient-metadata cases, and model disagreements.
- Compare the same candidate snapshots and input visibility for each method. A rich-summary classifier should not be credited with pre-extraction savings; a metadata model cannot be given future summaries at test time.
- Launch in shadow mode before hiding stories. A safe initial product behavior is ranking with a visible all-items view and clear user corrections. Gradual cost-saving admission requires a separately demonstrated high-recall result and an audit sample of deferred items.
- For a generative challenger, bound output to a small enum/schema, disable thinking, cap tokens and validate output. Generation failures should produce insufficient evidence, not a discard. Do not use self-reported model confidence as calibrated certainty.

### Gaps

- Required label count, achievable precision/recall, training wall time and recurring retraining frequency are empirical unknowns for this user.
- Relevance and quality can disagree. Without a stated user preference for diversity, source quality, novelty and time budget, no model can uniquely determine a single correct ranking.
