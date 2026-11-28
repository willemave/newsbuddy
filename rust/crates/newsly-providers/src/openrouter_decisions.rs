//! Shared single-attempt `OpenRouter` / `TypeSafe` Jev decision client, billing, and probability
//! validation helpers used by `news_filter` and `news_categories`.

use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

use reqwest::{Client, StatusCode};
use secrecy::{ExposeSecret, SecretString};
use serde::{Deserialize, Serialize};

pub(crate) const JEV_REQUEST_MODEL: &str = "typesafe/jev-1.14";
pub(crate) const JEV_RESPONSE_MODEL: &str = "typesafe/jev-1.14-20261014";
pub(crate) const DECISIONS_ENDPOINT: &str = "https://openrouter.ai/api/alpha/decisions";
pub(crate) const DECISIONS_DEADLINE: Duration = Duration::from_secs(5);
pub(crate) const MAX_DECISIONS_BYTES: usize = 64 * 1024;
const INPUT_RATE_USD: f64 = 0.042 / 1_000_000.0;

#[derive(Serialize)]
pub(crate) struct Evidence<'a> {
    pub(crate) title: &'a str,
    pub(crate) summary: &'a str,
    pub(crate) key_points: &'a [String],
}

#[derive(Serialize)]
pub(crate) struct Routing {
    only: [&'static str; 1],
    allow_fallbacks: bool,
    require_parameters: bool,
    data_collection: &'static str,
    zdr: bool,
}

impl Routing {
    pub(crate) const fn typesafe_zdr() -> Self {
        Self {
            only: ["typesafe"],
            allow_fallbacks: false,
            require_parameters: true,
            data_collection: "deny",
            zdr: true,
        }
    }
}

pub(crate) fn validate_evidence(title: &str, summary: &str, key_points: &[String]) -> bool {
    let evidence_bytes =
        title.len() + summary.len() + key_points.iter().map(String::len).sum::<usize>();
    !title.trim().is_empty()
        && !summary.trim().is_empty()
        && title.len() <= 1_024
        && summary.len() <= 8_192
        && key_points.len() <= 16
        && key_points.iter().all(|point| point.len() <= 2_048)
        && evidence_bytes <= 16_384
}

#[derive(Debug, Clone, PartialEq)]
pub(crate) struct MeteredBilling {
    pub(crate) input_tokens: u64,
    pub(crate) output_tokens: u64,
    pub(crate) cost_usd: f64,
    pub(crate) is_byok: bool,
    pub(crate) generation_id: Option<String>,
}

#[derive(Deserialize)]
struct ResponseUsage {
    input_tokens: u64,
    output_tokens: u64,
    cost: f64,
    #[serde(default)]
    is_byok: bool,
}

pub(crate) fn observed_billing(response: &serde_json::Value) -> Option<MeteredBilling> {
    // Billing is independent evidence: preserve bounded provider-reported charges even when its
    // route, decision, or frozen price contract is wrong.
    let usage: ResponseUsage = serde_json::from_value(response["usage"].clone()).ok()?;
    if !usage.cost.is_finite()
        || !(0.0..=100.0).contains(&usage.cost)
        || usage.input_tokens > 10_000_000
        || usage.output_tokens > 10_000_000
    {
        return None;
    }
    let generation_id = response["id"]
        .as_str()
        .filter(|id| !id.trim().is_empty() && id.chars().count() <= 100)
        .map(str::to_owned);
    Some(MeteredBilling {
        input_tokens: usage.input_tokens,
        output_tokens: usage.output_tokens,
        cost_usd: usage.cost,
        is_byok: usage.is_byok,
        generation_id,
    })
}

pub(crate) fn validate_envelope(
    response: &serde_json::Value,
    reserved_tokens: u64,
) -> Result<(MeteredBilling, String), EnvelopeValidationError> {
    let billing = observed_billing(response).ok_or(EnvelopeValidationError::Unmetered)?;
    if response["model"].as_str() != Some(JEV_RESPONSE_MODEL)
        || response["provider"].as_str() != Some("TypeSafe")
    {
        return Err(EnvelopeValidationError::Metered {
            code: "route_mismatch",
            billing,
        });
    }
    let Some(generation_id) = billing.generation_id.clone() else {
        return Err(EnvelopeValidationError::Metered {
            code: "generation_id",
            billing,
        });
    };
    if !response["error"].is_null() {
        return Err(EnvelopeValidationError::Metered {
            code: "provider_error",
            billing,
        });
    }
    let Ok(input_tokens) = u32::try_from(billing.input_tokens) else {
        return Err(EnvelopeValidationError::Metered {
            code: "metered_usage",
            billing,
        });
    };
    let expected_cost = f64::from(input_tokens) * INPUT_RATE_USD;
    if billing.is_byok
        || billing.input_tokens > reserved_tokens
        || (billing.cost_usd - expected_cost).abs()
            > (billing.cost_usd.abs().max(expected_cost.abs()) * 1e-6).max(1e-12)
    {
        return Err(EnvelopeValidationError::Metered {
            code: "metered_usage",
            billing,
        });
    }
    Ok((billing, generation_id))
}

pub(crate) fn validate_probabilities(
    values: &[f64],
    selected: f64,
    non_cent_tolerance: f64,
) -> Result<(), &'static str> {
    if values
        .iter()
        .any(|value| !value.is_finite() || !(0.0..=1.0).contains(value))
    {
        return Err("probability_range");
    }
    let cent_rounded = values
        .iter()
        .all(|value| (value - (value * 100.0).round() / 100.0).abs() < 1e-12);
    let tolerance = if cent_rounded {
        f64::from(u32::try_from(values.len()).expect("bounded decision options")) * 0.005 + 1e-12
    } else {
        non_cent_tolerance
    };
    if (values.iter().sum::<f64>() - 1.0).abs() > tolerance {
        return Err("probability_sum");
    }
    if values.iter().any(|value| selected < value - 1e-8) {
        return Err("choice_probability_mismatch");
    }
    Ok(())
}

