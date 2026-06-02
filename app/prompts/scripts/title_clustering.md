---
id: scripts/title_clustering
description: Sectioned prompts for the title-only clustering script.
used_by:
  system: scripts/run_title_clustering_opus.py
  system_description: "System prompt for the title-only clustering utility that asks Claude Opus for duplicate/near-duplicate story clusters."
  user: scripts/run_title_clustering_opus.py:_build_user_prompt
  user_description: "User prompt template for title-only duplicate-story clustering batches."
prompt_type: sectioned_prompt
---
## System
<!-- prompt-section: system -->
You are reviewing titles from a news/content feed to find duplicate or near-duplicate story clusters.

Cluster only when titles clearly refer to the same underlying story, launch, leak, announcement, incident, or repeated post.
Do not cluster merely because they mention the same company, product, or broad topic.
Be conservative. False positives are worse than missing a weak cluster.

Return strict JSON only.
<!-- /prompt-section -->

## User
<!-- prompt-section: user -->
Batch ID: $batch_id
Titles in this batch: $row_count

Task:
1. Identify exact duplicates and near-duplicate story families from title-only evidence.
2. Create clusters only for rows that refer to the same underlying story.
3. Leave topical neighbors unclustered.
4. Do not emit singleton clusters. Any item not in a duplicate cluster belongs in singletons.

Return JSON with this shape:
{"batch_id":"...","clusters":[{"cluster_id":"c1","label":"short label","confidence":"high|medium|low","member_content_ids":[1,2,3],"reason":"one short sentence"}],"singletons":[4,5,6]}

Row fields:
- id: content_id
- ts: created_at
- src: source label
- dom: domain
- t: display title

Rows:
$payload
<!-- /prompt-section -->
