use chrono::{DateTime, Utc};
use newsly_extraction::{
    DocumentExtractorClient, ExtractIntent, ExtractRequest, ExtractResult, ExtractionClientError,
    ExtractionFailure, ExtractionFailureCode, ExtractionFallbackRequired, ExtractionMethod,
    ExtractionSuccess, PubMedDelegation, PublicUrl,
};

use super::firecrawl::{FirecrawlClient, FirecrawlError};
use super::model::{
    ExtractedArticle, ExtractionUsageBatch, FeedCandidate, FirecrawlUsage, UsageWrite,
};

#[derive(Debug, Clone)]
pub struct ContentExtractionRuntime {
    extractor: DocumentExtractorClient,
    firecrawl: FirecrawlClient,
}

#[derive(Debug, Clone)]
pub(crate) enum ExtractionAttempt {
    Success {
        article: ExtractedArticle,
        usage: Vec<UsageWrite>,
    },
    Failure {
        reason: String,
        code: String,
        retryable: bool,
        usage: Vec<UsageWrite>,
    },
}

impl ContentExtractionRuntime {
    pub const fn new(extractor: DocumentExtractorClient, firecrawl: FirecrawlClient) -> Self {
        Self {
            extractor,
            firecrawl,
        }
    }

    pub(super) async fn analyze(
        &self,
        url: &str,
        request_id_base: &str,
        deadline: DateTime<Utc>,
    ) -> ExtractionAttempt {
        let public_url = match PublicUrl::parse(url) {
            Ok(url) => url,
            Err(error) => return client_failure(&error, Vec::new()),
        };
        self.extract_one(
            public_url,
            ExtractIntent::StaticAnalyze,
            &format!("{request_id_base}-analyze"),
            deadline,
            url,
            "article",
            Vec::new(),
        )
        .await
    }

    pub(crate) async fn process_article(
        &self,
        url: &str,
        content_type: &str,
        request_id_base: &str,
        deadline: DateTime<Utc>,
    ) -> ExtractionAttempt {
        let original_url = url.to_owned();
        let public_url = match PublicUrl::parse(url) {
            Ok(url) => url,
            Err(error) => return client_failure(&error, Vec::new()),
        };
        if is_pubmed_article(&public_url) {
            return self
                .process_pubmed(
                    public_url,
                    &original_url,
                    content_type,
                    request_id_base,
                    deadline,
                )
                .await;
        }
        self.extract_one(
            public_url,
            ExtractIntent::ExtractArticle,
            &format!("{request_id_base}-article"),
            deadline,
            &original_url,
            content_type,
            Vec::new(),
        )
        .await
    }

    async fn process_pubmed(
        &self,
        public_url: PublicUrl,
        original_url: &str,
        content_type: &str,
        request_id_base: &str,
        deadline: DateTime<Utc>,
    ) -> ExtractionAttempt {
        let request = ExtractRequest::new(
            format!("{request_id_base}-pubmed"),
            public_url,
            ExtractIntent::ResolvePubmed,
            deadline,
        );
        let result = match self.extractor.extract(&request).await {
            Ok(result) => result,
            Err(error) => return client_failure(&error, Vec::new()),
        };
        match result {
            ExtractResult::Delegation(delegation) => {
                if delegation.reason != "pubmed_full_text" {
                    return unsupported_pubmed_delegation(delegation);
                }
                let usage = usage_write(
                    delegation.request_id.clone(),
                    ExtractIntent::ResolvePubmed,
                    None,
                    delegation.usage_events.clone(),
                )
                .into_iter()
                .collect();
                let mut attempt = self
                    .extract_one(
                        delegation.next_url,
                        ExtractIntent::ExtractArticle,
                        &format!("{request_id_base}-article"),
                        deadline,
                        original_url,
                        content_type,
                        usage,
                    )
                    .await;
                if let ExtractionAttempt::Success { article, .. } = &mut attempt {
                    let mut warnings = delegation.warnings;
                    warnings.append(&mut article.warnings);
                    article.warnings = warnings;
                    let mut timings = delegation.timings;
                    timings.append(&mut article.timings);
                    article.timings = timings;
                }
                attempt
            }
            ExtractResult::Failure(failure) => typed_failure(failure, Vec::new()),
            ExtractResult::Success(success) => success_attempt(
                success,
                ExtractIntent::ResolvePubmed,
                original_url,
                content_type,
                Vec::new(),
            ),
            ExtractResult::FallbackRequired(fallback) => {
                self.firecrawl_fallback(
                    fallback,
                    ExtractIntent::ResolvePubmed,
                    original_url,
                    content_type,
                    request_id_base,
                    deadline,
                    Vec::new(),
                )
                .await
            }
        }
    }

