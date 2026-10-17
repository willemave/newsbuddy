use chrono::Utc;
use std::collections::{BTreeMap, BTreeSet};
use std::error::Error;
use std::sync::Arc;

use newsly_db::{
    PersistedContentRecord, PersistedNewsRecord, ScrapeConfigSnapshot, ScrapeRepositoryError,
    ScrapedContentRecord, ScrapedNewsRecord, due_discussion_refresh_ids,
    matching_scrape_config_ids, persist_scraped_content, persist_scraped_news,
    prepare_scrape_sources, record_first_edition_scrape_result,
};
use newsly_domain::AggregatorKey;
use newsly_providers::{
    FeedEntrySelection, FeedScrapeTarget, RedditScrapeTarget, ScrapeFailure, ScrapeGateway,
    ScrapeProviderOutcome, ScrapedItem,
};
use newsly_queue::{EnqueueRequest, OwnedWorkPlan, QueueKernel, TaskResult, TaskType};
use serde_json::{Map, Value};
use sqlx::{Acquire, PgPool, Postgres, Transaction};

use crate::{
    HandlerExecution, HandlerFinalizerFuture, HandlerFuture, LeaseHealth, TaskFinalizer,
    TaskFinalizerResult, TaskHandler,
};

const DISCUSSION_REFRESH_LIMIT: i64 = 100;

mod brutalist;
mod source_plans;
use brutalist::brutalist_budget_keys;
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

#[derive(Debug)]
struct ScrapeFinalizer {
    queue: QueueKernel,
    request: ScrapeRequest,
    prepared: Vec<ScrapeConfigSnapshot>,
    outcomes: Vec<SourceOutcome>,
}

enum PersistedScrapeItem {
    Content(PersistedContentRecord),
    News(PersistedNewsRecord),
}

#[derive(Default)]
struct ScrapeFinalizationFailures {
    persistence_count: u64,
    retryable: bool,
    configuration: ScrapeConfigurationState,
    progress: ScrapeProgressState,
}

#[derive(Default)]
enum ScrapeConfigurationState {
    #[default]
    Current,
    Changed,
}

#[derive(Default)]
enum ScrapeProgressState {
    #[default]
    Recorded,
    Failed,
}

impl ScrapeFinalizationFailures {
    fn new(outcomes: &[SourceOutcome], config_mismatch: bool) -> Self {
        Self {
            retryable: config_mismatch || outcomes.iter().any(SourceOutcome::retryable_failure),
            configuration: if config_mismatch {
                ScrapeConfigurationState::Changed
            } else {
                ScrapeConfigurationState::Current
            },
            ..Self::default()
        }
    }

    fn configuration_is_current(&self) -> bool {
        matches!(self.configuration, ScrapeConfigurationState::Current)
    }

    fn record_persistence(&mut self, error: &ScrapeRepositoryError) {
        self.persistence_count += 1;
        self.retryable |= error.retryable();
    }

    fn record_progress_failure(&mut self) {
        self.progress = ScrapeProgressState::Failed;
        self.retryable = true;
    }

    fn into_result(self) -> TaskFinalizerResult {
        if self.persistence_count > 0 {
            return TaskFinalizerResult::Override(TaskResult::fail(
                Some(format!(
                    "{} scraper items could not be persisted",
                    self.persistence_count
                )),
                self.retryable,
            ));
        }
        if matches!(self.progress, ScrapeProgressState::Failed) {
            return TaskFinalizerResult::Override(TaskResult::fail(
                Some("Could not record onboarding scraper progress".to_owned()),
                true,
            ));
        }
        if matches!(self.configuration, ScrapeConfigurationState::Changed) {
            return TaskFinalizerResult::Override(TaskResult::fail(
                Some("scraper configuration changed before finalization".to_owned()),
                true,
            ));
        }
        TaskFinalizerResult::Keep
    }
}

