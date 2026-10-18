use std::collections::{HashMap, HashSet};
use std::sync::Arc;

use newsly_db::{
    NewOnboardingSuggestion, OnboardingAttemptStatus, OnboardingTaskSnapshot,
    PrepareOnboardingTaskOutcome, complete_onboarding_discovery_task,
    ensure_weekly_discovery_session, prepare_onboarding_discovery_task,
    settle_onboarding_discovery_attempt,
};
use newsly_providers::{
    FeedPublishingActivity, FeedValidationError, FeedValidator, OnboardingAudioLane,
    OnboardingDiscoverySeeds, OnboardingGateway, OnboardingLaneTarget, OnboardingSuggestionSeed,
    ValidatedFeed,
};
use newsly_queue::{OwnedWorkPlan, TaskResult, TaskType};
use serde_json::Value;
use sqlx::{PgPool, Postgres, Transaction};

use crate::agent_usage::{AgentUsageAttribution, AgentUsageRecorder};
use crate::task_tools::record_exa_tool_usage;
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
        Box::pin(async move {
            let request = match parse_request(&plan) {
                Ok(request) => request,
                Err(error) => {
                    return HandlerExecution::from_result(TaskResult::fail(Some(error), false));
                }
            };
            execute_audio_discovery(
                &services,
                &plan,
                &lease,
                request.user_id,
                request.run_id,
                &request.narration,
            )
            .await
        })
    }
}

#[derive(Debug, Clone)]
struct OnboardingRequest {
    narration: String,
    user_id: i64,
    run_id: i64,
}

