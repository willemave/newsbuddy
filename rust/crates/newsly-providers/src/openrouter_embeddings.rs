use newsly_agent_runtime::ProviderUsage;
use reqwest::{Client, StatusCode, Url};
use secrecy::{ExposeSecret, SecretString};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use thiserror::Error;

use crate::openrouter::openrouter_provider_routing;

const MAX_EMBEDDING_BATCH: usize = 128;
const MAX_EMBEDDING_TEXT_CHARS: usize = 50_000;
const MAX_EMBEDDING_TOTAL_CHARS: usize = 1_000_000;
const MAX_PROVIDER_RESPONSE_BYTES: usize = 16 * 1024 * 1024;

#[derive(Debug, Clone)]
pub(crate) struct OpenRouterEmbeddingClient {
    client: Client,
    model: String,
    endpoint: Url,
    key: Option<SecretString>,
}

impl OpenRouterEmbeddingClient {
    pub(crate) fn new(
        client: Client,
        model_spec: &str,
        base_url: &str,
        key: Option<SecretString>,
    ) -> Result<Self, OpenRouterEmbeddingError> {
        let model = model_spec
            .strip_prefix("openrouter:")
            .filter(|model| !model.trim().is_empty())
            .ok_or_else(|| OpenRouterEmbeddingError::UnsupportedModel(model_spec.to_owned()))?
            .to_owned();
        let mut base = Url::parse(base_url)
            .map_err(|error| OpenRouterEmbeddingError::Url(error.to_string()))?;
        if !base.path().ends_with('/') {
            base.set_path(&format!("{}/", base.path().trim_end_matches('/')));
        }
        let endpoint = base
            .join("embeddings")
            .map_err(|error| OpenRouterEmbeddingError::Url(error.to_string()))?;
        Ok(Self {
            client,
            model,
            endpoint,
            key,
        })
    }

    pub(crate) fn model(&self) -> &str {
        &self.model
    }

    pub(crate) async fn embed(
        &self,
        texts: &[String],
    ) -> Result<OpenRouterEmbeddingBatch, OpenRouterEmbeddingError> {
        validate_input(texts)?;
        if texts.is_empty() {
            return Ok(OpenRouterEmbeddingBatch {
                vectors: Vec::new(),
                model: self.model.clone(),
                usage: ProviderUsage::default(),
                provider_response_id: None,
            });
        }
        let key = self
            .key
            .as_ref()
            .ok_or(OpenRouterEmbeddingError::Unavailable)?;
        let response = self
            .client
            .post(self.endpoint.clone())
            .bearer_auth(key.expose_secret())
            .json(&EmbeddingRequest {
                model: &self.model,
                input: texts,
                encoding_format: "float",
                provider: openrouter_provider_routing(),
            })
            .send()
            .await?;
        let status = response.status();
        let request_id = response
            .headers()
            .get("x-request-id")
            .and_then(|value| value.to_str().ok())
            .map(str::to_owned);
        let bytes = response.bytes().await?;
        if bytes.len() > MAX_PROVIDER_RESPONSE_BYTES {
            return Err(OpenRouterEmbeddingError::ResponseTooLarge(bytes.len()));
        }
        if !status.is_success() {
            return Err(OpenRouterEmbeddingError::Provider {
                status,
                message: String::from_utf8_lossy(&bytes)
                    .chars()
                    .take(1_000)
                    .collect(),
            });
        }
        let payload = serde_json::from_slice::<EmbeddingResponse>(&bytes)?;
        let mut rows = payload.data;
        rows.sort_by_key(|row| row.index);
        if rows.len() != texts.len()
            || rows
                .iter()
                .enumerate()
                .any(|(expected, row)| row.index != expected)
        {
            return Err(OpenRouterEmbeddingError::Shape {
                expected: texts.len(),
                actual: rows.len(),
            });
        }
        let dimension = rows.first().map_or(0, |row| row.embedding.len());
        if dimension == 0 || rows.iter().any(|row| row.embedding.len() != dimension) {
            return Err(OpenRouterEmbeddingError::Invalid(
                "provider returned empty or inconsistent vector dimensions".to_owned(),
            ));
        }
        let vectors = rows
            .into_iter()
            .map(|row| normalize_vector(row.embedding))
            .collect::<Result<Vec<_>, _>>()?;
        let usage = payload.usage.unwrap_or_default();
        Ok(OpenRouterEmbeddingBatch {
            vectors,
            model: payload.model.unwrap_or_else(|| self.model.clone()),
            usage: ProviderUsage {
                request_count: 1,
                input_tokens: usage.prompt_tokens.or(usage.total_tokens).unwrap_or(0),
                ..ProviderUsage::default()
            },
            provider_response_id: payload.id.or(request_id),
        })
    }
}

#[derive(Debug)]
pub(crate) struct OpenRouterEmbeddingBatch {
    pub(crate) vectors: Vec<Vec<f64>>,
    pub(crate) model: String,
    pub(crate) usage: ProviderUsage,
    pub(crate) provider_response_id: Option<String>,
}

