//! Per-lane evidence for audio onboarding discovery.
//!
//! Each lane gathers evidence from the source that can actually ground its target. Feed lanes
//! search the web one query at a time. Podcast lanes add Apple's podcast directory, which
//! carries real RSS URLs, and resolve Apple Podcasts pages found on the web to their feeds.
//! Reddit lanes use Reddit's own community search, because web search no longer indexes
//! reddit.com; results are real, public communities with member counts.

use std::collections::{HashMap, HashSet};
use std::time::Duration;

use secrecy::{ExposeSecret, SecretString};
use serde::Deserialize;

use super::{
    FAST_DISCOVER_TIMEOUT, OnboardingAudioLane, OnboardingExaUsage, OnboardingGateway,
    OnboardingGatewayError, OnboardingLaneTarget, WebResult, dedupe_web_results, secret_env,
};

pub(super) struct LaneEvidence {
    pub(super) results: Vec<WebResult>,
    pub(super) exa_usage: OnboardingExaUsage,
}

const ITUNES_SEARCH_URL: &str = "https://itunes.apple.com/search";
const ITUNES_LOOKUP_URL: &str = "https://itunes.apple.com/lookup";
const REDDIT_TOKEN_URL: &str = "https://www.reddit.com/api/v1/access_token";
const REDDIT_SEARCH_URL: &str = "https://oauth.reddit.com/subreddits/search";
const DEFAULT_REDDIT_USER_AGENT: &str = "Newsly/1.0 (onboarding discovery)";
const DIRECTORY_TIMEOUT: Duration = Duration::from_secs(8);

pub(super) const EXA_RESULTS_PER_QUERY: usize = 8;
const MAX_LANE_QUERIES: usize = 3;
const MAX_DIRECTORY_TERMS: usize = 4;
const PODCASTS_PER_TERM: usize = 6;
const MIN_PODCAST_EPISODES: u64 = 5;
const SUBREDDITS_PER_TERM: usize = 6;
const MIN_SUBREDDIT_MEMBERS: u64 = 5_000;

/// Words that describe the lane's format rather than its subject, dropped from directory terms.
const FORMAT_WORDS: [&str; 10] = [
    "community",
    "communities",
    "discussion",
    "discussions",
    "subreddit",
    "subreddits",
    "reddit",
    "podcast",
    "podcasts",
    "shows",
];

#[derive(Debug, Clone)]
pub(super) struct RedditCredentials {
    client_id: SecretString,
    client_secret: SecretString,
    user_agent: String,
}

impl RedditCredentials {
    pub(super) fn from_env() -> Option<Self> {
        Some(Self {
            client_id: secret_env("REDDIT_CLIENT_ID")?,
            client_secret: secret_env("REDDIT_CLIENT_SECRET")?,
            user_agent: std::env::var("REDDIT_USER_AGENT")
                .ok()
                .map(|value| value.trim().to_owned())
                .filter(|value| !value.is_empty())
                .unwrap_or_else(|| DEFAULT_REDDIT_USER_AGENT.to_owned()),
        })
    }
}

impl OnboardingGateway {
    /// Collects one lane's evidence. Web-search lanes fail only when every search request fails,
    /// so the durable task retries a provider outage instead of publishing an empty success.
    /// Directory lookups are best effort: their outage leaves the lane with web evidence alone.
    pub(super) async fn lane_evidence(
        &self,
        lane: &OnboardingAudioLane,
        inferred_topics: &[String],
    ) -> Result<LaneEvidence, OnboardingGatewayError> {
        let mut exa_usage = OnboardingExaUsage::default();
        let mut results = match lane.target {
            OnboardingLaneTarget::Reddit => {
                self.subreddit_results(&reddit_terms(lane, inferred_topics))
                    .await
            }
            OnboardingLaneTarget::Feeds => {
                let outcome = self.web_lane_results(lane).await;
                if outcome.all_attempts_failed() {
                    return Err(OnboardingGatewayError::SearchUnavailable);
                }
                exa_usage = outcome.usage;
                outcome.results
            }
            OnboardingLaneTarget::Podcasts => {
                let mut results = self
                    .podcast_directory_results(&podcast_terms(lane, inferred_topics))
                    .await;
                let outcome = self.web_lane_results(lane).await;
                if outcome.all_attempts_failed() && results.is_empty() {
                    return Err(OnboardingGatewayError::SearchUnavailable);
                }
                exa_usage = outcome.usage;
                let mut web = outcome.results;
                self.attach_apple_podcast_feeds(&mut web).await;
                results.extend(web);
                results
            }
        };
        dedupe_web_results(&mut results);
        tracing::info!(
            lane = %lane.name,
            target = lane.target.as_str(),
            results = results.len(),
            "onboarding lane evidence collected"
        );
        Ok(LaneEvidence { results, exa_usage })
    }

