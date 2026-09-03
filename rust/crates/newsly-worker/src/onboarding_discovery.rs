use std::collections::HashSet;
use std::sync::Arc;

use newsly_db::{
    NewOnboardingSuggestion, OnboardingAttemptStatus, OnboardingTaskSnapshot,
    PrepareOnboardingTaskOutcome, complete_onboarding_discovery_task,
    ensure_weekly_discovery_session, prepare_onboarding_discovery_task,
    settle_onboarding_discovery_attempt,
};
use newsly_providers::{
    FeedValidationError, FeedValidator, OnboardingAudioLane, OnboardingDiscoverySeeds,
    OnboardingGateway, OnboardingLaneTarget, OnboardingSuggestionSeed,
};
use newsly_queue::{OwnedWorkPlan, TaskResult, TaskType};
use serde_json::Value;
use sqlx::{PgPool, Postgres, Transaction};

use crate::{
    HandlerExecution, HandlerFinalizerFuture, HandlerFuture, LeaseHealth, TaskFinalizer,
    TaskFinalizerResult, TaskHandler,
};

const MAX_SUGGESTIONS_PER_KIND: usize = 5;

#[derive(Debug, Clone)]
pub struct OnboardingDiscoveryWorkerServices {
    pool: PgPool,
    provider: OnboardingGateway,
    feed_validator: FeedValidator,
    max_retries: i32,
}

impl OnboardingDiscoveryWorkerServices {
    pub const fn new(
        pool: PgPool,
        provider: OnboardingGateway,
        feed_validator: FeedValidator,
        max_retries: i32,
    ) -> Self {
        Self {
            pool,
            provider,
            feed_validator,
            max_retries,
        }
    }
}

#[derive(Debug, Clone)]
pub struct OnboardingDiscoverHandler {
    services: Arc<OnboardingDiscoveryWorkerServices>,
}

impl OnboardingDiscoverHandler {
    pub fn new(services: Arc<OnboardingDiscoveryWorkerServices>) -> Self {
        Self { services }
    }
}

impl TaskHandler for OnboardingDiscoverHandler {
    fn task_type(&self) -> TaskType {
        TaskType::OnboardingDiscover
    }

    fn execute(&self, plan: Arc<OwnedWorkPlan>, lease: LeaseHealth) -> HandlerFuture<'_> {
        let services = Arc::clone(&self.services);
        Box::pin(async move { execute_discovery(&services, &plan, &lease).await })
    }
}

#[derive(Debug, Clone)]
struct OnboardingRequest {
    user_id: i64,
    run_id: i64,
}

async fn execute_discovery(
    services: &OnboardingDiscoveryWorkerServices,
    task: &OwnedWorkPlan,
    lease: &LeaseHealth,
) -> HandlerExecution {
    let request = match parse_request(task) {
        Ok(request) => request,
        Err(error) => {
            return HandlerExecution::from_result(TaskResult::fail(Some(error), false));
        }
    };
    execute_audio_discovery(services, task, lease, request.user_id, request.run_id).await
}

