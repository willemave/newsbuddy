//! Research-paper aggregators: arXiv daily category listings and Hugging Face Daily Papers.
//!
//! Both normalize to the arXiv abstract page so the same paper keeps one story URL regardless of
//! which list surfaced it.

use chrono::{DateTime, Utc};
use newsly_domain::AggregatorKey;
use serde::Deserialize;
use serde_json::{Value, json};

use super::{
    NewsItemInput, ScrapeGateway, ScrapeGatewayError, ScrapeProviderOutcome, ScrapedItem, clean,
    news_item,
};
use crate::public_http::MAX_SOURCE_RESPONSE_BYTES;

const HF_DAILY_PAPERS_URL: &str = "https://huggingface.co/api/daily_papers?limit=50";

impl ScrapeGateway {
    pub(super) async fn fetch_arxiv(&self) -> Result<ScrapeProviderOutcome, ScrapeGatewayError> {
        let mut items = Vec::new();
        let mut errors = Vec::new();
        let mut retryable_failure = false;
        for &category in AggregatorKey::Arxiv.topics() {
            let listing_url = format!("https://rss.arxiv.org/rss/{category}");
            let parsed = self
                .fetch_public_bytes(&listing_url, Some(MAX_SOURCE_RESPONSE_BYTES))
                .await
                .and_then(|bytes| normalize_arxiv_listing(category, &bytes));
            match parsed {
                Ok(listing) => items.extend(listing),
                Err(error) => {
                    retryable_failure |= error.retryable();
                    errors.push(format!("{category}: {error}"));
                }
            }
        }
        Ok(ScrapeProviderOutcome {
            items,
            item_errors: errors,
            retryable_failure,
        })
    }

    pub(super) async fn fetch_hf_papers(
        &self,
    ) -> Result<ScrapeProviderOutcome, ScrapeGatewayError> {
        let bytes = self
            .fetch_public_bytes(HF_DAILY_PAPERS_URL, Some(MAX_SOURCE_RESPONSE_BYTES))
            .await?;
        normalize_hf_daily_papers(&bytes)
    }
}

/// Normalizes one arXiv category RSS listing, keeping only first announcements whose primary
/// category is `category`. Cross-lists and replacements are skipped, so a paper appears in exactly
/// one category listing.
pub(super) fn normalize_arxiv_listing(
    category: &'static str,
    bytes: &[u8],
) -> Result<Vec<ScrapedItem>, ScrapeGatewayError> {
    let feed = feed_rs::parser::parse(bytes)
        .map_err(|error| ScrapeGatewayError::Feed(error.to_string()))?;
    let mut items = Vec::new();
    for entry in feed.entries {
        let description = entry
            .summary
            .as_ref()
            .map(|value| value.content.as_str())
            .unwrap_or_default();
        if announce_type(description) != Some("new") {
            continue;
        }
        let Some(arxiv_id) = entry
            .links
            .iter()
            .find_map(|link| arxiv_id_from_abs_url(&link.href))
        else {
            continue;
        };
        let Some(title) = entry.title.and_then(|value| clean(Some(value.content))) else {
            continue;
        };
        let authors = entry
            .authors
            .into_iter()
            .filter_map(|person| clean(Some(person.name)))
            .collect::<Vec<_>>()
            .join(", ");
        let categories = entry
            .categories
            .into_iter()
            .map(|value| value.term)
            .collect::<Vec<_>>();
        let published_at = entry.published.or(entry.updated);
        items.push(paper_item(PaperInput {
            key: AggregatorKey::Arxiv,
            arxiv_id,
            title,
            abstract_text: arxiv_abstract(description),
            authors: clean(Some(authors)),
            topic: Some(category),
            discussion_url: None,
            published_at,
            details: json!({ "categories": categories }),
            comment_count: None,
        }));
    }
    Ok(items)
}

/// Normalizes the Hugging Face Daily Papers API response. Entries are decoded one at a time so a
/// single malformed paper is reported without discarding the rest of the list.
pub(super) fn normalize_hf_daily_papers(
    bytes: &[u8],
) -> Result<ScrapeProviderOutcome, ScrapeGatewayError> {
    let entries = serde_json::from_slice::<Vec<Value>>(bytes)
        .map_err(|error| ScrapeGatewayError::Feed(format!("hfpapers JSON: {error}")))?;
    let mut items = Vec::new();
    let mut errors = Vec::new();
    for entry in entries {
        let entry = match serde_json::from_value::<HfDailyPaper>(entry) {
            Ok(entry) => entry,
            Err(error) => {
                errors.push(format!("hfpapers entry is malformed: {error}"));
                continue;
            }
        };
        let paper = entry.paper;
        let Some(arxiv_id) = canonical_arxiv_id(&paper.id) else {
            errors.push(format!(
                "hfpapers entry {:?} has no arXiv identifier",
                paper.id
            ));
            continue;
        };
        let Some(title) = clean(paper.title).or_else(|| clean(entry.title)) else {
            errors.push(format!("hfpapers {arxiv_id} has no title"));
            continue;
        };
        let authors = paper
            .authors
            .into_iter()
            .filter(|author| !author.hidden)
            .filter_map(|author| clean(author.name))
            .collect::<Vec<_>>()
            .join(", ");
        let comment_count = entry.num_comments.unwrap_or(0);
        let organization = entry
            .organization
            .or(paper.organization)
            .and_then(|organization| clean(organization.fullname));
        items.push(paper_item(PaperInput {
            key: AggregatorKey::HfPapers,
            discussion_url: Some(format!("https://huggingface.co/papers/{arxiv_id}")),
            arxiv_id,
            title,
            abstract_text: clean(paper.summary).or_else(|| clean(entry.summary)),
            authors: clean(Some(authors)),
            topic: None,
            published_at: entry.published_at.or(paper.published_at),
            details: json!({
                "upvotes": paper.upvotes.unwrap_or(0),
                "comments_count": comment_count,
                "organization": organization,
                "thumbnail_url": clean(entry.thumbnail),
            }),
            comment_count: Some(comment_count),
        }));
    }
    Ok(ScrapeProviderOutcome::new(items, errors))
}

