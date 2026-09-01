# Hacker News Integration

Newsly treats Hacker News ingestion and discussion refresh as related but
separate durable workflows. Rust owns both provider boundaries and all
persistence, queueing, retry, and publication state.

## Top-story ingestion

The Hacker News aggregator:

1. Fetches `topstories.json` from the Firebase API.
2. Takes the first 15 IDs and fetches their item metadata with at most eight
   requests in flight.
3. Accepts only items whose Firebase type is `story` and whose external article
   URL normalizes to HTTP or HTTPS. Text-only and job items are skipped.
4. Creates a News candidate with distinct article and discussion identities.

The stored provider metadata includes the item ID, title, author, score,
declared comment count, item type, timestamp, linked article URL, source domain,
and canonical discussion URL:

```text
https://news.ycombinator.com/item?id=<item_id>
```

Successful items remain in the batch when another item request fails. The
provider outcome records those item-scoped errors for the owning scrape task.

## Discussion refresh

`fetch_news_item_discussion` is the durable task for refreshing a stored HN
discussion. It:

1. Resolves the positive numeric item ID from the stored external ID or
   discussion URL.
2. Uses Firebase metadata to reject missing, dead, or deleted discussions and to
   capture authoritative thread fields.
3. Uses the Algolia item endpoint for the nested comment tree.
4. Flattens comments while preserving parent ID, depth, author, timestamp, text,
   and per-comment HN URL.
5. Extracts links from comment bodies and stores bounded raw evidence before any
   summary publication.

Comment text is stripped of HTML for summary input, while link targets remain
available for related-link extraction. A refresh stores at most 1,000 comments
and reports whether that cap was reached. Provider responses are bounded to
16 MiB.

Discussion collection and summary publication have independent cadences.
Unchanged input reuses durable state; changed comments are tracked and may
trigger a full or incremental summary according to the materiality and age
policy. Final publication uses the exact live task lease and revalidates the
stored discussion identity, so a stale refresh cannot overwrite newer state.

## Failure behavior

- Missing, dead, or deleted HN items become terminal unavailable discussions.
- Invalid identities and unsupported platforms are rejected before provider
  work.
- Transport errors, malformed provider payloads, and bounded-response failures
  retain their retry classification.
- Partial top-story fetch failures remain item-scoped.
- No provider call runs while holding the finalization transaction.

The implementation lives in `newsly-providers` for Firebase/Algolia transport
and normalization, and in the Rust discussion worker for durable orchestration
and publication. The behavioral cadence and fencing requirements are canonical
in `docs/laws/processing-and-reliability.md`.
