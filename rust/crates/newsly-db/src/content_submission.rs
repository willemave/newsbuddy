mod metadata;

use metadata::{
    append_share_and_chat_request, build_new_metadata, json_truthy, metadata_object,
    processing_flag, runtime_metadata, set_processing_field, submission_user_id,
    summary_is_readable,
};
use serde_json::{Map, Value};
use sqlx::{FromRow, Postgres, Transaction};
use thiserror::Error;

use crate::content_actions::{
    ContentActionRepositoryError, mark_content_read, save_content_to_knowledge,
};

const SELF_SUBMISSION_SOURCE: &str = "self submission";
const DEFAULT_SUBMISSION_CHANNEL: &str = "share_sheet";
const X_BOOKMARK_SUBMISSION_CHANNEL: &str = "x_bookmarks";

#[derive(Debug, Clone)]
#[allow(clippy::struct_excessive_bools)] // Mirrors the established submission action contract.
pub struct ContentSubmissionInput<'a> {
    pub url: &'a str,
    pub title: Option<&'a str>,
    pub platform: Option<&'a str>,
    pub instruction: Option<&'a str>,
    pub crawl_links: bool,
    pub subscribe_to_feed: bool,
    pub share_and_chat: bool,
    pub chat_initial_message: Option<&'a str>,
    pub save_to_knowledge_and_mark_read: bool,
    pub user_id: i64,
    pub submitted_via: &'a str,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SubmissionTaskResolution {
    None,
    Reuse(i64),
    EnqueueAnalyze,
}

#[derive(Debug, Clone, PartialEq, Eq)]
#[allow(clippy::struct_excessive_bools)] // Queue decisions must remain independently composable.
pub struct AppliedContentSubmission {
    pub content_id: i64,
    pub content_type: String,
    pub status: String,
    pub platform: Option<String>,
    pub source: String,
    pub already_exists: bool,
    pub task_resolution: SubmissionTaskResolution,
    pub enqueue_dig_deeper: bool,
    pub enqueue_generated_image: bool,
}

#[derive(Debug, Clone, FromRow)]
struct ContentRow {
    id: i64,
    content_type: String,
    status: String,
    platform: Option<String>,
    source: Option<String>,
    source_url: Option<String>,
    title: Option<String>,
    classification: Option<String>,
    content_metadata: Value,
}

#[derive(Debug, FromRow)]
struct ActiveTaskRow {
    id: i64,
    payload: Value,
}

/// Applies the durable, database-owned portion of one URL submission.
///
/// The caller owns the transaction and must atomically apply the returned queue decisions before
/// committing. No filesystem, network, or other external work occurs here.
///
/// # Errors
///
/// Returns a database, content-action, or durable-state error. The caller must roll the
/// transaction back and must not enqueue any returned decisions when this fails.
pub async fn apply_content_submission(
    transaction: &mut Transaction<'_, Postgres>,
    input: &ContentSubmissionInput<'_>,
) -> Result<AppliedContentSubmission, ContentSubmissionRepositoryError> {
    lock_active_user(transaction, input.user_id).await?;

    let submission_channel = if input.submitted_via.trim().is_empty() {
        DEFAULT_SUBMISSION_CHANNEL
    } else {
        input.submitted_via.trim()
    };
    let behavior = SubmissionBehavior {
        channel: submission_channel,
        share_and_chat: input.share_and_chat && !input.subscribe_to_feed,
        save_to_knowledge_and_mark_read: input.save_to_knowledge_and_mark_read
            && !input.subscribe_to_feed,
    };

    let existing = find_content_for_update(transaction, input.url).await?;
    let (mut content, already_exists) = if let Some(existing) = existing {
        (existing, true)
    } else {
        let metadata = build_new_metadata(
            input.user_id,
            behavior.channel,
            input.platform,
            input.subscribe_to_feed,
            behavior.share_and_chat,
            input.chat_initial_message,
        );
        match insert_content(transaction, input, metadata).await? {
            Some(inserted) => (inserted, false),
            None => (
                find_content_for_update(transaction, input.url)
                    .await?
                    .ok_or(ContentSubmissionRepositoryError::LostDuplicateRace)?,
                true,
            ),
        }
    };

    let enqueue_dig_deeper = if already_exists {
        update_existing_content(
            transaction,
            &mut content,
            input,
            behavior.channel,
            behavior.share_and_chat,
        )
        .await?;
        apply_submission_user_state(transaction, &content, input, &behavior).await?;
        behavior.share_and_chat && content.status == "completed"
    } else {
        if !input.subscribe_to_feed {
            apply_submission_user_state(transaction, &content, input, &behavior).await?;
        }
        false
    };

    let task_resolution = if already_exists {
        resolve_existing_analysis_task(transaction, &content, input).await?
    } else {
        SubmissionTaskResolution::EnqueueAnalyze
    };
    let enqueue_generated_image = is_generated_image_candidate(transaction, &content).await?;

    Ok(AppliedContentSubmission {
        content_id: content.id,
        content_type: content.content_type,
        status: content.status,
        platform: if already_exists {
            content.platform
        } else {
            None
        },
        source: content
            .source
            .filter(|source| !source.is_empty())
            .unwrap_or_else(|| SELF_SUBMISSION_SOURCE.to_owned()),
        already_exists,
        task_resolution,
        enqueue_dig_deeper,
        enqueue_generated_image,
    })
}