async fn execute_audio_discovery(
    services: &OnboardingDiscoveryWorkerServices,
    task: &OwnedWorkPlan,
    lease: &LeaseHealth,
    user_id: i64,
    run_id: i64,
) -> HandlerExecution {
    let snapshot = match prepare_onboarding_discovery_task(
        &services.pool,
        task.task_id,
        task.retry_count,
        user_id,
        run_id,
    )
    .await
    {
        Ok(PrepareOnboardingTaskOutcome::Ready(snapshot)) => snapshot,
        Ok(
            PrepareOnboardingTaskOutcome::AlreadyCompleted
            | PrepareOnboardingTaskOutcome::Superseded,
        ) => {
            return HandlerExecution::from_result(TaskResult::ok());
        }
        Ok(PrepareOnboardingTaskOutcome::MissingOrInactive) => {
            return HandlerExecution::from_result(TaskResult::fail(
                Some("Onboarding discovery run was not found for the active owner".to_owned()),
                false,
            ));
        }
        Err(error) => {
            return HandlerExecution::from_result(TaskResult::fail(Some(error.to_string()), true));
        }
    };
    if lease.ownership_lost() {
        return HandlerExecution::from_result(TaskResult::fail(
            Some("Onboarding discovery lease was lost before provider work".to_owned()),
            true,
        ));
    }
    let lanes = match provider_lanes(&snapshot) {
        Ok(lanes) => lanes,
        Err(error) => {
            return audio_failure(services, task, snapshot, error, false);
        }
    };
    let seeds = match services
        .provider
        .discover_from_lanes(&snapshot.topic_summary, &snapshot.inferred_topics, &lanes)
        .await
    {
        Ok(seeds) => seeds,
        Err(error) => {
            return audio_failure(services, task, snapshot, error.to_string(), true);
        }
    };
    if lease.ownership_lost() {
        return HandlerExecution::from_result(TaskResult::fail(
            Some("Onboarding discovery lease was lost after provider work".to_owned()),
            true,
        ));
    }
    let suggestions = match normalize_seeds(
        &services.feed_validator,
        seeds,
        &snapshot.topic_summary,
        &snapshot.inferred_topics,
    )
    .await
    {
        Ok(suggestions) => suggestions,
        Err(error) => {
            return audio_failure(services, task, snapshot, error.to_string(), true);
        }
    };
    HandlerExecution::with_finalizer(
        TaskResult::ok(),
        OnboardingSuccessFinalizer {
            task_id: task.task_id,
            retry_count: task.retry_count,
            snapshot,
            suggestions,
        },
    )
}

fn audio_failure(
    services: &OnboardingDiscoveryWorkerServices,
    task: &OwnedWorkPlan,
    snapshot: OnboardingTaskSnapshot,
    error: impl Into<String>,
    retryable: bool,
) -> HandlerExecution {
    let error = error.into();
    let exhausted = !retryable || task.retry_count >= services.max_retries;
    HandlerExecution::with_finalizer(
        TaskResult::fail(Some(error.clone()), retryable),
        OnboardingFailureFinalizer {
            task_id: task.task_id,
            retry_count: task.retry_count,
            run_id: snapshot.run_id,
            user_id: snapshot.user_id,
            status: if exhausted {
                OnboardingAttemptStatus::Failed
            } else {
                OnboardingAttemptStatus::Pending
            },
            error,
        },
    )
}

fn parse_request(task: &OwnedWorkPlan) -> Result<OnboardingRequest, String> {
    let user_id = task
        .owner_user_id
        .filter(|value| *value > 0)
        .ok_or_else(|| "onboarding_discover requires a positive owner user_id".to_owned())?;
    if task.payload.get("user_id").and_then(Value::as_i64) != Some(user_id) {
        return Err("onboarding_discover owner and payload user_id must match".to_owned());
    }
    let run_id = task
        .payload
        .get("run_id")
        .and_then(Value::as_i64)
        .filter(|value| *value > 0)
        .ok_or_else(|| "onboarding_discover requires a positive run_id".to_owned())?;
    Ok(OnboardingRequest { user_id, run_id })
}

fn provider_lanes(snapshot: &OnboardingTaskSnapshot) -> Result<Vec<OnboardingAudioLane>, String> {
    snapshot
        .lanes
        .iter()
        .map(|lane| {
            let target = match lane.target.trim().to_ascii_lowercase().as_str() {
                "feeds" | "feed" | "substack" | "atom" => OnboardingLaneTarget::Feeds,
                "podcasts" | "podcast" | "podcast_rss" => OnboardingLaneTarget::Podcasts,
                "reddit" | "subreddit" => OnboardingLaneTarget::Reddit,
                other => return Err(format!("unsupported onboarding lane target {other:?}")),
            };
            Ok(OnboardingAudioLane {
                name: lane.name.clone(),
                goal: lane.goal.clone(),
                target,
                queries: lane.queries.clone(),
            })
        })
        .collect()
}

