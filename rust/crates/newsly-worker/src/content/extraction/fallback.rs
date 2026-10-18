//! Shared credit admission happens only after local extraction requests paid work.
use super::super::FirecrawlUsage;
use super::super::firecrawl::FirecrawlError;
use super::super::firecrawl_accounting::FirecrawlRecorder;
use super::super::model::{ExtractedArticle, UsageWrite};
use super::{
    ContentExtractionRuntime, ExtractionAttempt, client_failure, firecrawl_failure, usage_write,
};
use chrono::{DateTime, Utc};
use newsly_db::{
    FirecrawlAdmission, FirecrawlCreditTransition, acquire_firecrawl_admission,
    observe_firecrawl_response,
};
use newsly_extraction::{ExtractIntent, ExtractionFallbackRequired, PublicUrl};

impl ContentExtractionRuntime {
    #[allow(clippy::too_many_arguments)]
    pub(super) async fn firecrawl_fallback(
        &self,
        fallback: ExtractionFallbackRequired,
        intent: ExtractIntent,
        original_url: &str,
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
        if self.demand_policy == super::FirecrawlDemandPolicy::NoSubscriberDemand {
            return ExtractionAttempt::Failure {
                reason: format!(
                    "Firecrawl fallback was not permitted because the global aggregator item is not visible to any active subscriber: {}",
                    fallback.reason
                ),
                code: newsly_db::aggregator_corpus::FIRECRAWL_NO_DEMAND_ERROR_CODE.to_owned(),
                retryable: false,
                usage,
            };
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
        let probe_token = if let Some(pool) = &self.credit_control {
            match acquire_firecrawl_admission(pool).await {
                Ok(FirecrawlAdmission::Allowed { probe_token }) => probe_token,
                Ok(FirecrawlAdmission::Paused { resume_at }) => return deferred(resume_at, usage),
                Err(error) => {
                    tracing::error!(%error, "Firecrawl credit admission unavailable; paid dispatch deferred");
                    return deferred(Utc::now() + chrono::Duration::seconds(60), usage);
                }
            }
        } else {
            None
        };
        let request_id = format!("{request_id_base}-firecrawl");
        let recorder = match (&self.credit_control, &self.attribution) {
            (Some(pool), Some(attribution)) => Some(FirecrawlRecorder::new(
                pool.clone(),
                *attribution,
                request_id.clone(),
                fallback.reason.clone(),
            )),
            (Some(_), None) => {
                tracing::error!("Firecrawl task attribution missing; paid dispatch deferred");
                return deferred(Utc::now() + chrono::Duration::seconds(60), usage);
            }
            (None, _) => None,
        };
        let attempt = tokio::time::timeout(
            remaining,
            self.firecrawl.scrape(&fallback.url, recorder.as_ref()),
        )
        .await;
        let status = match &attempt {
            Ok(Ok(result)) if result.error.is_none() => result.status_code,
            Ok(Ok(result)) if result.status_code == 402 => 402,
            Ok(Err(
                FirecrawlError::HttpStatus { status, .. }
                | FirecrawlError::InvalidResponse { status, .. }
                | FirecrawlError::ResponseTooLarge { status },
            )) if status.as_u16() == 402 => 402,
            _ => 0,
        };
        let mut resume_at = Utc::now() + chrono::Duration::minutes(15);
        if let Some(pool) = &self.credit_control {
            match observe_firecrawl_response(pool, probe_token, status).await {
                Ok(FirecrawlCreditTransition::Paused {
                    resume_at: paused_until,
                }) => resume_at = paused_until,
                Ok(FirecrawlCreditTransition::Recovered) => tracing::info!(
                    event = "firecrawl_credit_recovered",
                    "Firecrawl shared credit pause cleared"
                ),
                Ok(FirecrawlCreditTransition::Unchanged) => {}
                Err(error) => tracing::error!(%error, "could not observe Firecrawl credit state"),
            }
        }
        if probe_token.is_some() && !(200..300).contains(&status) {
            return deferred(resume_at, usage);
        }
        let result = match attempt {
            Ok(Ok(result)) => result,
            Ok(Err(error)) if status != 402 => return firecrawl_failure(&error, usage),
            Ok(Err(_)) => return deferred(resume_at, usage),
            Err(_) => {
                return ExtractionAttempt::Failure {
                    reason: "Firecrawl fallback exceeded the task deadline".to_owned(),
                    code: "deadline_exceeded".to_owned(),
                    retryable: true,
                    usage,
                };
            }
        };
        // Production records billing at the response boundary, before task finalization.
        // Standalone fixture runtimes retain the in-memory projection for persistence tests.
        if recorder.is_none() {
            usage.push(UsageWrite::Firecrawl(FirecrawlUsage {
                request_id,
                url: fallback.url.to_string(),
                status_code: result.status_code,
                credit_cost_usd: result.credit_cost_usd,
                credits_used: result.credits_used,
                num_pages: result.num_pages,
                total_pages: result.total_pages,
                scrape_id: result.scrape_id.clone(),
                cost_usd: result.cost_usd,
            }));
        }
        if status == 402 {
            return deferred(resume_at, usage);
        }
        if let Some(message) = result.error {
            return firecrawl_failure(
                &FirecrawlError::HttpStatus {
                    status: reqwest::StatusCode::from_u16(result.status_code)
                        .unwrap_or(reqwest::StatusCode::INTERNAL_SERVER_ERROR),
                    message,
                },
                usage,
            );
        }
        let Some(markdown) = result.markdown else {
            return firecrawl_failure(&FirecrawlError::EmptyMarkdown, usage);
        };
        let final_url = match PublicUrl::parse(result.final_url.trim()) {
            Ok(url) => url,
            Err(error) => return client_failure(&error, usage),
        };
        if let Err(error) = final_url.validate_dns().await {
            return client_failure(&error, usage);
        }
        ExtractionAttempt::Success {
            article: ExtractedArticle {
                original_url: original_url.to_owned(),
                final_url: final_url.to_string(),
                title: result.title.unwrap_or_else(|| "Untitled".to_owned()),
                author: None,
                published_at: result.published_at,
                body: markdown,
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

fn deferred(resume_at: DateTime<Utc>, usage: Vec<UsageWrite>) -> ExtractionAttempt {
    ExtractionAttempt::Deferred {
        reason: "Firecrawl credit prerequisite unavailable; full article extraction deferred"
            .to_owned(),
        resume_at,
        usage,
    }
}