    #[allow(clippy::too_many_arguments)]
    async fn extract_one(
        &self,
        url: PublicUrl,
        intent: ExtractIntent,
        request_id: &str,
        deadline: DateTime<Utc>,
        original_url: &str,
        content_type: &str,
        mut usage: Vec<UsageWrite>,
    ) -> ExtractionAttempt {
        let request = ExtractRequest::new(request_id, url, intent, deadline);
        let result = match self.extractor.extract(&request).await {
            Ok(result) => result,
            Err(error) => return client_failure(&error, usage),
        };
        match result {
            ExtractResult::Success(success) => {
                success_attempt(success, intent, original_url, content_type, usage)
            }
            ExtractResult::Failure(failure) => typed_failure(failure, usage),
            ExtractResult::FallbackRequired(fallback) => {
                self.firecrawl_fallback(
                    fallback,
                    intent,
                    original_url,
                    content_type,
                    request_id,
                    deadline,
                    usage,
                )
                .await
            }
            ExtractResult::Delegation(delegation) => {
                if let Some(write) =
                    usage_write(delegation.request_id, intent, None, delegation.usage_events)
                {
                    usage.push(write);
                }
                ExtractionAttempt::Failure {
                    reason: "document extractor delegated outside PubMed resolution".to_owned(),
                    code: "unsupported_delegation".to_owned(),
                    retryable: false,
                    usage,
                }
            }
        }
    }

    #[allow(clippy::too_many_arguments)]
    async fn firecrawl_fallback(
        &self,
        fallback: ExtractionFallbackRequired,
        intent: ExtractIntent,
        original_url: &str,
        _content_type: &str,
        request_id_base: &str,
        deadline: DateTime<Utc>,
        mut usage: Vec<UsageWrite>,
    ) -> ExtractionAttempt {
        if let Some(write) = usage_write(
            fallback.request_id.clone(),
            intent,
            None,
            fallback.usage_events,
        ) {
            usage.push(write);
        }
        let remaining = match (deadline - Utc::now()).to_std() {
            Ok(remaining) if !remaining.is_zero() => remaining,
            _ => {
                return ExtractionAttempt::Failure {
                    reason: "Firecrawl fallback deadline elapsed".to_owned(),
                    code: "deadline_exceeded".to_owned(),
                    retryable: true,
                    usage,
                };
            }
        };
        let result =
            match tokio::time::timeout(remaining, self.firecrawl.scrape(&fallback.url)).await {
                Ok(Ok(result)) => result,
                Ok(Err(error)) => {
                    return firecrawl_failure(&error, usage);
                }
                Err(_) => {
                    return ExtractionAttempt::Failure {
                        reason: "Firecrawl fallback exceeded the task deadline".to_owned(),
                        code: "deadline_exceeded".to_owned(),
                        retryable: true,
                        usage,
                    };
                }
            };
        let firecrawl_request_id = format!("{request_id_base}-firecrawl");
        usage.push(UsageWrite::Firecrawl(FirecrawlUsage {
            request_id: firecrawl_request_id,
            url: fallback.url.to_string(),
            status_code: result.status_code,
            cost_usd: result.cost_usd,
        }));
        let title = result.title.unwrap_or_else(|| "Untitled".to_owned());
        let final_url = result.final_url.to_string();
        ExtractionAttempt::Success {
            article: ExtractedArticle {
                original_url: original_url.to_owned(),
                final_url: final_url.clone(),
                title,
                author: None,
                published_at: result.published_at,
                body: result.markdown,
                feed_candidates: Vec::new(),
                extraction_method: "firecrawl".to_owned(),
                warnings: vec![format!(
                    "document extractor requested Firecrawl fallback: {}",
                    fallback.reason
                )],
                timings: fallback.timings,
                used_firecrawl: true,
            },
            usage,
        }
    }
}

