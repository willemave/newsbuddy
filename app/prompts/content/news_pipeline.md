---
id: content/news_pipeline
description: Sectioned prompts for news processing and Fast Read reranking.
used_by:
  processing_user: app/services/news_processing.py:_build_processing_prompt
  processing_user_description: "Opening instruction for short-form news processing prompts assembled from article, aggregator, and discussion evidence."
  reranker_system: app/services/news_reranker.py
  reranker_system_description: "System prompt embedded in the Qwen reranker chat prefix for title-aware news relation matching."
prompt_type: sectioned_prompt
---
## Processing User
<!-- prompt-section: processing_user -->
Create a compact short-form news summary grounded only in this evidence.
<!-- /prompt-section -->

## Reranker System
<!-- prompt-section: reranker_system -->
Judge whether the Document meets the requirements based on the Query and the Instruct provided. Note that the answer can only be "yes" or "no".
<!-- /prompt-section -->
