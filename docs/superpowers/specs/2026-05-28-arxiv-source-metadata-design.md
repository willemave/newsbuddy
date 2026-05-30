# arXiv Source Metadata Design

Date: 2026-05-28
Status: Design approved for display-only implementation

## Goal

Add display-only source metadata for arXiv-backed content so Newsly can show where authors work and a brief paper synopsis on both long-form article details and Fast Read news-item details.

The design should also create a stable pattern for future source metadata without adding database columns or changing feed ranking, filtering, or search behavior.

## Current Pipeline

Fast Reads use the canonical `news_items` pipeline.

1. Scrapers upsert a `news_items` row.
2. Newly created, non-legacy rows that are not already ready enqueue `ENRICH_NEWS_ITEM_ARTICLE`.
3. `ENRICH_NEWS_ITEM_ARTICLE` resolves the outbound article URL through the shared strategy registry.
4. arXiv article URLs are handled by `ArxivProcessorStrategy`.
5. The enrichment task persists the article body pointer in `news_items.raw_metadata`.
6. The enrichment handler enqueues `PROCESS_NEWS_ITEM`, which generates the Fast Read summary.

Long-form article submissions use `PROCESS_CONTENT`, which also resolves arXiv URLs through the same `ArxivProcessorStrategy` before summarization.

This means the arXiv strategy is the right collection seam, while persistence needs to happen in both the long-form worker and the news-item enrichment path.

## Scope

In scope:

- Store arXiv source metadata in existing JSON metadata fields.
- Show metadata in detail views only.
- Use the metadata as optional prompt grounding for summaries.
- Keep metadata best-effort and nonfatal.
- Establish a typed, versioned envelope that other source providers can reuse.

Out of scope:

- New DB columns, indexes, or migrations.
- Feed-card chips.
- Search, filtering, ranking, or recommendation logic.
- Backfilling old rows.
- Making author affiliation extraction perfect.

## Data Shape

Use a top-level `source_metadata` object in both metadata stores:

- `contents.content_metadata["source_metadata"]`
- `news_items.raw_metadata["source_metadata"]`

Initial shape:

```json
{
  "schema_version": 1,
  "kind": "research_paper",
  "provider": "arxiv",
  "source_id": "2509.15194v2",
  "canonical_abs_url": "https://arxiv.org/abs/2509.15194v2",
  "pdf_url": "https://arxiv.org/pdf/2509.15194v2",
  "title": "Paper title",
  "abstract": "Original arXiv abstract text.",
  "brief_synopsis": "One or two sentence reader-facing synopsis.",
  "authors": [
    {
      "name": "Author Name",
      "affiliation": "Institution or company, when available",
      "affiliation_source": "arxiv_api",
      "confidence": "direct"
    }
  ],
  "categories": [
    {"term": "cs.AI", "primary": true}
  ],
  "published_at": "2026-01-01T00:00:00Z",
  "updated_at": "2026-01-02T00:00:00Z",
  "doi": null,
  "journal_ref": null,
  "comment": null,
  "extracted_at": "2026-05-28T00:00:00Z"
}
```

Affiliation values should be optional. If the public metadata source does not provide affiliations, store authors with `affiliation: null`, `affiliation_source: "missing"`, and `confidence: "unknown"` rather than inventing institution names.

## Collection

Add `app/services/arxiv_metadata.py`.

Responsibilities:

- Parse arXiv IDs from `/abs/...` and `/pdf/...` URLs.
- Fetch arXiv Atom metadata by ID.
- Normalize title, abstract, authors, categories, dates, DOI, journal ref, comment, and links.
- Return a typed source metadata model or `None`.
- Fail softly with structured logs.

`ArxivProcessorStrategy.extract_data()` should call this service and add `source_metadata` to the returned extracted data. PDF text extraction remains the primary success path.

For `brief_synopsis`, use the arXiv abstract initially:

- Prefer a deterministic compressed synopsis derived from the abstract when possible.
- Do not add a second LLM call in phase 1.
- The later summary prompt can still produce richer wording from the body and metadata.

## Persistence

Long-form:

- In `ContentWorker._process_article`, copy `extracted_data["source_metadata"]` into `metadata_update["source_metadata"]`.
- Add `source_metadata` to the API metadata large-value allowlist.
- Keep body text out of API metadata as today.

Fast Reads:

- In `enrich_news_item_article`, copy `extracted_data["source_metadata"]` into `news_items.raw_metadata["source_metadata"]`.
- Preserve existing `article_body_ref` behavior.
- Sanitize news-item detail metadata before API response so body refs and storage internals do not leak.

Existing rows without `source_metadata` should continue decoding and rendering normally.

## Prompt Grounding

Fast Reads:

- Add a small "Research metadata" block in `_build_processing_prompt`.
- Include title, synopsis, primary category, authors, known affiliations, and arXiv ID.
- Keep it bounded so article body remains the primary evidence.

Long-form:

- Include `source_metadata` in the metadata passed to long-form artifact prompt building.
- Use it as source context only. Do not add new required fields to `LongformArtifactEnvelope`.

## API and iOS Display

Backend:

- `ContentDetailResponse.metadata` can carry `source_metadata` for article details.
- News-item detail metadata can carry `source_metadata` for Fast Read details.
- No list response additions in phase 1.

iOS:

- Add Swift metadata models that decode `source_metadata`.
- Render a compact detail-only section for arXiv metadata:
  - brief synopsis
  - authors
  - affiliations when present
  - categories and publication date
  - arXiv link
- Hide the section when metadata is missing.
- Avoid feed-card changes.

## Implementation Plan

1. Add typed Python metadata models.
2. Add arXiv metadata fetch and parse service.
3. Extend `ArxivProcessorStrategy` to return `source_metadata`.
4. Persist metadata in long-form processing.
5. Persist metadata in news-item enrichment.
6. Allowlist and sanitize metadata at API boundaries.
7. Add Swift decoding and detail-only UI.
8. Add focused tests.

## Tests

Backend tests:

- arXiv ID parsing for `/abs/...`, `/pdf/...`, versioned IDs, and query strings.
- Atom response parsing into the source metadata envelope.
- arXiv strategy returns text plus `source_metadata`.
- Long-form processing persists `source_metadata`.
- News enrichment persists `source_metadata` and still enqueues `PROCESS_NEWS_ITEM`.
- Fast Read prompt includes bounded research metadata when present.
- API metadata includes `source_metadata` but excludes storage refs.

iOS tests:

- Decode article detail metadata with `source_metadata`.
- Decode news detail metadata with `source_metadata`.
- Render section only when displayable metadata exists.

## Self-Review

This design intentionally keeps the feature display-only. It does not require schema changes, does not alter ranking or readiness, and uses the already shared arXiv strategy seam for both long-form and news items. The main implementation risk is affiliation completeness; the design handles that by allowing missing affiliations and avoiding inferred institution names unless a future source supports them with confidence metadata.
