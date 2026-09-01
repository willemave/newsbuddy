use std::collections::BTreeSet;
use std::error::Error;

use chrono::{NaiveDateTime, Utc};
use newsly_db::{
    XSyncRepositoryError, remove_stale_x_bookmark_save, resolve_x_bookmark_destination,
    save_x_bookmark_destination, x_bookmark_destination_needs_image,
};
use newsly_extraction::ExtractionTiming;
use newsly_queue::{EnqueueRequest, QueueKernel, TaskType};
use serde_json::{Map, Value, json};
use sqlx::{FromRow, PgPool, Postgres, Transaction};
use thiserror::Error;
use url::Url;

use crate::{HandlerFinalizerFuture, TaskFinalizer, TaskFinalizerResult};

use super::extraction::{intent_name, method_name};
use super::model::{
    ContentFinalizationPlan, ContentMutation, ContentSnapshot, FeedCandidate, InstructionLinkPlan,
    UsageWrite,
};
use super::storage::StagedContentBody;

const PROCESSING_KEY: &str = "processing";
const DOMAIN_KEY: &str = "domain";
const CONTENT_USER_LOCK_ATTEMPTS: usize = 2;
const RAW_BODY_KEYS: [&str; 6] = [
    "content",
    "transcript",
    "content_to_summarize",
    "file_path",
    "transcript_path",
    "full_text",
];

/// Loads the immutable content input used during the external-work phase. The query owns no
/// explicit transaction and returns before any extractor request begins.
pub(super) async fn load_content_snapshot(
    pool: &PgPool,
    content_id: i64,
) -> Result<Option<ContentSnapshot>, ContentRepositoryError> {
    let row = sqlx::query_as::<_, ContentSnapshot>(
        r"
        SELECT
            content.id::bigint AS id,
            content.content_type,
            content.url,
            content.title,
            content.status,
            COALESCE(content.content_metadata, '{}'::json) AS content_metadata,
            content.platform,
            body.storage_provider AS body_storage_provider,
            body.storage_key AS body_storage_key
        FROM contents AS content
        LEFT JOIN content_bodies AS body
            ON body.content_id = content.id AND body.variant = 'source'
        WHERE content.id::bigint = $1
        ",
    )
    .bind(content_id)
    .fetch_optional(pool)
    .await?;
    Ok(row)
}

/// Product-state effect published only inside the queue kernel's exact-lease transaction.
#[derive(Debug, Clone)]
pub(super) struct ContentFinalizer {
    queue: QueueKernel,
    plan: ContentFinalizationPlan,
}

impl ContentFinalizer {
    pub(super) const fn new(queue: QueueKernel, plan: ContentFinalizationPlan) -> Self {
        Self { queue, plan }
    }

