use std::time::Duration;

use reqwest::Url;
use secrecy::{ExposeSecret, SecretString};
use serde::{Deserialize, Serialize};

use super::{DISCOVERY_SNIPPET_CHARS, clean};

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

#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub struct OnboardingExaUsage {
    pub request_count: u64,
    pub result_count: u64,
    pub summary_count: u64,
    pub text_count: u64,
    pub estimated_cost_usd: Option<f64>,
    pub known_estimated_cost_usd: f64,
    pub missing_cost_estimate_count: u64,
}

impl OnboardingExaUsage {
    pub(super) fn add_assign(&mut self, other: Self) {
        if other.request_count > 0 {
            self.estimated_cost_usd = if self.request_count == 0 {
                other.estimated_cost_usd
            } else {
                self.estimated_cost_usd
                    .zip(other.estimated_cost_usd)
                    .map(|(left, right)| left + right)
            };
        }
        self.request_count = self.request_count.saturating_add(other.request_count);
        self.result_count = self.result_count.saturating_add(other.result_count);
        self.summary_count = self.summary_count.saturating_add(other.summary_count);
        self.text_count = self.text_count.saturating_add(other.text_count);
        self.known_estimated_cost_usd += other.known_estimated_cost_usd;
        self.missing_cost_estimate_count = self
            .missing_cost_estimate_count
            .saturating_add(other.missing_cost_estimate_count);
    }

    pub(super) fn record_search(&mut self, outcome: &ExaSearchOutcome) {
        let rows = &outcome.results;
        let count = |present: fn(&ExaSearchRow) -> bool| {
            u64::try_from(rows.iter().filter(|row| present(row)).count()).unwrap_or(u64::MAX)
        };
        self.add_assign(Self {
            request_count: 1,
            result_count: u64::try_from(rows.len()).unwrap_or(u64::MAX),
            summary_count: count(|row| clean(row.summary.clone()).is_some()),
            text_count: count(|row| clean(row.text.clone()).is_some()),
            estimated_cost_usd: outcome.estimated_cost_usd,
            known_estimated_cost_usd: outcome.estimated_cost_usd.unwrap_or_default(),
            missing_cost_estimate_count: u64::from(outcome.estimated_cost_usd.is_none()),
        });
    }

    pub(super) fn record_unpriced_attempt(&mut self) {
        self.add_assign(Self {
            request_count: 1,
            missing_cost_estimate_count: 1,
            ..Self::default()
        });
    }
}

#[derive(Debug)]
pub(super) struct ExaSearchOutcome {
    pub(super) results: Vec<ExaSearchRow>,
    pub(super) estimated_cost_usd: Option<f64>,
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
    #[serde(rename = "costDollars")]
    cost_dollars: Option<serde_json::Value>,
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
) -> Result<ExaSearchOutcome, reqwest::Error> {
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
    let estimated_cost_usd = exa_estimated_cost_usd(response.cost_dollars.as_ref());
    let results = response
        .results
        .into_iter()
        .filter(|result| !result.url.trim().is_empty())
        .collect();
    Ok(ExaSearchOutcome {
        results,
        estimated_cost_usd,
    })
}

fn exa_estimated_cost_usd(cost_dollars: Option<&serde_json::Value>) -> Option<f64> {
    cost_dollars
        .and_then(|value| value.get("total"))
        .and_then(serde_json::Value::as_f64)
        .filter(|value| value.is_finite() && *value >= 0.0)
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

    #[test]
    fn onboarding_cost_is_known_only_when_every_attempt_has_an_estimate() {
        let row = ExaSearchRow {
            title: None,
            url: "https://example.com".to_owned(),
            summary: None,
            text: Some("evidence".to_owned()),
            published_date: None,
        };
        let mut usage = OnboardingExaUsage::default();
        usage.record_search(&ExaSearchOutcome {
            results: vec![row],
            estimated_cost_usd: Some(0.01),
        });
        usage.record_search(&ExaSearchOutcome {
            results: Vec::new(),
            estimated_cost_usd: Some(0.02),
        });
        assert!(
            usage
                .estimated_cost_usd
                .is_some_and(|cost| (cost - 0.03).abs() < f64::EPSILON)
        );
        assert_eq!(usage.missing_cost_estimate_count, 0);
        assert!((usage.known_estimated_cost_usd - 0.03).abs() < f64::EPSILON);

        usage.record_unpriced_attempt();
        assert_eq!(usage.estimated_cost_usd, None);
        assert!((usage.known_estimated_cost_usd - 0.03).abs() < f64::EPSILON);
        assert_eq!(usage.missing_cost_estimate_count, 1);
        assert_eq!(usage.request_count, 3);
    }

    #[test]
    fn malformed_exa_cost_is_unknown_without_rejecting_the_response() {
        for value in [
            serde_json::json!({"results": []}),
            serde_json::json!({"results": [], "costDollars": null}),
            serde_json::json!({"results": [], "costDollars": {"total": "unknown"}}),
            serde_json::json!({"results": [], "costDollars": {"total": -0.01}}),
        ] {
            let response: ExaSearchResponse =
                serde_json::from_value(value).expect("malformed estimate must not reject response");
            assert_eq!(exa_estimated_cost_usd(response.cost_dollars.as_ref()), None);
        }
    }

    #[test]
    fn empty_usage_groups_do_not_poison_a_complete_estimate() {
        let mut usage = OnboardingExaUsage::default();
        usage.add_assign(OnboardingExaUsage::default());
        usage.record_search(&ExaSearchOutcome {
            results: Vec::new(),
            estimated_cost_usd: Some(0.007),
        });
        usage.add_assign(OnboardingExaUsage::default());

        assert_eq!(usage.request_count, 1);
        assert_eq!(usage.result_count, 0);
        assert_eq!(usage.estimated_cost_usd, Some(0.007));
        assert!((usage.known_estimated_cost_usd - 0.007).abs() < f64::EPSILON);
        assert_eq!(usage.missing_cost_estimate_count, 0);
    }
}
