from app.core.logging import get_logger
from app.http_client.robust_http_client import RobustHttpClient
from app.processing_strategies.arxiv_strategy import ArxivProcessorStrategy
from app.processing_strategies.base_strategy import UrlProcessorStrategy
from app.processing_strategies.hackernews_strategy import HackerNewsProcessorStrategy
from app.processing_strategies.html_strategy import HtmlProcessorStrategy
from app.processing_strategies.image_strategy import ImageProcessorStrategy
from app.processing_strategies.pdf_strategy import PdfProcessorStrategy
from app.processing_strategies.plain_text_strategy import PlainTextProcessorStrategy
from app.processing_strategies.pubmed_strategy import PubMedProcessorStrategy
from app.processing_strategies.twitter_share_strategy import TwitterShareProcessorStrategy
from app.processing_strategies.youtube_strategy import YouTubeProcessorStrategy

logger = get_logger(__name__)


class StrategyRegistry:
    """Registry for content processing strategies."""

    def __init__(self) -> None:
        self.strategies: list[UrlProcessorStrategy] = []
        self.http_client = RobustHttpClient()
        self._initialize_default_strategies()

    def _initialize_default_strategies(self) -> None:
        """Initialize with default strategies."""
        # Order is important: more specific strategies should come before general ones.
        # HackerNewsStrategy for HN item URLs (needs to be early to intercept HN links)
        # ArxivStrategy for /abs/ links, which then become PDF links.
        # PubMedStrategy for specific domain.
        # YouTubeStrategy for YouTube video URLs.
        # PdfProcessorStrategy for direct .pdf links or Content-Type PDF.
        # ImageProcessorStrategy for image files.
        # PlainTextProcessorStrategy for direct .txt/text responses.
        # TwitterShareProcessorStrategy for tweet-only share URLs.
        # HtmlProcessorStrategy should be checked before URL strategy fallback.
        self.register(HackerNewsProcessorStrategy(self.http_client))  # HackerNews discussions
        self.register(ArxivProcessorStrategy(self.http_client))  # Handles arxiv.org/abs/ links
        self.register(PubMedProcessorStrategy(self.http_client))  # Specific domain
        self.register(YouTubeProcessorStrategy(self.http_client))  # YouTube videos
        self.register(TwitterShareProcessorStrategy(self.http_client))  # Tweet share URLs
        self.register(
            PdfProcessorStrategy(self.http_client)
        )  # Specific content type by extension/common URL pattern
        self.register(ImageProcessorStrategy(self.http_client))  # Image files
        self.register(PlainTextProcessorStrategy(self.http_client))  # Plain text documents
        self.register(HtmlProcessorStrategy(self.http_client))  # HTML content (uses crawl4ai)

    def register(self, strategy: UrlProcessorStrategy):
        """Register a new strategy."""
        self.strategies.append(strategy)
        logger.info(f"Registered strategy: {strategy.__class__.__name__}")

    def get_strategy(
        self, url: str, headers: dict[str, str] | None = None
    ) -> UrlProcessorStrategy | None:
        """Get the appropriate strategy for a URL."""
        # Convert dict headers to httpx.Headers if needed
        httpx_headers = None
        if headers:
            import httpx

            httpx_headers = httpx.Headers(headers)

        for strategy in self.strategies:
            if strategy.can_handle_url(url, httpx_headers):
                logger.debug(f"Using {strategy.__class__.__name__} for {url}")
                return strategy

        logger.warning(f"No strategy found for URL: {url}")
        return None


# Global registry instance
_registry = None


def get_strategy_registry() -> StrategyRegistry:
    """Get the global strategy registry."""
    global _registry
    if _registry is None:
        _registry = StrategyRegistry()
    return _registry