    #[allow(clippy::too_many_lines)]
    async fn apply_inner(
        &self,
        transaction: &mut Transaction<'static, Postgres>,
    ) -> Result<(), ContentRepositoryError> {
        let (mut content, usage_user_id) =
            lock_content_after_usage_user(transaction, self.plan.content_id).await?;
        persist_usage(
            transaction,
            &self.plan,
            content.as_ref().map(|row| row.id),
            usage_user_id,
        )
        .await?;

        let Some(content) = content.as_mut() else {
            return Ok(());
        };

        if mutation_scrubs_instruction(&self.plan.mutation) {
            scrub_task_instruction(transaction, self.plan.task_id).await?;
        }
        if is_terminal_status(&content.status) {
            return Ok(());
        }

        match &self.plan.mutation {
            ContentMutation::AnalyzeClassified {
                content_type,
                platform,
                metadata_updates,
                subscribe_to_feed,
                ..
            } => {
                let mut metadata = metadata_map(&content.content_metadata);
                for (key, value) in metadata_updates {
                    set_domain_field(&mut metadata, key, value.clone());
                }
                finalize_analysis(
                    transaction,
                    &self.queue,
                    content,
                    &mut metadata,
                    content_type,
                    platform.as_deref(),
                    &[],
                    *subscribe_to_feed,
                )
                .await?;
            }
            ContentMutation::AnalyzeSuccess {
                content_type,
                platform,
                title,
                body,
                body_char_count,
                feed_candidates,
                extraction_method,
                warnings,
                timings,
                metadata_updates,
                instruction_links,
                subscribe_to_feed,
                ..
            } => {
                let mut metadata = metadata_map(&content.content_metadata);
                for (key, value) in metadata_updates {
                    set_domain_field(&mut metadata, key, value.clone());
                }
                set_domain_field(
                    &mut metadata,
                    "document_extractor_method",
                    Value::String(extraction_method.clone()),
                );
                set_domain_field(
                    &mut metadata,
                    "document_extractor_warnings",
                    strings_value(warnings),
                );
                set_domain_field(
                    &mut metadata,
                    "document_extractor_timings",
                    timings_value(timings),
                );
                if *body_char_count >= 500 {
                    set_domain_field(
                        &mut metadata,
                        "analyze_url_source_body_ready",
                        Value::Bool(true),
                    );
                }
                if content.title.as_deref().is_none_or(str::is_empty) {
                    content.title = Some(truncate_chars(title, 500));
                }
                upsert_source_body(transaction, content.id, body).await?;
                finalize_analysis(
                    transaction,
                    &self.queue,
                    content,
                    &mut metadata,
                    content_type,
                    platform.as_deref(),
                    feed_candidates,
                    *subscribe_to_feed,
                )
                .await?;
                apply_instruction_links(
                    transaction,
                    &self.queue,
                    content,
                    usage_user_id,
                    instruction_links,
                )
                .await?;
            }
            ContentMutation::AnalyzeTweet {
                target_url,
                content_type,
                platform,
                title,
                metadata_updates,
                body,
                body_char_count,
                ..
            } => {
                let mut metadata = metadata_map(&content.content_metadata);
                for (key, value) in metadata_updates {
                    set_domain_field(&mut metadata, key, value.clone());
                }
                if content.source_url.is_none() {
                    content.source_url = runtime_value(&metadata, "discussion_url")
                        .and_then(Value::as_str)
                        .map(str::to_owned)
                        .or_else(|| Some(content.url.clone()));
                }
                if content.title.as_deref().is_none_or(str::is_empty)
                    && let Some(title) = title.as_deref().and_then(nonempty)
                {
                    content.title = Some(truncate_chars(title, 500));
                }
                if let Some(body) = body {
                    upsert_source_body(transaction, content.id, body).await?;
                    if *body_char_count >= 500 {
                        set_domain_field(
                            &mut metadata,
                            "analyze_url_source_body_ready",
                            Value::Bool(true),
                        );
                    }
                }
                let duplicate_id =
                    find_duplicate_content(transaction, content.id, content_type, target_url)
                        .await?;
                if let Some(duplicate_id) = duplicate_id {
                    let bookmark_bindings = x_bookmark_destination_bindings(
                        transaction,
                        content.id,
                        x_bookmark_fallback_user_id(&metadata, usage_user_id),
                    )
                    .await?;
                    set_processing_field(
                        &mut metadata,
                        "canonical_content_id",
                        Value::from(duplicate_id),
                    );
                    "skipped".clone_into(&mut content.status);
                    content.error_message =
                        Some("Canonical URL conflicts with existing content".to_owned());
                    content.processed_at = Some(Utc::now().naive_utc());
                    relink_canonical_user_state(transaction, content.id, duplicate_id).await?;
                    reconcile_x_bookmark_destination_users(
                        transaction,
                        &self.queue,
                        duplicate_id,
                        &bookmark_bindings,
                    )
                    .await?;
                } else {
                    target_url.clone_into(&mut content.url);
                    content_type.clone_into(&mut content.content_type);
                    content.platform = Some(truncate_chars(platform, 50));
                    set_domain_field(&mut metadata, "platform", Value::String(platform.clone()));
                    enqueue_content_task(
                        transaction,
                        &self.queue,
                        TaskType::ProcessContent,
                        content.id,
                    )
                    .await?;
                    content.error_message = None;
                }
                update_content(transaction, content, metadata).await?;
            }
            ContentMutation::ProcessArticle {
                article,
                body,
                subscribe_to_feed,
            } => {
                let mut metadata = metadata_map(&content.content_metadata);
                content.title = nonempty(&article.title)
                    .map(|title| truncate_chars(title, 500))
                    .or_else(|| content.title.clone());
                if content.source_url.is_none() {
                    content.source_url = Some(article.original_url.clone());
                }
                if article.final_url != content.url {
                    let duplicate_id = find_duplicate_content(
                        transaction,
                        content.id,
                        &content.content_type,
                        &article.final_url,
                    )
                    .await?;
                    if let Some(duplicate_id) = duplicate_id {
                        set_processing_field(
                            &mut metadata,
                            "canonical_content_id",
                            Value::from(duplicate_id),
                        );
                    } else {
                        content.url.clone_from(&article.final_url);
                    }
                }
                set_domain_field(
                    &mut metadata,
                    "author",
                    article
                        .author
                        .as_ref()
                        .map_or(Value::Null, |author| Value::String(author.clone())),
                );
                set_domain_field(
                    &mut metadata,
                    "publication_date",
                    article.published_at.map_or(Value::Null, |published_at| {
                        Value::String(published_at.to_rfc3339())
                    }),
                );
                set_domain_field(
                    &mut metadata,
                    "content_type",
                    Value::String("html".to_owned()),
                );
                set_domain_field(
                    &mut metadata,
                    "document_extractor_method",
                    Value::String(article.extraction_method.clone()),
                );
                set_domain_field(
                    &mut metadata,
                    "document_extractor_warnings",
                    strings_value(&article.warnings),
                );
                set_domain_field(
                    &mut metadata,
                    "document_extractor_timings",
                    timings_value(&article.timings),
                );
                if article.used_firecrawl {
                    set_domain_field(&mut metadata, "used_firecrawl_fallback", Value::Bool(true));
                    set_domain_field(
                        &mut metadata,
                        "firecrawl_fallback_length",
                        Value::from(body.char_count),
                    );
                }
                let excerpt = compact_excerpt(&article.body);
                if let Some(excerpt) = &excerpt {
                    set_domain_field(&mut metadata, "excerpt", Value::String(excerpt.clone()));
                }
                remove_raw_body_fields(&mut metadata);
                attach_feed_candidates(&mut metadata, &article.feed_candidates);
                upsert_source_body(transaction, content.id, body).await?;

                let subscribed = if *subscribe_to_feed {
                    apply_feed_subscription(
                        transaction,
                        &self.queue,
                        content,
                        &mut metadata,
                        &article.feed_candidates,
                    )
                    .await?;
                    true
                } else {
                    false
                };
                if !subscribed {
                    let next_task = next_content_task(&metadata, content.platform.as_deref());
                    enqueue_content_task(transaction, &self.queue, next_task, content.id).await?;
                }

                if subscribed { "skipped" } else { "processing" }.clone_into(&mut content.status);
                content.error_message = None;
                content.processed_at = Some(Utc::now().naive_utc());
                content.publication_date = article
                    .published_at
                    .map(|value| value.naive_utc())
                    .or(content.publication_date)
                    .or(Some(content.created_at));
                content.search_text = excerpt;
                update_content(transaction, content, metadata).await?;
            }
            ContentMutation::PodcastHandoff => {
                enqueue_content_task(
                    transaction,
                    &self.queue,
                    TaskType::ProcessPodcastMedia,
                    content.id,
                )
                .await?;
                "processing".clone_into(&mut content.status);
                content.error_message = None;
                content.processed_at = Some(Utc::now().naive_utc());
                update_content(
                    transaction,
                    content,
                    metadata_map(&content.content_metadata),
                )
                .await?;
            }
            ContentMutation::ExtractionFailure {
                stage,
                reason,
                code,
                terminal,
                ..
            } => {
                let mut metadata = metadata_map(&content.content_metadata);
                let mut errors = runtime_array(&metadata, "processing_errors");
                errors.push(json!({
                    "stage": stage,
                    "reason": reason,
                    "code": code,
                    "timestamp": Utc::now().to_rfc3339(),
                }));
                set_processing_field(&mut metadata, "processing_errors", Value::Array(errors));
                set_processing_field(
                    &mut metadata,
                    "extraction_error",
                    Value::String(reason.clone()),
                );
                set_processing_field(
                    &mut metadata,
                    "extraction_error_code",
                    Value::String(code.clone()),
                );
                content.error_message = Some(truncate_chars(reason, 8_000));
                if *terminal {
                    remove_raw_body_fields(&mut metadata);
                    "failed".clone_into(&mut content.status);
                    content.processed_at = Some(Utc::now().naive_utc());
                }
                update_content(transaction, content, metadata).await?;
            }
        }
        Ok(())
    }
}

impl TaskFinalizer for ContentFinalizer {
    fn apply<'a>(
        &'a self,
        transaction: &'a mut Transaction<'static, Postgres>,
    ) -> HandlerFinalizerFuture<'a> {
        Box::pin(async move {
            self.apply_inner(transaction)
                .await
                .map_err(|error| Box::new(error) as Box<dyn Error + Send + Sync>)?;
            Ok(TaskFinalizerResult::Keep)
        })
    }
}

#[derive(Debug, FromRow)]
struct LockedContent {
    id: i64,
    content_type: String,
    url: String,
    source_url: Option<String>,
    title: Option<String>,
    status: String,
    error_message: Option<String>,
    content_metadata: Value,
    created_at: NaiveDateTime,
    processed_at: Option<NaiveDateTime>,
    publication_date: Option<NaiveDateTime>,
    platform: Option<String>,
    search_text: Option<String>,
}