    async fn web_lane_results(&self, lane: &OnboardingAudioLane) -> super::SearchManyOutcome {
        let queries = lane_queries(lane);
        self.search_many(queries, EXA_RESULTS_PER_QUERY, false, FAST_DISCOVER_TIMEOUT)
            .await
    }

    async fn podcast_directory_results(&self, terms: &[String]) -> Vec<WebResult> {
        let mut results = Vec::new();
        for term in terms {
            let limit = PODCASTS_PER_TERM.to_string();
            let response = self
                .client
                .get(ITUNES_SEARCH_URL)
                .timeout(DIRECTORY_TIMEOUT)
                .query(&[
                    ("media", "podcast"),
                    ("entity", "podcast"),
                    ("limit", limit.as_str()),
                    ("term", term.as_str()),
                ])
                .send()
                .await
                .and_then(reqwest::Response::error_for_status);
            let rows = match response {
                Ok(response) => match response.json::<ItunesResponse>().await {
                    Ok(body) => body.results,
                    Err(error) => {
                        tracing::warn!(error = %error, term, "podcast directory response was invalid");
                        continue;
                    }
                },
                Err(error) => {
                    tracing::warn!(error = %error, term, "podcast directory search failed");
                    continue;
                }
            };
            results.extend(
                rows.into_iter()
                    .filter_map(|row| podcast_directory_result(row, term)),
            );
        }
        results
    }

    /// Appends the RSS feed to web results that are Apple Podcasts show pages, so the model can
    /// suggest the feed rather than a directory page that fails feed validation.
    async fn attach_apple_podcast_feeds(&self, results: &mut [WebResult]) {
        let ids = results
            .iter()
            .filter_map(|result| apple_podcast_id(&result.url))
            .collect::<HashSet<_>>();
        if ids.is_empty() {
            return;
        }
        let ids = ids.into_iter().collect::<Vec<_>>().join(",");
        let response = self
            .client
            .get(ITUNES_LOOKUP_URL)
            .timeout(DIRECTORY_TIMEOUT)
            .query(&[("id", ids.as_str()), ("entity", "podcast")])
            .send()
            .await
            .and_then(reqwest::Response::error_for_status);
        let rows = match response {
            Ok(response) => response
                .json::<ItunesResponse>()
                .await
                .map(|body| body.results)
                .unwrap_or_default(),
            Err(error) => {
                tracing::warn!(error = %error, "podcast directory lookup failed");
                return;
            }
        };
        let feeds = rows
            .into_iter()
            .filter_map(|row| Some((row.collection_id?.to_string(), row.feed_url?)))
            .collect::<HashMap<_, _>>();
        for result in results {
            let Some(feed) = apple_podcast_id(&result.url).and_then(|id| feeds.get(&id)) else {
                continue;
            };
            let snippet = result.snippet.take().unwrap_or_default();
            result.snippet = Some(format!("RSS feed: {feed}. {snippet}"));
        }
    }