#[derive(Debug, Clone, Copy)]
struct SubmissionBehavior<'a> {
    channel: &'a str,
    share_and_chat: bool,
    save_to_knowledge_and_mark_read: bool,
}

async fn lock_active_user(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
) -> Result<(), ContentSubmissionRepositoryError> {
    let active = sqlx::query_scalar::<_, i64>(
        "SELECT id::bigint FROM users WHERE id::bigint = $1::bigint AND is_active = TRUE FOR SHARE",
    )
    .bind(user_id)
    .fetch_optional(&mut **transaction)
    .await?;
    if active.is_none() {
        return Err(ContentSubmissionRepositoryError::UserMissingOrInactive);
    }
    Ok(())
}

async fn find_content_for_update(
    transaction: &mut Transaction<'_, Postgres>,
    url: &str,
) -> Result<Option<ContentRow>, sqlx::Error> {
    sqlx::query_as::<_, ContentRow>(
        r"
        SELECT
            id::bigint AS id,
            content_type,
            status,
            platform,
            source,
            source_url,
            title,
            classification,
            content_metadata
        FROM contents
        WHERE url = $1
        ORDER BY id
        LIMIT 1
        FOR UPDATE
        ",
    )
    .bind(url)
    .fetch_optional(&mut **transaction)
    .await
}

async fn insert_content(
    transaction: &mut Transaction<'_, Postgres>,
    input: &ContentSubmissionInput<'_>,
    metadata: Map<String, Value>,
) -> Result<Option<ContentRow>, sqlx::Error> {
    sqlx::query_as::<_, ContentRow>(
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
            'unknown',
            $1,
            $1,
            $2,
            $3,
            $4,
            FALSE,
            'new',
            0,
            'to_read',
            $5,
            timezone('UTC', now()),
            timezone('UTC', now())
        )
        ON CONFLICT (url, content_type) DO NOTHING
        RETURNING
            id::bigint AS id,
            content_type,
            status,
            platform,
            source,
            source_url,
            title,
            classification,
            content_metadata
        ",
    )
    .bind(input.url)
    .bind(input.title)
    .bind(SELF_SUBMISSION_SOURCE)
    .bind(input.platform)
    .bind(Value::Object(metadata))
    .fetch_optional(&mut **transaction)
    .await
}

