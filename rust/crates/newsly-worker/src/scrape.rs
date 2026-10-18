use chrono::Utc;
use std::collections::BTreeSet;
use std::sync::Arc;

use newsly_db::{ScrapeConfigSnapshot, prepare_scrape_sources};
use newsly_domain::AggregatorKey;
use newsly_providers::{
    FeedEntrySelection, FeedScrapeTarget, RedditScrapeTarget, ScrapeFailure, ScrapeGateway,
    ScrapeProviderOutcome,
};
use newsly_queue::{OwnedWorkPlan, QueueKernel, TaskResult, TaskType};
use serde_json::Value;
use sqlx::PgPool;

use crate::{HandlerExecution, HandlerFuture, LeaseHealth, TaskHandler};

mod brutalist;
mod finalizer;
mod source_plans;
#[cfg(test)]
use finalizer::ScrapeFinalizationFailures;
use finalizer::ScrapeFinalizer;
use source_plans::build_source_plans;

#[derive(Debug, Clone)]
pub struct ScrapeWorkerServices {
    pool: PgPool,
    queue: QueueKernel,
    gateway: ScrapeGateway,
}

impl ScrapeWorkerServices {
    pub const fn new(pool: PgPool, queue: QueueKernel, gateway: ScrapeGateway) -> Self {
        Self {
            pool,
            queue,
            gateway,
        }
    }
}

#[derive(Debug, Clone)]
pub struct ScrapeHandler {
    services: Arc<ScrapeWorkerServices>,
}

impl ScrapeHandler {
    pub fn new(services: Arc<ScrapeWorkerServices>) -> Self {
        Self { services }
    }
}

impl TaskHandler for ScrapeHandler {
    fn task_type(&self) -> TaskType {
        TaskType::Scrape
    }

    fn execute(&self, plan: Arc<OwnedWorkPlan>, lease: LeaseHealth) -> HandlerFuture<'_> {
        let services = Arc::clone(&self.services);
        Box::pin(async move { execute_scrape(&services, &plan, lease).await })
    }
}

