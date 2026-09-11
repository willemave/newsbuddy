# News Shortform Eval Corpus

This directory stores frozen JSONL slices exported from a local copy of production-shaped data.

Expected slices:

- `exact_duplicates.jsonl`
- `mixed_source_windows.jsonl`
- `user_scoped_x_windows.jsonl`
- `gold_reviewed.jsonl` (optional manual review labels)

Each line is a standalone JSON object describing one legacy news row, enough to rebuild a `NewsItem`-shaped eval sample without hitting the live database again.

Within one user/window case, byte-identical normalized titles are treated as
reposts of the same event even when synthetic fixture URLs differ. This mirrors
the production policy's content-based merge behavior. Distinct titles continue
to use the reviewed `gold_cluster_id`; user isolation is represented by the case
boundary.