async fn load_locked_content(
    transaction: &mut Transaction<'static, Postgres>,
    content_id: i64,
) -> Result<Option<LockedContent>, sqlx::Error> {
    sqlx::query_as::<_, LockedContent>(
        r"
        SELECT
            id::bigint AS id,
            content_type,
            url,
            source_url,
            title,
            status,
            error_message,
            COALESCE(content_metadata, '{}'::json) AS content_metadata,
            created_at,
            processed_at,
            publication_date,
            platform,
            search_text
        FROM contents
        WHERE id::bigint = $1
        FOR UPDATE
        ",
    )
    .bind(content_id)
    .fetch_optional(&mut **transaction)
    .await
}

/// Locks the submitted user before the content row, matching submission and account-deletion
/// transactions. The unlocked attribution read is revalidated after the content lock. If a first
/// submission races a previously unattributed worker result, rolling back to the savepoint releases
/// only the content lock; the finalizer then locks the newly observed user and retries without
/// repeating external work.
async fn lock_content_after_usage_user(
    transaction: &mut Transaction<'static, Postgres>,
    content_id: i64,
) -> Result<(Option<LockedContent>, Option<i64>), ContentRepositoryError> {
    let mut expected_user_id = load_content_usage_user_id(transaction, content_id).await?;
    for _ in 0..CONTENT_USER_LOCK_ATTEMPTS {
        let active_user_id = lock_active_usage_user(transaction, expected_user_id).await?;
        sqlx::query("SAVEPOINT content_user_lock_order")
            .execute(&mut **transaction)
            .await?;
        let content = load_locked_content(transaction, content_id).await?;
        let locked_user_id = content
            .as_ref()
            .and_then(|content| content.content_metadata.as_object())
            .and_then(|metadata| runtime_positive_integer(metadata, "submitted_by_user_id"));
        if locked_user_id == expected_user_id {
            sqlx::query("RELEASE SAVEPOINT content_user_lock_order")
                .execute(&mut **transaction)
                .await?;
            return Ok((content, active_user_id));
        }

        sqlx::query("ROLLBACK TO SAVEPOINT content_user_lock_order")
            .execute(&mut **transaction)
            .await?;
        sqlx::query("RELEASE SAVEPOINT content_user_lock_order")
            .execute(&mut **transaction)
            .await?;
        expected_user_id = locked_user_id;
    }

    Err(ContentRepositoryError::ContentUserAttributionChanged {
        content_id,
        attempts: CONTENT_USER_LOCK_ATTEMPTS,
    })
}

async fn load_content_usage_user_id(
    transaction: &mut Transaction<'static, Postgres>,
    content_id: i64,
) -> Result<Option<i64>, sqlx::Error> {
    let metadata = sqlx::query_scalar::<_, Value>(
        r"
        SELECT COALESCE(content_metadata, '{}'::json)
        FROM contents
        WHERE id::bigint = $1
        ",
    )
    .bind(content_id)
    .fetch_optional(&mut **transaction)
    .await?;
    Ok(metadata
        .as_ref()
        .and_then(Value::as_object)
        .and_then(|metadata| runtime_positive_integer(metadata, "submitted_by_user_id")))
}

#[allow(clippy::too_many_arguments)]
async fn finalize_analysis(
    transaction: &mut Transaction<'static, Postgres>,
    queue: &QueueKernel,
    content: &mut LockedContent,
    metadata: &mut Map<String, Value>,
    content_type: &str,
    platform: Option<&str>,
    feed_candidates: &[FeedCandidate],
    subscribe_to_feed: bool,
) -> Result<(), ContentRepositoryError> {
    attach_feed_candidates(metadata, feed_candidates);
    if let Some(platform) = platform.and_then(nonempty) {
        content.platform = Some(truncate_chars(platform, 50));
        set_domain_field(metadata, "platform", Value::String(platform.to_owned()));
    }

    let duplicate_id =
        find_duplicate_content(transaction, content.id, content_type, &content.url).await?;
    if let Some(duplicate_id) = duplicate_id {
        set_processing_field(metadata, "canonical_content_id", Value::from(duplicate_id));
        "skipped".clone_into(&mut content.status);
        content.error_message = Some("Canonical URL conflicts with existing content".to_owned());
        content.processed_at = Some(Utc::now().naive_utc());
        relink_canonical_user_state(transaction, content.id, duplicate_id).await?;
        update_content(transaction, content, metadata.clone()).await?;
        return Ok(());
    }

    content_type.clone_into(&mut content.content_type);
    if subscribe_to_feed {
        apply_feed_subscription(transaction, queue, content, metadata, feed_candidates).await?;
        "skipped".clone_into(&mut content.status);
        content.error_message = None;
        content.processed_at = Some(Utc::now().naive_utc());
    } else {
        enqueue_content_task(transaction, queue, TaskType::ProcessContent, content.id).await?;
    }
    update_content(transaction, content, metadata.clone()).await?;
    Ok(())
}

async fn apply_instruction_links(
    transaction: &mut Transaction<'static, Postgres>,
    queue: &QueueKernel,
    source: &LockedContent,
    active_user_id: Option<i64>,
    links: &[InstructionLinkPlan],
) -> Result<(), ContentRepositoryError> {
    let Some(user_id) = active_user_id else {
        return Ok(());
    };
    let Some(source_url) = normalize_content_url(&source.url) else {
        return Ok(());
    };
    let mut seen = BTreeSet::from([source_url]);
    let mut image_content_ids = BTreeSet::new();
    let mut requests = Vec::new();
    for link in links {
        let Some(url) = normalize_content_url(&link.url) else {
            continue;
        };
        if !seen.insert(url.clone()) {
            continue;
        }
        let (content_id, created) =
            resolve_instruction_child(transaction, source.id, user_id, link, &url).await?;
        let inbox_created = ensure_inbox_status(transaction, user_id, content_id).await?;
        if created {
            requests.push(instruction_child_analysis_request(content_id, user_id));
        }
        if inbox_created
            && x_bookmark_destination_needs_image(transaction, content_id).await?
            && image_content_ids.insert(content_id)
        {
            requests.push(generated_image_request(content_id));
        }
    }
    if !requests.is_empty() {
        queue
            .enqueue_many_in_transaction(transaction, requests)
            .await?;
    }
    Ok(())
}