async fn execute_scrape(
    services: &ScrapeWorkerServices,
    task: &OwnedWorkPlan,
    lease: LeaseHealth,
) -> HandlerExecution {
    let request = match ScrapeRequest::parse(task) {
        Ok(request) => request,
        Err(error) => {
            return HandlerExecution::from_result(TaskResult::fail(Some(error), false));
        }
    };
    let config_id = task.payload.get("config_id").and_then(Value::as_i64);
    if config_id.is_some() && task.owner_user_id.is_none() {
        return HandlerExecution::from_result(TaskResult::fail(
            Some("Configured scrape requires an owner".to_owned()),
            false,
        ));
    }
    let scraper_types = request
        .sources
        .iter()
        .filter_map(|source| match source {
            RequestedSource::Aggregator(_) => Some("aggregator"),
            RequestedSource::Reddit => Some("reddit"),
            RequestedSource::Substack => Some("substack"),
            RequestedSource::Atom => Some("atom"),
            RequestedSource::Podcast => Some("podcast_rss"),
            RequestedSource::DiscussionComments => None,
        })
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect::<Vec<_>>();
    let mut prepared = match prepare_scrape_sources(
        &services.pool,
        request.first_edition_run_id,
        config_id,
        task.owner_user_id,
        &scraper_types,
    )
    .await
    {
        Ok(prepared) => prepared,
        Err(error) => {
            let retryable = error.retryable();
            return HandlerExecution::from_result(TaskResult::fail(
                Some(error.to_string()),
                retryable,
            ));
        }
    };
    prepared.retain(|config| {
        config.scraper_type != "aggregator"
            || config
                .config
                .get("key")
                .and_then(Value::as_str)
                .and_then(AggregatorKey::from_key)
                .is_some_and(|key| request.sources.contains(&RequestedSource::Aggregator(key)))
    });
    if config_id.is_some() && prepared.is_empty() {
        return HandlerExecution::from_result(TaskResult::ok());
    }
    let mut source_plans = build_source_plans(&request, &prepared);
    if source_plans.is_empty() {
        return HandlerExecution::from_result(TaskResult::ok());
    }

    let due_only = task
        .payload
        .get("due_only")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    if due_only && task.retry_count == 0 {
        let mut connection = match services.pool.acquire().await {
            Ok(connection) => connection,
            Err(error) => {
                return HandlerExecution::from_result(TaskResult::fail(
                    Some(error.to_string()),
                    true,
                ));
            }
        };
        let mut due = Vec::new();
        for source in source_plans {
            if matches!(source.kind, SourcePlanKind::Aggregator { .. }) {
                match newsly_db::aggregator_corpus::aggregator_due(
                    &mut connection,
                    &source.source,
                    Utc::now(),
                )
                .await
                {
                    Ok(false) => continue,
                    Ok(true) => {}
                    Err(error) => {
                        return HandlerExecution::from_result(TaskResult::fail(
                            Some(error.to_string()),
                            true,
                        ));
                    }
                }
            }
            due.push(source);
        }
        source_plans = due;
    }
    if source_plans.iter().any(|source| {
        matches!(
            source.kind,
            SourcePlanKind::Aggregator {
                key: AggregatorKey::Brutalist,
                ..
            }
        )
    }) {
        let topics = match services.pool.acquire().await {
            Ok(mut connection) => newsly_db::brutalist::demanded_topics(&mut connection).await,
            Err(error) => Err(error),
        };
        let topics = match topics {
            Ok(topics) => topics,
            Err(error) => {
                return HandlerExecution::from_result(TaskResult::fail(
                    Some(error.to_string()),
                    true,
                ));
            }
        };
        for source in &mut source_plans {
            if let SourcePlanKind::Aggregator {
                key: AggregatorKey::Brutalist,
                brutalist_topics,
                ..
            } = &mut source.kind
            {
                brutalist_topics.clone_from(&topics);
            }
        }
        source_plans.retain(|source| !matches!(&source.kind, SourcePlanKind::Aggregator { key: AggregatorKey::Brutalist, brutalist_topics, .. } if brutalist_topics.is_empty()));
    }
    let mut children = isolated_scrape_requests(&source_plans, request.first_edition_run_id);
    if due_only {
        for child in &mut children {
            if let Some(payload) = &mut child.payload {
                payload.insert("due_only".to_owned(), Value::Bool(true));
            }
        }
    }
    if request.sources.len() > 1 || children.len() > 1 {
        return HandlerExecution::with_finalizer(
            TaskResult::ok(),
            crate::EnqueueTasksFinalizer {
                queue: services.queue.clone(),
                requests: children,
            },
        );
    }

    let Some(mut source_plan) = source_plans.pop() else {
        return HandlerExecution::from_result(TaskResult::ok());
    };
    if !source_plans.is_empty() {
        return HandlerExecution::from_result(TaskResult::fail(
            Some("inline scrape retained multiple source plans after fan-out".to_owned()),
            false,
        ));
    }
    if let SourcePlanKind::Feed(targets) = &mut source_plan.kind {
        for target in targets {
            let content_type = if target.scraper_type == "podcast_rss" {
                "podcast"
            } else {
                "article"
            };
            target.known_urls = match newsly_db::known_feed_urls(
                &services.pool,
                target.user_id,
                content_type,
            )
            .await
            {
                Ok(urls) => urls,
                Err(error) => {
                    return HandlerExecution::from_result(TaskResult::fail(
                        Some(error.to_string()),
                        true,
                    ));
                }
            };
        }
    }
    let outcomes = vec![execute_source(&services.gateway, source_plan).await];

    if lease.ownership_lost() {
        return HandlerExecution::from_result(TaskResult::fail(
            Some("scrape task ownership was lost during provider work".to_owned()),
            true,
        ));
    }
    let failed_sources = outcomes
        .iter()
        .filter(|outcome| outcome.failed_without_progress() || outcome.retryable_failure())
        .map(|outcome| outcome.source.clone())
        .collect::<Vec<_>>();
    let mut task_result = if failed_sources.is_empty() {
        TaskResult::ok()
    } else {
        TaskResult::fail(
            Some(format!(
                "Scraper sources failed: {}",
                failed_sources.join(", ")
            )),
            outcomes.iter().any(SourceOutcome::retryable_failure),
        )
    };
    task_result.retry_delay_seconds = outcomes
        .iter()
        .filter_map(|outcome| {
            outcome
                .result
                .as_ref()
                .err()
                .and_then(|error| error.retry_after)
        })
        .max();
    HandlerExecution::with_finalizer(
        task_result,
        ScrapeFinalizer {
            task_id: task.task_id,
            queue: services.queue.clone(),
            request,
            prepared,
            outcomes,
        },
    )
}