async fn update_existing_content(
    transaction: &mut Transaction<'_, Postgres>,
    content: &mut ContentRow,
    input: &ContentSubmissionInput<'_>,
    submission_channel: &str,
    share_and_chat: bool,
) -> Result<(), sqlx::Error> {
    let original_source_url = content.source_url.clone();
    let original_title = content.title.clone();
    let original_platform = content.platform.clone();
    let original_metadata = content.content_metadata.clone();

    if content.source_url.as_deref().is_none_or(str::is_empty) {
        content.source_url = Some(input.url.to_owned());
    }
    if input.title.is_some_and(|title| !title.is_empty())
        && content.title.as_deref().is_none_or(str::is_empty)
    {
        content.title = input.title.map(str::to_owned);
    }
    if input.platform.is_some() && content.platform.as_deref().is_none_or(str::is_empty) {
        content.platform = input.platform.map(str::to_owned);
    }

    if input.subscribe_to_feed {
        let mut metadata = metadata_object(&content.content_metadata);
        set_processing_field(&mut metadata, "subscribe_to_feed", Value::Bool(true));
        if submission_user_id(&metadata).is_none() {
            set_processing_field(
                &mut metadata,
                "submitted_by_user_id",
                Value::from(input.user_id),
            );
        }
        if !processing_flag(&metadata, "submitted_via")
            .as_ref()
            .is_some_and(json_truthy)
        {
            set_processing_field(
                &mut metadata,
                "submitted_via",
                Value::from(submission_channel),
            );
        }
        if let Some(platform) = input.platform
            && !processing_flag(&metadata, "platform_hint")
                .as_ref()
                .is_some_and(json_truthy)
        {
            set_processing_field(&mut metadata, "platform_hint", Value::from(platform));
        }
        content.content_metadata = Value::Object(metadata);
    } else if share_and_chat && content.status != "completed" {
        content.content_metadata = Value::Object(append_share_and_chat_request(
            metadata_object(&content.content_metadata),
            input.user_id,
            input.chat_initial_message,
        ));
    }

    if content.source_url != original_source_url
        || content.title != original_title
        || content.platform != original_platform
        || content.content_metadata != original_metadata
    {
        sqlx::query(
            r"
            UPDATE contents
            SET
                source_url = $2,
                title = $3,
                platform = $4,
                content_metadata = $5,
                updated_at = timezone('UTC', now())
            WHERE id::bigint = $1::bigint
            ",
        )
        .bind(content.id)
        .bind(&content.source_url)
        .bind(&content.title)
        .bind(&content.platform)
        .bind(&content.content_metadata)
        .execute(&mut **transaction)
        .await?;
    }
    Ok(())
}

async fn apply_submission_user_state(
    transaction: &mut Transaction<'_, Postgres>,
    content: &ContentRow,
    input: &ContentSubmissionInput<'_>,
    behavior: &SubmissionBehavior<'_>,
) -> Result<(), ContentSubmissionRepositoryError> {
    if input.subscribe_to_feed {
        return Ok(());
    }
    if behavior.channel != X_BOOKMARK_SUBMISSION_CHANNEL
        && matches!(
            content.content_type.as_str(),
            "article" | "podcast" | "news" | "unknown"
        )
    {
        sqlx::query(
            r"
            INSERT INTO content_status (user_id, content_id, status, created_at, updated_at)
            VALUES (
                $1::bigint::integer,
                $2::bigint::integer,
                'inbox',
                timezone('UTC', now()),
                timezone('UTC', now())
            )
            ON CONFLICT (user_id, content_id) DO NOTHING
            ",
        )
        .bind(input.user_id)
        .bind(content.id)
        .execute(&mut **transaction)
        .await?;
    }
    if behavior.share_and_chat || behavior.save_to_knowledge_and_mark_read {
        mark_content_read(transaction, input.user_id, content.id).await?;
    }
    if behavior.save_to_knowledge_and_mark_read {
        save_content_to_knowledge(transaction, input.user_id, content.id).await?;
    }
    Ok(())
}

