import json
import logging
import random
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib.parse import urlparse

import jmespath
import yaml
from playwright.sync_api import Response, sync_playwright

from app.core.db import get_db
from app.core.logging import get_logger
from app.models.metadata import ContentType
from app.models.schema import Content
from app.scraping.base import BaseScraper
from app.services.x_api import fetch_list_tweets
from app.utils.error_logger import log_scraper_event

logger = get_logger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config" / "twitter.yml"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

DEFAULT_GUEST_BEARER = "Bearer AAAAAAAAAAAAAAAAAAAAAANRILgAAAAA7dbR1mQ4pcFZscR0gLDOk4ew3E"
GUEST_TOKEN_TTL = timedelta(minutes=20)
RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


class TwitterUnifiedScraper(BaseScraper):
    """Playwright-based Twitter scraper for lists and searches."""

    def __init__(self):
        super().__init__("Twitter")
        self.config = self._load_config()
        self.settings = self.config.get("settings", {})
        client_section = self.config.get("client") or {}
        cookies_path = client_section.get("cookies_path")
        self.cookies_path = self._resolve_cookies_path(cookies_path) if cookies_path else None
        self._auth_warning_lists: set[str] = set()
        self._playwright_skip_lists: set[str] = set()
        self._guest_token: str | None = None
        self._guest_token_acquired_at: datetime | None = None
        self._has_auth_cookies = False
        self._playwright_auth_checked = False
        self._playwright_auth_available = False
        self._api_access_token: str | None = None
        self._api_access_token_checked = False
        self._bearer_token = self.settings.get("default_bearer_token", DEFAULT_GUEST_BEARER)
        # You can configure proxy here if needed
        self.proxy = self.settings.get("proxy")  # Format: "http://user:pass@host:port"

    def _load_config(self) -> dict[str, Any]:
        """Load Twitter configuration from YAML file."""
        config_path = CONFIG_PATH

        if not config_path.exists():
            logger.warning(f"Twitter config file not found: {config_path}")
            return {"twitter_searches": [], "twitter_lists": [], "settings": {}}

        try:
            with open(config_path) as f:
                config = yaml.safe_load(f)

            searches = len(config.get("twitter_searches", []))
            lists = len(config.get("twitter_lists", []))
            logger.info(f"Loaded {searches} searches and {lists} lists from Twitter config")
            return config

        except Exception as e:
            logger.error(f"Error loading Twitter config: {e}")
            return {"twitter_searches": [], "twitter_lists": [], "settings": {}}

    def scrape(self) -> list[dict[str, Any]]:
        """Scrape Twitter lists using Playwright."""
        all_items = []

        # Check if we have any configuration
        has_lists = bool(self.config.get("twitter_lists"))

        if not has_lists:
            logger.warning("No Twitter lists configured")
            return []

        # Scrape lists using Playwright
        for list_config in self.config.get("twitter_lists", []):
            list_name = list_config.get("name", "Unknown List")
            list_id = list_config.get("list_id")

            if not list_id:
                logger.error("No list_id provided for list: %s", list_name)
                continue

            recent_scrape_hours = self._recent_scrape_hours(list_config)
            if recent_scrape_hours > 0 and self._check_recent_scrape(
                str(list_id), hours=recent_scrape_hours
            ):
                logger.info(
                    "Skipping list %s - already scraped within last %.2f hours",
                    list_name,
                    recent_scrape_hours,
                )
                continue

            try:
                try:
                    items = self._scrape_list_api(list_config)
                except Exception as exc:
                    logger.warning(
                        "X API list scrape failed for %s (%s); falling back to Playwright: %s",
                        list_name,
                        list_id,
                        exc,
                    )
                    items = None
                if not items and not self._has_playwright_auth_available():
                    self._emit_playwright_skip_info(list_name, str(list_id))
                    continue
                if not items:
                    items = self._scrape_list_playwright(list_config)
                if items:
                    all_items.extend(items)
                    logger.info("Scraped Twitter list: %s", list_name)
            except Exception as e:
                logger.error("Error scraping Twitter list %s: %s", list_name, e)
                continue

        logger.info(f"Total Twitter list items scraped: {len(all_items)}")
        return all_items

    def _recent_scrape_hours(self, list_config: dict[str, Any]) -> float:
        raw_value = list_config.get(
            "recent_scrape_hours",
            self.settings.get("default_recent_scrape_hours", 0),
        )
        try:
            hours = float(raw_value)
        except (TypeError, ValueError):
            return 0.0
        return max(hours, 0.0)

    def _check_recent_scrape(self, list_id: str, hours: float = 0) -> bool:
        """Check if list was scraped recently."""
        if hours <= 0:
            return False

        with get_db() as db:
            cutoff_time = datetime.now(UTC) - timedelta(hours=hours)
            recent_rows = (
                db.query(Content.content_metadata)
                .filter(
                    Content.platform == "twitter",
                    Content.created_at > cutoff_time,
                )
                .all()
            )

        for row in recent_rows:
            metadata = row[0] if isinstance(row, tuple) else getattr(row, "content_metadata", None)
            if not isinstance(metadata, dict):
                continue
            aggregator = metadata.get("aggregator")
            aggregator_metadata = aggregator.get("metadata") if isinstance(aggregator, dict) else {}
            if not isinstance(aggregator_metadata, dict):
                continue
            if str(aggregator_metadata.get("list_id") or "").strip() == str(list_id).strip():
                return True

        return False

    def _has_playwright_auth_available(self) -> bool:
        """Return True when Playwright fallback has usable auth cookies."""
        if self._playwright_auth_checked:
            return self._playwright_auth_available

        self._playwright_auth_checked = True
        self._playwright_auth_available = False

        if not self.cookies_path or not self.cookies_path.exists():
            return False

        try:
            raw_content = self.cookies_path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.info("Unable to read Twitter cookies from %s: %s", self.cookies_path, exc)
            return False

        prepared = self._parse_cookie_file(raw_content)
        self._playwright_auth_available = any(
            cookie.get("name") == "auth_token" for cookie in prepared
        )
        return self._playwright_auth_available

    def _emit_playwright_skip_info(self, list_name: str, list_id: str) -> None:
        identifier = str(list_id)
        if identifier in self._playwright_skip_lists:
            return
        self._playwright_skip_lists.add(identifier)
        logger.info(
            (
                "Skipping Playwright fallback for Twitter list '%s' (%s); "
                "auth cookies are unavailable."
            ),
            list_name,
            list_id,
        )

    def _get_x_api_access_token(self) -> str | None:
        """Resolve an active X OAuth user token for list scraping."""
        if self._api_access_token_checked:
            return self._api_access_token

        self._api_access_token_checked = True

        try:
            from app.models.schema import UserIntegrationConnection
            from app.services.x_integration import _ensure_valid_access_token
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Unable to load X integration helpers: %s", exc)
            return None

        with get_db() as db:
            connections = (
                db.query(UserIntegrationConnection)
                .filter(UserIntegrationConnection.provider == "x")
                .filter(UserIntegrationConnection.is_active.is_(True))
                .order_by(UserIntegrationConnection.updated_at.desc())
                .all()
            )
            for connection in connections:
                scopes = {
                    scope.strip()
                    for scope in (connection.scopes or [])
                    if isinstance(scope, str) and scope.strip()
                }
                required_scopes = {"tweet.read", "users.read", "list.read"}
                if not required_scopes.issubset(scopes):
                    continue

                try:
                    token = _ensure_valid_access_token(db, connection)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Unable to refresh X OAuth token for list scraping via connection %s: %s",
                        connection.id,
                        exc,
                    )
                    continue

                if token:
                    logger.info(
                        "Using X API auth fallback for Twitter list scraping via connection %s",
                        connection.id,
                    )
                    self._api_access_token = token
                    return token

        return None

    def _scrape_list_api(self, list_config: dict[str, Any]) -> list[dict[str, Any]] | None:
        """Scrape a Twitter list via the official X API using an active OAuth token."""
        list_name = list_config.get("name", "Unknown List")
        list_id = str(list_config.get("list_id") or "").strip()
        limit = int(list_config.get("limit", self.settings.get("default_limit", 50)))
        hours_back = list_config.get("hours_back", self.settings.get("default_hours_back", 24))

        if not list_id:
            return None

        access_token = self._get_x_api_access_token()
        if access_token:
            logger.info(
                "Scraping Twitter list via X API with user auth: %s (%s)",
                list_name,
                list_id,
            )
        else:
            logger.info(
                "Scraping Twitter list via X API with app bearer fallback: %s (%s)",
                list_name,
                list_id,
            )

        tweets: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        next_token: str | None = None
        cutoff_time = datetime.now(UTC) - timedelta(hours=hours_back)
        page_size = min(max(limit, 5), 100)
        max_pages = max(1, (limit + page_size - 1) // page_size)

        for _ in range(max_pages):
            page = fetch_list_tweets(
                list_id=list_id,
                access_token=access_token,
                pagination_token=next_token,
                max_results=page_size,
            )
            if not page.tweets:
                break

            for tweet in page.tweets:
                if tweet.id in seen_ids:
                    continue
                seen_ids.add(tweet.id)

                tweet_date = self._parse_tweet_date(tweet.created_at or "")
                if tweet_date and tweet_date < cutoff_time:
                    continue

                if (
                    not self.settings.get("include_retweets", False)
                    and "retweeted" in tweet.referenced_tweet_types
                ):
                    continue

                if not self.settings.get("include_replies", False) and tweet.in_reply_to_user_id:
                    continue

                likes = tweet.like_count or 0
                retweets = tweet.retweet_count or 0
                min_engagement = self.settings.get("min_engagement", 0)
                if (likes + retweets) < min_engagement:
                    continue

                tweets.append(
                    {
                        "id": tweet.id,
                        "url": f"https://x.com/i/status/{tweet.id}",
                        "date": tweet.created_at or "",
                        "username": tweet.author_username or "unknown",
                        "display_name": (
                            tweet.author_name or tweet.author_username or "Unknown User"
                        ),
                        "content": tweet.text,
                        "likes": likes,
                        "retweets": retweets,
                        "replies": tweet.reply_count or 0,
                        "quotes": 0,
                        "created_at": tweet.created_at or "",
                        "is_retweet": "retweeted" in tweet.referenced_tweet_types,
                        "in_reply_to_status_id": tweet.in_reply_to_user_id,
                        "links": [
                            {
                                "url": external_url,
                                "expanded_url": external_url,
                                "display_url": external_url,
                            }
                            for external_url in tweet.external_urls
                        ],
                    }
                )

                if len(tweets) >= limit:
                    break

            if len(tweets) >= limit or not page.next_token:
                break
            next_token = page.next_token

        if not tweets:
            return None

        return self._build_news_entries(
            tweets=tweets,
            list_id=list_id,
            list_name=list_name,
            hours_back=int(hours_back),
        )

    def _scrape_list_playwright(self, list_config: dict[str, Any]) -> list[dict[str, Any]] | None:
        """Scrape a Twitter list using Playwright to intercept XHR requests."""
        list_name = list_config.get("name", "Unknown List")
        list_id = list_config.get("list_id")
        limit = list_config.get("limit", self.settings.get("default_limit", 50))
        hours_back = list_config.get("hours_back", self.settings.get("default_hours_back", 24))

        if not list_id:
            logger.error(f"No list_id provided for list: {list_name}")
            return None

        logger.info(f"Scraping Twitter list with Playwright: {list_name} ({list_id})")

        tweets = []
        cutoff_time = datetime.now(UTC) - timedelta(hours=hours_back)

        try:
            with sync_playwright() as pw:
                browser_args = {"headless": True}
                if self.proxy:
                    browser_args["proxy"] = {"server": self.proxy}

                browser = pw.chromium.launch(**browser_args)
                context = browser.new_context(user_agent=DEFAULT_USER_AGENT)
                try:
                    self._apply_cookies(context)
                    headers = self._build_authenticated_headers()
                    context.set_extra_http_headers(headers)
                    page = context.new_page()

                    # Set user agent to avoid detection and include guest headers when needed
                    page.set_extra_http_headers(headers)

                    xhr_calls = []

                    # Capture Twitter API calls (broader patterns)
                    def on_response(response):
                        url = response.url
                        # Look for Twitter API endpoints
                        api_patterns = [
                            "ListLatestTweets",
                            "TweetResultByRestId",
                            "UserTweets",
                            "HomeTimeline",
                            "SearchTimeline",
                            "ListTweets",
                            "graphql",
                            "api/graphql",
                            "i/api/graphql",
                        ]
                        if any(pattern in url for pattern in api_patterns):
                            xhr_calls.append(response)
                            logger.info(f"Captured API call: {url[:100]}...")
                        elif "twitter.com" in url and (
                            "json" in response.headers.get("content-type", "") or "api" in url
                        ):
                            xhr_calls.append(response)
                            logger.info(f"Captured potential API call: {url[:100]}...")

                    page.on("response", on_response)

                    # Navigate to the list page
                    list_url = f"https://twitter.com/i/lists/{list_id}"
                    logger.info(f"Navigating to: {list_url}")

                    try:
                        # Try to navigate to the list page
                        response = page.goto(list_url, wait_until="domcontentloaded", timeout=15000)
                        status = response.status if response else "No response"
                        logger.info(f"Page response status: {status}")

                        # Check if we're redirected to login
                        current_url = page.url
                        if "login" in current_url.lower() or "authenticate" in current_url.lower():
                            logger.warning(f"Twitter requires login. URL: {current_url}")
                            raise Exception(
                                "Login required - cannot access list without authentication"
                            )

                        # Wait for content to load (be more lenient with timeout)
                        try:
                            page.wait_for_selector(
                                "[data-testid='tweet'], [data-testid='cellInnerDiv']", timeout=5000
                            )
                            logger.info("Found tweet elements on page")
                        except Exception:
                            logger.info("Could not find tweet elements, but continuing.")

                        # Give some time for dynamic content to load
                        page.wait_for_timeout(3000)

                        # Try scrolling to trigger more API calls
                        max_scrolls = min(3, (limit // 20) + 1)
                        for i in range(max_scrolls):
                            page.mouse.wheel(0, 2000)
                            page.wait_for_timeout(1500)
                            logger.debug(f"Scroll {i + 1}/{max_scrolls}")

                        logger.info(f"Captured {len(xhr_calls)} API responses")

                    except Exception as e:
                        logger.warning(f"Error loading Twitter list page: {e}")
                        if "timeout" in str(e).lower():
                            logger.info("Continuing with any captured API calls...")
                        else:
                            raise

                    # Process captured API responses (before closing browser)
                    auth_required = False
                    for call in xhr_calls:
                        try:
                            if call.status != 200:
                                if (
                                    call.status in {401, 403, 404}
                                    and "ListLatestTweetsTimeline" in call.url
                                ):
                                    auth_required = True
                                logger.debug(
                                    "Skipping response from %s due to status %s",
                                    call.url[:80],
                                    call.status,
                                )
                                continue

                            decoded = self._decode_response_json(call)
                            if not decoded:
                                continue

                            data, payload_size = decoded
                            logger.info(
                                "Processing API response from %s... (~%s chars)",
                                call.url[:50],
                                payload_size,
                            )

                            list_tweets = self._extract_tweets_from_response(data)
                            logger.info(f"Extracted {len(list_tweets)} tweets from this response")

                            for tweet_data in list_tweets:
                                if len(tweets) >= limit:
                                    break

                                tweet_date = self._parse_tweet_date(
                                    tweet_data.get("created_at", "")
                                )
                                if tweet_date and tweet_date < cutoff_time:
                                    continue

                                if not self.settings.get(
                                    "include_retweets", False
                                ) and tweet_data.get("is_retweet"):
                                    continue

                                if not self.settings.get(
                                    "include_replies", False
                                ) and tweet_data.get("in_reply_to_status_id"):
                                    continue

                                min_engagement = self.settings.get("min_engagement", 0)
                                likes = tweet_data.get("likes", tweet_data.get("favorite_count", 0))
                                retweets = tweet_data.get(
                                    "retweets", tweet_data.get("retweet_count", 0)
                                )
                                if (likes + retweets) < min_engagement:
                                    continue

                                tweets.append(tweet_data)
                                logger.debug(
                                    "Added tweet from @%s", tweet_data.get("username", "unknown")
                                )

                        except Exception as e:
                            logger.warning(
                                "Error processing API response from %s: %s",
                                call.url[:50],
                                e,
                            )
                            continue

                    if auth_required and not tweets:
                        self._emit_auth_warning(list_name, list_id)
                finally:
                    context.close()
                    browser.close()
                logger.info(f"Total tweets collected: {len(tweets)}")

        except Exception as e:
            logger.error(f"Playwright scraping failed for list {list_id}: {e}")
            return None

        if not tweets:
            logger.info(f"No tweets found for list: {list_name}")
            return None

        return self._build_news_entries(
            tweets=tweets,
            list_id=str(list_id),
            list_name=list_name,
            hours_back=int(hours_back),
        )

    def _build_news_entries(
        self,
        *,
        tweets: list[dict[str, Any]],
        list_id: str,
        list_name: str,
        hours_back: int,
    ) -> list[dict[str, Any]] | None:
        """Convert normalized tweet payloads into news entries."""
        news_entries: list[dict[str, Any]] = []
        seen_pairs: set[tuple[str, str]] = set()

        for tweet in tweets:
            links = tweet.get("links", [])
            if not links:
                continue

            for link in links:
                article_url = link.get("expanded_url") or link.get("url")
                if not article_url:
                    continue

                normalized_article_url = self._normalize_external_url(article_url)
                if not normalized_article_url:
                    continue

                unique_key = (normalized_article_url, str(tweet.get("id")))
                if unique_key in seen_pairs:
                    continue
                seen_pairs.add(unique_key)

                domain = self._extract_domain(normalized_article_url)
                headline = tweet.get("content", "").split("\n", 1)[0].strip()
                display_title = headline or link.get("title") or normalized_article_url

                metadata = {
                    "platform": "twitter",
                    "source": domain,
                    "article": {
                        "url": normalized_article_url,
                        "title": display_title,
                        "source_domain": domain,
                    },
                    "aggregator": {
                        "name": "Twitter",
                        "title": tweet.get("content", "").strip(),
                        "external_id": str(tweet.get("id")),
                        "author": tweet.get("display_name"),
                        "metadata": {
                            "username": tweet.get("username"),
                            "likes": tweet.get("likes"),
                            "retweets": tweet.get("retweets"),
                            "replies": tweet.get("replies"),
                            "quotes": tweet.get("quotes"),
                            "tweet_created_at": tweet.get("created_at"),
                            "list_id": list_id,
                            "list_name": list_name,
                            "hours_back": hours_back,
                        },
                    },
                    "discussion_url": tweet.get("url"),
                    "discovery_time": datetime.now(UTC).isoformat(),
                }

                news_entries.append(
                    {
                        "url": normalized_article_url,
                        "title": display_title[:280],
                        "content_type": ContentType.NEWS,
                        "is_aggregate": False,
                        "metadata": metadata,
                    }
                )

        return news_entries or None

    def _extract_tweets_from_response(self, data: dict) -> list[dict[str, Any]]:
        """Extract tweet data from Twitter API response using a resilient AST walk."""

        candidate_nodes: list[dict[str, Any]] = []
        seen_nodes: set[int] = set()

        queries = [
            "data.list.tweets_timeline.timeline.instructions[].entries[].content.itemContent.tweet_results.result",
            "data.list.tweets_timeline.timeline.instructions[*].entries[*].content.itemContent.tweet_results.result",
            "data.timeline.timeline.instructions[].entries[].content.itemContent.tweet_results.result",
            "data.user.result.timeline.timeline.instructions[].entries[].content.itemContent.tweet_results.result",
            "data.tweetResult.result",
            "data.home.home_timeline_urt.instructions[].entries[].content.itemContent.tweet_results.result",
        ]

        for query in queries:
            try:
                results = jmespath.search(query, data)
            except Exception as exc:  # pragma: no cover - defensive logging only
                logger.debug("Query '%s' failed: %s", query, exc)
                continue

            if isinstance(results, list):
                for result in results:
                    if isinstance(result, dict) and id(result) not in seen_nodes:
                        candidate_nodes.append(result)
                        seen_nodes.add(id(result))
            elif isinstance(results, dict) and id(results) not in seen_nodes:
                candidate_nodes.append(results)
                seen_nodes.add(id(results))

        # Depth-first walk to catch any new/unknown response shapes
        for node in self._collect_tweet_result_nodes(data):
            node_id = id(node)
            if node_id in seen_nodes:
                continue
            candidate_nodes.append(node)
            seen_nodes.add(node_id)

        if not candidate_nodes:
            try:
                generic_results = jmespath.search("**.legacy", data)
            except Exception:  # pragma: no cover - defensive fallback
                generic_results = None

            if isinstance(generic_results, list):
                for legacy in generic_results:
                    if (
                        isinstance(legacy, dict)
                        and "full_text" in legacy
                        and id(legacy) not in seen_nodes
                    ):
                        candidate_nodes.append({"legacy": legacy})
                        seen_nodes.add(id(legacy))
            elif (
                isinstance(generic_results, dict)
                and "full_text" in generic_results
                and id(generic_results) not in seen_nodes
            ):
                candidate_nodes.append({"legacy": generic_results})
                seen_nodes.add(id(generic_results))

        processed_tweets: list[dict[str, Any]] = []
        seen_tweet_ids: set[str] = set()
        for tweet_result in candidate_nodes:
            normalized = self._normalize_tweet_result(tweet_result)
            if not normalized:
                continue

            legacy_data = normalized.get("legacy", {})
            user_data = normalized.get("user", {})

            content_value = legacy_data.get("full_text") or legacy_data.get("text")
            if not content_value:
                continue

            tweet_id = (
                legacy_data.get("id_str") or legacy_data.get("id") or normalized.get("rest_id", "")
            )
            username = user_data.get("screen_name") or user_data.get("username") or "unknown"

            tweet_identifier = str(tweet_id)
            if not tweet_identifier:
                continue

            if tweet_identifier in seen_tweet_ids:
                continue
            seen_tweet_ids.add(tweet_identifier)

            processed_tweets.append(
                {
                    "id": tweet_identifier,
                    "url": f"https://twitter.com/{username}/status/{tweet_identifier}",
                    "date": legacy_data.get("created_at", ""),
                    "username": username,
                    "display_name": user_data.get("name", "Unknown User"),
                    "content": content_value,
                    "likes": legacy_data.get("favorite_count", 0),
                    "retweets": legacy_data.get("retweet_count", 0),
                    "replies": legacy_data.get("reply_count", 0),
                    "quotes": legacy_data.get("quote_count", 0),
                    "created_at": legacy_data.get("created_at", ""),
                    "is_retweet": bool(
                        legacy_data.get("retweeted_status_result")
                        or legacy_data.get("retweeted_status")
                    ),
                    "in_reply_to_status_id": legacy_data.get("in_reply_to_status_id_str")
                    or legacy_data.get("in_reply_to_status_id"),
                    "links": self._extract_external_links(legacy_data),
                }
            )

        return processed_tweets

    def _collect_tweet_result_nodes(self, payload: Any) -> list[dict[str, Any]]:
        """Traverse payload recursively to gather tweet result dictionaries."""

        stack: list[Any] = [payload]
        collected: list[dict[str, Any]] = []
        visited: set[int] = set()

        while stack:
            current = stack.pop()
            node_id = id(current)
            if node_id in visited:
                continue
            visited.add(node_id)

            if isinstance(current, dict):
                tweet_results = current.get("tweet_results")
                if isinstance(tweet_results, dict):
                    result = tweet_results.get("result")
                    if isinstance(result, dict):
                        collected.append(result)
                        stack.append(result)

                for value in current.values():
                    stack.append(value)
            elif isinstance(current, list):
                stack.extend(current)

        return collected

    def _resolve_cookies_path(self, raw_path: str) -> Path | None:
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path

    def _apply_cookies(self, context) -> None:
        if not self.cookies_path:
            return

        try:
            raw_content = self.cookies_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.warning(
                "Twitter cookies file not found at %s; continuing without authentication",
                self.cookies_path,
            )
            return
        prepared = self._parse_cookie_file(raw_content)
        self._has_auth_cookies = any(cookie.get("name") == "auth_token" for cookie in prepared)
        if not prepared:
            logger.warning(
                "Twitter cookies file %s did not contain any usable cookies",
                self.cookies_path,
            )
            self._has_auth_cookies = False
            return

        context.add_cookies(prepared)
        logger.info("Loaded %s Twitter cookies from %s", len(prepared), self.cookies_path)

    def _parse_cookie_file(self, raw_content: str) -> list[dict[str, Any]]:
        """Parse cookie exports in either JSON or Netscape formats."""

        json_cookies = self._parse_cookie_json(raw_content)
        if json_cookies:
            return json_cookies

        netscape_cookies = self._parse_netscape_cookies(raw_content)
        if netscape_cookies:
            return netscape_cookies

        return []

    def _parse_cookie_json(self, raw_content: str) -> list[dict[str, Any]]:
        try:
            cookie_data = json.loads(raw_content)
        except json.JSONDecodeError:
            return []

        cookies = (
            cookie_data.get("cookies")
            if isinstance(cookie_data, dict) and "cookies" in cookie_data
            else cookie_data
        )

        if not isinstance(cookies, list):
            return []

        prepared: list[dict[str, Any]] = []
        for entry in cookies:
            if not isinstance(entry, dict):
                continue

            required_keys = {"name", "value", "domain"}
            if not required_keys.issubset(entry):
                continue

            prepared_cookie = {
                "name": entry["name"],
                "value": entry["value"],
                "domain": entry["domain"],
                "path": entry.get("path", "/"),
                "secure": entry.get("secure", False),
                "httpOnly": entry.get("httpOnly", False),
                "sameSite": entry.get("sameSite", "Lax"),
            }

            if "expires" in entry:
                prepared_cookie["expires"] = entry["expires"]

            prepared.append(prepared_cookie)

        return prepared

    def _parse_netscape_cookies(self, raw_content: str) -> list[dict[str, Any]]:
        prepared: list[dict[str, Any]] = []

        for line in raw_content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            parts = line.split("\t")
            if len(parts) != 7:
                continue

            domain, _include, path, secure_flag, expires, name, value = parts
            secure = secure_flag.upper() == "TRUE"

            cookie: dict[str, Any] = {
                "name": name,
                "value": value,
                "domain": domain,
                "path": path or "/",
                "secure": secure,
                "httpOnly": False,
                "sameSite": "Lax",
            }

            if expires and expires.isdigit():
                cookie["expires"] = int(expires)

            prepared.append(cookie)

        return prepared

    def _emit_auth_warning(self, list_name: str, list_id: str) -> None:
        identifier = str(list_id)
        if identifier in self._auth_warning_lists:
            return
        self._auth_warning_lists.add(identifier)
        logger.warning(
            "Twitter list '%s' (%s) requires auth. Add cookies via cookies_path.",
            list_name,
            list_id,
        )

    def _build_authenticated_headers(self) -> dict[str, str]:
        headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "application/json, text/plain, */*",
        }

        if self._has_auth_cookies:
            return headers

        token = self._ensure_guest_token()
        if token:
            headers["Authorization"] = self._bearer_token
            headers["x-guest-token"] = token
            headers["x-twitter-active-user"] = "yes"

        return headers

    def _ensure_guest_token(self, force_refresh: bool = False) -> str | None:
        if self._has_auth_cookies:
            return None

        now = datetime.now(UTC)
        if (
            not force_refresh
            and self._guest_token
            and self._guest_token_acquired_at
            and now - self._guest_token_acquired_at < GUEST_TOKEN_TTL
        ):
            return self._guest_token

        token = self._activate_guest_token()
        if token:
            self._guest_token = token
            self._guest_token_acquired_at = now
            logger.info("Acquired new Twitter guest token")
            return token

        self._guest_token = None
        self._guest_token_acquired_at = None
        log_scraper_event(
            service="Twitter",
            event="guest_token_unavailable",
            level=logging.WARNING,
            metric="scrape_transient_http",
            endpoint="https://api.x.com/1.1/guest/activate.json",
            retryable=False,
            token_refreshed=False,
        )
        return None

    def _activate_guest_token(self) -> str | None:
        headers = {
            "Authorization": self._bearer_token,
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
        }

        status, content_type, body_text = self._perform_http_request(
            "https://api.x.com/1.1/guest/activate.json",
            "POST",
            headers,
            b"{}",
        )

        if status != 200:
            log_scraper_event(
                service="Twitter",
                event="guest_activation_failed",
                level=logging.WARNING,
                metric="scrape_transient_http",
                endpoint="https://api.x.com/1.1/guest/activate.json",
                status=status,
                content_type=content_type or "unknown",
                retryable=status in RETRYABLE_STATUSES,
                token_refreshed=False,
            )
            return None

        try:
            payload = json.loads(body_text)
        except json.JSONDecodeError as exc:
            log_scraper_event(
                service="Twitter",
                event="guest_activation_json_fail",
                level=logging.WARNING,
                metric="scrape_json_parse_fail",
                endpoint="https://api.x.com/1.1/guest/activate.json",
                status=status,
                content_type=content_type or "unknown",
                length=len(body_text),
                preview=body_text[:200].replace("\n", " "),
                retryable=False,
                token_refreshed=False,
                error=str(exc),
            )
            return None

        token = payload.get("guest_token")
        if not token:
            log_scraper_event(
                service="Twitter",
                event="guest_activation_missing_token",
                level=logging.WARNING,
                metric="scrape_transient_http",
                endpoint="https://api.x.com/1.1/guest/activate.json",
                status=status,
                content_type=content_type or "unknown",
                retryable=False,
                token_refreshed=False,
            )
            return None

        return str(token)

    def _decode_response_json(self, response: Response) -> tuple[Any, int] | None:
        """Safely decode JSON payloads captured by Playwright responses."""

        url = response.url
        status = response.status
        content_type = (response.header_value("content-type") or "").lower()
        hostname = urlparse(url).hostname or ""

        if hostname.endswith("abs.twimg.com"):
            logger.debug("Skipping asset response from %s", url)
            return None

        if status in RETRYABLE_STATUSES:
            retry_decoded, _ = self._retry_fetch_json(response, status)
            if retry_decoded:
                return retry_decoded
            return None

        if status in {401, 403} and not self._has_auth_cookies:
            retry_decoded, _ = self._retry_fetch_json(response, status)
            if retry_decoded:
                return retry_decoded
            return None

        if status != 200:
            logger.debug("Skipping non-success response from %s (status %s)", url, status)
            return None

        should_attempt = (
            "json" in content_type or "graphql" in url.lower() or url.lower().endswith(".json")
        )

        if not should_attempt:
            logger.debug(
                "Skipping response from %s due to content-type '%s'",
                url,
                content_type or "unknown",
            )
            return None

        body_text = self._read_response_text(response)
        if body_text is None:
            return None

        if not body_text.strip():
            logger.debug("Skipping empty response body from %s", url)
            return None

        try:
            data = json.loads(body_text)
        except json.JSONDecodeError as exc:
            preview = body_text[:200].replace("\n", " ")
            log_scraper_event(
                service="Twitter",
                event="json_decode_failure",
                level=logging.WARNING,
                metric="scrape_json_parse_fail",
                endpoint=url,
                status=status,
                content_type=content_type or "unknown",
                length=len(body_text),
                preview=preview,
                retryable=False,
                token_refreshed=False,
                error=str(exc),
            )
            return None

        return data, len(body_text)

    def _read_response_text(self, response: Response) -> str | None:
        """Return response text with a permissive fallback decoder."""
        url = response.url
        try:
            return response.text()
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("Unable to read response text from %s: %s", url, exc)
            try:
                body_bytes = response.body()
            except Exception as body_exc:  # pragma: no cover - defensive
                logger.debug("Unable to read response bytes from %s: %s", url, body_exc)
                return None
            try:
                return body_bytes.decode("utf-8")
            except UnicodeDecodeError:
                return body_bytes.decode("utf-8", errors="ignore")

    def _retry_fetch_json(
        self, response: Response, initial_status: int
    ) -> tuple[tuple[Any, int] | None, dict[str, Any]]:
        request_obj = getattr(response, "request", None)
        if request_obj is None:
            log_scraper_event(
                service="Twitter",
                event="retry_unavailable",
                level=logging.DEBUG,
                endpoint=response.url,
                status=initial_status,
                retryable=initial_status in RETRYABLE_STATUSES,
                token_refreshed=False,
            )
            return None, {"status": initial_status, "token_refreshed": False}

        method = getattr(request_obj, "method", "GET").upper()
        post_data_attr = getattr(request_obj, "post_data", None)
        post_data_value = post_data_attr() if callable(post_data_attr) else post_data_attr

        if isinstance(post_data_value, str):
            payload_bytes: bytes | None = post_data_value.encode("utf-8")
        elif isinstance(post_data_value, bytes):
            payload_bytes = post_data_value
        else:
            payload_bytes = None

        max_attempts = 3
        base_delay = 0.5
        last_status = initial_status
        last_content_type = response.header_value("content-type") or ""
        last_body = ""
        token_refreshed = False

        for attempt in range(1, max_attempts + 1):
            if attempt > 1:
                sleep_for = base_delay * (2 ** (attempt - 2))
                jitter = random.uniform(0, sleep_for * 0.25)
                wait_time = sleep_for + jitter
                log_scraper_event(
                    service="Twitter",
                    event="retry_backoff",
                    level=logging.INFO,
                    endpoint=response.url,
                    attempt=attempt,
                    wait_seconds=round(wait_time, 2),
                    status=last_status,
                    retryable=True,
                    token_refreshed=token_refreshed,
                )
                time.sleep(wait_time)

            headers = self._build_authenticated_headers()
            if payload_bytes and not any(k.lower() == "content-type" for k in headers):
                headers["Content-Type"] = "application/json"

            request_body = payload_bytes if method != "GET" else None

            status_code, resp_content_type, resp_body = self._perform_http_request(
                response.url,
                method,
                headers,
                request_body,
            )
            last_status = status_code
            last_content_type = resp_content_type
            last_body = resp_body

            if status_code == 200:
                try:
                    data = json.loads(resp_body)
                except json.JSONDecodeError as exc:
                    log_scraper_event(
                        service="Twitter",
                        event="json_decode_retry_fail",
                        level=logging.WARNING,
                        metric="scrape_json_parse_fail",
                        endpoint=response.url,
                        status=status_code,
                        content_type=resp_content_type,
                        length=len(resp_body),
                        preview=resp_body[:200].replace("\n", " "),
                        retryable=False,
                        token_refreshed=token_refreshed,
                        error=str(exc),
                    )
                    return None, {
                        "status": status_code,
                        "content_type": resp_content_type,
                        "token_refreshed": token_refreshed,
                        "body_preview": resp_body[:200].replace("\n", " "),
                    }
                return (data, len(resp_body)), {
                    "status": status_code,
                    "content_type": resp_content_type,
                    "token_refreshed": token_refreshed,
                }

            if status_code in RETRYABLE_STATUSES and attempt < max_attempts:
                log_scraper_event(
                    service="Twitter",
                    event="retry_scheduled",
                    level=logging.INFO,
                    endpoint=response.url,
                    attempt=attempt,
                    status=status_code,
                    retryable=True,
                    token_refreshed=token_refreshed,
                )
                continue

            if status_code in {401, 403} and not self._has_auth_cookies and attempt < max_attempts:
                log_scraper_event(
                    service="Twitter",
                    event="retry_auth_refresh",
                    level=logging.INFO,
                    endpoint=response.url,
                    attempt=attempt,
                    status=status_code,
                    retryable=True,
                    token_refreshed=token_refreshed,
                )
                refreshed = self._ensure_guest_token(force_refresh=True)
                if refreshed:
                    token_refreshed = True
                    continue
                log_scraper_event(
                    service="Twitter",
                    event="retry_auth_refresh_failed",
                    level=logging.WARNING,
                    metric="scrape_transient_http",
                    endpoint=response.url,
                    status=status_code,
                    retryable=False,
                    token_refreshed=token_refreshed,
                )
                return None, {
                    "status": status_code,
                    "content_type": resp_content_type,
                    "token_refreshed": token_refreshed,
                    "body_preview": resp_body[:200].replace("\n", " "),
                }

            if status_code == 200:
                continue

            if status_code in RETRYABLE_STATUSES:
                break

            if status_code >= 400:
                log_scraper_event(
                    service="Twitter",
                    event="retry_failed",
                    level=logging.WARNING,
                    metric="scrape_transient_http",
                    endpoint=response.url,
                    status=status_code,
                    content_type=resp_content_type,
                    retryable=False,
                    token_refreshed=token_refreshed,
                )
                return None, {
                    "status": status_code,
                    "content_type": resp_content_type,
                    "token_refreshed": token_refreshed,
                    "body_preview": resp_body[:200].replace("\n", " "),
                }

        log_scraper_event(
            service="Twitter",
            event="retry_exhausted",
            level=logging.WARNING,
            metric="scrape_transient_http",
            endpoint=response.url,
            status=last_status,
            content_type=last_content_type,
            length=len(last_body),
            preview=last_body[:200].replace("\n", " "),
            retryable=last_status in RETRYABLE_STATUSES,
            token_refreshed=token_refreshed,
        )
        return None, {
            "status": last_status,
            "content_type": last_content_type,
            "token_refreshed": token_refreshed,
            "body_preview": last_body[:200].replace("\n", " "),
        }

    def _perform_http_request(
        self,
        url: str,
        method: str,
        headers: dict[str, str],
        data: bytes | None,
    ) -> tuple[int, str, str]:
        request_headers = headers.copy()
        req = urlrequest.Request(url, data=data, headers=request_headers, method=method)

        try:
            with urlrequest.urlopen(req, timeout=15) as resp:
                status = resp.status
                content_type = resp.headers.get("Content-Type", "") or ""
                body_bytes = resp.read()
        except urlerror.HTTPError as exc:
            status = exc.code
            content_type = exc.headers.get("Content-Type", "") if exc.headers else ""
            body_bytes = exc.read() if exc.fp else b""
        except Exception as exc:  # pragma: no cover - defensive
            log_scraper_event(
                service="Twitter",
                event="http_request_exception",
                level=logging.WARNING,
                metric="scrape_transient_http",
                endpoint=url,
                status=599,
                content_type="",
                retryable=True,
                token_refreshed=False,
                error=str(exc),
            )
            return 599, "", ""

        body_text = body_bytes.decode("utf-8", errors="ignore") if body_bytes else ""
        return status, content_type, body_text

    def _normalize_tweet_result(self, tweet_result: dict[str, Any]) -> dict[str, Any] | None:
        """Normalize tweet payloads from GraphQL responses into a consistent shape."""
        if not tweet_result or not isinstance(tweet_result, dict):
            return None

        current: dict[str, Any] | None = tweet_result
        max_depth = 6
        depth = 0

        while isinstance(current, dict) and depth < max_depth:
            typename = current.get("__typename")
            if typename == "TweetTombstone":
                return None

            if "tweet" in current and isinstance(current["tweet"], dict):
                current = current["tweet"]
                depth += 1
                continue

            if "result" in current and isinstance(current["result"], dict):
                current = current["result"]
                depth += 1
                continue

            break

        if not isinstance(current, dict):
            return None

        legacy_data = current.get("legacy")
        if not isinstance(legacy_data, dict):
            if "full_text" in current or "text" in current:
                legacy_data = current
            else:
                return None

        core_data = current.get("core", {})
        user_results = core_data.get("user_results", {}).get("result", {})
        if isinstance(user_results, dict) and "legacy" in user_results:
            user_data = user_results["legacy"]
        elif isinstance(user_results, dict):
            user_data = user_results
        else:
            user_data = {}

        if not user_data and isinstance(legacy_data.get("user"), dict):
            user_data = legacy_data["user"]

        if not user_data and isinstance(current.get("author"), dict):
            user_data = current["author"].get("legacy", {}) or current["author"].get("result", {})

        rest_id = current.get("rest_id") or legacy_data.get("id_str") or legacy_data.get("id")

        return {
            "legacy": legacy_data,
            "core": core_data,
            "user": user_data,
            "rest_id": rest_id,
        }

    def _extract_external_links(self, legacy_data: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract expanded external links from tweet entities."""

        if not isinstance(legacy_data, dict):
            return []

        urls = legacy_data.get("entities", {}).get("urls", [])
        links: list[dict[str, Any]] = []

        for url_info in urls:
            if not isinstance(url_info, dict):
                continue
            expanded = (
                url_info.get("expanded_url") or url_info.get("unwound_url") or url_info.get("url")
            )
            if not expanded:
                continue
            normalized = self._normalize_external_url(expanded)
            if not normalized:
                continue
            links.append(
                {
                    "url": url_info.get("url"),
                    "expanded_url": normalized,
                    "display_url": url_info.get("display_url"),
                }
            )

        return links

    def _normalize_external_url(self, url: str) -> str | None:
        try:
            parsed = urlparse(url)
        except Exception:
            return None

        if not parsed.netloc:
            return None

        domain = parsed.netloc.lower()
        if domain.endswith("twitter.com") or domain.endswith("t.co"):
            return None

        scheme = parsed.scheme or "https"
        normalized = parsed._replace(scheme=scheme)
        url_str = normalized.geturl()
        if url_str.startswith("http://"):
            url_str = "https://" + url_str[len("http://") :]
        return url_str

    def _extract_domain(self, url: str) -> str:
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()
            if domain.startswith("www."):
                domain = domain[4:]
            return domain
        except Exception:
            return ""

    def _parse_tweet_date(self, date_str: str) -> datetime | None:
        """Parse Twitter's date format to datetime."""
        if not date_str:
            return None

        try:
            # Twitter date format: "Wed Oct 05 20:17:27 +0000 2022"
            return datetime.strptime(date_str, "%a %b %d %H:%M:%S %z %Y")
        except Exception:
            pass

        try:
            iso_candidate = date_str.replace("Z", "+00:00")
            return datetime.fromisoformat(iso_candidate)
        except Exception:
            return None
