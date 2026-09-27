//! Exa search transport and the usage units it reports for onboarding.

use super::{
    DISCOVERY_SNIPPET_CHARS, Duration, EXCLUDED_DOMAINS, ExposeSecret, OnboardingDiscoverySeeds,
    OnboardingProfile, SecretString, Url, clean,
};
use serde::{Deserialize, Serialize};

/// Exa search units observed during one onboarding operation.
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

    /// Counts one successful search request and the result contents it returned.
    pub(super) fn record_search(&mut self, rows: &[ExaSearchRow]) {
        let count = |present: fn(&ExaSearchRow) -> bool| {
            u64::try_from(rows.iter().filter(|row| present(row)).count()).unwrap_or(u64::MAX)
        };
        self.add_assign(Self {
            request_count: 1,
            result_count: u64::try_from(rows.len()).unwrap_or(u64::MAX),
            summary_count: count(|row| clean(row.summary.clone()).is_some()),
            text_count: count(|row| clean(row.text.clone()).is_some()),
        });
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OnboardingProfileOutcome {
    pub profile: OnboardingProfile,
    pub exa_usage: OnboardingExaUsage,
}

#[derive(Debug, Clone, PartialEq)]
pub struct OnboardingDiscoveryOutcome {
    pub seeds: OnboardingDiscoverySeeds,
    pub exa_usage: OnboardingExaUsage,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub(super) struct ExaSearchRequest<'a> {
    pub(super) query: &'a str,
    pub(super) num_results: usize,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(super) exclude_domains: Option<Vec<&'static str>>,
    pub(super) contents: ExaContents,
}

/// A short page excerpt per result, so the model selects on what a source covers rather than on
/// its title alone. No summaries or live crawling: those are the slow, costly parts.
#[derive(Debug, Serialize)]
pub(super) struct ExaContents {
    pub(super) text: ExaText,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub(super) struct ExaText {
    pub(super) max_characters: usize,
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