async fn resolve_instruction_child(
    transaction: &mut Transaction<'static, Postgres>,
    source_content_id: i64,
    user_id: i64,
    link: &InstructionLinkPlan,
    url: &str,
) -> Result<(i64, bool), sqlx::Error> {
    let existing_id = sqlx::query_scalar::<_, i64>(
        r"
        SELECT id::bigint
        FROM contents
        WHERE url = $1
        ORDER BY id
        LIMIT 1
        FOR SHARE
        ",
    )
    .bind(url)
    .fetch_optional(&mut **transaction)
    .await?;
    if let Some(content_id) = existing_id {
        return Ok((content_id, false));
    }

    let metadata = json!({
        "source": "self submission",
        "submitted_by_user_id": user_id,
        "submitted_via": "share_sheet_instruction",
        "instruction_source_content_id": source_content_id,
        "instruction_link": {
            "title": link.title,
            "context": link.context,
            "content_type": link.content_type,
            "platform": link.platform,
            "source": link.source,
        },
    });
    let inserted = sqlx::query_scalar::<_, i64>(
        r"
        INSERT INTO contents (
            content_type,
            url,
            source_url,
            title,
            source,
            platform,
            is_aggregate,
            status,
            retry_count,
            classification,
            content_metadata,
            created_at,
            updated_at
        )
        VALUES (
            'unknown', $1, $1, NULL, 'self submission', NULL, FALSE,
            'new', 0, 'to_read', $2, timezone('UTC', now()), timezone('UTC', now())
        )
        ON CONFLICT (url, content_type) DO NOTHING
        RETURNING id::bigint
        ",
    )
    .bind(url)
    .bind(metadata)
    .fetch_optional(&mut **transaction)
    .await?;
    if let Some(content_id) = inserted {
        return Ok((content_id, true));
    }
    let content_id = sqlx::query_scalar::<_, i64>(
        "SELECT id::bigint FROM contents WHERE url = $1 ORDER BY id LIMIT 1 FOR SHARE",
    )
    .bind(url)
    .fetch_one(&mut **transaction)
    .await?;
    Ok((content_id, false))
}

fn instruction_child_analysis_request(content_id: i64, user_id: i64) -> EnqueueRequest {
    let mut payload = Map::new();
    payload.insert("content_id".to_owned(), Value::from(content_id));
    let mut request = EnqueueRequest::new(TaskType::AnalyzeUrl);
    request.content_id = Some(content_id);
    request.payload = Some(payload);
    request.access_user_id = Some(user_id);
    request
}

fn generated_image_request(content_id: i64) -> EnqueueRequest {
    let mut request = EnqueueRequest::new(TaskType::GenerateImage);
    request.content_id = Some(content_id);
    request.dedupe = Some(true);
    request
}

async fn ensure_inbox_status(
    transaction: &mut Transaction<'static, Postgres>,
    user_id: i64,
    content_id: i64,
) -> Result<bool, sqlx::Error> {
    let inserted = sqlx::query(
        r"
        INSERT INTO content_status (user_id, content_id, status, created_at, updated_at)
        VALUES ($1::bigint::integer, $2::bigint::integer, 'inbox', timezone('UTC', now()), timezone('UTC', now()))
        ON CONFLICT (user_id, content_id) DO NOTHING
        ",
    )
    .bind(user_id)
    .bind(content_id)
    .execute(&mut **transaction)
    .await?
    .rows_affected()
        > 0;
    Ok(inserted)
}

fn x_bookmark_fallback_user_id(
    metadata: &Map<String, Value>,
    active_user_id: Option<i64>,
) -> Option<i64> {
    runtime_value(metadata, "submitted_via")
        .and_then(Value::as_str)
        .is_some_and(|value| value.trim().eq_ignore_ascii_case("x_bookmarks"))
        .then_some(active_user_id)
        .flatten()
}

#[derive(Debug, Default)]
struct XBookmarkDestinationBindings {
    synced_item_ids: Vec<i64>,
    user_ids: BTreeSet<i64>,
}

async fn x_bookmark_destination_bindings(
    transaction: &mut Transaction<'static, Postgres>,
    content_id: i64,
    fallback_user_id: Option<i64>,
) -> Result<XBookmarkDestinationBindings, sqlx::Error> {
    let rows = sqlx::query_as::<_, (i64, i64)>(
        r"
        SELECT synced_item.id::bigint, connection.user_id::bigint
        FROM user_integration_synced_items AS synced_item
        JOIN user_integration_connections AS connection
          ON connection.id = synced_item.connection_id
        JOIN users AS app_user
          ON app_user.id = connection.user_id
        WHERE synced_item.content_id::bigint = $1
          AND synced_item.channel = 'bookmarks'
          AND connection.provider = 'x'
          AND app_user.is_active IS TRUE
        ",
    )
    .bind(content_id)
    .fetch_all(&mut **transaction)
    .await?;
    let mut bindings = XBookmarkDestinationBindings::default();
    for (synced_item_id, user_id) in rows {
        bindings.synced_item_ids.push(synced_item_id);
        bindings.user_ids.insert(user_id);
    }
    if let Some(user_id) = fallback_user_id {
        bindings.user_ids.insert(user_id);
    }
    Ok(bindings)
}

async fn reconcile_x_bookmark_destination_users(
    transaction: &mut Transaction<'static, Postgres>,
    queue: &QueueKernel,
    canonical_content_id: i64,
    bindings: &XBookmarkDestinationBindings,
) -> Result<(), ContentRepositoryError> {
    if bindings.user_ids.is_empty() {
        return Ok(());
    }
    let destination_content_id = resolve_x_bookmark_destination(transaction, canonical_content_id)
        .await?
        .unwrap_or(canonical_content_id);
    if destination_content_id != canonical_content_id && !bindings.synced_item_ids.is_empty() {
        sqlx::query(
            r"
            UPDATE user_integration_synced_items
            SET content_id = $2, updated_at = timezone('UTC', now())
            WHERE id::bigint = ANY($1::bigint[])
            ",
        )
        .bind(&bindings.synced_item_ids)
        .bind(destination_content_id)
        .execute(&mut **transaction)
        .await?;
    }
    for user_id in &bindings.user_ids {
        save_x_bookmark_destination(transaction, *user_id, destination_content_id).await?;
        remove_stale_x_bookmark_save(
            transaction,
            *user_id,
            canonical_content_id,
            destination_content_id,
        )
        .await?;
    }
    if x_bookmark_destination_needs_image(transaction, destination_content_id).await? {
        queue
            .enqueue_many_in_transaction(
                transaction,
                vec![generated_image_request(destination_content_id)],
            )
            .await?;
    }
    Ok(())
}

fn normalize_content_url(value: &str) -> Option<String> {
    let mut url = Url::parse(value.trim()).ok()?;
    if !matches!(url.scheme(), "http" | "https") || url.host().is_none() {
        return None;
    }
    if url.scheme() == "http" && url.set_scheme("https").is_err() {
        return None;
    }
    url.set_fragment(None);
    Some(url.to_string())
}

async fn find_duplicate_content(
    transaction: &mut Transaction<'static, Postgres>,
    content_id: i64,
    content_type: &str,
    url: &str,
) -> Result<Option<i64>, sqlx::Error> {
    sqlx::query_scalar::<_, i64>(
        r"
        SELECT id::bigint
        FROM contents
        WHERE id::bigint <> $1 AND content_type = $2 AND url = $3
        ORDER BY id
        LIMIT 1
        FOR SHARE
        ",
    )
    .bind(content_id)
    .bind(content_type)
    .bind(url)
    .fetch_optional(&mut **transaction)
    .await
}