pub(crate) async fn normalize_seeds(
    validator: &FeedValidator,
    seeds: OnboardingDiscoverySeeds,
    profile_summary: &str,
    inferred_topics: &[String],
) -> Result<Vec<NewOnboardingSuggestion>, FeedValidationError> {
    let mut output = Vec::new();
    let mut validation_failures = normalize_feed_seeds(
        validator,
        seeds.substacks,
        "substack",
        profile_summary,
        inferred_topics,
        &mut output,
    )
    .await;
    validation_failures.extend(
        normalize_feed_seeds(
            validator,
            seeds.podcasts,
            "podcast_rss",
            profile_summary,
            inferred_topics,
            &mut output,
        )
        .await,
    );
    let validated_feed_count = output.len();
    normalize_subreddits(
        seeds.subreddits,
        profile_summary,
        inferred_topics,
        &mut output,
    );
    if validated_feed_count == 0
        && let Some(error) = validation_failures.into_iter().next()
    {
        return Err(error);
    }
    Ok(output)
}

async fn normalize_feed_seeds(
    validator: &FeedValidator,
    seeds: Vec<OnboardingSuggestionSeed>,
    suggestion_type: &str,
    profile_summary: &str,
    inferred_topics: &[String],
    output: &mut Vec<NewOnboardingSuggestion>,
) -> Vec<FeedValidationError> {
    let mut seen = HashSet::new();
    let mut requested = HashSet::new();
    let mut candidates = Vec::new();
    for seed in seeds {
        let site_url = clean_optional(seed.site_url, 2_048);
        let mut candidate = clean_optional(seed.feed_url, 2_048)
            .or_else(|| clean_optional(seed.candidate_feed_url, 2_048));
        if candidate.is_none() && suggestion_type == "substack" {
            candidate = site_url
                .as_deref()
                .map(|value| format!("{}/feed", value.trim_end_matches('/')));
        }
        if candidate.is_none() && seed.is_likely_feed == Some(true) {
            candidate = site_url.as_deref().and_then(infer_feed_url);
        }
        let Some(candidate) = candidate else {
            continue;
        };
        if requested.insert(candidate.clone()) {
            candidates.push((seed.title, seed.rationale, seed.score, site_url, candidate));
        }
    }
    let urls = candidates
        .iter()
        .map(|(_, _, _, _, candidate)| candidate.clone())
        .collect::<Vec<_>>();
    let validations = validator.validate_feeds(&urls).await;
    let mut failures = Vec::new();
    for ((title, rationale, score, site_url, candidate), validation) in
        candidates.into_iter().zip(validations)
    {
        let Some(feed_url) = (match validation {
            Ok(Some(validated))
                if suggestion_type == "podcast_rss" && !validated.has_audio_entries =>
            {
                tracing::debug!(
                    url = %candidate,
                    "onboarding podcast candidate had no audio entries"
                );
                None
            }
            Ok(validated) => validated.map(|feed| feed.effective_url),
            Err(error) => {
                tracing::warn!(
                    url = %candidate,
                    error = %error,
                    "onboarding feed candidate validation failed"
                );
                failures.push(error);
                None
            }
        }) else {
            continue;
        };
        let feed_url = newsly_db::canonicalize_feed_url(&feed_url);
        if !seen.insert(feed_url.clone()) {
            continue;
        }
        let title = clean_optional(title, 500);
        let rationale = clean_optional(rationale, 2_000).or_else(|| {
            Some(default_rationale(
                suggestion_type,
                title.as_deref().unwrap_or(&feed_url),
                profile_summary,
                inferred_topics,
            ))
        });
        output.push(NewOnboardingSuggestion {
            suggestion_type: suggestion_type.to_owned(),
            title,
            site_url,
            feed_url: Some(feed_url),
            subreddit: None,
            rationale,
            score: normalized_score(score),
        });
        if seen.len() >= MAX_SUGGESTIONS_PER_KIND {
            break;
        }
    }
    failures
}