async fn persist_scrape_item(
    transaction: &mut Transaction<'_, Postgres>,
    item: &ScrapedItem,
) -> Result<PersistedScrapeItem, ScrapeRepositoryError> {
    match item {
        ScrapedItem::Content(item) => persist_scraped_content(
            transaction,
            &ScrapedContentRecord {
                url: item.url.clone(),
                source_url: item.source_url.clone(),
                title: item.title.clone(),
                content_type: item.content_type.clone(),
                user_id: item.user_id,
                source: item.source.clone(),
                platform: item.platform.clone(),
                metadata: item.metadata.clone(),
                published_at: item.published_at,
            },
        )
        .await
        .map(PersistedScrapeItem::Content),
        ScrapedItem::News(item) => persist_scraped_news(
            transaction,
            &ScrapedNewsRecord {
                visibility_scope: item.visibility_scope.clone(),
                owner_user_id: item.owner_user_id,
                platform: item.platform.clone(),
                source_type: item.source_type.clone(),
                source_label: item.source_label.clone(),
                source_external_id: item.source_external_id.clone(),
                user_scraper_config_id: item.user_scraper_config_id,
                canonical_item_url: item.canonical_item_url.clone(),
                canonical_story_url: item.canonical_story_url.clone(),
                article_url: item.article_url.clone(),
                article_domain: item.article_domain.clone(),
                discussion_url: item.discussion_url.clone(),
                article_title: item.title.clone(),
                summary_key_points: item.summary_key_points.clone(),
                summary_text: item.summary_text.clone(),
                raw_metadata: item.raw_metadata.clone(),
                status: item.status.clone(),
                published_at: item.published_at,
            },
        )
        .await
        .map(PersistedScrapeItem::News),
    }
}