async fn execute_audio_discovery(
    services: &OnboardingDiscoveryWorkerServices,
    task: &OwnedWorkPlan,
    lease: &LeaseHealth,
    user_id: i64,
    run_id: i64,
    narration: &str,
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
    let usage = Arc::new(AgentUsageRecorder::new(
        services.pool.clone(),
        AgentUsageAttribution {
            operation: "onboarding.audio_discover".to_owned(),
            source: "rust_worker".to_owned(),
            task_id: task.task_id,
            content_id: None,
            session_id: None,
            message_id: None,
            user_id: Some(user_id),
        },
    ));
    let events: Arc<dyn newsly_agent_runtime::AgentEventSink> = usage.clone();
    let result = services
        .provider
        .discover_from_lanes(
            &snapshot.topic_summary,
            narration,
            &snapshot.inferred_topics,
            &lanes,
            events,
        )
        .await;
    usage.finish().await;
    let outcome = match result {
        Ok(outcome) => outcome,
        Err(error) => {
            return audio_failure(services, task, snapshot, error.to_string(), true);
        }
    };
    record_exa_tool_usage(
        &services.pool,
        user_id,
        &format!("task-{}-attempt-{}", task.task_id, task.retry_count),
        "onboarding.audio_discover.search",
        outcome.exa_usage.into(),
    )
    .await;
    if lease.ownership_lost() {
        return HandlerExecution::from_result(TaskResult::fail(
            Some("Onboarding discovery lease was lost after provider work".to_owned()),
            true,
        ));
    }
    let suggestions = match normalize_seeds_with_cached_feeds(
        &services.feed_validator,
        outcome.seeds,
        &snapshot.topic_summary,
        &snapshot.inferred_topics,
        outcome.validated_feeds,
    )
    .await
    {
        Ok(suggestions) => suggestions
            .into_iter()
            .map(|(suggestion, _)| suggestion)
            .collect(),
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
    let narration = task
        .payload
        .get("narration")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .chars()
        .take(8_000)
        .collect();
    Ok(OnboardingRequest {
        narration,
        user_id,
        run_id,
    })
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
    Ok(
        normalize_seeds_with_activity(validator, seeds, profile_summary, inferred_topics)
            .await?
            .into_iter()
            .map(|(suggestion, _)| suggestion)
            .collect(),
    )
}

pub(crate) async fn normalize_seeds_with_activity(
    validator: &FeedValidator,
    seeds: OnboardingDiscoverySeeds,
    profile_summary: &str,
    inferred_topics: &[String],
) -> Result<Vec<(NewOnboardingSuggestion, Option<FeedPublishingActivity>)>, FeedValidationError> {
    normalize_seeds_with_cached_feeds(
        validator,
        seeds,
        profile_summary,
        inferred_topics,
        HashMap::new(),
    )
    .await
}

async fn normalize_seeds_with_cached_feeds(
    validator: &FeedValidator,
    seeds: OnboardingDiscoverySeeds,
    profile_summary: &str,
    inferred_topics: &[String],
    cached_feeds: HashMap<String, ValidatedFeed>,
) -> Result<Vec<(NewOnboardingSuggestion, Option<FeedPublishingActivity>)>, FeedValidationError> {
    let mut seen = HashSet::new();
    let urls = seeds
        .substacks
        .iter()
        .map(|seed| (seed, "substack"))
        .chain(seeds.podcasts.iter().map(|seed| (seed, "podcast_rss")))
        .filter_map(|(seed, kind)| feed_seed_candidate_url(seed, kind))
        .filter(|url| !cached_feeds.contains_key(url) && seen.insert(url.clone()))
        .collect::<Vec<_>>();
    // Both kinds share the validator's eight-request limit; slow newsletter hosts no longer
    // postpone every podcast request, and same-attempt evidence is never fetched twice.
    let validations = validator.validate_feeds(&urls).await;
    let mut validated = cached_feeds
        .into_iter()
        .map(|(url, feed)| (url, Some(feed)))
        .collect::<HashMap<_, _>>();
    let mut failures = Vec::new();
    for (url, result) in urls.into_iter().zip(validations) {
        match result {
            Ok(feed) => {
                validated.insert(url, feed);
            }
            Err(error) => {
                failures.push(error);
            }
        }
    }
    let mut output = Vec::new();
    normalize_feed_seeds(
        &validated,
        seeds.substacks,
        "substack",
        profile_summary,
        inferred_topics,
        &mut output,
    );
    normalize_feed_seeds(
        &validated,
        seeds.podcasts,
        "podcast_rss",
        profile_summary,
        inferred_topics,
        &mut output,
    );
    let validated_feed_count = output.len();
    let mut subreddits = Vec::new();
    normalize_subreddits(
        seeds.subreddits,
        profile_summary,
        inferred_topics,
        &mut subreddits,
    );
    output.extend(subreddits.into_iter().map(|suggestion| (suggestion, None)));
    if validated_feed_count == 0
        && let Some(error) = failures.into_iter().next()
    {
        return Err(error);
    }
    Ok(output)
}

fn feed_seed_candidate_url(
    seed: &OnboardingSuggestionSeed,
    suggestion_type: &str,
) -> Option<String> {
    let site_url = clean_optional(seed.site_url.clone(), 2_048);
    clean_optional(seed.feed_url.clone(), 2_048)
        .or_else(|| clean_optional(seed.candidate_feed_url.clone(), 2_048))
        .or_else(|| {
            (suggestion_type == "substack")
                .then(|| {
                    site_url
                        .as_deref()
                        .map(|url| format!("{}/feed", url.trim_end_matches('/')))
                })
                .flatten()
        })
        .or_else(|| {
            (seed.is_likely_feed == Some(true))
                .then(|| site_url.as_deref().and_then(infer_feed_url))
                .flatten()
        })
}

fn normalize_feed_seeds(
    validations: &HashMap<String, Option<ValidatedFeed>>,
    seeds: Vec<OnboardingSuggestionSeed>,
    suggestion_type: &str,
    profile_summary: &str,
    inferred_topics: &[String],
    output: &mut Vec<(NewOnboardingSuggestion, Option<FeedPublishingActivity>)>,
) {
    let mut seen = HashSet::new();
    let proposed = seeds.len();
    let mut without_feed = 0_usize;
    let mut not_a_feed = 0_usize;
    let mut without_audio = 0_usize;
    let mut failed = 0_usize;
    for seed in seeds {
        let Some(candidate) = feed_seed_candidate_url(&seed, suggestion_type) else {
            without_feed += 1;
            continue;
        };
        let Some(validation) = validations.get(&candidate) else {
            failed += 1;
            continue;
        };
        let site_url = clean_optional(seed.site_url, 2_048);
        let (title, rationale, score) = (seed.title, seed.rationale, seed.score);
        let Some(validated) = (match validation {
            Some(validated) if suggestion_type == "podcast_rss" && !validated.has_audio_entries => {
                tracing::info!(
                    url = %candidate,
                    "onboarding podcast candidate had no audio entries"
                );
                without_audio += 1;
                None
            }
            Some(validated) => Some(validated),
            None => {
                tracing::info!(url = %candidate, "onboarding candidate was not a feed");
                not_a_feed += 1;
                None
            }
        }) else {
            continue;
        };
        let feed_url = newsly_db::canonicalize_feed_url(&validated.effective_url);
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
        output.push((
            NewOnboardingSuggestion {
                suggestion_type: suggestion_type.to_owned(),
                title,
                site_url,
                feed_url: Some(feed_url),
                subreddit: None,
                rationale,
                score: normalized_score(score),
            },
            Some(if suggestion_type == "podcast_rss" {
                validated.podcast_publishing_activity.clone()
            } else {
                validated.publishing_activity.clone()
            }),
        ));
        if seen.len() >= MAX_SUGGESTIONS_PER_KIND {
            break;
        }
    }
    // One line per kind, so an empty category is explained without enabling debug logs.
    tracing::info!(
        suggestion_type,
        proposed,
        kept = seen.len(),
        without_feed,
        not_a_feed,
        without_audio,
        failed,
        "onboarding feed suggestions normalized"
    );
}

fn normalize_subreddits(
    seeds: Vec<OnboardingSuggestionSeed>,
    profile_summary: &str,
    inferred_topics: &[String],
    output: &mut Vec<NewOnboardingSuggestion>,
) {
    let mut seen = HashSet::new();
    let proposed = seeds.len();
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
    tracing::info!(
        proposed,
        kept = seen.len(),
        "onboarding subreddit suggestions normalized"
    );
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
    fn narration_survives_in_the_durable_payload_without_relying_on_summary_language() {
        let narration = format!("{} Quiero entrevistas en español.", "ñ".repeat(7_900));
        let request = parse_request(&plan(
            &serde_json::json!({"user_id": 7, "run_id": 3, "narration": narration}),
        ))
        .unwrap();
        assert_eq!(
            request.narration,
            narration.chars().take(8_000).collect::<String>()
        );
        assert!(
            request
                .narration
                .ends_with("Quiero entrevistas en español.")
        );
        let legacy = parse_request(&plan(&serde_json::json!({"user_id": 7, "run_id": 3}))).unwrap();
        assert!(legacy.narration.is_empty());
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

#[cfg(test)]
mod validation_tests {
    use super::*;
    use newsly_providers::ValidatedFeedFormat;

    fn cached_feed(url: &str, has_audio: bool) -> ValidatedFeed {
        let now = chrono::Utc::now();
        let activity = FeedPublishingActivity {
            checked_at: now,
            window_start: now,
            year_window_start: now,
            window_end: now,
            latest_item: None,
            observed_count: None,
            observed_year_count: None,
            undated_count: 1,
        };
        ValidatedFeed {
            effective_url: url.to_owned(),
            format: ValidatedFeedFormat::Rss,
            has_audio_entries: has_audio,
            publishing_activity: activity.clone(),
            podcast_publishing_activity: activity,
        }
    }
    fn seed(url: &str) -> OnboardingSuggestionSeed {
        serde_json::from_value(serde_json::json!({"title": "Grounded show", "feed_url": url}))
            .unwrap()
    }

    #[tokio::test]
    async fn exact_attempt_cache_avoids_refetch_and_preserves_canonical_dedupe() {
        // These URLs cannot pass a public-network fetch; a result proves reuse of the attempt's
        // validation instead of a hidden second HTTP request.
        let first = "http://127.0.0.1/first";
        let second = "http://127.0.0.1/second";
        let canonical = "https://example.com/feed";
        let result = normalize_seeds_with_cached_feeds(
            &FeedValidator::new(),
            OnboardingDiscoverySeeds {
                podcasts: vec![seed(first), seed(second)],
                ..OnboardingDiscoverySeeds::default()
            },
            "Founder interviews",
            &[],
            HashMap::from([
                (first.to_owned(), cached_feed(canonical, true)),
                (second.to_owned(), cached_feed(canonical, true)),
            ]),
        )
        .await
        .unwrap();
        assert_eq!(result.len(), 1);
        assert_eq!(result[0].0.feed_url.as_deref(), Some(canonical));
        assert!(result[0].1.is_some());
    }

    #[tokio::test]
    async fn shared_validation_still_requires_audio_and_preserves_kind_order() {
        let article = "http://127.0.0.1/article";
        let show = "http://127.0.0.1/show";
        let result = normalize_seeds_with_cached_feeds(
            &FeedValidator::new(),
            OnboardingDiscoverySeeds {
                substacks: vec![seed(article)],
                podcasts: vec![seed(article), seed(show)],
                ..OnboardingDiscoverySeeds::default()
            },
            "Research",
            &[],
            HashMap::from([
                (
                    article.to_owned(),
                    cached_feed("https://example.com/article-feed", false),
                ),
                (
                    show.to_owned(),
                    cached_feed("https://example.com/show-feed", true),
                ),
            ]),
        )
        .await
        .unwrap();
        assert_eq!(
            result
                .iter()
                .map(|(s, _)| s.suggestion_type.as_str())
                .collect::<Vec<_>>(),
            vec!["substack", "podcast_rss"]
        );
    }
}