async fn resolve_existing_analysis_task(
    transaction: &mut Transaction<'_, Postgres>,
    content: &ContentRow,
    input: &ContentSubmissionInput<'_>,
) -> Result<SubmissionTaskResolution, sqlx::Error> {
    let should_analyze = input.subscribe_to_feed
        || input.crawl_links
        || input.instruction.is_some()
        || matches!(
            content.status.as_str(),
            "new" | "pending" | "failed" | "skipped"
        );
    if !should_analyze {
        return Ok(SubmissionTaskResolution::None);
    }

    let active_analyze = sqlx::query_as::<_, ActiveTaskRow>(
        r"
        SELECT id::bigint AS id, payload
        FROM processing_tasks
        WHERE
            content_id::bigint = $1::bigint
            AND task_type = 'analyze_url'
            AND status IN ('pending', 'processing')
        ORDER BY id
        LIMIT 1
        FOR UPDATE
        ",
    )
    .bind(content.id)
    .fetch_optional(&mut **transaction)
    .await?;
    if let Some(task) = active_analyze {
        let mut payload = metadata_object(&task.payload);
        payload
            .entry("content_id".to_owned())
            .or_insert_with(|| Value::from(content.id));
        if let Some(instruction) = input.instruction {
            payload.insert("instruction".to_owned(), Value::from(instruction));
        }
        if input.crawl_links {
            payload.insert("crawl_links".to_owned(), Value::Bool(true));
        }
        if input.subscribe_to_feed {
            payload.insert("subscribe_to_feed".to_owned(), Value::Bool(true));
        }
        sqlx::query("UPDATE processing_tasks SET payload = $2 WHERE id::bigint = $1::bigint")
            .bind(task.id)
            .bind(Value::Object(payload))
            .execute(&mut **transaction)
            .await?;
        return Ok(SubmissionTaskResolution::Reuse(task.id));
    }

    if input.instruction.is_none() && !input.crawl_links && !input.subscribe_to_feed {
        let active_process = sqlx::query_scalar::<_, i64>(
            r"
            SELECT id::bigint
            FROM processing_tasks
            WHERE
                content_id::bigint = $1::bigint
                AND task_type = 'process_content'
                AND status IN ('pending', 'processing')
            ORDER BY id
            LIMIT 1
            FOR UPDATE
            ",
        )
        .bind(content.id)
        .fetch_optional(&mut **transaction)
        .await?;
        if let Some(task_id) = active_process {
            return Ok(SubmissionTaskResolution::Reuse(task_id));
        }
    }
    Ok(SubmissionTaskResolution::EnqueueAnalyze)
}

async fn is_generated_image_candidate(
    transaction: &mut Transaction<'_, Postgres>,
    content: &ContentRow,
) -> Result<bool, sqlx::Error> {
    if !matches!(content.content_type.as_str(), "article" | "podcast")
        || !matches!(content.status.as_str(), "awaiting_image" | "completed")
        || content.classification.as_deref() == Some("skip")
    {
        return Ok(false);
    }
    let runtime = runtime_metadata(&metadata_object(&content.content_metadata));
    if runtime.get("image_generated_at").is_some_and(json_truthy)
        || !summary_is_readable(&runtime, &content.content_type)
    {
        return Ok(false);
    }
    let visible = sqlx::query_scalar::<_, bool>(
        r"
        SELECT
            EXISTS(
                SELECT 1 FROM content_status
                WHERE content_id::bigint = $1::bigint AND status = 'inbox'
            )
            OR EXISTS(
                SELECT 1 FROM content_knowledge_saves
                WHERE content_id::bigint = $1::bigint
            )
        ",
    )
    .bind(content.id)
    .fetch_one(&mut **transaction)
    .await?;
    if !visible {
        return Ok(false);
    }
    let active = sqlx::query_scalar::<_, bool>(
        r"
        SELECT EXISTS(
            SELECT 1 FROM processing_tasks
            WHERE
                content_id::bigint = $1::bigint
                AND task_type = 'generate_image'
                AND status IN ('pending', 'processing')
        )
        ",
    )
    .bind(content.id)
    .fetch_one(&mut **transaction)
    .await?;
    Ok(!active)
}

#[derive(Debug, Error)]
pub enum ContentSubmissionRepositoryError {
    #[error("content submission database operation failed")]
    Sqlx(#[from] sqlx::Error),
    #[error("submission user is missing or inactive")]
    UserMissingOrInactive,
    #[error("duplicate submission race did not resolve an existing content row")]
    LostDuplicateRace,
    #[error("content action persistence failed")]
    ContentAction(#[from] ContentActionRepositoryError),
}
