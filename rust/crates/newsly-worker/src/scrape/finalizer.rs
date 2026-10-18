//! Atomic scrape publication, progress, downstream work and bounded grouping recovery.

use std::collections::{BTreeMap, BTreeSet};
use std::error::Error;

use newsly_db::{
    PersistedContentRecord, PersistedNewsRecord, ScrapeConfigSnapshot, ScrapeRepositoryError,
    ScrapedContentRecord, ScrapedNewsRecord, due_discussion_refresh_ids,
    matching_scrape_config_ids, persist_scraped_content, persist_scraped_news,
    record_first_edition_scrape_result,
};
use newsly_domain::AggregatorKey;
use newsly_providers::ScrapedItem;
use newsly_queue::{EnqueueRequest, QueueKernel, TaskResult, TaskType};
use serde_json::{Map, Value};
use sqlx::{Acquire, Postgres, Transaction};

use super::brutalist::brutalist_budget_keys;
use super::{ScrapeRequest, SourceOutcome};
use crate::{HandlerFinalizerFuture, TaskFinalizer, TaskFinalizerResult};

const DISCUSSION_REFRESH_LIMIT: i64 = 100;

#[derive(Debug)]
pub(super) struct ScrapeFinalizer {
    pub(super) task_id: i64,
    pub(super) queue: QueueKernel,
    pub(super) request: ScrapeRequest,
    pub(super) prepared: Vec<ScrapeConfigSnapshot>,
    pub(super) outcomes: Vec<SourceOutcome>,
}

enum PersistedScrapeItem {
    Content(PersistedContentRecord),
    News(PersistedNewsRecord),
}

#[derive(Default)]
pub(super) struct ScrapeFinalizationFailures {
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
    pub(super) fn new(outcomes: &[SourceOutcome], config_mismatch: bool) -> Self {
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

    pub(super) fn record_persistence(&mut self, error: &ScrapeRepositoryError) {
        self.persistence_count += 1;
        self.retryable |= error.retryable();
    }

    fn record_progress_failure(&mut self) {
        self.progress = ScrapeProgressState::Failed;
        self.retryable = true;
    }

    pub(super) fn into_result(self) -> TaskFinalizerResult {
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
    pub(super) async fn apply_inner(
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
                            if let ScrapeRepositoryError::Database(source) = &error
                                && newsly_db::news_grouping::is_contention(source)
                            {
                                // A busy grouping scope invalidates this entire publication,
                                // including earlier items and first-edition/source progress.
                                return Err(Box::new(error));
                            }
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
        Box::pin(async move {
            // Use a named savepoint so all item saves and progress can be rolled back together
            // while retaining the outer queue lease and its durable deferral observation.
            sqlx::query("SAVEPOINT news_grouping_scrape")
                .execute(&mut **transaction)
                .await?;
            match self.apply_inner(transaction).await {
                Ok(result) => {
                    sqlx::query("RELEASE SAVEPOINT news_grouping_scrape")
                        .execute(&mut **transaction)
                        .await?;
                    newsly_db::news_grouping::clear_deferral(transaction, self.task_id).await?;
                    Ok(result)
                }
                Err(error) if newsly_db::news_grouping::contention_source(error.as_ref()) => {
                    sqlx::query("ROLLBACK TO SAVEPOINT news_grouping_scrape")
                        .execute(&mut **transaction)
                        .await?;
                    sqlx::query("RELEASE SAVEPOINT news_grouping_scrape")
                        .execute(&mut **transaction)
                        .await?;
                    let deferral =
                        newsly_db::news_grouping::record_deferral(transaction, self.task_id)
                            .await?;
                    tracing::warn!(
                        task_id = self.task_id,
                        count = deferral.count,
                        age_seconds = deferral.age_seconds,
                        exhausted = deferral.exhausted,
                        code = "scrape_news_grouping_contention",
                        "Scrape publication encountered a busy News grouping scope"
                    );
                    let result = if deferral.exhausted {
                        TaskResult::fail(Some("news_grouping_budget_exhausted".into()), false)
                    } else {
                        TaskResult::defer(deferral.delay_seconds)
                    };
                    Ok(TaskFinalizerResult::Override(result))
                }
                Err(error) => Err(error),
            }
        })
    }
}

fn item_config_id(item: &ScrapedItem) -> Option<i64> {
    match item {
        ScrapedItem::News(item) => item.user_scraper_config_id,
        ScrapedItem::Content(item) => Some(item.config_id),
    }
}