mod dispatch;
use dispatch::isolated_scrape_requests;

#[derive(Debug, Clone)]
struct ScrapeRequest {
    sources: Vec<RequestedSource>,
    first_edition_run_id: Option<i64>,
}

impl ScrapeRequest {
    fn parse(task: &OwnedWorkPlan) -> Result<Self, String> {
        let raw_sources = task
            .payload
            .get("sources")
            .and_then(Value::as_array)
            .ok_or_else(|| "scrape sources must be a non-empty array".to_owned())?;
        if raw_sources.is_empty() {
            return Err("scrape sources must be a non-empty array".to_owned());
        }
        let mut sources = BTreeSet::new();
        for source in raw_sources {
            let value = source
                .as_str()
                .ok_or_else(|| "scrape source names must be strings".to_owned())?;
            if normalize_name(value) == "all" {
                sources.extend(RequestedSource::all_concrete());
            } else {
                sources.insert(
                    RequestedSource::parse(value)
                        .ok_or_else(|| format!("unknown scrape source {value:?}"))?,
                );
            }
        }
        let first_edition_run_id = task
            .payload
            .get("first_edition_run_id")
            .map(|value| {
                value
                    .as_i64()
                    .filter(|value| *value > 0)
                    .ok_or_else(|| "scrape first_edition_run_id must be positive".to_owned())
            })
            .transpose()?;
        Ok(Self {
            sources: sources.into_iter().collect(),
            first_edition_run_id,
        })
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
enum RequestedSource {
    Aggregator(AggregatorKey),
    Reddit,
    Substack,
    Atom,
    Podcast,
    DiscussionComments,
}

impl RequestedSource {
    fn parse(value: &str) -> Option<Self> {
        if let Some(key) = AggregatorKey::parse(value) {
            return Some(Self::Aggregator(key));
        }
        let normalized = normalize_name(value);
        match normalized.as_str() {
            "reddit" => Some(Self::Reddit),
            "substack" => Some(Self::Substack),
            "atom" => Some(Self::Atom),
            "podcast" => Some(Self::Podcast),
            "discussioncomments" => Some(Self::DiscussionComments),
            _ => None,
        }
    }

    fn all_concrete() -> BTreeSet<Self> {
        let mut sources = AggregatorKey::ALL
            .into_iter()
            .map(Self::Aggregator)
            .collect::<BTreeSet<_>>();
        sources.extend([
            Self::Reddit,
            Self::Substack,
            Self::Atom,
            Self::Podcast,
            Self::DiscussionComments,
        ]);
        sources
    }

    fn canonical_name(self) -> String {
        match self {
            Self::Aggregator(key) => key.as_str().to_owned(),
            Self::Reddit => "reddit".to_owned(),
            Self::Substack => "substack".to_owned(),
            Self::Atom => "atom".to_owned(),
            Self::Podcast => "podcast".to_owned(),
            Self::DiscussionComments => "discussion_comments".to_owned(),
        }
    }
}

#[derive(Debug, Clone)]
enum SourcePlanKind {
    Aggregator {
        key: AggregatorKey,
        required_config_ids: Vec<i64>,
        brutalist_topics: Vec<String>,
    },
    Feed(Vec<FeedScrapeTarget>),
    Reddit(Vec<RedditScrapeTarget>),
    DiscussionComments,
}

#[derive(Debug, Clone)]
struct SourcePlan {
    source: String,
    kind: SourcePlanKind,
}

fn feed_target(config: &ScrapeConfigSnapshot) -> Option<FeedScrapeTarget> {
    let feed_url = config
        .feed_url
        .clone()
        .or_else(|| clean_string(config.config.get("feed_url")))
        .or_else(|| clean_string(config.config.get("url")))?;
    Some(FeedScrapeTarget {
        known_urls: std::collections::BTreeSet::new(),
        entry_selection: FeedEntrySelection::StopAtKnown,
        config_id: config.id,
        user_id: config.user_id,
        scraper_type: config.scraper_type.clone(),
        display_name: config
            .display_name
            .clone()
            .or_else(|| clean_string(config.config.get("name"))),
        feed_url,
        limit: config
            .config
            .get("limit")
            .and_then(Value::as_u64)
            .and_then(|value| usize::try_from(value).ok())
            .filter(|value| (1..=100).contains(value))
            .unwrap_or(10),
        fingerprint: config.fingerprint.clone(),
    })
}

fn reddit_target(config: &ScrapeConfigSnapshot) -> Option<RedditScrapeTarget> {
    let subreddit = clean_string(config.config.get("subreddit"))?
        .trim_start_matches("r/")
        .trim_matches('/')
        .to_owned();
    if subreddit.is_empty() || subreddit.eq_ignore_ascii_case("front") {
        return None;
    }
    Some(RedditScrapeTarget {
        config_id: config.id,
        user_id: config.user_id,
        subreddit,
        limit: config
            .config
            .get("limit")
            .and_then(Value::as_u64)
            .and_then(|value| usize::try_from(value).ok())
            .filter(|value| (1..=100).contains(value))
            .unwrap_or(10),
        fingerprint: config.fingerprint.clone(),
    })
}

#[derive(Debug)]
struct SourceOutcome {
    source: String,
    required_config_ids: Vec<i64>,
    result: Result<ScrapeProviderOutcome, ScrapeFailure>,
    discussion_catchup: bool,
}

impl SourceOutcome {
    fn retryable_failure(&self) -> bool {
        match &self.result {
            Err(error) => error.retryable,
            Ok(outcome) => outcome.retryable_failure,
        }
    }

    fn failed_without_progress(&self) -> bool {
        match &self.result {
            Err(_) => true,
            Ok(outcome) => !outcome.item_errors.is_empty() && outcome.items.is_empty(),
        }
    }
}

async fn execute_source(gateway: &ScrapeGateway, plan: SourcePlan) -> SourceOutcome {
    match plan.kind {
        SourcePlanKind::Aggregator {
            key,
            required_config_ids,
            brutalist_topics,
        } => SourceOutcome {
            source: plan.source,
            required_config_ids,
            result: gateway
                .fetch_aggregator(key, &brutalist_topics)
                .await
                .map_err(ScrapeFailure::from),
            discussion_catchup: false,
        },
        SourcePlanKind::Feed(targets) => {
            let result = if let Some(target) = targets.into_iter().next() {
                let result = gateway
                    .fetch_feed(&target)
                    .await
                    .map_err(ScrapeFailure::from);
                Some((target.config_id, result))
            } else {
                None
            };
            configured_source_outcome(plan.source, result)
        }
        SourcePlanKind::Reddit(targets) => {
            let config_id = targets.first().map(|target| target.config_id);
            let result = match gateway.fetch_reddit_targets(&targets).await {
                Ok(results) => results.into_iter().next(),
                Err(error) => config_id.map(|id| (id, Err(ScrapeFailure::from(error)))),
            };
            configured_source_outcome(plan.source, result)
        }
        SourcePlanKind::DiscussionComments => SourceOutcome {
            source: plan.source,
            required_config_ids: Vec::new(),
            result: Ok(ScrapeProviderOutcome {
                retryable_failure: false,
                items: Vec::new(),
                item_errors: Vec::new(),
            }),
            discussion_catchup: true,
        },
    }
}

fn configured_source_outcome(
    source: String,
    outcome: Option<(i64, Result<ScrapeProviderOutcome, ScrapeFailure>)>,
) -> SourceOutcome {
    let (required_config_ids, result) = outcome.map_or_else(
        || {
            (
                Vec::new(),
                Ok(ScrapeProviderOutcome {
                    items: Vec::new(),
                    item_errors: Vec::new(),
                    retryable_failure: false,
                }),
            )
        },
        |(config_id, result)| (vec![config_id], result),
    );
    SourceOutcome {
        source,
        required_config_ids,
        result,
        discussion_catchup: false,
    }
}

fn normalize_name(value: &str) -> String {
    value
        .trim()
        .to_ascii_lowercase()
        .chars()
        .filter(char::is_ascii_alphanumeric)
        .collect()
}

fn clean_string(value: Option<&Value>) -> Option<String> {
    value
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(ToOwned::to_owned)
}

#[cfg(test)]
mod tests;
