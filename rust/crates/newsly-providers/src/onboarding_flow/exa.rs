use std::time::Duration;

use reqwest::Url;
use secrecy::{ExposeSecret, SecretString};
use serde::{Deserialize, Serialize};

use super::DISCOVERY_SNIPPET_CHARS;

const EXCLUDED_DOMAINS: [&str; 8] = [
    "facebook.com",
    "linkedin.com",
    "twitter.com",
    "x.com",
    "instagram.com",
    "tiktok.com",
    "pinterest.com",
    "reddit.com",
];

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct OnboardingExaUsage {
    pub request_count: u64,
    pub result_count: u64,
    pub summary_count: u64,
    pub text_count: u64,
}

impl OnboardingExaUsage {
    pub(super) fn add_assign(&mut self, other: Self) {
        self.request_count = self.request_count.saturating_add(other.request_count);
        self.result_count = self.result_count.saturating_add(other.result_count);
        self.summary_count = self.summary_count.saturating_add(other.summary_count);
        self.text_count = self.text_count.saturating_add(other.text_count);
    }
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct ExaSearchRequest<'a> {
    query: &'a str,
    num_results: usize,
    #[serde(skip_serializing_if = "Option::is_none")]
    exclude_domains: Option<Vec<&'static str>>,
    contents: ExaContents,
}

/// A short page excerpt per result, so the model selects on what a source covers rather than on
/// its title alone. No summaries or live crawling: those are the slow, costly parts.
#[derive(Debug, Serialize)]
struct ExaContents {
    text: ExaText,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct ExaText {
    max_characters: usize,
}

#[derive(Debug, Deserialize)]
struct ExaSearchResponse {
    #[serde(default)]
    results: Vec<ExaSearchRow>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(super) struct ExaSearchRow {
    pub(super) title: Option<String>,
    pub(super) url: String,
    pub(super) summary: Option<String>,
    pub(super) text: Option<String>,
    pub(super) published_date: Option<String>,
}

pub(super) async fn search_exa(
    client: &reqwest::Client,
    endpoint: Url,
    api_key: &SecretString,
    query: &str,
    num_results: usize,
    include_social: bool,
    timeout: Duration,
) -> Result<Vec<ExaSearchRow>, reqwest::Error> {
    let payload = ExaSearchRequest {
        query,
        num_results,
        exclude_domains: (!include_social).then(|| EXCLUDED_DOMAINS.to_vec()),
        contents: ExaContents {
            text: ExaText {
                max_characters: DISCOVERY_SNIPPET_CHARS,
            },
        },
    };
    let response = client
        .post(endpoint)
        .timeout(timeout)
        .header("x-api-key", api_key.expose_secret())
        .json(&payload)
        .send()
        .await?
        .error_for_status()?
        .json::<ExaSearchResponse>()
        .await?;
    Ok(response
        .results
        .into_iter()
        .filter(|result| !result.url.trim().is_empty())
        .collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn discovery_search_requests_only_a_short_text_excerpt() {
        let payload = serde_json::to_value(ExaSearchRequest {
            query: "piano RSS feeds",
            num_results: 8,
            exclude_domains: None,
            contents: ExaContents {
                text: ExaText {
                    max_characters: DISCOVERY_SNIPPET_CHARS,
                },
            },
        })
        .expect("Exa search request serializes");

        assert_eq!(payload["query"], "piano RSS feeds");
        assert_eq!(payload["numResults"], 8);
        assert_eq!(
            payload["contents"],
            serde_json::json!({ "text": { "maxCharacters": DISCOVERY_SNIPPET_CHARS } })
        );
    }
}
