//! Scheduled source fetchers for the native Rust scrape worker.
//!
//! Provider work is deliberately database-free. Callers pass immutable source snapshots and
//! receive owned items which can be published later under the queue lease fence.

use std::collections::BTreeSet;
use std::env;
use std::sync::Arc;
use std::time::Duration;

use chrono::{DateTime, Utc};
use futures_util::StreamExt;
use newsly_extraction::PublicUrl;
use reqwest::{StatusCode, Url};
use scraper::{ElementRef, Html, Selector};
use secrecy::{ExposeSecret, SecretString};
use serde::Deserialize;
use serde_json::{Value, json};
use thiserror::Error;

use crate::feed_validation::entry_audio_url;

const MAX_RESPONSE_BYTES: usize = 20 * 1024 * 1024;
const MAX_REDIRECTS: usize = 5;
const MAX_HN_CONCURRENCY: usize = 8;
const DEFAULT_USER_AGENT: &str = "newsly-scraper/2.0 (+https://newsly.app)";

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum AggregatorKey {
    HackerNews,
    Techmeme,
    Mediagazer,
    Memeorandum,
    SciUrls,
    FinUrls,
    Brutalist,
}

impl AggregatorKey {
    pub const ALL: [Self; 7] = [
        Self::HackerNews,
        Self::Techmeme,
        Self::Mediagazer,
        Self::Memeorandum,
        Self::SciUrls,
        Self::FinUrls,
        Self::Brutalist,
    ];

