# app/processing_strategies/

Source folder: `app/processing_strategies`

## Purpose
Ordered URL/content extraction strategies used by `ContentWorker` to turn a submitted URL into normalized content for summarization, discussion extraction, media processing, or terminal failure.

## Runtime behavior
- `registry.py` registers strategies in order: Hacker News, arXiv, PubMed, YouTube, Twitter share, PDF, image, plain text, then HTML.
- Strategies implement URL matching, download, extraction, and LLM-preparation hooks from `base_strategy.py`.
- `ContentWorker` treats `NonRetryableError` as terminal content failure and respects strategy `skip_processing` flags.
- HTML extraction uses crawl4ai/trafilatura paths with Firecrawl fallback.
- PDF and arXiv paths can use Gemini extraction and fall back to local PDF extraction.
- YouTube extraction uses `yt-dlp` and `app/scraping/youtube_config.py`.
- Tweet share extraction uses official X lookup and treats missing text/lookup failures as nonretryable.

## Important files
| File | Purpose |
|---|---|
| `base_strategy.py` | Abstract `UrlProcessorStrategy` contract. |
| `registry.py` | Shared strategy registry and ordering. |
| `hackernews_strategy.py` | HN discussion pages and comment-summary context. |
| `arxiv_strategy.py` | arXiv abstract/PDF delegation and extraction. |
| `pubmed_strategy.py` | PubMed-specific article handling/delegation. |
| `youtube_strategy.py` | YouTube video metadata/transcript handling. |
| `twitter_share_strategy.py` | Tweet share URL extraction through X lookup. |
| `pdf_strategy.py` | Direct PDF extraction. |
| `image_strategy.py` | Direct image URL handling. |
| `plain_text_strategy.py` | Direct text document handling. |
| `html_strategy.py` | General webpage extraction fallback. |

## Integration points
- `app/pipeline/worker.py` selects and executes strategies.
- `app/http_client/robust_http_client.py` provides the shared synchronous HTTP client.
- Provider keys and external extraction settings come from `app/core/settings.py`.
