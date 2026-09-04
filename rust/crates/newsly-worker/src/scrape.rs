use std::collections::{BTreeMap, BTreeSet};
use std::error::Error;
use std::sync::Arc;

use futures_util::stream::{self, StreamExt};
use newsly_db::{
    PersistedContentRecord, PersistedNewsRecord, PreparedScrapeSources, ScrapeConfigSnapshot,
    ScrapeRepositoryError, ScrapedContentRecord, ScrapedNewsRecord, due_discussion_refresh_ids,
    matching_scrape_config_ids, persist_scraped_content, persist_scraped_news,
    prepare_scrape_sources, record_first_edition_scrape_result,
};
use newsly_providers::{
    AggregatorKey, FeedScrapeTarget, RedditScrapeTarget, ScrapeGateway, ScrapeProviderOutcome,
    ScrapedItem,
};
use newsly_queue::{EnqueueRequest, OwnedWorkPlan, QueueKernel, TaskResult, TaskType};
use serde_json::{Map, Value};
use sqlx::{Acquire, PgPool, Postgres, Transaction};

use crate::{
    HandlerExecution, HandlerFinalizerFuture, HandlerFuture, LeaseHealth, TaskFinalizer,
    TaskFinalizerResult, TaskHandler,
};

const MAX_CONCURRENT_SOURCES: usize = 6;
const MAX_CONCURRENT_FEEDS: usize = 4;
const DISCUSSION_REFRESH_LIMIT: i64 = 100;

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
    let prepared = match prepare_scrape_sources(&services.pool, request.first_edition_run_id).await
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
    let source_plans = match build_source_plans(&request, &prepared) {
        Ok(plans) => plans,
        Err(error) => {
            return HandlerExecution::from_result(TaskResult::fail(Some(error), false));
        }
    };
    if source_plans.is_empty() {
        return HandlerExecution::from_result(TaskResult::ok());
    }

    let gateway = services.gateway.clone();
    let outcomes = stream::iter(source_plans)
        .map(move |plan| {
            let gateway = gateway.clone();
            async move { execute_source(&gateway, plan).await }
        })
        .buffer_unordered(MAX_CONCURRENT_SOURCES)
        .collect::<Vec<_>>()
        .await;

    if lease.ownership_lost() {
        return HandlerExecution::from_result(TaskResult::fail(
            Some("scrape task ownership was lost during provider work".to_owned()),
            true,
        ));
    }
    let failed_sources = outcomes
        .iter()
        .filter(|outcome| outcome.failed_without_progress())
        .map(|outcome| outcome.source.clone())
        .collect::<Vec<_>>();
    let task_result = if failed_sources.is_empty() {
        TaskResult::ok()
    } else {
        TaskResult::fail(
            Some(format!(
                "Scraper sources failed: {}",
                failed_sources.join(", ")
            )),
            true,
        )
    };
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
            let parsed = RequestedSource::parse(value)
                .ok_or_else(|| format!("unknown scrape source {value:?}"))?;
            if parsed == RequestedSource::All {
                sources.extend(RequestedSource::all_concrete());
            } else {
                sources.insert(parsed);
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
    All,
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
            "all" => Some(Self::All),
            "reddit" => Some(Self::Reddit),
            "substack" => Some(Self::Substack),
            "atom" => Some(Self::Atom),
            "podcast" | "podcasts" | "podcastrss" | "podcastunified" => Some(Self::Podcast),
            "discussioncomments" | "discussions" => Some(Self::DiscussionComments),
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
            Self::All => "all".to_owned(),
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

fn build_source_plans(
    request: &ScrapeRequest,
    prepared: &PreparedScrapeSources,
) -> Result<Vec<SourcePlan>, String> {
    let mut plans = Vec::new();
    for source in &request.sources {
        match *source {
            RequestedSource::All => unreachable!("all is expanded while parsing"),
            RequestedSource::Aggregator(key) => {
                let required_config_ids = prepared
                    .configs
                    .iter()
                    .filter(|config| {
                        config.scraper_type == "aggregator"
                            && config
                                .config
                                .get("key")
                                .and_then(Value::as_str)
                                .is_some_and(|value| AggregatorKey::parse(value) == Some(key))
                    })
                    .map(|config| config.id)
                    .collect::<Vec<_>>();
                if request.first_edition_run_id.is_some() && required_config_ids.is_empty() {
                    return Err(format!(
                        "first-edition run has no active {} source",
                        key.as_str()
                    ));
                }
                plans.push(SourcePlan {
                    source: key.as_str().to_owned(),
                    kind: SourcePlanKind::Aggregator {
                        key,
                        required_config_ids,
                    },
                });
            }
            RequestedSource::Reddit => {
                let targets = prepared
                    .configs
                    .iter()
                    .filter(|config| config.scraper_type == "reddit")
                    .filter_map(reddit_target)
                    .collect::<Vec<_>>();
                plans.push(SourcePlan {
                    source: source.canonical_name(),
                    kind: SourcePlanKind::Reddit(targets),
                });
            }
            RequestedSource::Substack | RequestedSource::Atom | RequestedSource::Podcast => {
                let scraper_type = match source {
                    RequestedSource::Substack => "substack",
                    RequestedSource::Atom => "atom",
                    RequestedSource::Podcast => "podcast_rss",
                    _ => unreachable!(),
                };
                let targets = prepared
                    .configs
                    .iter()
                    .filter(|config| config.scraper_type == scraper_type)
                    .filter_map(feed_target)
                    .collect::<Vec<_>>();
                plans.push(SourcePlan {
                    source: source.canonical_name(),
                    kind: SourcePlanKind::Feed(targets),
                });
            }
            RequestedSource::DiscussionComments => plans.push(SourcePlan {
                source: source.canonical_name(),
                kind: SourcePlanKind::DiscussionComments,
            }),
        }
    }
    Ok(plans)
}

fn feed_target(config: &ScrapeConfigSnapshot) -> Option<FeedScrapeTarget> {
    let feed_url = config
        .feed_url
        .clone()
        .or_else(|| clean_string(config.config.get("feed_url")))
        .or_else(|| clean_string(config.config.get("url")))?;
    Some(FeedScrapeTarget {
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
    result: Result<ScrapeProviderOutcome, String>,
    discussion_catchup: bool,
}

impl SourceOutcome {
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
        } => SourceOutcome {
            source: plan.source,
            required_config_ids,
            result: gateway
                .fetch_aggregator(key)
                .await
                .map_err(|error| error.to_string()),
            discussion_catchup: false,
        },
        SourcePlanKind::Feed(targets) => {
            let gateway = gateway.clone();
            let results = stream::iter(targets)
                .map(move |target| {
                    let gateway = gateway.clone();
                    async move {
                        let result = gateway
                            .fetch_feed(&target)
                            .await
                            .map_err(|error| error.to_string());
                        (target.config_id, result)
                    }
                })
                .buffer_unordered(MAX_CONCURRENT_FEEDS)
                .collect::<Vec<_>>()
                .await;
            combine_config_outcomes(plan.source, results)
        }
        SourcePlanKind::Reddit(targets) => {
            let target_ids = targets
                .iter()
                .map(|target| target.config_id)
                .collect::<BTreeSet<_>>();
            match gateway.fetch_reddit_targets(&targets).await {
                Ok(results) => combine_config_outcomes(plan.source, results),
                Err(error) => SourceOutcome {
                    source: plan.source,
                    required_config_ids: target_ids.iter().copied().collect(),
                    result: Err(error.to_string()),
                    discussion_catchup: false,
                },
            }
        }
        SourcePlanKind::DiscussionComments => SourceOutcome {
            source: plan.source,
            required_config_ids: Vec::new(),
            result: Ok(ScrapeProviderOutcome {
                items: Vec::new(),
                item_errors: Vec::new(),
            }),
            discussion_catchup: true,
        },
    }
}

fn combine_config_outcomes(
    source: String,
    results: Vec<(i64, Result<ScrapeProviderOutcome, String>)>,
) -> SourceOutcome {
    let required_config_ids = results.iter().map(|(id, _)| *id).collect::<Vec<_>>();
    let mut items = Vec::new();
    let mut item_errors = Vec::new();
    for (config_id, result) in results {
        match result {
            Ok(mut outcome) => {
                items.append(&mut outcome.items);
                item_errors.append(&mut outcome.item_errors);
            }
            Err(error) => {
                item_errors.push(format!("config {config_id}: {error}"));
            }
        }
    }
    SourceOutcome {
        source,
        required_config_ids,
        result: Ok(ScrapeProviderOutcome { items, item_errors }),
        discussion_catchup: false,
    }
}

#[derive(Debug)]
struct ScrapeFinalizer {
    queue: QueueKernel,
    request: ScrapeRequest,
    prepared: PreparedScrapeSources,
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
            retryable: config_mismatch
                || outcomes.iter().any(SourceOutcome::failed_without_progress),
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
        let valid_config_ids =
            matching_scrape_config_ids(transaction, &self.prepared.configs).await?;
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
            let mut processed_by_config = BTreeMap::<i64, i64>::new();
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
                                process_content_ids.insert(persisted.content_id);
                            }
                        }
                        Ok(PersistedScrapeItem::News(persisted)) => {
                            savepoint.commit().await?;
                            if persisted.created
                                && !matches!(item, ScrapedItem::News(item) if item.status == "ready")
                            {
                                enrich_news_ids.insert(persisted.news_item_id);
                            }
                            if persisted.discussion_refresh_ready {
                                discussion_ids.insert(persisted.news_item_id);
                            }
                        }
                        Err(error) => {
                            savepoint.rollback().await?;
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
            if outcome.discussion_catchup {
                discussion_ids.extend(
                    due_discussion_refresh_ids(transaction, DISCUSSION_REFRESH_LIMIT).await?,
                );
            }
            if let Some(run_id) = self.request.first_edition_run_id
                && (outcome.source == "reddit" || AggregatorKey::parse(&outcome.source).is_some())
                && failures.configuration_is_current()
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
            request.dedupe = Some(false);
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
