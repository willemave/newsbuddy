use newsly_db::{
    AppliedContentSubmission, ContentSubmissionInput, ContentSubmissionRepositoryError,
    ScraperConfigRepositoryError, SubmissionTaskResolution, apply_content_submission,
    apply_validated_feed_subscription,
};
use newsly_queue::{EnqueueRequest, QueueError, QueueKernel, TaskType};
use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};
use sqlx::{Postgres, Transaction};
use thiserror::Error;

use super::workflows::{ContentActionInput, FeedActionInput};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct SubmittedShareContent {
    pub content_id: i64,
    pub task_id: Option<i64>,
    pub already_exists: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct AppliedShareFeedSubscription {
    pub config_id: i64,
    pub outcome: &'static str,
    pub feed_url: String,
    pub feed_type: String,
    pub feed_format: String,
    pub backfill_task_id: Option<i64>,
}

#[derive(Debug, Clone, Copy)]
pub(crate) struct ShareSubmissionPolicy {
    pub subscribe_to_feed: bool,
    pub share_and_chat: bool,
    pub save_to_knowledge_and_mark_read: bool,
}

impl ShareSubmissionPolicy {
    pub(crate) const fn content_saved() -> Self {
        Self {
            subscribe_to_feed: false,
            share_and_chat: false,
            save_to_knowledge_and_mark_read: true,
        }
    }

    pub(crate) const fn content_inbox() -> Self {
        Self {
            subscribe_to_feed: false,
            share_and_chat: false,
            save_to_knowledge_and_mark_read: false,
        }
    }

    pub(crate) const fn chat() -> Self {
        Self {
            subscribe_to_feed: false,
            share_and_chat: true,
            save_to_knowledge_and_mark_read: true,
        }
    }
}

pub(crate) async fn submit_content_action(
    transaction: &mut Transaction<'_, Postgres>,
    queue: &QueueKernel,
    user_id: i64,
    input: &ContentActionInput,
    policy: ShareSubmissionPolicy,
) -> Result<SubmittedShareContent, ShareSubmissionError> {
    submit(
        transaction,
        queue,
        user_id,
        &input.url,
        input.title.as_deref(),
        input.platform.as_deref(),
        input.instruction.as_deref(),
        input.chat_initial_message.as_deref(),
        policy,
    )
    .await
}

pub(crate) async fn apply_validated_feed_action(
    transaction: &mut Transaction<'_, Postgres>,
    queue: &QueueKernel,
    user_id: i64,
    input: &FeedActionInput,
) -> Result<AppliedShareFeedSubscription, ShareSubmissionError> {
    let feed_type = input
        .feed_type
        .as_deref()
        .ok_or(ShareSubmissionError::MissingValidatedFeedMetadata)?;
    let feed_format = input
        .feed_format
        .as_deref()
        .ok_or(ShareSubmissionError::MissingValidatedFeedMetadata)?;
    let applied = apply_validated_feed_subscription(
        transaction,
        user_id,
        feed_type,
        feed_format,
        input.title.as_deref(),
        &input.url,
    )
    .await?;
    let backfill_task_id = if applied.mutation.needs_backfill() {
        let mut request = EnqueueRequest::new(TaskType::BackfillFeeds);
        request.payload = json!({
            "user_id": user_id,
            "config_ids": [applied.config.id],
            "count": 2,
        })
        .as_object()
        .cloned();
        request.dedupe = Some(true);
        request.owner_user_id = Some(user_id);
        request.access_user_id = Some(user_id);
        queue
            .enqueue_many_in_transaction(transaction, vec![request])
            .await?
            .task_ids
            .into_iter()
            .next()
            .ok_or(ShareSubmissionError::MissingBackfillTask)?
            .into()
    } else {
        None
    };
    Ok(AppliedShareFeedSubscription {
        config_id: applied.config.id,
        outcome: applied.mutation.as_str(),
        feed_url: applied.config.feed_url.unwrap_or_else(|| input.url.clone()),
        feed_type: applied.config.scraper_type,
        feed_format: feed_format.to_owned(),
        backfill_task_id,
    })
}

#[allow(clippy::too_many_arguments)]
async fn submit(
    transaction: &mut Transaction<'_, Postgres>,
    queue: &QueueKernel,
    user_id: i64,
    url: &str,
    title: Option<&str>,
    platform: Option<&str>,
    instruction: Option<&str>,
    chat_initial_message: Option<&str>,
    policy: ShareSubmissionPolicy,
) -> Result<SubmittedShareContent, ShareSubmissionError> {
    let normalized_platform = platform.map(str::to_ascii_lowercase);
    let applied = apply_content_submission(
        transaction,
        &ContentSubmissionInput {
            url,
            title,
            platform: normalized_platform.as_deref(),
            instruction,
            crawl_links: false,
            subscribe_to_feed: policy.subscribe_to_feed,
            share_and_chat: policy.share_and_chat,
            chat_initial_message,
            save_to_knowledge_and_mark_read: policy.save_to_knowledge_and_mark_read,
            user_id,
            submitted_via: "share_action",
        },
    )
    .await?;

    let mut task_id = None;
    if let SubmissionTaskResolution::Reuse(existing_task_id) = applied.task_resolution {
        queue
            .grant_access_in_transaction(transaction, existing_task_id, user_id)
            .await?;
        task_id = Some(existing_task_id);
    }

    let mut requests = Vec::new();
    if applied.enqueue_dig_deeper {
        requests.push(dig_deeper_request(
            applied.content_id,
            user_id,
            chat_initial_message,
        ));
    }
    let analyze_index = if applied.task_resolution == SubmissionTaskResolution::EnqueueAnalyze {
        let index = requests.len();
        requests.push(analyze_request(
            &applied,
            instruction,
            policy.subscribe_to_feed,
            user_id,
        ));
        Some(index)
    } else {
        None
    };
    if applied.enqueue_generated_image {
        let mut request = EnqueueRequest::new(TaskType::GenerateImage);
        request.content_id = Some(applied.content_id);
        requests.push(request);
    }
    if !requests.is_empty() {
        let enqueued = queue
            .enqueue_many_in_transaction(transaction, requests)
            .await?;
        if let Some(index) = analyze_index {
            task_id = enqueued.task_ids.get(index).copied();
            if task_id.is_none() {
                return Err(ShareSubmissionError::MissingAnalysisTask);
            }
        }
    }
    Ok(SubmittedShareContent {
        content_id: applied.content_id,
        task_id,
        already_exists: applied.already_exists,
    })
}

fn analyze_request(
    applied: &AppliedContentSubmission,
    instruction: Option<&str>,
    subscribe_to_feed: bool,
    user_id: i64,
) -> EnqueueRequest {
    let mut payload = Map::from_iter([("content_id".to_owned(), Value::from(applied.content_id))]);
    if let Some(instruction) = instruction {
        payload.insert("instruction".to_owned(), Value::from(instruction));
    }
    if subscribe_to_feed {
        payload.insert("subscribe_to_feed".to_owned(), Value::Bool(true));
    }
    let mut request = EnqueueRequest::new(TaskType::AnalyzeUrl);
    request.content_id = Some(applied.content_id);
    request.payload = Some(payload);
    request.dedupe = Some(true);
    request.access_user_id = Some(user_id);
    request
}

fn dig_deeper_request(
    content_id: i64,
    user_id: i64,
    initial_message: Option<&str>,
) -> EnqueueRequest {
    let mut payload = Map::from_iter([("user_id".to_owned(), Value::from(user_id))]);
    if let Some(message) = initial_message {
        payload.insert("initial_message".to_owned(), Value::from(message));
    }
    let digest = Sha256::digest(initial_message.unwrap_or_default().as_bytes());
    let mut request = EnqueueRequest::new(TaskType::DigDeeper);
    request.content_id = Some(content_id);
    request.payload = Some(payload);
    request.dedupe_key = Some(format!(
        "dig_deeper|user:{user_id}|content:{content_id}|message:{}",
        &hex_encode(&digest)[..16]
    ));
    request.owner_user_id = Some(user_id);
    request
}

fn hex_encode(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut encoded = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        encoded.push(char::from(HEX[usize::from(byte >> 4)]));
        encoded.push(char::from(HEX[usize::from(byte & 0x0f)]));
    }
    encoded
}