    pub const fn as_str(self) -> &'static str {
        match self {
            Self::HackerNews => "hackernews",
            Self::Techmeme => "techmeme",
            Self::Mediagazer => "mediagazer",
            Self::Memeorandum => "memeorandum",
            Self::SciUrls => "sciurls",
            Self::FinUrls => "finurls",
            Self::Brutalist => "brutalist",
        }
    }

    pub const fn display_name(self) -> &'static str {
        match self {
            Self::HackerNews => "Hacker News",
            Self::Techmeme => "Techmeme",
            Self::Mediagazer => "Mediagazer",
            Self::Memeorandum => "Memeorandum",
            Self::SciUrls => "SciURLs",
            Self::FinUrls => "FinURLs",
            Self::Brutalist => "Brutalist Report",
        }
    }

    pub fn parse(value: &str) -> Option<Self> {
        let normalized = value
            .chars()
            .filter(char::is_ascii_alphanumeric)
            .flat_map(char::to_lowercase)
            .collect::<String>();
        match normalized.as_str() {
            "hackernews" | "hn" => Some(Self::HackerNews),
            "techmeme" => Some(Self::Techmeme),
            "mediagazer" => Some(Self::Mediagazer),
            "memeorandum" => Some(Self::Memeorandum),
            "sciurls" => Some(Self::SciUrls),
            "finurls" => Some(Self::FinUrls),
            "brutalist" | "brutalistreport" => Some(Self::Brutalist),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FeedScrapeTarget {
    pub config_id: i64,
    pub user_id: i64,
    pub scraper_type: String,
    pub display_name: Option<String>,
    pub feed_url: String,
    pub limit: usize,
    pub fingerprint: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RedditScrapeTarget {
    pub config_id: i64,
    pub user_id: i64,
    pub subreddit: String,
    pub limit: usize,
    pub fingerprint: String,
}

#[derive(Debug, Clone, PartialEq)]
pub enum ScrapedItem {
    News(Box<ScrapedNewsItem>),
    Content(Box<ScrapedContentItem>),
}

#[derive(Debug, Clone, PartialEq)]
pub struct ScrapedNewsItem {
    pub url: String,
    pub title: Option<String>,
    pub visibility_scope: String,
    pub owner_user_id: Option<i64>,
    pub platform: String,
    pub source_type: String,
    pub source_label: Option<String>,
    pub source_external_id: Option<String>,
    pub user_scraper_config_id: Option<i64>,
    pub canonical_item_url: Option<String>,
    pub canonical_story_url: Option<String>,
    pub article_url: Option<String>,
    pub article_domain: Option<String>,
    pub discussion_url: Option<String>,
    pub summary_key_points: Vec<String>,
    pub summary_text: Option<String>,
    pub raw_metadata: Value,
    pub status: String,
    pub published_at: Option<DateTime<Utc>>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ScrapedContentItem {
    pub url: String,
    pub source_url: String,
    pub title: Option<String>,
    pub content_type: String,
    pub user_id: i64,
    pub source: Option<String>,
    pub platform: String,
    pub metadata: Value,
    pub published_at: Option<DateTime<Utc>>,
    pub config_id: i64,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ScrapeProviderOutcome {
    pub items: Vec<ScrapedItem>,
    pub item_errors: Vec<String>,
}

impl ScrapeProviderOutcome {
    fn new(items: Vec<ScrapedItem>, item_errors: Vec<String>) -> Self {
        Self { items, item_errors }
    }
}

#[derive(Debug, Clone)]
pub struct ScrapeGateway {
    client: reqwest::Client,
    reddit_client_id: Option<SecretString>,
    reddit_client_secret: Option<SecretString>,
    reddit_user_agent: String,
}

impl ScrapeGateway {
    /// Creates the scraper provider gateway from environment configuration.
    ///
    /// # Errors
    ///
    /// Returns an error when the bounded HTTP client cannot be constructed.
    pub fn from_env() -> Result<Self, ScrapeGatewayError> {
        let timeout =
            Duration::from_secs(env_u64("SCRAPER_HTTP_TIMEOUT_SECONDS", 30).clamp(5, 120));
        let client = reqwest::Client::builder()
            .connect_timeout(Duration::from_secs(15))
            .timeout(timeout)
            .redirect(reqwest::redirect::Policy::none())
            .no_proxy()
            .build()?;
        Ok(Self {
            client,
            reddit_client_id: secret_env("REDDIT_CLIENT_ID"),
            reddit_client_secret: secret_env("REDDIT_CLIENT_SECRET"),
            reddit_user_agent: clean_env("REDDIT_USER_AGENT")
                .unwrap_or_else(|| DEFAULT_USER_AGENT.to_owned()),
        })
    }

    /// Fetches and normalizes one supported public aggregator.
    ///
    /// # Errors
    ///
    /// Returns an error when the source cannot be fetched, parsed, or normalized.
    pub async fn fetch_aggregator(
        &self,
        key: AggregatorKey,
    ) -> Result<ScrapeProviderOutcome, ScrapeGatewayError> {
        match key {
            AggregatorKey::HackerNews => self.fetch_hacker_news().await,
            AggregatorKey::Techmeme => {
                self.fetch_rss_cluster(key, "https://www.techmeme.com/feed.xml", 25, 6)
                    .await
            }
            AggregatorKey::Mediagazer => {
                self.fetch_rss_cluster(key, "https://www.mediagazer.com/feed.xml", 25, 6)
                    .await
            }
            AggregatorKey::Memeorandum => {
                self.fetch_rss_cluster(key, "https://www.memeorandum.com/feed.xml", 25, 6)
                    .await
            }
            AggregatorKey::SciUrls => {
                self.fetch_html_grouped(key, "https://sciurls.com", 100)
                    .await
            }
            AggregatorKey::FinUrls => {
                self.fetch_html_grouped(key, "https://finurls.com", 100)
                    .await
            }
            AggregatorKey::Brutalist => self.fetch_brutalist().await,
        }
    }

    /// Fetches and normalizes one configured RSS, Atom, Substack, or podcast feed.
    ///
    /// # Errors
    ///
    /// Returns an error when the source cannot be fetched, parsed, or normalized.
    pub async fn fetch_feed(
        &self,
        target: &FeedScrapeTarget,
    ) -> Result<ScrapeProviderOutcome, ScrapeGatewayError> {
        let bytes = self.fetch_public_bytes(&target.feed_url, None).await?;
        normalize_feed_document(target, bytes.as_slice())
    }

    /// Fetches all configured Reddit targets with one shared application access token.
    ///
    /// # Errors
    ///
    /// Returns an error when credentials are unavailable or token acquisition fails. Per-target
    /// scrape failures are returned alongside their configuration identifiers.
    pub async fn fetch_reddit_targets(
        &self,
        targets: &[RedditScrapeTarget],
    ) -> Result<Vec<(i64, Result<ScrapeProviderOutcome, String>)>, ScrapeGatewayError> {
        if targets.is_empty() {
            return Ok(Vec::new());
        }
        let token = self.reddit_access_token().await?;
        let mut outcomes = Vec::with_capacity(targets.len());
        for target in targets {
            let result = self
                .fetch_subreddit(target, &token)
                .await
                .map_err(|error| error.to_string());
            outcomes.push((target.config_id, result));
        }
        Ok(outcomes)
    }

    async fn fetch_hacker_news(&self) -> Result<ScrapeProviderOutcome, ScrapeGatewayError> {
        let story_ids = self
            .client
            .get("https://hacker-news.firebaseio.com/v0/topstories.json")
            .header(reqwest::header::USER_AGENT, DEFAULT_USER_AGENT)
            .send()
            .await?
            .error_for_status()?
            .json::<Vec<i64>>()
            .await?;
        let gateway = Arc::new(self.clone());
        let results = futures_util::stream::iter(story_ids.into_iter().take(15))
            .map(|story_id| {
                let gateway = Arc::clone(&gateway);
                async move {
                    let result = gateway
                        .client
                        .get(format!(
                            "https://hacker-news.firebaseio.com/v0/item/{story_id}.json"
                        ))
                        .header(reqwest::header::USER_AGENT, DEFAULT_USER_AGENT)
                        .send()
                        .await?
                        .error_for_status()?
                        .json::<HackerNewsStory>()
                        .await;
                    Ok::<_, reqwest::Error>((story_id, result))
                }
            })
            .buffer_unordered(MAX_HN_CONCURRENCY)
            .collect::<Vec<_>>()
            .await;
        let mut items = Vec::new();
        let mut errors = Vec::new();
        for result in results {
            let (story_id, story) = match result {
                Ok((story_id, Ok(story))) => (story_id, story),
                Ok((story_id, Err(error))) => {
                    errors.push(format!("HN story {story_id}: {error}"));
                    continue;
                }
                Err(error) => {
                    errors.push(format!("HN story request: {error}"));
                    continue;
                }
            };
            if story.kind.as_deref() != Some("story") {
                continue;
            }
            let Some(article_url) = story.url.as_deref().and_then(normalize_http_url) else {
                continue;
            };
            let discussion_url = format!("https://news.ycombinator.com/item?id={story_id}");
            let domain = domain_of(&article_url);
            let metadata = json!({
                "platform": "hackernews",
                "source": domain,
                "article": {
                    "url": article_url,
                    "title": story.title,
                    "source_domain": domain,
                },
                "aggregator": {
                    "key": "hackernews",
                    "name": "Hacker News",
                    "title": story.title,
                    "external_id": story_id.to_string(),
                    "author": story.by,
                    "metadata": {
                        "score": story.score.unwrap_or(0),
                        "comments_count": story.descendants.unwrap_or(0),
                        "item_type": story.kind,
                        "timestamp": story.time,
                        "hn_linked_url": article_url,
                    },
                },
                "discussion_url": discussion_url,
                "excerpt": story.text,
                "discovery_time": Utc::now().to_rfc3339(),
                "comment_count": story.descendants.unwrap_or(0),
            });
            items.push(ScrapedItem::News(Box::new(news_item(NewsItemInput {
                key: AggregatorKey::HackerNews,
                article_url,
                title: story.title,
                external_id: Some(story_id.to_string()),
                discussion_url: Some(discussion_url),
                owner_user_id: None,
                published_at: None,
                raw_metadata: metadata,
            }))));
        }
        Ok(ScrapeProviderOutcome::new(items, errors))
    }

    async fn fetch_rss_cluster(
        &self,
        key: AggregatorKey,
        feed_url: &str,
        limit: usize,
        max_related: usize,
    ) -> Result<ScrapeProviderOutcome, ScrapeGatewayError> {
        let bytes = self
            .fetch_public_bytes(feed_url, Some(MAX_RESPONSE_BYTES))
            .await?;
        let feed = feed_rs::parser::parse(bytes.as_slice())
            .map_err(|error| ScrapeGatewayError::Feed(error.to_string()))?;
        let feed_title = feed
            .title
            .and_then(|value| clean(Some(value.content)))
            .unwrap_or_else(|| key.display_name().to_owned());
        let anchor_selector = selector("a[href]")?;
        let mut items = Vec::new();
        let mut errors = Vec::new();
        for entry in feed.entries.into_iter().take(limit) {
            let permalink = entry_html_url(&entry).unwrap_or_else(|| entry.id.clone());
            let description = entry
                .summary
                .as_ref()
                .map(|value| value.content.clone())
                .or_else(|| entry.content.as_ref().and_then(|value| value.body.clone()))
                .unwrap_or_default();
            let fragment = Html::parse_fragment(&description);
            let anchors = cluster_anchors(&fragment, &anchor_selector);
            let primary = anchors
                .iter()
                .find(|(url, _)| !host_matches(url, key.as_str()))
                .cloned();
            let Some((article_url, anchor_title)) = primary else {
                errors.push(format!("{} cluster has no external primary link", entry.id));
                continue;
            };
            let domain = domain_of(&article_url);
            let headline = entry
                .title
                .as_ref()
                .and_then(|value| clean(Some(value.content.clone())))
                .or(anchor_title)
                .unwrap_or_else(|| article_url.clone());
            let source_name = anchors
                .iter()
                .find(|(url, text)| {
                    url != &article_url && domain_of(url) == domain && text.is_some()
                })
                .and_then(|(_, text)| text.clone())
                .unwrap_or_else(|| domain.clone().unwrap_or_default());
            let related = cluster_related_links(&anchors, &article_url, key, max_related);
            let published_at = entry.published.or(entry.updated);
            let cluster_token = clean(Some(entry.id.clone())).or_else(|| {
                Url::parse(&permalink)
                    .ok()
                    .and_then(|url| url.path_segments()?.next_back().map(ToOwned::to_owned))
            });
            let summary_text = clean(Some(
                fragment.root_element().text().collect::<Vec<_>>().join(" "),
            ));
            let metadata = json!({
                "platform": key.as_str(),
                "source": domain,
                "article": {
                    "url": article_url,
                    "title": headline,
                    "source_domain": domain,
                },
                "aggregator": {
                    "key": key.as_str(),
                    "name": key.display_name(),
                    "title": headline,
                    "external_id": cluster_token,
                    "metadata": {
                        "summary_text": summary_text,
                        "related_links": related,
                        "comments_count": related.len(),
                        "feed_name": feed_title,
                        "source_name": source_name,
                    },
                },
                "discussion_url": permalink,
                "excerpt": summary_text,
                "discovery_time": published_at.unwrap_or_else(Utc::now).to_rfc3339(),
            });
            items.push(ScrapedItem::News(Box::new(news_item(NewsItemInput {
                key,
                article_url,
                title: Some(headline),
                external_id: cluster_token,
                discussion_url: normalize_http_url(&permalink),
                owner_user_id: None,
                published_at,
                raw_metadata: metadata,
            }))));
        }
        Ok(ScrapeProviderOutcome::new(items, errors))
    }

    async fn fetch_html_grouped(
        &self,
        key: AggregatorKey,
        url: &str,
        limit: usize,
    ) -> Result<ScrapeProviderOutcome, ScrapeGatewayError> {
        let bytes = self
            .fetch_public_bytes(url, Some(MAX_RESPONSE_BYTES))
            .await?;
        let document = Html::parse_document(std::str::from_utf8(&bytes).map_err(|error| {
            ScrapeGatewayError::Html(format!("{} returned non-UTF-8 HTML: {error}", key.as_str()))
        })?);
        let block_selector = selector(".publisher-block")?;
        let header_selector = selector(".publisher-header")?;
        let title_selector = selector(".publisher-text .title .primary")?;
        let source_link_selector = selector("a.icon-container[href]")?;
        let article_selector = selector(".publisher-link a.article-link[href]")?;
        let base = Url::parse(url).map_err(|error| ScrapeGatewayError::Url(error.to_string()))?;
        let mut seen = BTreeSet::new();
        let mut items = Vec::new();
        for block in document.select(&block_selector) {
            let header = block.select(&header_selector).next();
            let source_name = header
                .and_then(|header| header.select(&title_selector).next())
                .and_then(|title| clean(Some(title.text().collect::<Vec<_>>().join(" "))));
            let source_url = header
                .and_then(|header| header.select(&source_link_selector).next())
                .and_then(|link| link.value().attr("href"))
                .and_then(|href| base.join(href).ok())
                .map(|url| url.to_string());
            for anchor in block.select(&article_selector) {
                let Some(href) = anchor.value().attr("href") else {
                    continue;
                };
                let Some(article_url) = base
                    .join(href)
                    .ok()
                    .and_then(|url| normalize_http_url(url.as_str()))
                else {
                    continue;
                };
                if domain_of(&article_url) == domain_of(url) || !seen.insert(article_url.clone()) {
                    continue;
                }
                let Some(title) = clean(Some(anchor.text().collect::<Vec<_>>().join(" "))) else {
                    continue;
                };
                let domain = domain_of(&article_url);
                let display_source = source_name.clone().or_else(|| domain.clone());
                let metadata = json!({
                    "platform": key.as_str(),
                    "source": display_source,
                    "article": {"url": article_url, "title": title, "source_domain": domain},
                    "aggregator": {
                        "key": key.as_str(),
                        "name": key.display_name(),
                        "metadata": {"source_name": source_name, "source_url": source_url},
                    },
                    "discussion_url": null,
                    "excerpt": null,
                    "discovery_time": Utc::now().to_rfc3339(),
                });
                items.push(ScrapedItem::News(Box::new(news_item(NewsItemInput {
                    key,
                    article_url,
                    title: Some(title),
                    external_id: None,
                    discussion_url: None,
                    owner_user_id: None,
                    published_at: None,
                    raw_metadata: metadata,
                }))));
                if items.len() >= limit {
                    return Ok(ScrapeProviderOutcome::new(items, Vec::new()));
                }
            }
        }
        if items.is_empty() {
            return Err(ScrapeGatewayError::Html(format!(
                "{} returned no parseable items",
                key.as_str()
            )));
        }
        Ok(ScrapeProviderOutcome::new(items, Vec::new()))
    }

    async fn fetch_brutalist(&self) -> Result<ScrapeProviderOutcome, ScrapeGatewayError> {
        let topics = ["science", "business", "politics", "sports"];
        let mut items = Vec::new();
        let mut errors = Vec::new();
        let mut seen = BTreeSet::new();
        for topic in topics {
            let topic_url = format!("https://brutalist.report/topic/{topic}?limit=25&hours=24");
            let bytes = match self
                .fetch_public_bytes(&topic_url, Some(MAX_RESPONSE_BYTES))
                .await
            {
                Ok(bytes) => bytes,
                Err(error) => {
                    errors.push(format!("{topic}: {error}"));
                    continue;
                }
            };
            let html = match std::str::from_utf8(&bytes) {
                Ok(html) => html,
                Err(error) => {
                    errors.push(format!("{topic}: non-UTF-8 HTML: {error}"));
                    continue;
                }
            };
            let document = Html::parse_document(html);
            let heading_selector = selector("h3, h4")?;
            let item_selector = selector("li a[href]")?;
            for heading in document.select(&heading_selector) {
                let source_name = clean(Some(heading.text().collect::<Vec<_>>().join(" ")))
                    .map(|value| value.replace("[rss]", "").trim().to_owned());
                let Some(list) = next_element_matching(heading, "ul") else {
                    continue;
                };
                for anchor in list.select(&item_selector) {
                    let Some(href) = anchor.value().attr("href") else {
                        continue;
                    };
                    let Some(article_url) = Url::parse(&topic_url)
                        .ok()
                        .and_then(|base| base.join(href).ok())
                        .and_then(|url| normalize_http_url(url.as_str()))
                    else {
                        continue;
                    };
                    if host_matches(&article_url, "brutalist.report")
                        || !seen.insert(article_url.clone())
                    {
                        continue;
                    }
                    let Some(title) = clean(Some(anchor.text().collect::<Vec<_>>().join(" ")))
                    else {
                        continue;
                    };
                    let domain = domain_of(&article_url);
                    let display_source = source_name.clone().or_else(|| domain.clone());
                    let metadata = json!({
                        "platform": "brutalist",
                        "source": display_source,
                        "article": {"url": article_url, "title": title, "source_domain": domain},
                        "aggregator": {
                            "key": "brutalist",
                            "name": "Brutalist Report",
                            "topic": topic,
                            "metadata": {"source_name": source_name, "topic_url": topic_url},
                        },
                        "discussion_url": null,
                        "excerpt": null,
                        "discovery_time": Utc::now().to_rfc3339(),
                    });
                    items.push(ScrapedItem::News(Box::new(news_item(NewsItemInput {
                        key: AggregatorKey::Brutalist,
                        article_url,
                        title: Some(title),
                        external_id: None,
                        discussion_url: None,
                        owner_user_id: None,
                        published_at: None,
                        raw_metadata: metadata,
                    }))));
                }
            }
        }
        if items.is_empty() && !errors.is_empty() {
            return Err(ScrapeGatewayError::Html(errors.join("; ")));
        }
        Ok(ScrapeProviderOutcome::new(items, errors))
    }

    async fn reddit_access_token(&self) -> Result<String, ScrapeGatewayError> {
        let client_id = self
            .reddit_client_id
            .as_ref()
            .ok_or(ScrapeGatewayError::RedditNotConfigured)?;
        let client_secret = self
            .reddit_client_secret
            .as_ref()
            .ok_or(ScrapeGatewayError::RedditNotConfigured)?;
        let response = self
            .client
            .post("https://www.reddit.com/api/v1/access_token")
            .basic_auth(
                client_id.expose_secret(),
                Some(client_secret.expose_secret()),
            )
            .header(reqwest::header::USER_AGENT, &self.reddit_user_agent)
            .form(&[("grant_type", "client_credentials")])
            .send()
            .await?
            .error_for_status()?
            .json::<RedditTokenResponse>()
            .await?;
        clean(Some(response.access_token)).ok_or(ScrapeGatewayError::RedditTokenMissing)
    }

    async fn fetch_subreddit(
        &self,
        target: &RedditScrapeTarget,
        access_token: &str,
    ) -> Result<ScrapeProviderOutcome, ScrapeGatewayError> {
        let response = self
            .client
            .get(format!(
                "https://oauth.reddit.com/r/{}/new.json",
                target.subreddit
            ))
            .query(&[
                ("limit", target.limit.clamp(1, 100).to_string()),
                ("raw_json", "1".to_owned()),
            ])
            .bearer_auth(access_token)
            .header(reqwest::header::USER_AGENT, &self.reddit_user_agent)
            .send()
            .await?
            .error_for_status()?
            .json::<RedditListing>()
            .await?;
        let mut items = Vec::new();
        for child in response.data.children {
            let post = child.data;
            if post.title.trim().is_empty() || post.removed_by_category.is_some() {
                continue;
            }
            let discussion_url = format!("https://www.reddit.com{}", post.permalink);
            let is_self = post.is_self;
            let url = if is_self {
                normalize_http_url(&discussion_url)
            } else {
                post.url
                    .as_deref()
                    .filter(|url| is_external_reddit_url(url))
                    .and_then(normalize_http_url)
            };
            let Some(url) = url else {
                continue;
            };
            let subreddit =
                clean(Some(post.subreddit.clone())).unwrap_or_else(|| target.subreddit.clone());
            let author = clean(post.author.clone());
            let selftext = clean(post.selftext.clone());
            let summary_text = selftext.clone().or_else(|| Some(post.title.clone()));
            let raw_metadata = reddit_raw_metadata(
                &post,
                &url,
                &discussion_url,
                &subreddit,
                author.as_deref(),
                selftext.as_deref(),
            );
            items.push(ScrapedItem::News(Box::new(ScrapedNewsItem {
                url: url.clone(),
                title: Some(post.title.clone()),
                visibility_scope: "user".to_owned(),
                owner_user_id: Some(target.user_id),
                platform: "reddit".to_owned(),
                source_type: "user_reddit".to_owned(),
                source_label: Some(subreddit),
                source_external_id: Some(post.id),
                user_scraper_config_id: Some(target.config_id),
                canonical_item_url: normalize_http_url(&discussion_url),
                canonical_story_url: Some(url.clone()),
                article_url: Some(url.clone()),
                article_domain: domain_of(&url),
                discussion_url: normalize_http_url(&discussion_url),
                summary_key_points: if is_self {
                    summary_text
                        .as_deref()
                        .map(|value| value.chars().take(220).collect())
                        .into_iter()
                        .collect()
                } else {
                    Vec::new()
                },
                summary_text: if is_self {
                    summary_text.map(|value| value.chars().take(500).collect())
                } else {
                    None
                },
                raw_metadata,
                status: if is_self { "ready" } else { "new" }.to_owned(),
                published_at: post.created_utc.and_then(timestamp_from_seconds),
            })));
        }
        Ok(ScrapeProviderOutcome::new(items, Vec::new()))
    }

    async fn fetch_public_bytes(
        &self,
        source_url: &str,
        max_response_bytes: Option<usize>,
    ) -> Result<Vec<u8>, ScrapeGatewayError> {
        let mut current = PublicUrl::parse(source_url)?;
        for redirect_count in 0..=MAX_REDIRECTS {
            current.validate_dns().await?;
            let response = self
                .client
                .get(current.as_url().clone())
                .header(reqwest::header::USER_AGENT, DEFAULT_USER_AGENT)
                .header(
                    reqwest::header::ACCEPT,
                    "application/rss+xml, application/atom+xml, application/xml, text/xml, text/html;q=0.9, */*;q=0.1",
                )
                .send()
                .await?;
            if response.status().is_redirection() {
                if redirect_count == MAX_REDIRECTS {
                    return Err(ScrapeGatewayError::TooManyRedirects);
                }
                let location = response
                    .headers()
                    .get(reqwest::header::LOCATION)
                    .and_then(|value| value.to_str().ok())
                    .ok_or(ScrapeGatewayError::RedirectLocationMissing)?;
                let resolved = current
                    .as_url()
                    .join(location)
                    .map_err(|error| ScrapeGatewayError::Url(error.to_string()))?;
                current = PublicUrl::parse(resolved.as_str())?;
                continue;
            }
            let response = response.error_for_status()?;
            if response
                .content_length()
                .is_some_and(|length| exceeds_response_limit(length, max_response_bytes))
            {
                return Err(ScrapeGatewayError::ResponseTooLarge);
            }
            let mut body = Vec::new();
            let mut stream = response.bytes_stream();
            while let Some(chunk) = stream.next().await {
                let chunk = chunk?;
                let next_size = body.len().saturating_add(chunk.len()) as u64;
                if exceeds_response_limit(next_size, max_response_bytes) {
                    return Err(ScrapeGatewayError::ResponseTooLarge);
                }
                body.extend_from_slice(&chunk);
            }
            return Ok(body);
        }
        Err(ScrapeGatewayError::TooManyRedirects)
    }
}

fn exceeds_response_limit(size: u64, max_response_bytes: Option<usize>) -> bool {
    max_response_bytes.is_some_and(|limit| size > limit as u64)
}

fn normalize_feed_document(
    target: &FeedScrapeTarget,
    bytes: &[u8],
) -> Result<ScrapeProviderOutcome, ScrapeGatewayError> {
    let feed = feed_rs::parser::parse(bytes)
        .map_err(|error| ScrapeGatewayError::Feed(error.to_string()))?;
    let feed_title = feed
        .title
        .as_ref()
        .and_then(|title| clean(Some(title.content.clone())))
        .or_else(|| target.display_name.clone());
    let feed_description = feed
        .description
        .as_ref()
        .and_then(|value| clean(Some(value.content.clone())));
    let mut items = Vec::new();
    let mut errors = Vec::new();
    let mut seen = BTreeSet::new();
    for entry in feed.entries.into_iter().take(target.limit.clamp(1, 100)) {
        let title = entry
            .title
            .as_ref()
            .and_then(|value| clean(Some(value.content.clone())));
        let published_at = entry.published.or(entry.updated);
        let author = entry
            .authors
            .first()
            .and_then(|person| clean(Some(person.name.clone())));
        let body = entry
            .content
            .as_ref()
            .and_then(|content| clean(content.body.clone()))
            .or_else(|| {
                entry
                    .summary
                    .as_ref()
                    .and_then(|summary| clean(Some(summary.content.clone())))
            });
        let audio_url = entry_audio_url(&entry);
        if target.scraper_type == "podcast_rss" && audio_url.is_none() {
            errors.push(format!(
                "{}: podcast entry has no usable audio enclosure",
                entry.id
            ));
            continue;
        }
        let url = if target.scraper_type == "podcast_rss" {
            entry_html_url(&entry).or_else(|| audio_url.clone())
        } else {
            entry_html_url(&entry)
        };
        let Some(url) = url.and_then(|value| normalize_http_url(&value)) else {
            errors.push(format!("{}: feed entry has no usable URL", entry.id));
            continue;
        };
        if !seen.insert(url.clone()) {
            continue;
        }
        let platform = match target.scraper_type.as_str() {
            "podcast_rss" => "podcast",
            "substack" => "substack",
            _ => "atom",
        };
        let content_type = if target.scraper_type == "podcast_rss" {
            "podcast"
        } else {
            "article"
        };
        let source = target.display_name.clone().or_else(|| feed_title.clone());
        let metadata = json!({
            "platform": platform,
            "source": source,
            "source_domain": domain_of(&url),
            "feed_url": target.feed_url,
            "feed_config_id": target.config_id,
            "feed_name": feed_title,
            "feed_description": feed_description,
            "author": author,
            "publication_date": published_at.map(|value| value.to_rfc3339()),
            "rss_content": body,
            "audio_url": audio_url,
            "entry_id": entry.id,
        });
        items.push(ScrapedItem::Content(Box::new(ScrapedContentItem {
            url: url.clone(),
            source_url: url,
            title,
            content_type: content_type.to_owned(),
            user_id: target.user_id,
            source,
            platform: platform.to_owned(),
            metadata,
            published_at,
            config_id: target.config_id,
        })));
    }
    Ok(ScrapeProviderOutcome::new(items, errors))
}

fn cluster_anchors(fragment: &Html, selector: &Selector) -> Vec<(String, Option<String>)> {
    fragment
        .select(selector)
        .filter_map(|anchor| {
            let href = anchor.value().attr("href")?.trim();
            normalize_http_url(href).map(|url| {
                let text = clean(Some(anchor.text().collect::<Vec<_>>().join(" ")));
                (url, text)
            })
        })
        .collect()
}

fn cluster_related_links(
    anchors: &[(String, Option<String>)],
    article_url: &str,
    key: AggregatorKey,
    max_related: usize,
) -> Vec<Value> {
    let mut seen = BTreeSet::from([article_url.to_owned()]);
    anchors
        .iter()
        .filter(|(url, _)| seen.insert(url.clone()) && !host_matches(url, key.as_str()))
        .take(max_related)
        .map(|(url, title)| {
            json!({
                "url": url,
                "title": title,
                "source": domain_of(url),
            })
        })
        .collect()
}

fn reddit_raw_metadata(
    post: &RedditPost,
    url: &str,
    discussion_url: &str,
    subreddit: &str,
    author: Option<&str>,
    selftext: Option<&str>,
) -> Value {
    json!({
        "platform": "reddit",
        "source": subreddit,
        "source_type": "user_reddit",
        "source_label": subreddit,
        "article": {"url": url, "title": &post.title, "source_domain": domain_of(url)},
        "aggregator": {
            "name": "Reddit",
            "title": &post.title,
            "external_id": &post.id,
            "author": author,
            "metadata": {
                "score": post.score,
                "comments_count": post.num_comments,
                "upvote_ratio": post.upvote_ratio,
                "subreddit": subreddit,
                "over_18": post.over_18,
            },
        },
        "discussion_url": discussion_url,
        "excerpt": selftext,
        "discovery_time": Utc::now().to_rfc3339(),
        "scraped_at": Utc::now().to_rfc3339(),
        "comment_count": post.num_comments,
    })
}

struct NewsItemInput {
    key: AggregatorKey,
    article_url: String,
    title: Option<String>,
    external_id: Option<String>,
    discussion_url: Option<String>,
    owner_user_id: Option<i64>,
    published_at: Option<DateTime<Utc>>,
    raw_metadata: Value,
}

fn news_item(input: NewsItemInput) -> ScrapedNewsItem {
    let NewsItemInput {
        key,
        article_url,
        title,
        external_id,
        discussion_url,
        owner_user_id,
        published_at,
        raw_metadata,
    } = input;
    let domain = domain_of(&article_url);
    ScrapedNewsItem {
        url: article_url.clone(),
        title,
        visibility_scope: if owner_user_id.is_some() {
            "user"
        } else {
            "global"
        }
        .to_owned(),
        owner_user_id,
        platform: key.as_str().to_owned(),
        source_type: key.display_name().to_owned(),
        source_label: domain
            .clone()
            .or_else(|| Some(key.display_name().to_owned())),
        source_external_id: external_id,
        user_scraper_config_id: None,
        canonical_item_url: discussion_url.clone().or_else(|| Some(article_url.clone())),
        canonical_story_url: Some(article_url.clone()),
        article_url: Some(article_url),
        article_domain: domain,
        discussion_url,
        summary_key_points: Vec::new(),
        summary_text: None,
        raw_metadata,
        status: "new".to_owned(),
        published_at,
    }
}

fn entry_html_url(entry: &feed_rs::model::Entry) -> Option<String> {
    entry
        .links
        .iter()
        .find(|link| {
            link.rel.as_deref().is_none_or(|rel| rel == "alternate")
                && link
                    .media_type
                    .as_deref()
                    .is_none_or(|kind| kind.contains("html"))
        })
        .or_else(|| {
            entry
                .links
                .iter()
                .find(|link| link.rel.as_deref() != Some("enclosure"))
        })
        .map(|link| link.href.clone())
}

fn next_element_matching<'a>(element: ElementRef<'a>, tag: &str) -> Option<ElementRef<'a>> {
    let mut sibling = element.next_sibling();
    while let Some(node) = sibling {
        if let Some(candidate) = ElementRef::wrap(node) {
            if candidate.value().name() == tag {
                return Some(candidate);
            }
            if matches!(candidate.value().name(), "h3" | "h4") {
                return None;
            }
        }
        sibling = node.next_sibling();
    }
    None
}

fn selector(value: &str) -> Result<Selector, ScrapeGatewayError> {
    Selector::parse(value).map_err(|error| ScrapeGatewayError::Html(error.to_string()))
}

fn normalize_http_url(value: &str) -> Option<String> {
    let mut url = Url::parse(value.trim()).ok()?;
    if !matches!(url.scheme(), "http" | "https") || url.host_str().is_none() {
        return None;
    }
    if url.scheme() == "http" {
        let _ = url.set_scheme("https");
    }
    url.set_fragment(None);
    Some(url.to_string())
}

fn domain_of(value: &str) -> Option<String> {
    Url::parse(value)
        .ok()
        .and_then(|url| url.host_str().map(ToOwned::to_owned))
        .map(|host| {
            host.strip_prefix("www.")
                .unwrap_or(&host)
                .to_ascii_lowercase()
        })
}

fn host_matches(value: &str, expected: &str) -> bool {
    domain_of(value).is_some_and(|host| {
        let expected = expected.trim_start_matches("www.");
        host == expected || host.ends_with(&format!(".{expected}"))
    })
}

fn is_external_reddit_url(value: &str) -> bool {
    let Some(host) = domain_of(value) else {
        return false;
    };
    !matches!(
        host.as_str(),
        "reddit.com" | "old.reddit.com" | "redd.it" | "i.redd.it" | "v.redd.it" | "preview.redd.it"
    )
}

fn clean(value: Option<String>) -> Option<String> {
    value
        .map(|value| value.split_whitespace().collect::<Vec<_>>().join(" "))
        .filter(|value| !value.is_empty())
}

fn timestamp_from_seconds(value: f64) -> Option<DateTime<Utc>> {
    let duration = std::time::Duration::try_from_secs_f64(value).ok()?;
    let seconds = i64::try_from(duration.as_secs()).ok()?;
    DateTime::from_timestamp(seconds, duration.subsec_nanos())
}

fn clean_env(name: &str) -> Option<String> {
    env::var(name)
        .ok()
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty())
}

fn secret_env(name: &str) -> Option<SecretString> {
    clean_env(name).map(SecretString::from)
}

fn env_u64(name: &str, default: u64) -> u64 {
    env::var(name)
        .ok()
        .and_then(|value| value.parse::<u64>().ok())
        .unwrap_or(default)
}

#[derive(Debug, Deserialize)]
struct HackerNewsStory {
    #[serde(rename = "type")]
    kind: Option<String>,
    by: Option<String>,
    descendants: Option<i64>,
    score: Option<i64>,
    text: Option<String>,
    time: Option<i64>,
    title: Option<String>,
    url: Option<String>,
}

#[derive(Debug, Deserialize)]
struct RedditTokenResponse {
    access_token: String,
}

#[derive(Debug, Deserialize)]
struct RedditListing {
    data: RedditListingData,
}

#[derive(Debug, Deserialize)]
struct RedditListingData {
    children: Vec<RedditListingChild>,
}

#[derive(Debug, Deserialize)]
struct RedditListingChild {
    data: RedditPost,
}

#[derive(Debug, Deserialize)]
struct RedditPost {
    id: String,
    title: String,
    subreddit: String,
    permalink: String,
    url: Option<String>,
    author: Option<String>,
    selftext: Option<String>,
    is_self: bool,
    removed_by_category: Option<Value>,
    score: i64,
    num_comments: i64,
    upvote_ratio: f64,
    over_18: bool,
    created_utc: Option<f64>,
}

#[derive(Debug, Error)]
pub enum ScrapeGatewayError {
    #[error("scraper HTTP request failed")]
    Http(#[from] reqwest::Error),
    #[error("source URL is invalid: {0}")]
    Url(String),
    #[error("public source URL validation failed")]
    PublicUrl(#[from] newsly_extraction::ExtractionClientError),
    #[error("source response exceeded the bounded body limit")]
    ResponseTooLarge,
    #[error("source redirect is missing Location")]
    RedirectLocationMissing,
    #[error("source exceeded the redirect limit")]
    TooManyRedirects,
    #[error("feed document is invalid: {0}")]
    Feed(String),
    #[error("aggregator HTML is invalid: {0}")]
    Html(String),
    #[error("Reddit credentials are not configured")]
    RedditNotConfigured,
    #[error("Reddit OAuth returned no access token")]
    RedditTokenMissing,
}

impl ScrapeGatewayError {
    pub fn diagnostic_code(&self) -> &'static str {
        match self {
            Self::Http(error) if error.is_timeout() => "http_timeout",
            Self::Http(error) if error.is_connect() => "http_connect",
            Self::Http(error) if error.status().is_some() => "http_status",
            Self::Http(_) => "http_request",
            Self::Url(_) => "invalid_url",
            Self::PublicUrl(_) => "public_url_validation",
            Self::ResponseTooLarge => "response_too_large",
            Self::RedirectLocationMissing => "redirect_location_missing",
            Self::TooManyRedirects => "too_many_redirects",
            Self::Feed(_) => "invalid_feed",
            Self::Html(_) => "invalid_html",
            Self::RedditNotConfigured => "reddit_not_configured",
            Self::RedditTokenMissing => "reddit_token_missing",
        }
    }

    pub fn http_status(&self) -> Option<u16> {
        match self {
            Self::Http(error) => error.status().map(|status| status.as_u16()),
            _ => None,
        }
    }

    pub fn retryable(&self) -> bool {
        match self {
            Self::Http(error) => error.status().is_none_or(|status| {
                status == StatusCode::TOO_MANY_REQUESTS || status.is_server_error()
            }),
            Self::Feed(_) | Self::Html(_) => true,
            Self::Url(_)
            | Self::PublicUrl(_)
            | Self::ResponseTooLarge
            | Self::RedirectLocationMissing
            | Self::TooManyRedirects
            | Self::RedditNotConfigured
            | Self::RedditTokenMissing => false,
        }
    }
}

#[cfg(test)]
mod tests;
