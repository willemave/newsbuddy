# app/processing_strategies/

Source folder: `app/processing_strategies`

## Purpose
Ordered URL-specific extraction strategies used by the content worker to turn raw URLs into normalized article, podcast, PDF, or discussion payloads.

## Runtime behavior
- Encapsulates source-specific logic for Hacker News, arXiv, PubMed, YouTube, PDFs, general HTML pages, and tweet shares.
- Uses a registry so worker code can stay generic while specialized strategies decide whether to skip, delegate, or extract content.

## Inventory scope
- Direct file inventory for `app/processing_strategies`.

## Modules and files
| File | Key symbols | Notes |
|---|---|---|
| `app/processing_strategies/__init__.py` | n/a | Supporting module or configuration file. |
| `app/processing_strategies/arxiv_strategy.py` | `ArxivProcessorStrategy` | Strategy for processing arXiv content URLs. |
| `app/processing_strategies/base_strategy.py` | `UrlProcessorStrategy` | This module defines the abstract base class for URL processing strategies. |
| `app/processing_strategies/hackernews_strategy.py` | `HackerNewsProcessorStrategy` | HackerNews processing strategy that handles HN discussion pages, fetches comments, and generates comment summaries. |
| `app/processing_strategies/html_strategy.py` | `HtmlProcessorStrategy` | This module defines the strategy for processing standard HTML web pages using crawl4ai. |
| `app/processing_strategies/image_strategy.py` | `ImageProcessorStrategy` | This module defines the strategy for handling image URLs |
| `app/processing_strategies/pdf_strategy.py` | `PdfProcessorStrategy` | Types: `PdfProcessorStrategy` |
| `app/processing_strategies/pubmed_strategy.py` | `PubMedProcessorStrategy` | This module defines the strategy for processing PubMed article pages |
| `app/processing_strategies/registry.py` | `StrategyRegistry`, `get_strategy_registry` | Types: `StrategyRegistry`. Functions: `get_strategy_registry` |
| `app/processing_strategies/twitter_share_strategy.py` | `TweetContent`, `TwitterShareProcessorStrategy` | Tweet-only processing strategy for share-sheet ingestion. |
| `app/processing_strategies/youtube_strategy.py` | `YouTubeProcessorStrategy` | Types: `YouTubeProcessorStrategy` |
