//! Bounded named-source resolution using publisher and directory evidence, without a model loop.
use std::{
    collections::{HashMap, HashSet},
    future::Future,
    time::Duration,
};

use crate::exa::{ExaSearchOptions, ExaSearchUsage, search_exa};
use crate::feed_validation::FeedDiscoveryEvidence;
use crate::podcast_directory::{self, ItunesPodcast};
use crate::{FeedValidator, PublicWebPage, ValidatedFeed};
use futures_util::{StreamExt, stream};
use rig_core::schemars::JsonSchema;
use secrecy::SecretString;
use serde::{Deserialize, Serialize};
use thiserror::Error;

#[derive(Debug, Clone, Copy, Serialize, Deserialize, JsonSchema, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum SourceKind {
    Podcast,
    Publication,
    Aggregator,
}

impl SourceKind {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Podcast => "podcast",
            Self::Publication => "publication",
            Self::Aggregator => "aggregator",
        }
    }
}

#[derive(Debug, Clone, Copy, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum SourceResolutionStatus {
    Found,
    NotFound,
    Ambiguous,
    Unavailable,
}

#[derive(Debug)]
pub struct ResolvedFeed {
    pub title: String,
    pub site_url: String,
    pub kind: SourceKind,
    pub feed: ValidatedFeed,
}

#[derive(Debug)]
pub struct SourceResolution {
    pub feeds: Vec<ResolvedFeed>,
    pub status: SourceResolutionStatus,
    pub limitations: Vec<String>,
    pub exa_usage: ExaSearchUsage,
}

impl Default for SourceResolution {
    fn default() -> Self {
        Self {
            feeds: Vec::new(),
            status: SourceResolutionStatus::NotFound,
            limitations: Vec::new(),
            exa_usage: ExaSearchUsage::default(),
        }
    }
}

#[derive(Debug, Error)]
pub enum SourceResolutionError {
    #[error("source resolver HTTP client failed")]
    Http(#[from] reqwest::Error),
    #[error("source resolver search URL is invalid: {0}")]
    Url(String),
    #[error("source input or host constraints exceed supported bounds")]
    InvalidInput,
}

#[derive(Clone, Debug)]
pub struct SourceResolver {
    client: reqwest::Client,
    exa_api_key: Option<SecretString>,
    exa_search_url: reqwest::Url,
    validator: FeedValidator,
}

impl SourceResolver {
    /// Build the neutral source resolver; no model credentials or profile discovery are needed.
    /// # Errors
    /// Returns an error if the HTTP client or configured Exa URL is invalid.
    pub fn from_env() -> Result<Self, SourceResolutionError> {
        let raw = std::env::var("EXA_API_BASE_URL").unwrap_or_else(|_| "https://api.exa.ai".into());
        let mut endpoint = reqwest::Url::parse(&raw)
            .map_err(|error| SourceResolutionError::Url(error.to_string()))?;
        if !endpoint.path().trim_end_matches('/').ends_with("/search") {
            endpoint.set_path(&format!("{}/", endpoint.path().trim_end_matches('/')));
            endpoint = endpoint
                .join("search")
                .map_err(|error| SourceResolutionError::Url(error.to_string()))?;
        }
        Ok(Self {
            client: reqwest::Client::builder()
                .timeout(Duration::from_secs(15))
                .build()?,
            exa_api_key: std::env::var("EXA_API_KEY")
                .ok()
                .map(|key| key.trim().to_owned())
                .filter(|key| !key.is_empty())
                .map(SecretString::from),
            exa_search_url: endpoint,
            validator: FeedValidator::new(),
        })
    }