fn normalize_subreddits(
    seeds: Vec<OnboardingSuggestionSeed>,
    profile_summary: &str,
    inferred_topics: &[String],
    output: &mut Vec<NewOnboardingSuggestion>,
) {
    let mut seen = HashSet::new();
    for seed in seeds {
        let site_url = clean_optional(seed.site_url, 2_048);
        let subreddit = clean_optional(seed.subreddit, 255)
            .and_then(|value| normalize_subreddit(&value))
            .or_else(|| site_url.as_deref().and_then(extract_subreddit));
        let Some(subreddit) = subreddit else {
            continue;
        };
        if !seen.insert(subreddit.to_ascii_lowercase()) {
            continue;
        }
        let title = clean_optional(seed.title, 500).or_else(|| Some(subreddit.clone()));
        let rationale = clean_optional(seed.rationale, 2_000).or_else(|| {
            Some(default_rationale(
                "reddit",
                title.as_deref().unwrap_or(&subreddit),
                profile_summary,
                inferred_topics,
            ))
        });
        output.push(NewOnboardingSuggestion {
            suggestion_type: "reddit".to_owned(),
            title,
            site_url,
            feed_url: None,
            subreddit: Some(subreddit),
            rationale,
            score: normalized_score(seed.score),
        });
        if seen.len() >= MAX_SUGGESTIONS_PER_KIND {
            break;
        }
    }
}

#[derive(Debug)]
struct OnboardingSuccessFinalizer {
    task_id: i64,
    retry_count: i32,
    snapshot: OnboardingTaskSnapshot,
    suggestions: Vec<NewOnboardingSuggestion>,
}

impl OnboardingSuccessFinalizer {
    async fn apply_inner(
        &self,
        transaction: &mut Transaction<'static, Postgres>,
    ) -> Result<TaskFinalizerResult, Box<dyn std::error::Error + Send + Sync>> {
        if !complete_onboarding_discovery_task(
            transaction,
            self.task_id,
            self.retry_count,
            &self.snapshot,
            &self.suggestions,
        )
        .await?
        {
            return Ok(TaskFinalizerResult::Keep);
        }
        let user_id = self.snapshot.user_id;
        ensure_weekly_discovery_session(transaction, user_id).await?;
        Ok(TaskFinalizerResult::Keep)
    }
}

impl TaskFinalizer for OnboardingSuccessFinalizer {
    fn apply<'a>(
        &'a self,
        transaction: &'a mut Transaction<'static, Postgres>,
    ) -> HandlerFinalizerFuture<'a> {
        Box::pin(async move { self.apply_inner(transaction).await })
    }
}

#[derive(Debug)]
struct OnboardingFailureFinalizer {
    task_id: i64,
    retry_count: i32,
    run_id: i64,
    user_id: i64,
    status: OnboardingAttemptStatus,
    error: String,
}

impl TaskFinalizer for OnboardingFailureFinalizer {
    fn apply<'a>(
        &'a self,
        transaction: &'a mut Transaction<'static, Postgres>,
    ) -> HandlerFinalizerFuture<'a> {
        Box::pin(async move {
            settle_onboarding_discovery_attempt(
                transaction,
                self.task_id,
                self.retry_count,
                self.run_id,
                self.user_id,
                self.status,
                &self.error,
            )
            .await?;
            Ok(TaskFinalizerResult::Keep)
        })
    }
}

fn infer_feed_url(site_url: &str) -> Option<String> {
    let trimmed = site_url.trim().trim_end_matches('/');
    if trimmed.starts_with("https://") || trimmed.starts_with("http://") {
        Some(format!("{trimmed}/feed"))
    } else {
        None
    }
}

fn normalize_subreddit(value: &str) -> Option<String> {
    let value = value.trim().trim_matches('/');
    let value = value
        .strip_prefix("r/")
        .or_else(|| value.strip_prefix("R/"))
        .unwrap_or(value);
    if value.is_empty()
        || value.len() > 100
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_')
    {
        return None;
    }
    Some(value.to_owned())
}