#[derive(Debug, Error)]
pub enum ShareSubmissionError {
    #[error(transparent)]
    Repository(#[from] ContentSubmissionRepositoryError),
    #[error(transparent)]
    ScraperConfig(#[from] ScraperConfigRepositoryError),
    #[error(transparent)]
    Queue(#[from] QueueError),
    #[error(transparent)]
    Json(#[from] serde_json::Error),
    #[error("content submission queue omitted the analysis task id")]
    MissingAnalysisTask,
    #[error("validated Share feed input is missing feed_type or feed_format")]
    MissingValidatedFeedMetadata,
    #[error("feed subscription queue omitted the initial backfill task id")]
    MissingBackfillTask,
}

#[cfg(test)]
mod tests {
    use newsly_db::ShareActionFinalizationTask;
    use newsly_queue::QueueKernel;
    use serde_json::{Map, Value, json};
    use sqlx::PgPool;

    use crate::share_actions::apply_share_action_host_action;
    use crate::share_actions::workflows::{
        BriefingActionInput, FeedActionInput, ShareActionHostInput,
    };

    #[sqlx::test(migrations = false)]
    async fn validated_rss_subscription_persists_config_and_atomic_backfill(pool: PgPool) {
        newsly_db::run_migrations(&pool)
            .await
            .expect("test database should migrate");
        let user_id = sqlx::query_scalar::<_, i64>(
            r"
            INSERT INTO users (apple_id, email, is_admin, is_active)
            VALUES ('share-feed-user', 'share-feed@example.test', FALSE, TRUE)
            RETURNING id::bigint
            ",
        )
        .fetch_one(&pool)
        .await
        .expect("test user should insert");
        let input = FeedActionInput {
            url: "https://this-week-in-rust.org/rss.xml".to_owned(),
            title: Some("This Week in Rust".to_owned()),
            platform: None,
            instruction: None,
            feed_type: Some("atom".to_owned()),
            feed_format: Some("rss".to_owned()),
        };
        let queue = QueueKernel::new(pool.clone());
        let task = ShareActionFinalizationTask {
            id: 1,
            user_id,
            mode: "add_feed".to_owned(),
            approval_policy: Map::new(),
            allowed_actions: vec!["subscribe_to_feed".to_owned()],
            input: Map::new(),
            status: "applying".to_owned(),
        };

        let mut transaction = pool.begin().await.expect("subscription transaction");
        let action_result = apply_share_action_host_action(
            &mut transaction,
            &queue,
            "/tmp/newsly-share-feed-test",
            &task,
            "subscribe_to_feed",
            &ShareActionHostInput::Feed(input.clone()),
        )
        .await
        .expect("validated feed should subscribe");
        transaction.commit().await.expect("subscription commit");

        assert_eq!(action_result["outcome"], "completed");
        assert_eq!(action_result["subscribed"], true);
        assert_eq!(action_result["subscription_outcome"], "created");
        assert_eq!(action_result["feed_type"], "atom");
        assert_eq!(action_result["feed_format"], "rss");
        let config_id = action_result["config_id"]
            .as_i64()
            .expect("action result should identify the active config");
        let backfill_task_id = action_result["backfill_task_id"]
            .as_i64()
            .expect("created subscription should enqueue backfill");
        let (stored_config_id, scraper_type, feed_url, config, is_active) =
            sqlx::query_as::<_, (i64, String, Option<String>, Value, bool)>(
                r"
            SELECT id::bigint, scraper_type, feed_url, config, is_active
            FROM user_scraper_configs
            WHERE user_id::bigint = $1
            ",
            )
            .bind(user_id)
            .fetch_one(&pool)
            .await
            .expect("subscription config should persist");
        assert_eq!(stored_config_id, config_id);
        assert_eq!(scraper_type, "atom");
        assert_eq!(feed_url.as_deref(), Some(input.url.as_str()));
        assert!(is_active);
        assert_eq!(config["feed_url"], input.url);
        assert_eq!(config["feed_format"], "rss");
        assert_eq!(config["limit"], 1);

        let (task_type, owner_user_id, payload) =
            sqlx::query_as::<_, (String, Option<i64>, Value)>(
                r"
                SELECT task_type, owner_user_id::bigint, payload
                FROM processing_tasks
                WHERE id::bigint = $1
                ",
            )
            .bind(backfill_task_id)
            .fetch_one(&pool)
            .await
            .expect("backfill task should persist atomically");
        assert_eq!(task_type, "backfill_feeds");
        assert_eq!(owner_user_id, Some(user_id));
        assert_eq!(
            payload,
            json!({"user_id": user_id, "config_ids": [config_id], "count": 2})
        );

        let mut repeat = pool.begin().await.expect("repeat transaction");
        let repeated = apply_share_action_host_action(
            &mut repeat,
            &queue,
            "/tmp/newsly-share-feed-test",
            &task,
            "subscribe_to_feed",
            &ShareActionHostInput::Feed(input),
        )
        .await
        .expect("repeat should be idempotent");
        repeat.commit().await.expect("repeat commit");
        assert_eq!(repeated["config_id"], config_id);
        assert_eq!(repeated["subscription_outcome"], "already_exists");
        assert_eq!(repeated["backfill_task_id"], Value::Null);

        sqlx::query("UPDATE user_scraper_configs SET is_active = FALSE WHERE id::bigint = $1")
            .bind(config_id)
            .execute(&pool)
            .await
            .expect("subscription should deactivate for reactivation coverage");
        let mut reactivation = pool.begin().await.expect("reactivation transaction");
        let reactivated = apply_share_action_host_action(
            &mut reactivation,
            &queue,
            "/tmp/newsly-share-feed-test",
            &task,
            "subscribe_to_feed",
            &ShareActionHostInput::Feed(FeedActionInput {
                url: "https://this-week-in-rust.org/rss.xml".to_owned(),
                title: Some("This Week in Rust".to_owned()),
                platform: None,
                instruction: None,
                feed_type: Some("atom".to_owned()),
                feed_format: Some("rss".to_owned()),
            }),
        )
        .await
        .expect("inactive subscription should reactivate");
        reactivation
            .commit()
            .await
            .expect("reactivation should commit");
        assert_eq!(reactivated["config_id"], config_id);
        assert_eq!(reactivated["subscription_outcome"], "reactivated");
        assert!(
            reactivated["backfill_task_id"].as_i64().is_some(),
            "reactivation should enqueue a new backfill"
        );

        let briefing_task = ShareActionFinalizationTask {
            id: 2,
            user_id,
            mode: "add_to_briefing".to_owned(),
            approval_policy: Map::new(),
            allowed_actions: vec!["add_to_briefing".to_owned()],
            input: Map::new(),
            status: "applying".to_owned(),
        };
        let mut briefing = pool.begin().await.expect("briefing transaction");
        let briefing_result = apply_share_action_host_action(
            &mut briefing,
            &queue,
            "/tmp/newsly-share-feed-test",
            &briefing_task,
            "add_to_briefing",
            &ShareActionHostInput::Briefing(BriefingActionInput::Feed(FeedActionInput {
                url: "https://this-week-in-rust.org/rss.xml".to_owned(),
                title: Some("This Week in Rust".to_owned()),
                platform: None,
                instruction: None,
                feed_type: Some("atom".to_owned()),
                feed_format: Some("rss".to_owned()),
            })),
        )
        .await
        .expect("briefing feed should use the direct subscription repository");
        briefing.commit().await.expect("briefing should commit");
        assert_eq!(briefing_result["resolved_kind"], "feed");
        assert_eq!(briefing_result["config_id"], config_id);
        assert_eq!(briefing_result["subscription_outcome"], "already_exists");
        assert_eq!(briefing_result["subscribed"], true);
    }
}