#[derive(Debug, Serialize)]
struct EmbeddingRequest<'a> {
    model: &'a str,
    input: &'a [String],
    encoding_format: &'static str,
    provider: Value,
}

#[derive(Debug, Deserialize)]
struct EmbeddingResponse {
    #[serde(default)]
    id: Option<String>,
    #[serde(default)]
    model: Option<String>,
    data: Vec<EmbeddingRow>,
    #[serde(default)]
    usage: Option<EmbeddingUsage>,
}

#[derive(Debug, Deserialize)]
struct EmbeddingRow {
    index: usize,
    embedding: Vec<f64>,
}

#[derive(Debug, Default, Deserialize)]
struct EmbeddingUsage {
    prompt_tokens: Option<u64>,
    total_tokens: Option<u64>,
}

fn validate_input(texts: &[String]) -> Result<(), OpenRouterEmbeddingError> {
    if texts.len() > MAX_EMBEDDING_BATCH {
        return Err(OpenRouterEmbeddingError::TooManyInputs(texts.len()));
    }
    let mut total = 0_usize;
    for text in texts {
        let count = text.chars().count();
        if count == 0 || count > MAX_EMBEDDING_TEXT_CHARS {
            return Err(OpenRouterEmbeddingError::InvalidInputLength(count));
        }
        total = total.saturating_add(count);
    }
    if total > MAX_EMBEDDING_TOTAL_CHARS {
        return Err(OpenRouterEmbeddingError::InputTooLarge(total));
    }
    Ok(())
}

fn normalize_vector(vector: Vec<f64>) -> Result<Vec<f64>, OpenRouterEmbeddingError> {
    if vector.iter().any(|value| !value.is_finite()) {
        return Err(OpenRouterEmbeddingError::Invalid(
            "provider returned a non-finite vector component".to_owned(),
        ));
    }
    let norm = vector.iter().map(|value| value * value).sum::<f64>().sqrt();
    if !norm.is_finite() || norm <= 1e-12 {
        return Err(OpenRouterEmbeddingError::Invalid(
            "provider returned a zero-length vector".to_owned(),
        ));
    }
    Ok(vector.into_iter().map(|value| value / norm).collect())
}

#[derive(Debug, Error)]
pub enum OpenRouterEmbeddingError {
    #[error("OpenRouter is required for hosted embeddings")]
    Unavailable,
    #[error("unsupported embedding model {0}; expected an openrouter: model")]
    UnsupportedModel(String),
    #[error("embedding request has {0} inputs; the maximum is {MAX_EMBEDDING_BATCH}")]
    TooManyInputs(usize),
    #[error("embedding input has invalid character length {0}")]
    InvalidInputLength(usize),
    #[error("embedding request has {0} total characters; limit is {MAX_EMBEDDING_TOTAL_CHARS}")]
    InputTooLarge(usize),
    #[error("embedding provider returned HTTP {status}: {message}")]
    Provider { status: StatusCode, message: String },
    #[error("embedding response has {0} bytes and exceeds the response limit")]
    ResponseTooLarge(usize),
    #[error("embedding response shape mismatch: expected {expected} rows, got {actual}")]
    Shape { expected: usize, actual: usize },
    #[error("invalid embedding: {0}")]
    Invalid(String),
    #[error(transparent)]
    Http(#[from] reqwest::Error),
    #[error("invalid provider URL: {0}")]
    Url(String),
    #[error(transparent)]
    Json(#[from] serde_json::Error),
}

impl OpenRouterEmbeddingError {
    pub(crate) fn retryable(&self) -> bool {
        match self {
            Self::Http(error) => crate::public_http::retryable_http_error(error),
            Self::Provider { status, .. } => {
                status.is_server_error() || matches!(status.as_u16(), 408 | 429)
            }
            _ => false,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn embedding_vectors_are_finite_nonzero_and_normalized() {
        let vector = normalize_vector(vec![3.0, 4.0]).expect("valid vector");
        assert!((vector[0] - 0.6).abs() < 1e-12);
        assert!((vector[1] - 0.8).abs() < 1e-12);
        assert!(normalize_vector(vec![0.0, 0.0]).is_err());
        assert!(normalize_vector(vec![f64::NAN]).is_err());
    }

    #[test]
    fn embedding_request_places_privacy_fields_directly_under_provider() {
        let texts = vec!["hello".to_owned()];
        let request = EmbeddingRequest {
            model: "qwen/qwen3-embedding-8b",
            input: &texts,
            encoding_format: "float",
            provider: openrouter_provider_routing(),
        };

        let value = serde_json::to_value(request).expect("embedding request should serialize");
        assert_eq!(value["provider"]["zdr"], json!(true));
        assert_eq!(value["provider"]["data_collection"], json!("deny"));
        assert!(value["provider"].get("provider").is_none());
    }
}