pub(crate) enum EnvelopeValidationError {
    Unmetered,
    Metered {
        code: &'static str,
        billing: MeteredBilling,
    },
}

pub(crate) enum DecisionsTransportError {
    Unavailable,
    PayloadTooLarge,
    ResponseTooLarge,
    ProviderStatus(StatusCode),
    MeteredHttpError(MeteredBilling),
    Http(reqwest::Error),
    Json(serde_json::Error),
}

#[derive(Debug, Clone)]
pub(crate) struct DecisionsClient {
    client: Client,
    key: Option<SecretString>,
}

impl DecisionsClient {
    pub(crate) fn new(key: Option<SecretString>) -> Result<Self, reqwest::Error> {
        Ok(Self {
            client: Client::builder()
                .timeout(DECISIONS_DEADLINE)
                .redirect(reqwest::redirect::Policy::none())
                .retry(reqwest::retry::never())
                .build()?,
            key,
        })
    }

    pub(crate) async fn post_json<T: Serialize>(
        &self,
        endpoint: &str,
        body: &T,
        http_started: Option<&AtomicBool>,
    ) -> Result<(serde_json::Value, u64), DecisionsTransportError> {
        let key = self
            .key
            .as_ref()
            .filter(|k| !k.expose_secret().trim().is_empty())
            .ok_or(DecisionsTransportError::Unavailable)?;
        let encoded = serde_json::to_vec(body).map_err(DecisionsTransportError::Json)?;
        if encoded.len() > MAX_DECISIONS_BYTES {
            return Err(DecisionsTransportError::PayloadTooLarge);
        }
        let reserved_tokens = encoded.len() as u64 + 4_096;
        let request = self
            .client
            .post(endpoint)
            .bearer_auth(key.expose_secret())
            .header(reqwest::header::CONTENT_TYPE, "application/json")
            .body(encoded)
            .build()
            .map_err(DecisionsTransportError::Http)?;
        if let Some(marker) = http_started {
            marker.store(true, Ordering::Release);
        }
        let response = self
            .client
            .execute(request)
            .await
            .map_err(DecisionsTransportError::Http)?;
        let status = response.status();
        let bytes = crate::read_bounded_response_bytes(response, MAX_DECISIONS_BYTES)
            .await
            .map_err(|error| match error {
                crate::BoundedBodyError::Transport(error) => DecisionsTransportError::Http(error),
                crate::BoundedBodyError::TooLarge(_) => DecisionsTransportError::ResponseTooLarge,
            })?;
        let raw = serde_json::from_slice::<serde_json::Value>(&bytes);
        if !status.is_success() {
            return Err(match raw.as_ref().ok().and_then(observed_billing) {
                Some(billing) => DecisionsTransportError::MeteredHttpError(billing),
                None => DecisionsTransportError::ProviderStatus(status),
            });
        }
        Ok((raw.map_err(DecisionsTransportError::Json)?, reserved_tokens))
    }
}