fn success_attempt(
    success: ExtractionSuccess,
    intent: ExtractIntent,
    original_url: &str,
    content_type: &str,
    mut usage: Vec<UsageWrite>,
) -> ExtractionAttempt {
    if let Some(write) = usage_write(
        success.request_id.clone(),
        intent,
        Some(success.method),
        success.usage_events.clone(),
    ) {
        usage.push(write);
    }
    let mut body = success.markdown.trim().to_owned();
    let tables = success
        .tables
        .iter()
        .filter(|table| !table.trim().is_empty())
        .map(|table| table.trim())
        .collect::<Vec<_>>();
    if !tables.is_empty() {
        body.push_str("\n\n## Extracted Tables\n");
        body.push_str(&tables.join("\n\n"));
    }
    let final_url = success.final_url.to_string();
    let feed_candidates = success
        .feed_links
        .iter()
        .map(|url| feed_candidate(url, content_type, &success.title))
        .collect();
    ExtractionAttempt::Success {
        article: ExtractedArticle {
            original_url: original_url.to_owned(),
            final_url,
            title: success.title,
            author: success.author,
            published_at: success.published_at,
            body,
            feed_candidates,
            extraction_method: extraction_method(success.method).to_owned(),
            warnings: success.warnings,
            timings: success.timings,
            used_firecrawl: false,
        },
        usage,
    }
}

fn usage_write(
    request_id: String,
    intent: ExtractIntent,
    method: Option<ExtractionMethod>,
    events: Vec<newsly_extraction::UsageEvent>,
) -> Option<UsageWrite> {
    (!events.is_empty()).then_some(UsageWrite::Extraction(ExtractionUsageBatch {
        request_id,
        intent,
        method,
        events,
    }))
}

fn typed_failure(failure: ExtractionFailure, usage: Vec<UsageWrite>) -> ExtractionAttempt {
    ExtractionAttempt::Failure {
        reason: failure.message,
        code: failure_code(failure.code).to_owned(),
        retryable: failure.retryable,
        usage,
    }
}

fn unsupported_pubmed_delegation(delegation: PubMedDelegation) -> ExtractionAttempt {
    let usage = usage_write(
        delegation.request_id,
        ExtractIntent::ResolvePubmed,
        None,
        delegation.usage_events,
    )
    .into_iter()
    .collect();
    ExtractionAttempt::Failure {
        reason: "document extractor returned an unsupported PubMed delegation".to_owned(),
        code: "unsupported_delegation".to_owned(),
        retryable: false,
        usage,
    }
}

fn client_failure(error: &ExtractionClientError, usage: Vec<UsageWrite>) -> ExtractionAttempt {
    let retryable = match &error {
        ExtractionClientError::DnsResolution { .. }
        | ExtractionClientError::NoDnsAddresses(_)
        | ExtractionClientError::Timeout
        | ExtractionClientError::Transport(_) => true,
        ExtractionClientError::HttpStatus { status, .. } => {
            status.is_server_error()
                || *status == reqwest::StatusCode::REQUEST_TIMEOUT
                || *status == reqwest::StatusCode::TOO_MANY_REQUESTS
        }
        ExtractionClientError::InvalidPublicUrl { .. }
        | ExtractionClientError::NonPublicAddress(_)
        | ExtractionClientError::InvalidConfiguration(_)
        | ExtractionClientError::InvalidRequest(_)
        | ExtractionClientError::ResponseTooLarge { .. }
        | ExtractionClientError::InvalidResponse(_)
        | ExtractionClientError::SchemaVersion { .. }
        | ExtractionClientError::RequestIdMismatch
        | ExtractionClientError::InvalidResponseBounds(_) => false,
    };
    ExtractionAttempt::Failure {
        reason: error.to_string(),
        code: "extractor_client".to_owned(),
        retryable,
        usage,
    }
}

fn firecrawl_failure(error: &FirecrawlError, usage: Vec<UsageWrite>) -> ExtractionAttempt {
    ExtractionAttempt::Failure {
        reason: error.to_string(),
        code: "firecrawl".to_owned(),
        retryable: error.retryable(),
        usage,
    }
}

fn is_pubmed_article(url: &PublicUrl) -> bool {
    let parsed = url.as_url();
    parsed
        .host_str()
        .is_some_and(|host| host.eq_ignore_ascii_case("pubmed.ncbi.nlm.nih.gov"))
        && parsed
            .path()
            .trim_matches('/')
            .chars()
            .all(|character| character.is_ascii_digit())
        && !parsed.path().trim_matches('/').is_empty()
}