    /// Resolve a named source, direct feed, Apple show URL, or publisher homepage.
    /// # Errors
    /// Invalid input returns a typed error; unavailable lookups are recorded in the result.
    pub async fn resolve_source(
        &self,
        input: &str,
        kind: Option<SourceKind>,
        hosts: &[String],
    ) -> Result<SourceResolution, SourceResolutionError> {
        resolve_with(self, input, kind, hosts).await
    }
}

#[derive(Debug)]
struct Candidate {
    title: String,
    site_url: String,
    feed_url: String,
    kind: Option<SourceKind>,
    author: Option<String>,
    name_query: Option<String>,
}

// The transport seam lets deterministic tests exercise the actual orchestration and cache.
trait SourceLookup: Sync {
    fn directory(
        &self,
        value: &str,
        lookup: bool,
    ) -> impl Future<Output = Result<Vec<ItunesPodcast>, ()>> + Send;
    fn search(
        &self,
        query: &str,
    ) -> impl Future<Output = (Vec<(String, String)>, ExaSearchUsage, bool)> + Send;
    fn page(&self, url: &str) -> impl Future<Output = Result<PublicWebPage, ()>> + Send;
    fn validate(
        &self,
        url: &str,
    ) -> impl Future<Output = Result<Option<(ValidatedFeed, FeedDiscoveryEvidence)>, ()>> + Send;
}

impl SourceLookup for SourceResolver {
    async fn directory(&self, value: &str, lookup: bool) -> Result<Vec<ItunesPodcast>, ()> {
        if lookup {
            podcast_directory::lookup(&self.client, value).await
        } else {
            podcast_directory::search(&self.client, value, 12).await
        }
        .map_err(|_| ())
    }
    async fn search(&self, query: &str) -> (Vec<(String, String)>, ExaSearchUsage, bool) {
        let mut usage = ExaSearchUsage::default();
        let Some(key) = &self.exa_api_key else {
            return (Vec::new(), usage, false);
        };
        if let Ok(search) = search_exa(
            &self.client,
            self.exa_search_url.clone(),
            key,
            query,
            ExaSearchOptions {
                num_results: 5,
                include_social: false,
                text_max_characters: 280,
                livecrawl: None,
                summary_query: None,
                timeout: Some(Duration::from_secs(12)),
            },
        )
        .await
        {
            usage.record_search(&search);
            (
                search
                    .results
                    .into_iter()
                    .map(|row| (row.title.unwrap_or_default(), row.url))
                    .collect(),
                usage,
                true,
            )
        } else {
            usage.record_unpriced_attempt();
            (Vec::new(), usage, false)
        }
    }
    async fn page(&self, url: &str) -> Result<PublicWebPage, ()> {
        crate::read_public_web_page(url, 12_000)
            .await
            .map_err(|_| ())
    }
    async fn validate(
        &self,
        url: &str,
    ) -> Result<Option<(ValidatedFeed, FeedDiscoveryEvidence)>, ()> {
        self.validator
            .validate_feed_with_evidence(url)
            .await
            .map_err(|_| ())
    }
}

type Validation = Result<Option<(ValidatedFeed, FeedDiscoveryEvidence)>, ()>;

async fn resolve_with(
    lookup: &impl SourceLookup,
    input: &str,
    kind: Option<SourceKind>,
    hosts: &[String],
) -> Result<SourceResolution, SourceResolutionError> {
    let input = input.trim();
    if input.is_empty()
        || input.len() > 2_048
        || hosts.len() > 8
        || hosts
            .iter()
            .any(|host| host.len() > 200 || host.trim().is_empty())
    {
        return Err(SourceResolutionError::InvalidInput);
    }
    let mut result = SourceResolution::default();
    let mut cache = HashMap::new();
    let mut candidates = Vec::new();
    if reqwest::Url::parse(input).is_ok() {
        if let Some(id) = podcast_directory::apple_podcast_id(input) {
            collect_directory(
                lookup,
                &id,
                true,
                None,
                kind,
                &mut candidates,
                &mut result.limitations,
            )
            .await;
        } else {
            candidates.push(Candidate {
                title: input.into(),
                site_url: input.into(),
                feed_url: input.into(),
                kind,
                author: None,
                name_query: None,
            });
            validate_candidates(lookup, &mut candidates, hosts, &mut cache, &mut result).await;
            if result.feeds.is_empty() {
                collect_page(
                    lookup,
                    input,
                    kind,
                    None,
                    &mut candidates,
                    &mut result.limitations,
                )
                .await;
            }
        }
    } else {
        if kind != Some(SourceKind::Publication) && kind != Some(SourceKind::Aggregator) {
            let term = format!("{input} {}", hosts.join(" "));
            collect_directory(
                lookup,
                &term,
                false,
                Some((input, hosts)),
                kind,
                &mut candidates,
                &mut result.limitations,
            )
            .await;
            validate_candidates(lookup, &mut candidates, hosts, &mut cache, &mut result).await;
        }
        // Directory rows are merely candidates. A stale or rejected feed must still permit search.
        if result.feeds.is_empty() && kind != Some(SourceKind::Aggregator) {
            collect_named_web(
                lookup,
                input,
                kind,
                hosts,
                &mut candidates,
                &mut cache,
                &mut result,
            )
            .await;
        }
    }
    validate_candidates(lookup, &mut candidates, hosts, &mut cache, &mut result).await;
    result.limitations.sort();
    result.limitations.dedup();
    result.status = match result.feeds.len() {
        0 if !result.limitations.is_empty() => SourceResolutionStatus::Unavailable,
        0 => SourceResolutionStatus::NotFound,
        1 => SourceResolutionStatus::Found,
        _ => SourceResolutionStatus::Ambiguous,
    };
    Ok(result)
}

async fn collect_named_web(
    lookup: &impl SourceLookup,
    input: &str,
    kind: Option<SourceKind>,
    hosts: &[String],
    candidates: &mut Vec<Candidate>,
    cache: &mut HashMap<String, Validation>,
    result: &mut SourceResolution,
) {
    let (rows, usage, available) = lookup
        .search(&format!("{input} {} RSS feed", hosts.join(" ")))
        .await;
    result.exa_usage = usage;
    if !available {
        result
            .limitations
            .push("Web source search unavailable".into());
    }
    for (title, url) in rows
        .into_iter()
        .filter(|(title, _)| publication_matches(input, title))
        .take(3)
    {
        candidates.push(Candidate {
            title,
            site_url: url.clone(),
            feed_url: url.clone(),
            kind,
            author: None,
            name_query: Some(input.into()),
        });
        let before = result.feeds.len();
        validate_candidates(lookup, candidates, hosts, cache, result).await;
        if result.feeds.len() == before {
            collect_page(
                lookup,
                &url,
                kind,
                Some((input, hosts)),
                candidates,
                &mut result.limitations,
            )
            .await;
            validate_candidates(lookup, candidates, hosts, cache, result).await;
        }
    }
}

async fn validate_candidates(
    lookup: &impl SourceLookup,
    candidates: &mut Vec<Candidate>,
    hosts: &[String],
    cache: &mut HashMap<String, Validation>,
    result: &mut SourceResolution,
) {
    let mut unique = HashSet::new();
    let mut candidates = std::mem::take(candidates)
        .into_iter()
        .filter(|c| unique.insert(c.feed_url.clone()))
        .collect::<Vec<_>>();
    if candidates.len() > 8 {
        result
            .limitations
            .push("Candidate feed check limit reached".into());
        candidates.truncate(8);
    }
    // Cache both successful and unsuccessful checks for the whole attempt. Only independent,
    // previously unseen URLs are fetched, with the same bounded concurrency as feed discovery.
    let mut urls = candidates
        .iter()
        .map(|candidate| &candidate.feed_url)
        .filter(|url| !cache.contains_key(*url))
        .cloned()
        .collect::<Vec<_>>();
    let remaining = 16_usize.saturating_sub(cache.len());
    if urls.len() > remaining {
        result
            .limitations
            .push("Feed validation budget exhausted".into());
        urls.truncate(remaining);
    }
    let validations = stream::iter(urls)
        .map(|url| async move {
            let validation = lookup.validate(&url).await;
            (url, validation)
        })
        .buffered(8)
        .collect::<Vec<_>>()
        .await;
    cache.extend(validations);
    for mut candidate in candidates {
        let Some(validation) = cache.get(&candidate.feed_url).cloned() else {
            continue;
        };
        match validation {
            Ok(Some((feed, evidence)))
                if (candidate.kind != Some(SourceKind::Podcast) || feed.has_audio_entries)
                    && candidate_matches_identity(
                        &candidate,
                        evidence.title.as_deref(),
                        &evidence.authors,
                        hosts,
                    ) =>
            {
                candidate.title = evidence.title.unwrap_or(candidate.title);
                add_validated(result, candidate, feed);
            }
            Ok(_) => {}
            Err(()) => result
                .limitations
                .push("A candidate feed check was unavailable".into()),
        }
    }
}

async fn collect_directory(
    lookup: &impl SourceLookup,
    value: &str,
    by_id: bool,
    identity: Option<(&str, &[String])>,
    kind: Option<SourceKind>,
    candidates: &mut Vec<Candidate>,
    limitations: &mut Vec<String>,
) {
    match lookup.directory(value, by_id).await {
        Ok(rows) => candidates.extend(
            rows.into_iter()
                .filter(|row| {
                    identity.is_none_or(|(query, hosts)| named_podcast_matches(query, hosts, row))
                })
                .filter_map(directory_candidate)
                .map(|mut candidate| {
                    candidate.kind = kind.or(candidate.kind);
                    candidate
                }),
        ),
        Err(()) => limitations.push("Podcast directory lookup unavailable".into()),
    }
}

async fn collect_page(
    lookup: &impl SourceLookup,
    url: &str,
    kind: Option<SourceKind>,
    identity: Option<(&str, &[String])>,
    candidates: &mut Vec<Candidate>,
    limitations: &mut Vec<String>,
) {
    let Ok(page) = lookup.page(url).await else {
        limitations.push("Publisher page could not be inspected".into());
        return;
    };
    if page.feed_urls.len() > 8 {
        limitations.push("Publisher feed candidate limit reached".into());
    }
    for feed_url in page.feed_urls.into_iter().take(8) {
        candidates.push(Candidate {
            title: page.title.clone().unwrap_or_else(|| page.url.clone()),
            site_url: page.url.clone(),
            feed_url,
            kind,
            author: None,
            name_query: identity.map(|(query, _)| query.into()),
        });
    }
    let mut ids = page
        .links
        .iter()
        .filter_map(|link| podcast_directory::apple_podcast_id(&link.url))
        .collect::<Vec<_>>();
    ids.sort();
    ids.dedup();
    ids.truncate(3);
    if !ids.is_empty() && kind != Some(SourceKind::Publication) {
        collect_directory(
            lookup,
            &ids.join(","),
            true,
            identity,
            kind,
            candidates,
            limitations,
        )
        .await;
    }
}

fn candidate_matches_identity(
    candidate: &Candidate,
    title: Option<&str>,
    authors: &[String],
    hosts: &[String],
) -> bool {
    host_names_match(
        hosts,
        candidate.author.as_deref().unwrap_or(&authors.join(" ")),
    ) && candidate
        .name_query
        .as_deref()
        .is_none_or(|query| title.is_some_and(|title| publication_matches(query, title)))
}

fn add_validated(result: &mut SourceResolution, candidate: Candidate, feed: ValidatedFeed) {
    if result
        .feeds
        .iter()
        .any(|existing| existing.feed.effective_url == feed.effective_url)
    {
        return;
    }
    let kind = candidate.kind.unwrap_or(if feed.has_audio_entries {
        SourceKind::Podcast
    } else {
        SourceKind::Publication
    });
    result.feeds.push(ResolvedFeed {
        title: candidate.title,
        site_url: candidate.site_url,
        kind,
        feed,
    });
}

fn directory_candidate(row: ItunesPodcast) -> Option<Candidate> {
    Some(Candidate {
        title: row.collection_name?,
        site_url: row.collection_view_url?,
        feed_url: row.feed_url?,
        kind: Some(SourceKind::Podcast),
        author: row.artist_name,
        name_query: None,
    })
}
fn words(value: &str) -> Vec<String> {
    value
        .split(|c: char| !c.is_alphanumeric())
        .filter(|w| !w.is_empty())
        .map(str::to_lowercase)
        .filter(|w| {
            !matches!(
                w.as_str(),
                "podcast" | "podcasts" | "rss" | "feed" | "with" | "by" | "the"
            )
        })
        .collect()
}
fn publication_matches(query: &str, title: &str) -> bool {
    let query = words(query);
    let title = words(title);
    !query.is_empty() && query.iter().all(|w| title.contains(w))
}
fn host_names_match(hosts: &[String], artist: &str) -> bool {
    let artist = words(artist).join(" ");
    hosts.iter().all(|host| {
        let host = words(host).join(" ");
        !host.is_empty() && artist.contains(&host)
    })
}

fn named_podcast_matches(query: &str, hosts: &[String], row: &ItunesPodcast) -> bool {
    let Some(title) = row.collection_name.as_deref() else {
        return false;
    };
    let artist = row.artist_name.as_deref().unwrap_or_default();
    if !host_names_match(hosts, artist) {
        return false;
    }
    let title_words = words(title);
    let query_words = words(query);
    let artist_words = words(artist);
    !title_words.is_empty()
        && !query_words.is_empty()
        && title_words.iter().all(|w| query_words.contains(w))
        && query_words
            .iter()
            .all(|w| title_words.contains(w) || artist_words.contains(w))
}

#[cfg(test)]
#[path = "source_resolution_tests.rs"]
mod tests;
