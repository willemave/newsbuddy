# News Shortform Eval Corpus

This directory stores frozen JSONL slices exported from a local copy of production-shaped data.

Expected slices:

- `exact_duplicates.jsonl`
- `mixed_source_windows.jsonl`
- `user_scoped_x_windows.jsonl`
- `gold_reviewed.jsonl` (optional manual review labels)

Each line is a standalone JSON object describing one legacy news row, enough to rebuild a `NewsItem`-shaped eval sample without hitting the live database again.