/// Move all unique-per-user overlays from a classification loser to its canonical winner.
///
/// Each insert absorbs a concurrent or pre-existing winner row before the loser is deleted. The
/// timestamped overlays retain the newer interaction, while independent chat histories only
/// change their content destination. The relink runs inside the queue/product finalize fence.
async fn relink_canonical_user_state(
    transaction: &mut Transaction<'static, Postgres>,
    loser_content_id: i64,
    winner_content_id: i64,
) -> Result<(), sqlx::Error> {
    sqlx::query(
        r"
        INSERT INTO content_status (user_id, content_id, status, created_at, updated_at)
        SELECT user_id, $2, status, created_at, updated_at
        FROM content_status
        WHERE content_id::bigint = $1
        ON CONFLICT (user_id, content_id) DO NOTHING
        ",
    )
    .bind(loser_content_id)
    .bind(winner_content_id)
    .execute(&mut **transaction)
    .await?;
    sqlx::query("DELETE FROM content_status WHERE content_id::bigint = $1")
        .bind(loser_content_id)
        .execute(&mut **transaction)
        .await?;

    sqlx::query(
        r"
        INSERT INTO content_read_status (user_id, content_id, read_at, created_at)
        SELECT user_id, $2, read_at, created_at
        FROM content_read_status
        WHERE content_id::bigint = $1
        ON CONFLICT (user_id, content_id) DO UPDATE
        SET read_at = GREATEST(content_read_status.read_at, EXCLUDED.read_at)
        ",
    )
    .bind(loser_content_id)
    .bind(winner_content_id)
    .execute(&mut **transaction)
    .await?;
    sqlx::query("DELETE FROM content_read_status WHERE content_id::bigint = $1")
        .bind(loser_content_id)
        .execute(&mut **transaction)
        .await?;

    sqlx::query(
        r"
        INSERT INTO content_knowledge_saves (user_id, content_id, saved_at, created_at)
        SELECT user_id, $2, saved_at, created_at
        FROM content_knowledge_saves
        WHERE content_id::bigint = $1
        ON CONFLICT (user_id, content_id) DO UPDATE
        SET saved_at = GREATEST(content_knowledge_saves.saved_at, EXCLUDED.saved_at)
        ",
    )
    .bind(loser_content_id)
    .bind(winner_content_id)
    .execute(&mut **transaction)
    .await?;
    sqlx::query("DELETE FROM content_knowledge_saves WHERE content_id::bigint = $1")
        .bind(loser_content_id)
        .execute(&mut **transaction)
        .await?;

    sqlx::query(
        r"
        INSERT INTO content_unlikes (user_id, content_id, unliked_at, created_at)
        SELECT user_id, $2, unliked_at, created_at
        FROM content_unlikes
        WHERE content_id::bigint = $1
        ON CONFLICT (user_id, content_id) DO UPDATE
        SET unliked_at = GREATEST(content_unlikes.unliked_at, EXCLUDED.unliked_at)
        ",
    )
    .bind(loser_content_id)
    .bind(winner_content_id)
    .execute(&mut **transaction)
    .await?;
    sqlx::query("DELETE FROM content_unlikes WHERE content_id::bigint = $1")
        .bind(loser_content_id)
        .execute(&mut **transaction)
        .await?;

    sqlx::query(
        r"
        UPDATE user_integration_synced_items
        SET content_id = $2, updated_at = timezone('UTC', now())
        WHERE content_id::bigint = $1
        ",
    )
    .bind(loser_content_id)
    .bind(winner_content_id)
    .execute(&mut **transaction)
    .await?;

    sqlx::query(
        r"
        UPDATE chat_sessions
        SET content_id = $2, updated_at = timezone('UTC', now())
        WHERE content_id::bigint = $1
        ",
    )
    .bind(loser_content_id)
    .bind(winner_content_id)
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

async fn update_content(
    transaction: &mut Transaction<'static, Postgres>,
    content: &LockedContent,
    metadata: Map<String, Value>,
) -> Result<(), sqlx::Error> {
    sqlx::query(
        r"
        UPDATE contents
        SET
            content_type = $2,
            url = $3,
            source_url = $4,
            title = $5,
            status = $6,
            error_message = $7,
            content_metadata = $8,
            processed_at = $9,
            publication_date = $10,
            platform = $11,
            search_text = $12,
            updated_at = timezone('UTC', now())
        WHERE id::bigint = $1
        ",
    )
    .bind(content.id)
    .bind(&content.content_type)
    .bind(&content.url)
    .bind(&content.source_url)
    .bind(&content.title)
    .bind(&content.status)
    .bind(&content.error_message)
    .bind(Value::Object(metadata))
    .bind(content.processed_at)
    .bind(content.publication_date)
    .bind(&content.platform)
    .bind(&content.search_text)
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

async fn upsert_source_body(
    transaction: &mut Transaction<'static, Postgres>,
    content_id: i64,
    body: &StagedContentBody,
) -> Result<(), sqlx::Error> {
    sqlx::query(
        r"
        INSERT INTO content_bodies (
            content_id,
            variant,
            storage_provider,
            storage_bucket,
            storage_key,
            content_format,
            sha256,
            byte_size,
            char_count,
            created_at,
            updated_at
        )
        VALUES ($1, 'source', $2, NULL, $3, $4, $5, $6, $7, timezone('UTC', now()), timezone('UTC', now()))
        ON CONFLICT (content_id, variant) DO UPDATE
        SET
            storage_provider = EXCLUDED.storage_provider,
            storage_bucket = EXCLUDED.storage_bucket,
            storage_key = EXCLUDED.storage_key,
            content_format = EXCLUDED.content_format,
            sha256 = EXCLUDED.sha256,
            byte_size = EXCLUDED.byte_size,
            char_count = EXCLUDED.char_count,
            updated_at = timezone('UTC', now())
        ",
    )
    .bind(content_id)
    .bind(body.storage_provider)
    .bind(&body.storage_key)
    .bind(body.content_format)
    .bind(&body.sha256)
    .bind(body.byte_size)
    .bind(body.char_count)
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

async fn enqueue_content_task(
    transaction: &mut Transaction<'static, Postgres>,
    queue: &QueueKernel,
    task_type: TaskType,
    content_id: i64,
) -> Result<i64, ContentRepositoryError> {
    let mut payload = Map::new();
    payload.insert("content_id".to_owned(), Value::from(content_id));
    let mut request = EnqueueRequest::new(task_type);
    request.content_id = Some(content_id);
    request.payload = Some(payload);
    let result = queue
        .enqueue_many_in_transaction(transaction, vec![request])
        .await?;
    result
        .task_ids
        .into_iter()
        .next()
        .ok_or(ContentRepositoryError::MissingEnqueueResult(task_type))
}

#[derive(Debug)]
struct FeedSubscriptionProjection {
    status: &'static str,
    created: bool,
    config_id: Option<i64>,
    backfill_task_id: Option<i64>,
}

#[allow(clippy::too_many_lines)]
async fn apply_feed_subscription(
    transaction: &mut Transaction<'static, Postgres>,
    queue: &QueueKernel,
    content: &LockedContent,
    metadata: &mut Map<String, Value>,
    candidates: &[FeedCandidate],
) -> Result<(), ContentRepositoryError> {
    set_processing_field(metadata, "subscribe_to_feed", Value::Bool(true));
    let Some(candidate) = candidates.first() else {
        set_processing_field(
            metadata,
            "feed_subscription",
            json!({"status": "no_feed_found"}),
        );
        return Ok(());
    };
    let Some(user_id) = runtime_positive_integer(metadata, "submitted_by_user_id") else {
        set_processing_field(
            metadata,
            "feed_subscription",
            json!({"status": "missing_user"}),
        );
        return Ok(());
    };
    let active = sqlx::query_scalar::<_, i64>(
        "SELECT id::bigint FROM users WHERE id::bigint = $1 AND is_active IS TRUE FOR SHARE",
    )
    .bind(user_id)
    .fetch_optional(&mut **transaction)
    .await?;
    if active.is_none() {
        set_processing_field(
            metadata,
            "feed_subscription",
            json!({"status": "inactive_user"}),
        );
        return Ok(());
    }

    let feed_url = canonicalize_feed_url(&candidate.url);
    let existing = sqlx::query_as::<_, ExistingFeedConfig>(
        r"
        SELECT id::bigint AS id, is_active
        FROM user_scraper_configs
        WHERE user_id::bigint = $1 AND scraper_type = $2 AND feed_url = $3
        FOR UPDATE
        ",
    )
    .bind(user_id)
    .bind(&candidate.feed_type)
    .bind(&feed_url)
    .fetch_optional(&mut **transaction)
    .await?;

    let projection = if let Some(existing) = existing {
        if existing.is_active {
            FeedSubscriptionProjection {
                status: "already_exists",
                created: false,
                config_id: Some(existing.id),
                backfill_task_id: None,
            }
        } else {
            sqlx::query(
                "UPDATE user_scraper_configs SET is_active = TRUE, updated_at = timezone('UTC', now()) WHERE id::bigint = $1",
            )
            .bind(existing.id)
            .execute(&mut **transaction)
            .await?;
            let task_id = enqueue_feed_backfill(transaction, queue, user_id, existing.id).await?;
            FeedSubscriptionProjection {
                status: "reactivated",
                created: false,
                config_id: Some(existing.id),
                backfill_task_id: Some(task_id),
            }
        }
    } else {
        let display_name = feed_display_name(candidate, content);
        let inserted_id = sqlx::query_scalar::<_, i64>(
            r"
            INSERT INTO user_scraper_configs (
                user_id,
                scraper_type,
                display_name,
                feed_url,
                config,
                is_active,
                created_at,
                updated_at
            )
            VALUES ($1, $2, $3, $4, $5, TRUE, timezone('UTC', now()), timezone('UTC', now()))
            ON CONFLICT (user_id, scraper_type, feed_url) DO NOTHING
            RETURNING id::bigint
            ",
        )
        .bind(user_id)
        .bind(&candidate.feed_type)
        .bind(display_name)
        .bind(&feed_url)
        .bind(json!({"feed_url": feed_url, "limit": 1}))
        .fetch_optional(&mut **transaction)
        .await?;
        if let Some(config_id) = inserted_id {
            let task_id = enqueue_feed_backfill(transaction, queue, user_id, config_id).await?;
            FeedSubscriptionProjection {
                status: "created",
                created: true,
                config_id: Some(config_id),
                backfill_task_id: Some(task_id),
            }
        } else {
            let raced_id = sqlx::query_scalar::<_, i64>(
                r"
                SELECT id::bigint
                FROM user_scraper_configs
                WHERE user_id::bigint = $1 AND scraper_type = $2 AND feed_url = $3
                ",
            )
            .bind(user_id)
            .bind(&candidate.feed_type)
            .bind(&feed_url)
            .fetch_one(&mut **transaction)
            .await?;
            FeedSubscriptionProjection {
                status: "already_exists",
                created: false,
                config_id: Some(raced_id),
                backfill_task_id: None,
            }
        }
    };

    let initial_download = if let (Some(config_id), Some(task_id)) =
        (projection.config_id, projection.backfill_task_id)
    {
        json!({
            "requested_count": 2,
            "ran": false,
            "status": "queued",
            "reason": projection.status,
            "config_id": config_id,
            "task_id": task_id,
        })
    } else {
        json!({
            "requested_count": 2,
            "ran": false,
            "status": "skipped",
            "reason": projection.status,
        })
    };
    set_processing_field(
        metadata,
        "feed_subscription",
        json!({
            "status": projection.status,
            "feed_url": feed_url,
            "feed_type": candidate.feed_type,
            "created": projection.created,
            "config_id": projection.config_id,
            "backfill_task_id": projection.backfill_task_id,
            "initial_download": initial_download,
        }),
    );
    Ok(())
}

#[derive(Debug, FromRow)]
struct ExistingFeedConfig {
    id: i64,
    is_active: bool,
}

async fn enqueue_feed_backfill(
    transaction: &mut Transaction<'static, Postgres>,
    queue: &QueueKernel,
    user_id: i64,
    config_id: i64,
) -> Result<i64, ContentRepositoryError> {
    let mut payload = Map::new();
    payload.insert("user_id".to_owned(), Value::from(user_id));
    payload.insert(
        "config_ids".to_owned(),
        Value::Array(vec![Value::from(config_id)]),
    );
    payload.insert("count".to_owned(), Value::from(2));
    let mut request = EnqueueRequest::new(TaskType::BackfillFeeds);
    request.payload = Some(payload);
    request.dedupe = Some(true);
    request.owner_user_id = Some(user_id);
    request.access_user_id = Some(user_id);
    let result = queue
        .enqueue_many_in_transaction(transaction, vec![request])
        .await?;
    result
        .task_ids
        .into_iter()
        .next()
        .ok_or(ContentRepositoryError::MissingEnqueueResult(
            TaskType::BackfillFeeds,
        ))
}

#[allow(clippy::too_many_lines)]
async fn persist_usage(
    transaction: &mut Transaction<'static, Postgres>,
    plan: &ContentFinalizationPlan,
    content_id: Option<i64>,
    user_id: Option<i64>,
) -> Result<(), ContentRepositoryError> {
    for write in &plan.usage {
        match write {
            UsageWrite::Extraction(batch) => {
                for (index, event) in batch.events.iter().enumerate() {
                    let quantity = i32::try_from(event.quantity).map_err(|_| {
                        ContentRepositoryError::UsageQuantityOutOfRange(event.quantity)
                    })?;
                    insert_usage(
                        transaction,
                        UsageInsert {
                            provider: "document_extractor",
                            model: method_name(batch.method),
                            feature: "document_extraction",
                            operation: intent_name(batch.intent),
                            request_id: Some(&batch.request_id),
                            task_id: plan.task_id,
                            content_id,
                            user_id,
                            // A response can report several differently-unitized counters. Keep
                            // one row per event, but count the extractor request only once.
                            request_count: (index == 0).then_some(1),
                            resource_count: Some(quantity),
                            input_tokens: None,
                            cache_read_tokens: None,
                            cache_write_tokens: None,
                            output_tokens: None,
                            total_tokens: None,
                            cost_usd: None,
                            pricing_version: None,
                            metadata: json!({
                                "kind": event.kind,
                                "quantity": event.quantity,
                                "unit": event.unit,
                            }),
                        },
                    )
                    .await?;
                }
            }
            UsageWrite::Firecrawl(usage) => {
                insert_usage(
                    transaction,
                    UsageInsert {
                        provider: "firecrawl",
                        model: "scrape-v2",
                        feature: "html_extraction",
                        operation: "firecrawl_scrape",
                        request_id: Some(&usage.request_id),
                        task_id: plan.task_id,
                        content_id,
                        user_id,
                        request_count: Some(1),
                        resource_count: Some(1),
                        input_tokens: None,
                        cache_read_tokens: None,
                        cache_write_tokens: None,
                        output_tokens: None,
                        total_tokens: None,
                        cost_usd: usage.cost_usd,
                        pricing_version: Some("configured-v1"),
                        metadata: json!({
                            "url": usage.url,
                            "status_code": usage.status_code,
                        }),
                    },
                )
                .await?;
            }
            UsageWrite::Model(usage) => {
                let total_tokens = usage
                    .usage
                    .input_tokens
                    .saturating_add(usage.usage.output_tokens);
                insert_usage(
                    transaction,
                    UsageInsert {
                        provider: &usage.provider,
                        model: &usage.model,
                        feature: "content_analyzer",
                        operation: "content_analyzer.analyze_url",
                        request_id: usage.response_id.as_deref(),
                        task_id: plan.task_id,
                        content_id,
                        user_id,
                        request_count: Some(saturating_i32(usage.usage.request_count)),
                        resource_count: None,
                        input_tokens: Some(saturating_i32(usage.usage.input_tokens)),
                        cache_read_tokens: Some(saturating_i32(usage.usage.cached_input_tokens)),
                        cache_write_tokens: Some(saturating_i32(usage.usage.cache_write_tokens)),
                        output_tokens: Some(saturating_i32(usage.usage.output_tokens)),
                        total_tokens: Some(saturating_i32(total_tokens)),
                        cost_usd: None,
                        pricing_version: None,
                        metadata: json!({
                            "reasoning_tokens": usage.usage.reasoning_tokens,
                        }),
                    },
                )
                .await?;
            }
            UsageWrite::X(usage) => {
                let resource_count = i32::try_from(usage.resource_count).map_err(|_| {
                    ContentRepositoryError::XResourceCountOutOfRange(usage.resource_count)
                })?;
                insert_usage(
                    transaction,
                    UsageInsert {
                        provider: "x",
                        model: "posts.read",
                        feature: "analyze_url",
                        operation: usage.operation,
                        request_id: Some(&usage.request_id),
                        task_id: plan.task_id,
                        content_id,
                        user_id,
                        request_count: Some(1),
                        resource_count: Some(resource_count),
                        input_tokens: None,
                        cache_read_tokens: None,
                        cache_write_tokens: None,
                        output_tokens: None,
                        total_tokens: None,
                        cost_usd: None,
                        pricing_version: None,
                        metadata: Value::Object(Map::new()),
                    },
                )
                .await?;
            }
        }
    }
    Ok(())
}

struct UsageInsert<'a> {
    provider: &'a str,
    model: &'a str,
    feature: &'a str,
    operation: &'a str,
    request_id: Option<&'a str>,
    task_id: i64,
    content_id: Option<i64>,
    user_id: Option<i64>,
    request_count: Option<i32>,
    resource_count: Option<i32>,
    input_tokens: Option<i32>,
    cache_read_tokens: Option<i32>,
    cache_write_tokens: Option<i32>,
    output_tokens: Option<i32>,
    total_tokens: Option<i32>,
    cost_usd: Option<f64>,
    pricing_version: Option<&'a str>,
    metadata: Value,
}

async fn insert_usage(
    transaction: &mut Transaction<'static, Postgres>,
    usage: UsageInsert<'_>,
) -> Result<(), sqlx::Error> {
    sqlx::query(
        r"
        INSERT INTO vendor_usage_records (
            provider,
            model,
            feature,
            operation,
            source,
            request_id,
            task_id,
            content_id,
            user_id,
            input_tokens,
            cache_read_tokens,
            cache_write_tokens,
            output_tokens,
            total_tokens,
            request_count,
            resource_count,
            cost_usd,
            currency,
            pricing_version,
            metadata,
            created_at
        )
        VALUES (
            $1, $2, $3, $4, 'rust_worker', $5, $6, $7, $8,
            $9, $10, $11, $12, $13, $14, $15,
            $16, 'USD', $17, $18, timezone('UTC', now())
        )
        ",
    )
    .bind(usage.provider)
    .bind(usage.model)
    .bind(usage.feature)
    .bind(usage.operation)
    .bind(usage.request_id)
    .bind(usage.task_id)
    .bind(usage.content_id)
    .bind(usage.user_id)
    .bind(usage.input_tokens)
    .bind(usage.cache_read_tokens)
    .bind(usage.cache_write_tokens)
    .bind(usage.output_tokens)
    .bind(usage.total_tokens)
    .bind(usage.request_count)
    .bind(usage.resource_count)
    .bind(usage.cost_usd)
    .bind(usage.pricing_version)
    .bind(usage.metadata)
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

async fn scrub_task_instruction(
    transaction: &mut Transaction<'static, Postgres>,
    task_id: i64,
) -> Result<(), sqlx::Error> {
    sqlx::query(
        r"
        UPDATE processing_tasks
        SET payload = (COALESCE(payload, '{}'::json)::jsonb - 'instruction')::json
        WHERE id::bigint = $1
        ",
    )
    .bind(task_id)
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

fn mutation_scrubs_instruction(mutation: &ContentMutation) -> bool {
    matches!(
        mutation,
        ContentMutation::AnalyzeClassified {
            scrub_instruction: true,
            ..
        } | ContentMutation::AnalyzeSuccess {
            scrub_instruction: true,
            ..
        } | ContentMutation::AnalyzeTweet {
            scrub_instruction: true,
            ..
        } | ContentMutation::ExtractionFailure {
            scrub_instruction: true,
            ..
        }
    )
}

fn metadata_map(value: &Value) -> Map<String, Value> {
    value.as_object().cloned().unwrap_or_default()
}

fn set_domain_field(metadata: &mut Map<String, Value>, key: &str, value: Value) {
    metadata.insert(key.to_owned(), value.clone());
    let domain = metadata
        .entry(DOMAIN_KEY.to_owned())
        .or_insert_with(|| Value::Object(Map::new()));
    if !domain.is_object() {
        *domain = Value::Object(Map::new());
    }
    domain
        .as_object_mut()
        .expect("domain was normalized to an object")
        .insert(key.to_owned(), value);
}

fn set_processing_field(metadata: &mut Map<String, Value>, key: &str, value: Value) {
    metadata.insert(key.to_owned(), value.clone());
    let processing = metadata
        .entry(PROCESSING_KEY.to_owned())
        .or_insert_with(|| Value::Object(Map::new()));
    if !processing.is_object() {
        *processing = Value::Object(Map::new());
    }
    processing
        .as_object_mut()
        .expect("processing was normalized to an object")
        .insert(key.to_owned(), value);
}

fn runtime_value<'a>(metadata: &'a Map<String, Value>, key: &str) -> Option<&'a Value> {
    metadata
        .get(key)
        .or_else(|| {
            metadata
                .get(PROCESSING_KEY)
                .and_then(Value::as_object)
                .and_then(|processing| processing.get(key))
        })
        .or_else(|| {
            metadata
                .get(DOMAIN_KEY)
                .and_then(Value::as_object)
                .and_then(|domain| domain.get(key))
        })
}

fn next_content_task(metadata: &Map<String, Value>, content_platform: Option<&str>) -> TaskType {
    let platform = runtime_value(metadata, "platform")
        .and_then(Value::as_str)
        .or(content_platform);
    let is_tweet = platform
        .is_some_and(|value| matches!(value.trim().to_ascii_lowercase().as_str(), "twitter" | "x"))
        || runtime_value(metadata, "tweet_id")
            .and_then(Value::as_str)
            .is_some_and(|value| !value.trim().is_empty());
    let has_video = runtime_value(metadata, "has_video").is_some_and(|value| match value {
        Value::Bool(value) => *value,
        Value::String(value) => value.trim().eq_ignore_ascii_case("true"),
        _ => false,
    });
    let has_transcript = runtime_value(metadata, "video_transcript")
        .and_then(Value::as_str)
        .is_some_and(|value| !value.trim().is_empty());
    if is_tweet && has_video && !has_transcript {
        TaskType::DownloadTweetVideoAudio
    } else {
        TaskType::Summarize
    }
}

fn runtime_positive_integer(metadata: &Map<String, Value>, key: &str) -> Option<i64> {
    runtime_value(metadata, key)
        .and_then(Value::as_i64)
        .filter(|value| *value > 0)
}

fn runtime_array(metadata: &Map<String, Value>, key: &str) -> Vec<Value> {
    runtime_value(metadata, key)
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default()
}

fn attach_feed_candidates(metadata: &mut Map<String, Value>, candidates: &[FeedCandidate]) {
    let Some(first) = candidates.first() else {
        return;
    };
    set_processing_field(metadata, "detected_feed", first.to_json());
    if candidates.len() > 1 {
        set_processing_field(
            metadata,
            "all_detected_feeds",
            Value::Array(candidates.iter().map(FeedCandidate::to_json).collect()),
        );
    }
}

fn remove_raw_body_fields(metadata: &mut Map<String, Value>) {
    for key in RAW_BODY_KEYS {
        metadata.remove(key);
        for namespace in [DOMAIN_KEY, PROCESSING_KEY] {
            if let Some(values) = metadata.get_mut(namespace).and_then(Value::as_object_mut) {
                values.remove(key);
            }
        }
    }
}

fn strings_value(values: &[String]) -> Value {
    Value::Array(values.iter().cloned().map(Value::String).collect())
}

fn timings_value(values: &[ExtractionTiming]) -> Value {
    Value::Array(
        values
            .iter()
            .map(|timing| {
                json!({
                    "name": timing.name,
                    "milliseconds": timing.milliseconds,
                })
            })
            .collect(),
    )
}

fn compact_excerpt(body: &str) -> Option<String> {
    let compact = body.split_whitespace().collect::<Vec<_>>().join(" ");
    nonempty(&compact).map(|value| truncate_chars(value, 1_000))
}

fn nonempty(value: &str) -> Option<&str> {
    let trimmed = value.trim();
    (!trimmed.is_empty()).then_some(trimmed)
}

fn truncate_chars(value: &str, max_chars: usize) -> String {
    value.chars().take(max_chars).collect()
}

fn canonicalize_feed_url(value: &str) -> String {
    let Ok(mut url) = Url::parse(value.trim()) else {
        return value.trim().trim_end_matches('/').to_owned();
    };
    url.set_fragment(None);
    let path = url.path().trim_end_matches('/').to_owned();
    url.set_path(&path);
    url.to_string()
}

fn feed_display_name(candidate: &FeedCandidate, content: &LockedContent) -> String {
    let host = Url::parse(&candidate.url)
        .ok()
        .and_then(|url| url.host_str().map(str::to_owned));
    let selected = candidate
        .title
        .as_deref()
        .and_then(nonempty)
        .or_else(|| content.title.as_deref().and_then(nonempty))
        .or(host.as_deref())
        .unwrap_or("Feed")
        .to_owned();
    truncate_chars(&selected, 255)
}

fn is_terminal_status(status: &str) -> bool {
    matches!(status, "completed" | "failed" | "skipped")
}

async fn lock_active_usage_user(
    transaction: &mut Transaction<'static, Postgres>,
    user_id: Option<i64>,
) -> Result<Option<i64>, sqlx::Error> {
    let Some(user_id) = user_id else {
        return Ok(None);
    };
    sqlx::query_scalar::<_, i64>(
        "SELECT id::bigint FROM users WHERE id::bigint = $1 AND is_active IS TRUE FOR SHARE",
    )
    .bind(user_id)
    .fetch_optional(&mut **transaction)
    .await
}

#[derive(Debug, Error)]
pub(super) enum ContentRepositoryError {
    #[error("PostgreSQL content finalization failed")]
    Sqlx(#[from] sqlx::Error),
    #[error("queue handoff failed during content finalization")]
    Queue(#[from] newsly_queue::QueueError),
    #[error("X bookmark destination reconciliation failed")]
    XSync(#[from] XSyncRepositoryError),
    #[error("{0} enqueue returned no task id")]
    MissingEnqueueResult(TaskType),
    #[error("extractor usage quantity {0} does not fit the persistence contract")]
    UsageQuantityOutOfRange(u64),
    #[error("X usage resource count {0} does not fit the persistence contract")]
    XResourceCountOutOfRange(usize),
    #[error(
        "content {content_id} submitted-user attribution changed across {attempts} lock attempts"
    )]
    ContentUserAttributionChanged { content_id: i64, attempts: usize },
}

fn saturating_i32(value: u64) -> i32 {
    i32::try_from(value).unwrap_or(i32::MAX)
}

#[cfg(test)]
mod tests;
