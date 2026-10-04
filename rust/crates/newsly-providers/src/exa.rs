use std::time::Duration;

use reqwest::Url;
use secrecy::{ExposeSecret, SecretString};
use serde::{Deserialize, Serialize};
use serde_json::Value;

pub(crate) const EXCLUDED_DOMAINS: [&str; 8] = [
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
pub struct ExaSearchUsage {
    pub request_count: u64,
    pub result_count: u64,
    pub summary_count: u64,
    pub text_count: u64,
    pub estimated_cost_usd: Option<f64>,
    pub known_estimated_cost_usd: f64,
    pub missing_cost_estimate_count: u64,
}

impl ExaSearchUsage {
    pub(crate) fn add_assign(&mut self, other: Self) {
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

    pub(crate) fn record_search(&mut self, outcome: &ExaSearchOutcome) {
        let rows = &outcome.results;
        let count = |present: fn(&ExaSearchRow) -> bool| {
            u64::try_from(rows.iter().filter(|row| present(row)).count()).unwrap_or(u64::MAX)
        };
        self.add_assign(Self {
            request_count: 1,
            result_count: u64::try_from(rows.len()).unwrap_or(u64::MAX),
            summary_count: count(|row| nonempty(row.summary.as_deref())),
            text_count: count(|row| nonempty(row.text.as_deref())),
            estimated_cost_usd: outcome.estimated_cost_usd,
            known_estimated_cost_usd: outcome.estimated_cost_usd.unwrap_or_default(),
            missing_cost_estimate_count: u64::from(outcome.estimated_cost_usd.is_none()),
        });
    }

    pub(crate) fn record_unpriced_attempt(&mut self) {
        self.add_assign(Self {
            request_count: 1,
            missing_cost_estimate_count: 1,
            ..Self::default()
        });
    }
}

#[derive(Debug)]
pub(crate) struct ExaSearchOutcome {
    pub(crate) results: Vec<ExaSearchRow>,
    pub(crate) estimated_cost_usd: Option<f64>,
}

#[derive(Debug, Clone, Copy)]
pub(crate) struct ExaSearchOptions<'a> {
    pub(crate) num_results: usize,
    pub(crate) include_social: bool,
    pub(crate) text_max_characters: usize,
    pub(crate) livecrawl: Option<&'a str>,
    pub(crate) summary_query: Option<&'a str>,
    pub(crate) timeout: Option<Duration>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct ExaSearchRequest<'a> {
    query: &'a str,
    num_results: usize,
    #[serde(skip_serializing_if = "Option::is_none")]
    exclude_domains: Option<Vec<&'static str>>,
    contents: ExaContents<'a>,
}

#[derive(Debug, Serialize)]
struct ExaContents<'a> {
    #[serde(skip_serializing_if = "Option::is_none")]
    livecrawl: Option<&'a str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    summary: Option<ExaSummary<'a>>,
    text: ExaText,
}

#[derive(Debug, Serialize)]
struct ExaSummary<'a> {
    query: &'a str,
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
    cost_dollars: Option<Value>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct ExaSearchRow {
    pub(crate) title: Option<String>,
    #[serde(default)]
    pub(crate) url: String,
    pub(crate) summary: Option<String>,
    pub(crate) text: Option<String>,
    pub(crate) published_date: Option<String>,
}

pub(crate) async fn search_exa(
    client: &reqwest::Client,
    endpoint: Url,
    api_key: &SecretString,
    query: &str,
    options: ExaSearchOptions<'_>,
) -> Result<ExaSearchOutcome, reqwest::Error> {
    let request = client
        .post(endpoint)
        .header("x-api-key", api_key.expose_secret())
        .json(&ExaSearchRequest {
            query,
            num_results: options.num_results,
            exclude_domains: (!options.include_social).then(|| EXCLUDED_DOMAINS.to_vec()),
            contents: ExaContents {
                livecrawl: options.livecrawl,
                summary: options.summary_query.map(|query| ExaSummary { query }),
                text: ExaText {
                    max_characters: options.text_max_characters,
                },
            },
        });
    let request = if let Some(timeout) = options.timeout {
        request.timeout(timeout)
    } else {
        request
    };
    let response = request
        .send()
        .await?
        .error_for_status()?
        .json::<ExaSearchResponse>()
        .await?;
    Ok(ExaSearchOutcome {
        estimated_cost_usd: exa_estimated_cost_usd(response.cost_dollars.as_ref()),
        results: response
            .results
            .into_iter()
            .filter(|result| !result.url.trim().is_empty())
            .collect(),
    })
}

pub(crate) fn exa_estimated_cost_usd(cost_dollars: Option<&Value>) -> Option<f64> {
    cost_dollars
        .and_then(|value| value.get("total"))
        .and_then(Value::as_f64)
        .filter(|value| value.is_finite() && *value >= 0.0)
}

fn nonempty(value: Option<&str>) -> bool {
    value.is_some_and(|value| !value.trim().is_empty())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn request_options_preserve_onboarding_and_briefing_payloads() {
        let onboarding = ExaSearchRequest {
            query: "piano RSS feeds",
            num_results: 8,
            exclude_domains: None,
            contents: ExaContents {
                livecrawl: None,
                summary: None,
                text: ExaText {
                    max_characters: 280,
                },
            },
        };
        let briefing = ExaSearchRequest {
            query: "market news",
            num_results: 5,
            exclude_domains: Some(EXCLUDED_DOMAINS.to_vec()),
            contents: ExaContents {
                livecrawl: Some("fallback"),
                summary: Some(ExaSummary {
                    query: "Key points and main takeaways",
                }),
                text: ExaText {
                    max_characters: 1_500,
                },
            },
        };
        let onboarding = serde_json::to_value(onboarding).expect("request serializes");
        let briefing = serde_json::to_value(briefing).expect("request serializes");
        assert_eq!(
            onboarding["contents"],
            serde_json::json!({"text": {"maxCharacters": 280}})
        );
        assert_eq!(briefing["contents"]["livecrawl"], "fallback");
        assert_eq!(
            briefing["contents"]["summary"]["query"],
            "Key points and main takeaways"
        );
    }

    #[test]
    fn cost_estimate_accepts_only_finite_nonnegative_total() {
        assert_eq!(
            exa_estimated_cost_usd(Some(&serde_json::json!({"total": 0.0123}))),
            Some(0.0123)
        );
        for value in [
            serde_json::json!(null),
            serde_json::json!({"total": "unknown"}),
            serde_json::json!({"total": -0.01}),
        ] {
            assert_eq!(exa_estimated_cost_usd(Some(&value)), None);
        }
    }

    #[test]
    fn aggregate_cost_is_unknown_when_any_attempt_is_unpriced() {
        let mut usage = ExaSearchUsage::default();
        usage.add_assign(ExaSearchUsage {
            request_count: 1,
            estimated_cost_usd: Some(0.01),
            known_estimated_cost_usd: 0.01,
            ..ExaSearchUsage::default()
        });
        usage.record_unpriced_attempt();
        assert_eq!(usage.estimated_cost_usd, None);
        assert!((usage.known_estimated_cost_usd - 0.01).abs() < f64::EPSILON);
        assert_eq!(usage.missing_cost_estimate_count, 1);
    }
}