    async fn subreddit_results(&self, terms: &[String]) -> Vec<WebResult> {
        let Some(credentials) = self.reddit.as_ref() else {
            tracing::warn!("Reddit is not configured; onboarding reddit lane has no evidence");
            return Vec::new();
        };
        let token = match self.reddit_token(credentials).await {
            Ok(token) => token,
            Err(error) => {
                tracing::warn!(error = %error, "Reddit token request failed for onboarding");
                return Vec::new();
            }
        };
        let mut results = Vec::new();
        for term in terms {
            let limit = SUBREDDITS_PER_TERM.to_string();
            let response = self
                .client
                .get(REDDIT_SEARCH_URL)
                .timeout(DIRECTORY_TIMEOUT)
                .bearer_auth(&token)
                .header(reqwest::header::USER_AGENT, &credentials.user_agent)
                .query(&[
                    ("q", term.as_str()),
                    ("limit", limit.as_str()),
                    ("raw_json", "1"),
                ])
                .send()
                .await
                .and_then(reqwest::Response::error_for_status);
            let listing = match response {
                Ok(response) => match response.json::<RedditListing>().await {
                    Ok(listing) => listing,
                    Err(error) => {
                        tracing::warn!(error = %error, term, "Reddit community search was invalid");
                        continue;
                    }
                },
                Err(error) => {
                    tracing::warn!(error = %error, term, "Reddit community search failed");
                    continue;
                }
            };
            results.extend(
                listing
                    .data
                    .children
                    .into_iter()
                    .filter_map(|child| subreddit_result(child.data, term)),
            );
        }
        results
    }

    async fn reddit_token(
        &self,
        credentials: &RedditCredentials,
    ) -> Result<String, reqwest::Error> {
        let response = self
            .client
            .post(REDDIT_TOKEN_URL)
            .timeout(DIRECTORY_TIMEOUT)
            .basic_auth(
                credentials.client_id.expose_secret(),
                Some(credentials.client_secret.expose_secret()),
            )
            .header(reqwest::header::USER_AGENT, &credentials.user_agent)
            .form(&[("grant_type", "client_credentials")])
            .send()
            .await?
            .error_for_status()?
            .json::<RedditToken>()
            .await?;
        Ok(response.access_token)
    }
}

