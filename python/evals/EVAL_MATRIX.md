# Maintained eval matrix

Print and review this matrix before a bulk run. Hosted candidates and judges make
paid calls; hosted Qwen and complete production-source bodies require separate
approval.

| Suite | Command entrypoint | Maintained cases | Candidate / judge | Maximum top-level calls | Privacy | Output |
| --- | --- | ---: | --- | ---: | --- | --- |
| Chat | `newsly-evals chat run python/evals/datasets/chat/podcast_history.yaml` | 12 | Sol / Sol | Runtime limits recorded in `run.json` | Synthetic | `test-results/.../chat` |
| Synthetic summary + Briefing | `newsly-evals pipeline run contracts/testing/pipeline/summarization.yaml` | 14 | Luna / Sol | Summary: 3 candidate requests; Briefing: 12 per window; 1 judge per case | Synthetic | `test-results/.../pipeline-synthetic` |
| Public frozen Briefing | pipeline runner with `contracts/testing/pipeline/tech_news_production.yaml` | 4 | Luna / Sol | Briefing: 12 candidate requests per window; 1 judge per case | Frozen public summaries | `test-results/.../pipeline-public` |
| Full production-source summary | pipeline runner with the approved ignored suite | 4 | Luna / Sol | 3 candidate requests and 1 judge per case | Complete source bodies; explicit approval required | `test-results/.../pipeline-full-source` |
| Curated relations | `python/evals/scripts/compare_news_embedding_models.py` | 101 | MiniLM and hosted Qwen | One embedding per unique Rust-canonical string; exact count emitted before hosted execution | Curated titles; OpenRouter approval required | `test-results/.../relations-curated` |
| Frozen feed relations | `python/evals/scripts/run_news_eval.py` | Dataset-derived | MiniLM diagnostic | One embedding per unique Rust-canonical string | Frozen metadata | `test-results/.../relations-feed` |

Relation thresholds default to the values emitted by `newsly-eval-driver`.
CLI overrides must specify primary and secondary together. Empty configured
datasets are errors, and `exact_duplicates` remains outside the default run until
it has a non-empty reviewed fixture.
