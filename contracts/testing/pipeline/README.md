# Shared pipeline fixtures

`summarization.yaml` describes synthetic initial state and output expectations for
local summarization/Briefing evaluations. It is consumed by both the Python runner
and opt-in Rust API integration checks. No production data or generated candidate
answers belong here.

See `python/evals/PIPELINE_EVALS.md` for stage boundaries, SQL generation, mocks,
model limits, local commands and reports. Lens keys define input source membership;
expectations are judge-only and are never inserted as generated output.

`tech_news_production.yaml` is a separate frozen snapshot of eight public production
tech-news summaries and key-point arrays in four conciseness cases. See
`tech_news_provenance.json` for source IDs, public URLs, snapshot date and scope.
These are production-derived inputs, not independently verified article text or
expected generated answers. URL query strings are removed; local runs do not fetch
source pages. No account data or production lens membership is included.