#[derive(Debug, Deserialize)]
struct ItunesResponse {
    #[serde(default)]
    results: Vec<ItunesPodcast>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ItunesPodcast {
    collection_id: Option<u64>,
    collection_name: Option<String>,
    artist_name: Option<String>,
    feed_url: Option<String>,
    collection_view_url: Option<String>,
    primary_genre_name: Option<String>,
    track_count: Option<u64>,
}

#[derive(Debug, Deserialize)]
struct RedditToken {
    access_token: String,
}

#[derive(Debug, Deserialize)]
struct RedditListing {
    data: RedditListingData,
}

#[derive(Debug, Deserialize)]
struct RedditListingData {
    #[serde(default)]
    children: Vec<RedditChild>,
}

#[derive(Debug, Deserialize)]
struct RedditChild {
    data: RedditCommunity,
}

#[derive(Debug, Deserialize)]
struct RedditCommunity {
    display_name: String,
    subscribers: Option<u64>,
    #[serde(default)]
    over18: bool,
    subreddit_type: Option<String>,
    public_description: Option<String>,
}

fn podcast_directory_result(row: ItunesPodcast, term: &str) -> Option<WebResult> {
    let feed_url = row.feed_url.filter(|url| url.starts_with("http"))?;
    let episodes = row.track_count.unwrap_or_default();
    if episodes < MIN_PODCAST_EPISODES {
        return None;
    }
    let title = row.collection_name?;
    let mut details = vec![format!("{episodes} episodes")];
    details.extend(row.artist_name.map(|artist| format!("by {artist}")));
    details.extend(row.primary_genre_name);
    details.extend(row.collection_view_url);
    Some(WebResult {
        title,
        url: feed_url.clone(),
        snippet: Some(format!(
            "Podcast directory entry. RSS feed: {feed_url}. {}",
            details.join(" · ")
        )),
        published_date: None,
        query: format!("podcast directory: {term}"),
    })
}

fn subreddit_result(community: RedditCommunity, term: &str) -> Option<WebResult> {
    let members = community.subscribers.unwrap_or_default();
    let public = community.subreddit_type.as_deref() == Some("public");
    if community.over18 || !public || members < MIN_SUBREDDIT_MEMBERS {
        return None;
    }
    let name = community.display_name;
    let description = community
        .public_description
        .unwrap_or_default()
        .replace('\n', " ");
    Some(WebResult {
        title: format!("r/{name}"),
        url: format!("https://www.reddit.com/r/{name}/"),
        snippet: Some(format!(
            "Reddit community, {members} members. {description}"
        )),
        published_date: None,
        query: format!("reddit community search: {term}"),
    })
}

/// The lane's own queries, searched one by one. Search operators are dropped: the web index
/// treats them as literal text, which is how whole lanes used to return nothing.
fn lane_queries(lane: &OnboardingAudioLane) -> Vec<String> {
    let mut queries = lane
        .queries
        .iter()
        .map(|query| {
            query
                .split_whitespace()
                .filter(|word| !word.contains(':'))
                .collect::<Vec<_>>()
                .join(" ")
        })
        .filter(|query| !query.is_empty())
        .take(MAX_LANE_QUERIES)
        .collect::<Vec<_>>();
    if queries.is_empty() {
        queries.push(lane.goal.trim().to_owned());
    }
    queries
}

/// Communities the plan named outright (`r/formula1`), then the lane's subject, then the user's
/// topics, which carry a generic lane named only "Reddit".
fn reddit_terms(lane: &OnboardingAudioLane, inferred_topics: &[String]) -> Vec<String> {
    let named = lane.queries.iter().flat_map(|query| {
        query
            .split(|character: char| {
                !(character.is_ascii_alphanumeric() || character == '_' || character == '/')
            })
            .filter_map(|token| token.split("r/").nth(1))
            .map(|name| name.trim_matches('/').to_owned())
            .filter(|name| name.len() >= 2)
            .collect::<Vec<_>>()
    });
    unique_terms(
        named
            .chain([subject(&lane.name)])
            .chain(inferred_topics.iter().cloned()),
    )
}

fn podcast_terms(lane: &OnboardingAudioLane, inferred_topics: &[String]) -> Vec<String> {
    unique_terms(
        [subject(&lane.name)]
            .into_iter()
            .chain(inferred_topics.iter().cloned()),
    )
}

fn subject(name: &str) -> String {
    name.split_whitespace()
        .filter(|word| !FORMAT_WORDS.contains(&word.to_ascii_lowercase().as_str()))
        .collect::<Vec<_>>()
        .join(" ")
}

fn unique_terms(terms: impl IntoIterator<Item = String>) -> Vec<String> {
    let mut seen = HashSet::new();
    terms
        .into_iter()
        .map(|term| term.trim().to_owned())
        .filter(|term| !term.is_empty() && seen.insert(term.to_ascii_lowercase()))
        .take(MAX_DIRECTORY_TERMS)
        .collect()
}

fn apple_podcast_id(url: &str) -> Option<String> {
    let url = reqwest::Url::parse(url).ok()?;
    if url.host_str()? != "podcasts.apple.com" {
        return None;
    }
    let segment = url.path_segments()?.next_back()?;
    let id = segment.strip_prefix("id")?;
    (!id.is_empty() && id.bytes().all(|byte| byte.is_ascii_digit())).then(|| id.to_owned())
}

#[cfg(test)]
mod tests {
    use super::{
        ItunesPodcast, OnboardingAudioLane, OnboardingLaneTarget, RedditCommunity,
        apple_podcast_id, lane_queries, podcast_directory_result, podcast_terms, reddit_terms,
        subreddit_result,
    };

