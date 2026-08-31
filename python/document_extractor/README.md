# Newsly document extractor

This is the only retained production Python service in the Rust backend architecture. It owns
Newsly's Crawl4AI and static HTML extraction policy behind a versioned private HTTP contract.

The process deliberately has no PostgreSQL driver, SQLAlchemy, Alembic, queue runtime, Newsly JWT,
or durable storage dependency. It accepts one bounded public URL, revalidates DNS and redirects,
returns a typed result, and leaves persistence, retries, Firecrawl calls, and usage accounting to
Rust. Startup rejects `DATABASE_URL` and `NEWSLY_DATABASE_URL` in every environment if either is
accidentally injected.

The language-neutral v1 protocol is checked in under `contracts/extraction/`. Every wire field is
explicit, including nullable values, and the response is a discriminated success, PubMed
delegation, Firecrawl request, or typed failure. Admission is bounded by
`DOCUMENT_EXTRACTOR_MAX_CONCURRENT_EXTRACTIONS` (eight by default), and each request's absolute
deadline includes time waiting for admission and for the single-flight browser.

Run locally from this directory:

```bash
uv sync
uv run playwright install chromium
DOCUMENT_EXTRACTOR_SHARED_SECRET=local-secret uv run newsly-document-extractor
```

Endpoints:

- `GET /health/live`
- `GET /health/ready`
- `POST /v1/extract`

Extraction requests always require the service-specific secret in
`X-Document-Extractor-Token`. Requests fail closed when no secret is configured, and production
refuses to start without `DOCUMENT_EXTRACTOR_SHARED_SECRET`.