fn feed_candidate(url: &PublicUrl, content_type: &str, page_title: &str) -> FeedCandidate {
    let host = url
        .as_url()
        .host_str()
        .unwrap_or_default()
        .to_ascii_lowercase();
    let feed_type = if host == "substack.com" || host.ends_with(".substack.com") {
        "substack"
    } else if content_type == "podcast" {
        "podcast_rss"
    } else {
        "atom"
    };
    FeedCandidate {
        url: canonicalize_url(url.as_url()),
        feed_type: feed_type.to_owned(),
        title: (!page_title.trim().is_empty()).then(|| page_title.trim().to_owned()),
    }
}

fn canonicalize_url(url: &url::Url) -> String {
    let mut canonical = url.clone();
    canonical.set_fragment(None);
    if canonical.path() != "/" {
        let path = canonical.path().trim_end_matches('/').to_owned();
        canonical.set_path(&path);
    }
    canonical.to_string()
}

fn extraction_method(method: ExtractionMethod) -> &'static str {
    match method {
        ExtractionMethod::StaticReadability => "static_readability",
        ExtractionMethod::Crawl4ai => "crawl4ai",
    }
}

pub(super) const fn intent_name(intent: ExtractIntent) -> &'static str {
    match intent {
        ExtractIntent::StaticAnalyze => "static_analyze",
        ExtractIntent::ExtractArticle => "extract_article",
        ExtractIntent::ResolvePubmed => "resolve_pubmed",
    }
}

pub(super) fn method_name(method: Option<ExtractionMethod>) -> &'static str {
    method.map_or("policy-v1", extraction_method)
}

fn failure_code(code: ExtractionFailureCode) -> &'static str {
    match code {
        ExtractionFailureCode::AccessGate => "access_gate",
        ExtractionFailureCode::CrawlFailed => "crawl_failed",
        ExtractionFailureCode::DeadlineExceeded => "deadline_exceeded",
        ExtractionFailureCode::FetchFailed => "fetch_failed",
        ExtractionFailureCode::InternalError => "internal_error",
        ExtractionFailureCode::InvalidUrl => "invalid_url",
        ExtractionFailureCode::NoContent => "no_content",
        ExtractionFailureCode::ResponseTooLarge => "response_too_large",
        ExtractionFailureCode::UnsupportedSchema => "unsupported_schema",
    }
}

#[cfg(test)]
mod tests {
    use newsly_extraction::{ExtractIntent, ExtractionSuccess};
    use serde_json::json;

    use super::{ExtractionAttempt, is_pubmed_article, success_attempt};

    #[test]
    fn success_projection_keeps_tables_feeds_and_usage() {
        let success: ExtractionSuccess = serde_json::from_value(json!({
            "schema_version": 1,
            "request_id": "worker-fixture-1",
            "final_url": "https://example.substack.com/post",
            "title": "Fixture Article",
            "author": null,
            "published_at": null,
            "markdown": "# Fixture Article\n\nBody",
            "tables": ["| A |\n| - |\n| 1 |"],
            "feed_links": ["https://example.substack.com/feed"],
            "method": "crawl4ai",
            "warnings": [],
            "usage_events": [
                {"kind": "downloaded_body", "quantity": 24, "unit": "byte"}
            ],
            "timings": []
        }))
        .unwrap();

        let attempt = success_attempt(
            success,
            ExtractIntent::ExtractArticle,
            "https://example.com/original",
            "article",
            Vec::new(),
        );
        let ExtractionAttempt::Success { article, usage } = attempt else {
            panic!("fixture success must remain successful");
        };
        assert!(article.body.contains("## Extracted Tables"));
        assert_eq!(article.feed_candidates.len(), 1);
        assert_eq!(article.feed_candidates[0].feed_type, "substack");
        assert_eq!(usage.len(), 1);
    }

    #[test]
    fn pubmed_detection_requires_a_numeric_article_path() {
        let article =
            newsly_extraction::PublicUrl::parse("https://pubmed.ncbi.nlm.nih.gov/12345678/")
                .unwrap();
        let search =
            newsly_extraction::PublicUrl::parse("https://pubmed.ncbi.nlm.nih.gov/?term=rust")
                .unwrap();
        assert!(is_pubmed_article(&article));
        assert!(!is_pubmed_article(&search));
    }
}