impl ScrapeFinalizer {
    async fn apply_inner(
        &self,
        transaction: &mut Transaction<'static, Postgres>,
    ) -> Result<TaskFinalizerResult, Box<dyn Error + Send + Sync>> {
        let required_ids = self
            .outcomes
            .iter()
            .flat_map(|outcome| {
                outcome.required_config_ids.iter().copied().chain(
                    outcome
                        .result
                        .as_ref()
                        .ok()
                        .into_iter()
                        .flat_map(|result| result.items.iter().filter_map(item_config_id)),
                )
            })
            .collect::<BTreeSet<_>>();
        let required_configs = self
            .prepared
            .iter()
            .filter(|config| required_ids.contains(&config.id))
            .cloned()
            .collect::<Vec<_>>();
        let valid_config_ids = matching_scrape_config_ids(transaction, &required_configs).await?;
        let mut brutalist_counts = if self.outcomes.iter().any(|outcome| {
            outcome.source == "brutalist"
                && outcome
                    .result
                    .as_ref()
                    .is_ok_and(|result| !result.items.is_empty())
        }) {
            // Audience writes and denied fallback publication share this outer lock.
            newsly_db::aggregator_corpus::lock_aggregator_demand(transaction).await?;
            // New-demand and scheduled fetches can overlap; one finalizer owns the rolling cap.
            sqlx::query("SELECT pg_advisory_xact_lock(6284057701001)")
                .execute(&mut **transaction)
                .await?;
            newsly_db::brutalist::recent_section_counts(transaction)
                .await?
                .into_iter()
                .map(|(topic, section, count)| ((topic, section), count))
                .collect::<BTreeMap<_, _>>()
        } else {
            BTreeMap::new()
        };
        let mut downstream = Vec::new();
        let mut process_content_ids = BTreeSet::new();
        let mut enrich_news_ids = BTreeSet::new();
        let mut discussion_ids = BTreeSet::new();
        let config_mismatch = self.outcomes.iter().any(|outcome| {
            outcome
                .required_config_ids
                .iter()
                .any(|id| !valid_config_ids.contains(id))
        });
        let mut failures = ScrapeFinalizationFailures::new(&self.outcomes, config_mismatch);

        for outcome in &self.outcomes {
            let mut processed = 0_i64;
            let mut created = 0_i64;
            let mut historical = 0_i64;
            let mut rejected = false;
            let mut processed_by_config = outcome
                .required_config_ids
                .iter()
                .map(|id| (*id, 0_i64))
                .collect::<BTreeMap<_, _>>();
            if let Ok(provider_outcome) = &outcome.result {
                for item in &provider_outcome.items {
                    let config_id = item_config_id(item);
                    if config_id.is_some_and(|id| !valid_config_ids.contains(&id)) {
                        continue;
                    }
                    let mut savepoint = transaction.begin().await?;
                    match persist_scrape_item(&mut savepoint, item).await {
                        Ok(PersistedScrapeItem::Content(persisted)) => {
                            savepoint.commit().await?;
                            if persisted.created {
                                created += 1;
                                if matches!(item, ScrapedItem::Content(item) if item.published_at.is_some_and(|date| date < chrono::Utc::now() - chrono::Duration::days(30)))
                                {
                                    historical += 1;
                                }
                                process_content_ids.insert(persisted.content_id);
                            }
                        }
                        Ok(PersistedScrapeItem::News(persisted)) => {
                            let budget_keys = brutalist_budget_keys(item);
                            if persisted.created
                                && budget_keys.iter().any(|key| {
                                    brutalist_counts.get(key).copied().unwrap_or(0)
                                        >= newsly_db::brutalist::DAILY_SECTION_LIMIT
                                })
                            {
                                savepoint.rollback().await?;
                                continue;
                            }
                            savepoint.commit().await?;
                            if persisted.created {
                                for key in budget_keys {
                                    *brutalist_counts.entry(key).or_default() += 1;
                                }
                            }
                            created += i64::from(persisted.created);
                            if persisted.reenrich_article
                                || (persisted.created
                                    && !matches!(item, ScrapedItem::News(item) if item.status == "ready"))
                            {
                                enrich_news_ids.insert(persisted.news_item_id);
                            }
                            if persisted.discussion_refresh_ready {
                                discussion_ids.insert(persisted.news_item_id);
                            }
                        }
                        Err(error) => {
                            savepoint.rollback().await?;
                            rejected = true;
                            failures.record_persistence(&error);
                            tracing::error!(
                                source = outcome.source,
                                config_id,
                                error = ?error,
                                "scraper item persistence failed"
                            );
                            continue;
                        }
                    }
                    processed = processed.saturating_add(1);
                    if let Some(config_id) = config_id {
                        *processed_by_config.entry(config_id).or_default() += 1;
                    }
                }
            }
            if !outcome.discussion_catchup && !config_mismatch {
                let error = match &outcome.result {
                    Err(error) => Some(error.message.as_str()),
                    Ok(value) if rejected || !value.item_errors.is_empty() => {
                        Some("source_items_rejected")
                    }
                    Ok(_) => None,
                };
                let config_ids = if AggregatorKey::from_key(&outcome.source).is_some() {
                    vec![None]
                } else {
                    outcome
                        .required_config_ids
                        .iter()
                        .copied()
                        .map(Some)
                        .collect()
                };
                for config_id in config_ids {
                    let key = config_id.map_or_else(
                        || format!("aggregator:{}", outcome.source),
                        |id| format!("config:{id}"),
                    );
                    let observation = newsly_db::record_source_health(
                        transaction,
                        &key,
                        config_id,
                        processed,
                        created,
                        error,
                    )
                    .await?;
                    newsly_db::pipeline_monitoring::record_intake(
                        transaction,
                        observation,
                        historical,
                        self.request.first_edition_run_id.is_some(),
                    )
                    .await?;
                }
            }
            if outcome.discussion_catchup {
                discussion_ids.extend(
                    due_discussion_refresh_ids(transaction, DISCUSSION_REFRESH_LIMIT).await?,
                );
            }
            if let Some(run_id) = self.request.first_edition_run_id
                && (outcome.source == "reddit"
                    || AggregatorKey::from_key(&outcome.source).is_some())
                && failures.configuration_is_current()
                && !outcome.retryable_failure()
            {
                let recorded = record_first_edition_scrape_result(
                    transaction,
                    run_id,
                    &outcome.source,
                    !outcome.failed_without_progress(),
                    processed,
                    &processed_by_config,
                )
                .await?;
                if !recorded {
                    failures.record_progress_failure();
                }
            }
        }

        downstream.extend(process_content_ids.into_iter().map(|content_id| {
            let mut request = EnqueueRequest::new(TaskType::ProcessContent);
            request.content_id = Some(content_id);
            request
        }));
        downstream.extend(enrich_news_ids.into_iter().map(|news_item_id| {
            let mut request = EnqueueRequest::new(TaskType::EnrichNewsItemArticle);
            request.payload = Some(Map::from_iter([(
                "news_item_id".to_owned(),
                Value::from(news_item_id),
            )]));
            request.dedupe = Some(true);
            request.dedupe_key = Some(format!("enrich-news-demand:{news_item_id}"));
            request
        }));
        downstream.extend(discussion_ids.into_iter().map(|news_item_id| {
            let mut request = EnqueueRequest::new(TaskType::FetchNewsItemDiscussion);
            request.payload = Some(Map::from_iter([(
                "news_item_id".to_owned(),
                Value::from(news_item_id),
            )]));
            request
        }));
        self.queue
            .enqueue_many_in_transaction(transaction, downstream)
            .await?;

        Ok(failures.into_result())
    }
}

impl TaskFinalizer for ScrapeFinalizer {
    fn apply<'a>(
        &'a self,
        transaction: &'a mut Transaction<'static, Postgres>,
    ) -> HandlerFinalizerFuture<'a> {
        Box::pin(async move { self.apply_inner(transaction).await })
    }
}

fn item_config_id(item: &ScrapedItem) -> Option<i64> {
    match item {
        ScrapedItem::News(item) => item.user_scraper_config_id,
        ScrapedItem::Content(item) => Some(item.config_id),
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