fn extract_subreddit(url: &str) -> Option<String> {
    let parsed = reqwest::Url::parse(url).ok()?;
    let mut segments = parsed.path_segments()?;
    if !segments
        .next()
        .is_some_and(|segment| segment.eq_ignore_ascii_case("r"))
    {
        return None;
    }
    normalize_subreddit(segments.next()?)
}

fn default_rationale(
    suggestion_type: &str,
    label: &str,
    profile_summary: &str,
    inferred_topics: &[String],
) -> String {
    let context = inferred_topics
        .iter()
        .take(2)
        .cloned()
        .collect::<Vec<_>>()
        .join(", ");
    let context = if context.is_empty() {
        profile_summary.trim()
    } else {
        &context
    };
    let context = if context.is_empty() {
        "your interests"
    } else {
        context
    };
    match suggestion_type {
        "podcast_rss" => {
            format!("Podcast covering {label} with discussions relevant to {context}.")
        }
        "reddit" => {
            format!("Active subreddit for {label} with ongoing threads related to {context}.")
        }
        _ => format!("Feed focused on {label} with updates tied to {context}."),
    }
}

fn normalized_score(value: Option<f64>) -> Option<f64> {
    value
        .filter(|value| value.is_finite())
        .map(|value| value.clamp(0.0, 1.0))
}

fn clean_optional(value: Option<String>, max_chars: usize) -> Option<String> {
    value
        .map(|value| clean_text(&value, max_chars))
        .filter(|value| !value.is_empty())
}

fn clean_text(value: &str, max_chars: usize) -> String {
    value
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .chars()
        .take(max_chars)
        .collect()
}

#[cfg(test)]
mod tests {
    use newsly_queue::{OwnedWorkPlan, TaskQueue, TaskType};

    use super::{extract_subreddit, normalize_subreddit, parse_request, provider_lanes};

    fn plan(payload: &serde_json::Value) -> OwnedWorkPlan {
        OwnedWorkPlan {
            task_id: 9,
            owner_user_id: Some(7),
            task_type: TaskType::OnboardingDiscover,
            content_id: None,
            payload: payload.as_object().cloned().unwrap(),
            retry_count: 0,
            queue_name: TaskQueue::Onboarding,
            executor_runtime: newsly_domain::RuntimeOwner::Rust,
            executor_version: 1,
            executor_namespace: "onboarding_discover".to_owned(),
        }
    }

    #[test]
    fn payload_owner_must_match() {
        let error =
            parse_request(&plan(&serde_json::json!({"user_id": 8, "run_id": 3}))).unwrap_err();
        assert!(error.contains("must match"));
    }

    #[test]
    fn discovery_requires_server_owned_run() {
        let error = parse_request(&plan(&serde_json::json!({
            "user_id": 7,
            "profile_summary": "client supplied profile"
        })))
        .unwrap_err();
        assert!(error.contains("positive run_id"));
    }

    #[test]
    fn subreddit_normalization_is_bounded() {
        assert_eq!(normalize_subreddit("/r/rust/"), Some("rust".to_owned()));
        assert_eq!(
            extract_subreddit("https://www.reddit.com/r/rust/"),
            Some("rust".to_owned())
        );
        assert_eq!(normalize_subreddit("bad-name"), None);
    }

    #[test]
    fn unknown_persisted_lane_target_is_rejected() {
        let snapshot = newsly_db::OnboardingTaskSnapshot {
            run_id: 1,
            user_id: 7,
            topic_summary: "Rust".to_owned(),
            inferred_topics: vec!["Rust".to_owned()],
            lanes: vec![newsly_db::OnboardingTaskLane {
                id: 1,
                name: "Other".to_owned(),
                goal: String::new(),
                target: "unknown".to_owned(),
                queries: vec!["rust".to_owned()],
            }],
        };
        assert!(provider_lanes(&snapshot).is_err());
    }
}