struct PaperInput {
    key: AggregatorKey,
    arxiv_id: String,
    title: String,
    abstract_text: Option<String>,
    authors: Option<String>,
    topic: Option<&'static str>,
    discussion_url: Option<String>,
    published_at: Option<DateTime<Utc>>,
    details: Value,
    comment_count: Option<i64>,
}

fn paper_item(input: PaperInput) -> ScrapedItem {
    let PaperInput {
        key,
        arxiv_id,
        title,
        abstract_text,
        authors,
        topic,
        discussion_url,
        published_at,
        mut details,
        comment_count,
    } = input;
    let article_url = format!("https://arxiv.org/abs/{arxiv_id}");
    if let Some(details) = details.as_object_mut() {
        details.insert("arxiv_id".to_owned(), json!(arxiv_id));
        details.insert(
            "pdf_url".to_owned(),
            json!(format!("https://arxiv.org/pdf/{arxiv_id}")),
        );
    }
    let mut aggregator = json!({
        "key": key.as_str(),
        "name": key.display_name(),
        "title": title,
        "external_id": arxiv_id,
        "author": authors,
        "metadata": details,
    });
    if let Some(topic) = topic {
        aggregator["topic"] = json!(topic);
    }
    let mut metadata = json!({
        "platform": key.as_str(),
        "source": "arxiv.org",
        "article": {"url": article_url, "title": title, "source_domain": "arxiv.org"},
        "aggregator": aggregator,
        "discussion_url": discussion_url,
        "excerpt": abstract_text,
        "discovery_time": published_at.unwrap_or_else(Utc::now).to_rfc3339(),
    });
    if let Some(count) = comment_count {
        metadata["comment_count"] = json!(count);
    }
    ScrapedItem::News(Box::new(news_item(NewsItemInput {
        key,
        article_url,
        title: Some(title),
        external_id: Some(arxiv_id),
        discussion_url,
        owner_user_id: None,
        published_at,
        raw_metadata: metadata,
    })))
}

/// Reads `new`, `cross`, `replace`, or `replace-cross` from an arXiv RSS description header.
fn announce_type(description: &str) -> Option<&str> {
    let (_, rest) = description.split_once("Announce Type:")?;
    rest.split_whitespace().next()
}

/// Strips the `arXiv:<id> Announce Type: <type>` header and `Abstract:` label.
fn arxiv_abstract(description: &str) -> Option<String> {
    let text = description
        .split_once("Abstract:")
        .map_or(description, |(_, abstract_text)| abstract_text);
    clean(Some(text.to_owned()))
}

/// Extracts the identifier from `https://arxiv.org/abs/<id>[vN]`.
fn arxiv_id_from_abs_url(url: &str) -> Option<String> {
    let url = reqwest::Url::parse(url.trim()).ok()?;
    if !matches!(url.host_str(), Some("arxiv.org" | "www.arxiv.org")) {
        return None;
    }
    canonical_arxiv_id(url.path().strip_prefix("/abs/")?.trim_end_matches('/'))
}

/// Returns the version-free arXiv identifier: modern `YYMM.NNNNN` or legacy `archive/YYMMNNN`.
fn canonical_arxiv_id(id: &str) -> Option<String> {
    let id = id.trim();
    let id = match id.rsplit_once('v') {
        Some((base, version))
            if !version.is_empty() && version.bytes().all(|byte| byte.is_ascii_digit()) =>
        {
            base
        }
        _ => id,
    };
    let modern = id.split_once('.').is_some_and(|(prefix, number)| {
        prefix.len() == 4
            && (4..=5).contains(&number.len())
            && prefix
                .bytes()
                .chain(number.bytes())
                .all(|b| b.is_ascii_digit())
    });
    let legacy = id.split_once('/').is_some_and(|(archive, number)| {
        !archive.is_empty()
            && archive
                .bytes()
                .all(|b| b.is_ascii_lowercase() || b == b'-' || b == b'.')
            && number.len() == 7
            && number.bytes().all(|b| b.is_ascii_digit())
    });
    (modern || legacy).then(|| id.to_owned())
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct HfDailyPaper {
    paper: HfPaper,
    title: Option<String>,
    summary: Option<String>,
    thumbnail: Option<String>,
    num_comments: Option<i64>,
    published_at: Option<DateTime<Utc>>,
    organization: Option<HfOrganization>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct HfPaper {
    id: String,
    title: Option<String>,
    summary: Option<String>,
    upvotes: Option<i64>,
    published_at: Option<DateTime<Utc>>,
    #[serde(default)]
    authors: Vec<HfAuthor>,
    organization: Option<HfOrganization>,
}

#[derive(Debug, Deserialize)]
struct HfAuthor {
    name: Option<String>,
    #[serde(default)]
    hidden: bool,
}

#[derive(Debug, Deserialize)]
struct HfOrganization {
    fullname: Option<String>,
}

#[cfg(test)]
mod tests;
