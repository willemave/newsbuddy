use chrono::{DateTime, Utc};
use schemars::JsonSchema;
use serde::{Deserialize, Deserializer, Serialize};

use crate::{EXTRACTION_SCHEMA_VERSION, ExtractionClientError, PublicUrl};

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum ExtractIntent {
    StaticAnalyze,
    ExtractArticle,
    ResolvePubmed,
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum ExtractionProfile {
    #[default]
    Automatic,
    Article,
    Newsletter,
    Scientific,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum ExtractionMethod {
    StaticReadability,
    Crawl4ai,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum FallbackKind {
    Firecrawl,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum ExtractionFailureCode {
    AccessGate,
    CrawlFailed,
    DeadlineExceeded,
    FetchFailed,
    InternalError,
    InvalidUrl,
    NoContent,
    ResponseTooLarge,
    UnsupportedSchema,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ExtractOptions {
    pub profile: ExtractionProfile,
    pub allow_browser_fallback: bool,
    pub discover_feeds: bool,
    pub max_download_bytes: usize,
    pub max_markdown_bytes: usize,
    pub static_minimum_characters: usize,
    pub browser_timeout_ms: u64,
}

impl Default for ExtractOptions {
    fn default() -> Self {
        Self {
            profile: ExtractionProfile::Automatic,
            allow_browser_fallback: true,
            discover_feeds: true,
            max_download_bytes: 5_000_000,
            max_markdown_bytes: 1_000_000,
            static_minimum_characters: 400,
            browser_timeout_ms: 90_000,
        }
    }
}

impl ExtractOptions {
    fn validate(&self) -> Result<(), ExtractionClientError> {
        if !(65_536..=10_000_000).contains(&self.max_download_bytes) {
            return Err(ExtractionClientError::InvalidRequest(
                "max_download_bytes must be between 65536 and 10000000",
            ));
        }
        if !(4_096..=2_000_000).contains(&self.max_markdown_bytes) {
            return Err(ExtractionClientError::InvalidRequest(
                "max_markdown_bytes must be between 4096 and 2000000",
            ));
        }
        if !(100..=10_000).contains(&self.static_minimum_characters) {
            return Err(ExtractionClientError::InvalidRequest(
                "static_minimum_characters must be between 100 and 10000",
            ));
        }
        if !(1_000..=180_000).contains(&self.browser_timeout_ms) {
            return Err(ExtractionClientError::InvalidRequest(
                "browser_timeout_ms must be between 1000 and 180000",
            ));
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct TraceContext {
    #[serde(deserialize_with = "deserialize_required_option")]
    #[schemars(required)]
    pub trace_id: Option<String>,
    #[serde(deserialize_with = "deserialize_required_option")]
    #[schemars(required)]
    pub span_id: Option<String>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ExtractRequest {
    pub schema_version: u16,
    pub request_id: String,
    pub url: PublicUrl,
    pub intent: ExtractIntent,
    pub absolute_deadline: DateTime<Utc>,
    pub options: ExtractOptions,
    pub trace: TraceContext,
}

impl ExtractRequest {
    pub fn new(
        request_id: impl Into<String>,
        url: PublicUrl,
        intent: ExtractIntent,
        absolute_deadline: DateTime<Utc>,
    ) -> Self {
        Self {
            schema_version: EXTRACTION_SCHEMA_VERSION,
            request_id: request_id.into(),
            url,
            intent,
            absolute_deadline,
            options: ExtractOptions::default(),
            trace: TraceContext::default(),
        }
    }

    pub(crate) fn validate(&self) -> Result<(), ExtractionClientError> {
        if self.schema_version != EXTRACTION_SCHEMA_VERSION {
            return Err(ExtractionClientError::SchemaVersion {
                expected: EXTRACTION_SCHEMA_VERSION,
                actual: self.schema_version,
            });
        }
        validate_identifier(&self.request_id)?;
        validate_optional_identifier(self.trace.trace_id.as_deref())?;
        validate_optional_identifier(self.trace.span_id.as_deref())?;
        self.options.validate()?;
        if self.absolute_deadline <= Utc::now() {
            return Err(ExtractionClientError::InvalidRequest(
                "absolute_deadline must be in the future",
            ));
        }
        Ok(())
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct UsageEvent {
    pub kind: String,
    pub quantity: u64,
    pub unit: String,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ExtractionTiming {
    pub name: String,
    pub milliseconds: u64,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ExtractionSuccess {
    pub schema_version: u16,
    pub request_id: String,
    pub final_url: PublicUrl,
    pub title: String,
    #[serde(deserialize_with = "deserialize_required_option")]
    #[schemars(required)]
    pub author: Option<String>,
    #[serde(deserialize_with = "deserialize_required_option")]
    #[schemars(required)]
    pub published_at: Option<DateTime<Utc>>,
    pub markdown: String,
    pub tables: Vec<String>,
    pub feed_links: Vec<PublicUrl>,
    pub method: ExtractionMethod,
    pub warnings: Vec<String>,
    pub usage_events: Vec<UsageEvent>,
    pub timings: Vec<ExtractionTiming>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct PubMedDelegation {
    pub schema_version: u16,
    pub request_id: String,
    pub next_url: PublicUrl,
    pub reason: String,
    pub warnings: Vec<String>,
    pub usage_events: Vec<UsageEvent>,
    pub timings: Vec<ExtractionTiming>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ExtractionFallbackRequired {
    pub schema_version: u16,
    pub request_id: String,
    pub fallback: FallbackKind,
    pub url: PublicUrl,
    pub reason: String,
    pub retryable: bool,
    pub usage_events: Vec<UsageEvent>,
    pub timings: Vec<ExtractionTiming>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ExtractionFailure {
    pub schema_version: u16,
    pub request_id: String,
    pub code: ExtractionFailureCode,
    pub retryable: bool,
    #[serde(deserialize_with = "deserialize_required_option")]
    #[schemars(required)]
    pub http_status: Option<u16>,
    pub message: String,
    pub timings: Vec<ExtractionTiming>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum ExtractResult {
    Success(ExtractionSuccess),
    Delegation(PubMedDelegation),
    FallbackRequired(ExtractionFallbackRequired),
    Failure(ExtractionFailure),
}

impl ExtractResult {
    pub fn schema_version(&self) -> u16 {
        match self {
            Self::Success(result) => result.schema_version,
            Self::Delegation(result) => result.schema_version,
            Self::FallbackRequired(result) => result.schema_version,
            Self::Failure(result) => result.schema_version,
        }
    }

    pub fn request_id(&self) -> &str {
        match self {
            Self::Success(result) => &result.request_id,
            Self::Delegation(result) => &result.request_id,
            Self::FallbackRequired(result) => &result.request_id,
            Self::Failure(result) => &result.request_id,
        }
    }

    pub(crate) fn validate_bounds(&self) -> Result<(), ExtractionClientError> {
        let (timings, usage_events): (&[ExtractionTiming], &[UsageEvent]) = match self {
            Self::Success(result) => {
                if !string_is_bounded(&result.title, 1, 1_000) {
                    return Err(ExtractionClientError::InvalidResponseBounds("title"));
                }
                if !string_is_bounded(&result.markdown, 1, 2_000_000) {
                    return Err(ExtractionClientError::InvalidResponseBounds("markdown"));
                }
                if result
                    .author
                    .as_ref()
                    .is_some_and(|value| !string_is_bounded(value, 1, 500))
                {
                    return Err(ExtractionClientError::InvalidResponseBounds("author"));
                }
                if result.tables.len() > 50
                    || result
                        .tables
                        .iter()
                        .any(|value| !string_is_bounded(value, 1, 250_000))
                {
                    return Err(ExtractionClientError::InvalidResponseBounds("tables"));
                }
                if result.feed_links.len() > 50
                    || !strings_are_bounded(&result.warnings, 25, 1, 500)
                {
                    return Err(ExtractionClientError::InvalidResponseBounds(
                        "feed_links or warnings",
                    ));
                }
                (&result.timings, &result.usage_events)
            }
            Self::Delegation(result) => {
                if result.reason != "pubmed_full_text"
                    || !strings_are_bounded(&result.warnings, 25, 1, 500)
                {
                    return Err(ExtractionClientError::InvalidResponseBounds(
                        "delegation reason or warnings",
                    ));
                }
                (&result.timings, &result.usage_events)
            }
            Self::FallbackRequired(result) => {
                if !string_is_bounded(&result.reason, 1, 2_000) {
                    return Err(ExtractionClientError::InvalidResponseBounds(
                        "fallback reason",
                    ));
                }
                (&result.timings, &result.usage_events)
            }
            Self::Failure(result) => {
                if !string_is_bounded(&result.message, 1, 2_000) {
                    return Err(ExtractionClientError::InvalidResponseBounds(
                        "failure message",
                    ));
                }
                if result
                    .http_status
                    .is_some_and(|status| !(100..=599).contains(&status))
                {
                    return Err(ExtractionClientError::InvalidResponseBounds(
                        "failure http_status",
                    ));
                }
                (&result.timings, &[])
            }
        };
        if timings.len() > 25
            || timings.iter().any(|timing| {
                !string_is_bounded(&timing.name, 1, 64) || timing.milliseconds > 3_600_000
            })
            || usage_events.len() > 25
            || usage_events.iter().any(|event| {
                !string_is_bounded(&event.kind, 1, 64)
                    || event.quantity > 100_000_000
                    || !string_is_bounded(&event.unit, 1, 32)
            })
        {
            return Err(ExtractionClientError::InvalidResponseBounds(
                "timings or usage_events",
            ));
        }
        Ok(())
    }

    pub(crate) async fn validate_public_urls(&self) -> Result<(), ExtractionClientError> {
        match self {
            Self::Success(result) => {
                result.final_url.validate_dns().await?;
                for feed_url in &result.feed_links {
                    feed_url.validate_dns().await?;
                }
            }
            Self::Delegation(result) => result.next_url.validate_dns().await?,
            Self::FallbackRequired(result) => result.url.validate_dns().await?,
            Self::Failure(_) => {}
        }
        Ok(())
    }
}

fn validate_identifier(value: &str) -> Result<(), ExtractionClientError> {
    if value.is_empty()
        || value.len() > 128
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || b"._:-".contains(&byte))
    {
        return Err(ExtractionClientError::InvalidRequest(
            "request and trace identifiers must be 1-128 safe ASCII characters",
        ));
    }
    Ok(())
}

fn validate_optional_identifier(value: Option<&str>) -> Result<(), ExtractionClientError> {
    value.map_or(Ok(()), validate_identifier)
}

fn deserialize_required_option<'de, D, T>(deserializer: D) -> Result<Option<T>, D::Error>
where
    D: Deserializer<'de>,
    T: Deserialize<'de>,
{
    Option::<T>::deserialize(deserializer)
}

fn string_is_bounded(value: &str, minimum: usize, maximum: usize) -> bool {
    let length = value.chars().count();
    (minimum..=maximum).contains(&length)
}

fn strings_are_bounded(
    values: &[String],
    maximum_items: usize,
    minimum_length: usize,
    maximum_length: usize,
) -> bool {
    values.len() <= maximum_items
        && values
            .iter()
            .all(|value| string_is_bounded(value, minimum_length, maximum_length))
}

#[cfg(test)]
mod tests {
    use serde_json::Value;

    use super::{ExtractRequest, ExtractResult};

    #[test]
    fn language_neutral_golden_deserializes() {
        let fixture: Value = serde_json::from_str(include_str!(
            "../../../../contracts/extraction/crawl4ai-golden.json"
        ))
        .expect("valid golden fixture");
        for case in fixture["cases"].as_array().expect("golden cases") {
            let request: ExtractRequest =
                serde_json::from_value(case["request"].clone()).expect("valid request");
            let result: ExtractResult =
                serde_json::from_value(case["expected"].clone()).expect("valid result");

            assert_eq!(result.request_id(), request.request_id);
        }
    }

    #[test]
    fn request_requires_options_and_trace_on_the_wire() {
        let fixture: Value = serde_json::from_str(include_str!(
            "../../../../contracts/extraction/crawl4ai-golden.json"
        ))
        .expect("valid golden fixture");
        for field in ["options", "trace"] {
            let mut request = fixture["cases"][0]["request"].clone();
            request
                .as_object_mut()
                .expect("request object")
                .remove(field);
            assert!(serde_json::from_value::<ExtractRequest>(request).is_err());
        }
    }

    #[test]
    fn nullable_fields_are_required_on_the_wire() {
        let fixture: Value = serde_json::from_str(include_str!(
            "../../../../contracts/extraction/crawl4ai-golden.json"
        ))
        .expect("valid golden fixture");

        let mut request = fixture["cases"][0]["request"].clone();
        request["trace"]
            .as_object_mut()
            .expect("trace object")
            .remove("trace_id");
        assert!(serde_json::from_value::<ExtractRequest>(request).is_err());

        let mut result = fixture["cases"][0]["expected"].clone();
        result
            .as_object_mut()
            .expect("result object")
            .remove("author");
        assert!(serde_json::from_value::<ExtractResult>(result).is_err());
    }
}