    fn lane(name: &str, target: OnboardingLaneTarget, queries: &[&str]) -> OnboardingAudioLane {
        OnboardingAudioLane {
            name: name.to_owned(),
            goal: "Find durable sources.".to_owned(),
            target,
            queries: queries.iter().map(|query| (*query).to_owned()).collect(),
        }
    }

    #[test]
    fn lane_queries_are_searched_separately_without_operators() {
        let lane = lane(
            "Piano",
            OnboardingLaneTarget::Feeds,
            &[
                "classical piano site:substack.com RSS",
                "independent piano",
                "a",
                "b",
            ],
        );
        assert_eq!(
            lane_queries(&lane),
            vec!["classical piano RSS", "independent piano", "a"]
        );
    }

    #[test]
    fn reddit_terms_prefer_named_communities_then_the_subject_then_topics() {
        let topics = ["Chess".to_owned()];
        let named = lane(
            "Formula One community",
            OnboardingLaneTarget::Reddit,
            &[
                "best subreddits for site:reddit.com/r/formula1 race discussion",
                "site:reddit.com/r/F1Technical car analysis",
            ],
        );
        assert_eq!(
            reddit_terms(&named, &topics),
            vec!["formula1", "F1Technical", "Formula One", "Chess"]
        );

        let generic = lane("Reddit", OnboardingLaneTarget::Reddit, &["best subreddits"]);
        assert_eq!(reddit_terms(&generic, &topics), vec!["Chess"]);
    }

    #[test]
    fn podcast_terms_lead_with_the_lane_subject() {
        let lane = lane("Economics podcasts", OnboardingLaneTarget::Podcasts, &[]);
        let topics = ["Chess".to_owned(), "economics".to_owned()];
        assert_eq!(podcast_terms(&lane, &topics), vec!["Economics", "Chess"]);
    }

    #[test]
    fn podcast_directory_entries_carry_their_feed_and_skip_thin_shows() {
        let row = |episodes| ItunesPodcast {
            collection_id: Some(1),
            collection_name: Some("F1 Nation".to_owned()),
            artist_name: Some("Formula 1".to_owned()),
            feed_url: Some("https://audioboom.com/channels/5024396.rss".to_owned()),
            collection_view_url: None,
            primary_genre_name: Some("Sports".to_owned()),
            track_count: Some(episodes),
        };
        let result = podcast_directory_result(row(324), "Formula One").expect("kept");
        assert_eq!(result.url, "https://audioboom.com/channels/5024396.rss");
        assert!(
            result
                .snippet
                .as_deref()
                .unwrap_or_default()
                .contains("RSS feed:")
        );
        assert!(podcast_directory_result(row(2), "Formula One").is_none());
    }

    #[test]
    fn subreddit_results_keep_only_public_established_communities() {
        let community = |members, over18, kind: &str| RedditCommunity {
            display_name: "formula1".to_owned(),
            subscribers: Some(members),
            over18,
            subreddit_type: Some(kind.to_owned()),
            public_description: Some("Formula 1\nnews".to_owned()),
        };
        let kept = subreddit_result(community(6_840_344, false, "public"), "f1").expect("kept");
        assert_eq!(kept.url, "https://www.reddit.com/r/formula1/");
        assert_eq!(kept.title, "r/formula1");
        assert!(subreddit_result(community(900, false, "public"), "f1").is_none());
        assert!(subreddit_result(community(90_000, true, "public"), "f1").is_none());
        assert!(subreddit_result(community(90_000, false, "restricted"), "f1").is_none());
    }

    #[test]
    fn apple_podcast_pages_resolve_to_their_numeric_id() {
        assert_eq!(
            apple_podcast_id("https://podcasts.apple.com/us/podcast/planet-money/id290783428"),
            Some("290783428".to_owned())
        );
        assert_eq!(apple_podcast_id("https://open.spotify.com/show/abc"), None);
        assert_eq!(
            apple_podcast_id("https://podcasts.apple.com/us/podcast/x/idabc"),
            None
        );
    }
}
