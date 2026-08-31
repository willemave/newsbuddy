use std::collections::{BTreeMap, BTreeSet};
use std::error::Error;

use newsly_db::{
    AppliedContentSubmission, ContentSubmissionInput, ContentSubmissionRepositoryError,
    NewXSyncUsage, SubmissionTaskResolution, XSyncConnectionUpdate, XSyncRepositoryError,
    apply_content_submission, complete_x_sync, find_reusable_x_bookmark_content,
    lock_current_x_sync_connection, mark_x_sync_failed, mark_x_sync_reauth_required,
    persist_x_bookmark_snapshot, persist_x_sync_connection_update,
    prepare_agent_data_sync_dedupe_key, record_x_sync_usage, remove_stale_x_bookmark_save,
    resolve_x_bookmark_destination, save_x_bookmark_destination, upsert_x_bookmark_ledger,
    x_bookmark_destination_needs_image,
};
use newsly_providers::XTweet;
use newsly_queue::{EnqueueRequest, QueueError, QueueKernel, TaskType};
use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};
use sqlx::{Postgres, Transaction};
use thiserror::Error;

use crate::{HandlerFinalizerFuture, TaskFinalizer, TaskFinalizerResult};

use super::model::{
    BookmarkFetchOutcome, DurableXCredentials, XRequestUsage, XSyncFinalizationPlan, XSyncMutation,
};

#[derive(Debug, Clone)]
pub(super) struct XSyncFinalizer {
    queue: QueueKernel,
    plan: XSyncFinalizationPlan,
}

impl XSyncFinalizer {
    pub(super) const fn new(queue: QueueKernel, plan: XSyncFinalizationPlan) -> Self {
        Self { queue, plan }
    }

    async fn apply_inner(
        &self,
        transaction: &mut Transaction<'static, Postgres>,
    ) -> Result<(), XSyncFinalizeError> {
        if !lock_current_x_sync_connection(transaction, &self.plan.prepared).await? {
            // Disconnect, OAuth replacement, or account deletion won while provider I/O was in
            // flight. Completing the obsolete queue row without publishing its output is the
            // only safe result.
            return Ok(());
        }
        match &self.plan.mutation {
            XSyncMutation::ReauthRequired {
                reason,
                recorded_at,
            } => {
                mark_x_sync_reauth_required(transaction, &self.plan.prepared, reason, *recorded_at)
                    .await?;
            }
            XSyncMutation::Failed {
                credentials,
                usage,
                error,
            } => {
                if let Some(credentials) = credentials {
                    persist_credentials(transaction, &self.plan, credentials).await?;
                }
                persist_usage(transaction, &self.plan, usage).await?;
                mark_x_sync_failed(transaction, self.plan.prepared.connection_id, error).await?;
            }
            XSyncMutation::Complete {
                credentials,
                bookmarks,
                usage,
                completed_at,
            } => {
                persist_credentials(transaction, &self.plan, credentials).await?;
                persist_usage(transaction, &self.plan, usage).await?;
                let summary = self
                    .persist_bookmarks(transaction, bookmarks, *completed_at)
                    .await?;
                let checkpoint = bookmarks.newest_item_id.as_deref().or(self
                    .plan
                    .prepared
                    .last_synced_item_id
                    .as_deref());
                let channel_last_synced_at = if bookmarks.status == "skipped_recently" {
                    self.plan
                        .prepared
                        .bookmark_last_synced_at
                        .map(|value| value.to_rfc3339())
                } else {
                    Some(completed_at.to_rfc3339())
                };
                let sync_metadata = json!({
                    "bookmarks": {
                        "status": bookmarks.status,
                        "fetched": bookmarks.fetched,
                        "accepted": summary.created + summary.reused,
                        "filtered_out": 0,
                        "errored": 0,
                        "created": summary.created,
                        "reused": summary.reused,
                        "last_synced_item_id": checkpoint,
                        "last_synced_at": channel_last_synced_at,
                    }
                });
                complete_x_sync(
                    transaction,
                    self.plan.prepared.connection_id,
                    "success",
                    bookmarks.newest_item_id.as_deref(),
                    &sync_metadata,
                    *completed_at,
                )
                .await?;
            }
        }
        Ok(())
    }

    #[allow(clippy::too_many_lines)]
    async fn persist_bookmarks(
        &self,
        transaction: &mut Transaction<'static, Postgres>,
        bookmarks: &BookmarkFetchOutcome,
        seen_at: chrono::DateTime<chrono::Utc>,
    ) -> Result<PersistedBookmarkSummary, XSyncFinalizeError> {
        let mut summary = PersistedBookmarkSummary::default();
        let mut requests = Vec::new();
        let mut generated_image_ids = BTreeSet::new();
        for tweet in bookmarks.tweets.iter().rev() {
            let tweet_url = canonical_tweet_url(&tweet.id);
            let shell_content_id = if let Some(content_id) = find_reusable_x_bookmark_content(
                transaction,
                self.plan.prepared.connection_id,
                &tweet.id,
            )
            .await?
            {
                summary.reused += 1;
                content_id
            } else {
                let applied = apply_content_submission(
                    transaction,
                    &ContentSubmissionInput {
                        url: &tweet_url,
                        title: None,
                        platform: Some("twitter"),
                        instruction: None,
                        crawl_links: false,
                        subscribe_to_feed: false,
                        share_and_chat: false,
                        chat_initial_message: None,
                        save_to_knowledge_and_mark_read: false,
                        user_id: self.plan.prepared.user_id,
                        submitted_via: "x_bookmarks",
                    },
                )
                .await?;
                if applied.already_exists {
                    summary.reused += 1;
                } else {
                    summary.created += 1;
                }
                append_submission_handoffs(
                    transaction,
                    &self.queue,
                    &mut requests,
                    &mut generated_image_ids,
                    &applied,
                    self.plan.prepared.user_id,
                )
                .await?;
                applied.content_id
            };

            let destination_content_id =
                resolve_x_bookmark_destination(transaction, shell_content_id)
                    .await?
                    .ok_or(XSyncFinalizeError::MissingContent(shell_content_id))?;
            let snapshot = build_snapshot(tweet, &bookmarks.included_tweets)?;
            persist_x_bookmark_snapshot(transaction, shell_content_id, &snapshot).await?;
            upsert_x_bookmark_ledger(
                transaction,
                self.plan.prepared.connection_id,
                &tweet.id,
                destination_content_id,
                &tweet_url,
                seen_at,
            )
            .await?;
            let save_created = save_x_bookmark_destination(
                transaction,
                self.plan.prepared.user_id,
                destination_content_id,
            )
            .await?;
            let stale_save_removed = remove_stale_x_bookmark_save(
                transaction,
                self.plan.prepared.user_id,
                shell_content_id,
                destination_content_id,
            )
            .await?;
            if save_created {
                requests.push(
                    agent_data_sync_request(
                        transaction,
                        self.plan.prepared.user_id,
                        destination_content_id,
                    )
                    .await?,
                );
            }
            if stale_save_removed {
                requests.push(
                    agent_data_sync_request(
                        transaction,
                        self.plan.prepared.user_id,
                        shell_content_id,
                    )
                    .await?,
                );
            }
            if x_bookmark_destination_needs_image(transaction, destination_content_id).await?
                && generated_image_ids.insert(destination_content_id)
            {
                let mut request = EnqueueRequest::new(TaskType::GenerateImage);
                request.content_id = Some(destination_content_id);
                requests.push(request);
            }
        }
        if !requests.is_empty() {
            self.queue
                .enqueue_many_in_transaction(transaction, requests)
                .await?;
        }
        Ok(summary)
    }
}

impl TaskFinalizer for XSyncFinalizer {
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

async fn persist_credentials(
    transaction: &mut Transaction<'static, Postgres>,
    plan: &XSyncFinalizationPlan,
    credentials: &DurableXCredentials,
) -> Result<(), XSyncRepositoryError> {
    persist_x_sync_connection_update(
        transaction,
        &plan.prepared,
        &XSyncConnectionUpdate {
            provider_user_id: credentials.provider_user_id.as_deref(),
            provider_username: credentials.provider_username.as_deref(),
            access_token_encrypted: &credentials.access_token_encrypted,
            refresh_token_encrypted: credentials.refresh_token_encrypted.as_deref(),
            token_expires_at: credentials.token_expires_at,
            scopes: &credentials.scopes,
        },
    )
    .await
}

async fn persist_usage(
    transaction: &mut Transaction<'static, Postgres>,
    plan: &XSyncFinalizationPlan,
    usage: &[XRequestUsage],
) -> Result<(), XSyncRepositoryError> {
    for entry in usage {
        let resource_count = i32::try_from(entry.resource_ids.len()).unwrap_or(i32::MAX);
        let _ = record_x_sync_usage(
            transaction,
            &NewXSyncUsage {
                model: entry.model,
                feature: entry.feature,
                operation: entry.operation,
                request_id: &entry.request_id,
                task_id: plan.task_id,
                user_id: plan.prepared.user_id,
                request_count: entry.request_count,
                resource_count,
                resource_ids: &entry.resource_ids,
                unit_cost_usd: entry.unit_cost_usd,
                channel: entry.channel,
            },
        )
        .await?;
    }
    Ok(())
}

async fn append_submission_handoffs(
    transaction: &mut Transaction<'static, Postgres>,
    queue: &QueueKernel,
    requests: &mut Vec<EnqueueRequest>,
    generated_image_ids: &mut BTreeSet<i64>,
    applied: &AppliedContentSubmission,
    user_id: i64,
) -> Result<(), XSyncFinalizeError> {
    match applied.task_resolution {
        SubmissionTaskResolution::None => {}
        SubmissionTaskResolution::Reuse(task_id) => {
            queue
                .grant_access_in_transaction(transaction, task_id, user_id)
                .await?;
        }
        SubmissionTaskResolution::EnqueueAnalyze => {
            let mut request = EnqueueRequest::new(TaskType::AnalyzeUrl);
            request.content_id = Some(applied.content_id);
            request.payload = Some(Map::from_iter([(
                "content_id".to_owned(),
                Value::from(applied.content_id),
            )]));
            request.dedupe = Some(true);
            request.access_user_id = Some(user_id);
            requests.push(request);
        }
    }
    if applied.enqueue_generated_image && generated_image_ids.insert(applied.content_id) {
        let mut request = EnqueueRequest::new(TaskType::GenerateImage);
        request.content_id = Some(applied.content_id);
        requests.push(request);
    }
    Ok(())
}

async fn agent_data_sync_request(
    transaction: &mut Transaction<'static, Postgres>,
    user_id: i64,
    content_id: i64,
) -> Result<EnqueueRequest, ContentSubmissionRepositoryError> {
    let payload_value = json!({
        "user_id": user_id,
        "content_ids": [content_id],
        "news_item_ids": [],
        "chat_session_ids": [],
        "briefing_dates": [],
    });
    let serialized = serde_json::to_string(&payload_value).expect("agent sync payload serializes");
    let digest = Sha256::digest(serialized.as_bytes());
    let base_key = format!(
        "agent-sync|user:{user_id}|payload:{}",
        &hex_encode(&digest)[..24]
    );
    let dedupe_key = prepare_agent_data_sync_dedupe_key(transaction, user_id, &base_key).await?;
    let mut request = EnqueueRequest::new(TaskType::SyncAgentData);
    request.payload = payload_value.as_object().cloned();
    request.owner_user_id = Some(user_id);
    request.dedupe = Some(true);
    request.dedupe_key = Some(dedupe_key);
    Ok(request)
}

fn build_snapshot(
    tweet: &XTweet,
    included: &BTreeMap<String, XTweet>,
) -> Result<Value, serde_json::Error> {
    let linked = tweet.linked_tweet_ids.iter().collect::<BTreeSet<_>>();
    let included = included
        .iter()
        .filter(|(tweet_id, _)| linked.contains(tweet_id))
        .map(|(tweet_id, tweet)| Ok((tweet_id.clone(), serde_json::to_value(tweet)?)))
        .collect::<Result<BTreeMap<_, _>, serde_json::Error>>()?;
    let mut snapshot = Map::from_iter([
        ("tweet_snapshot".to_owned(), serde_json::to_value(tweet)?),
        (
            "tweet_snapshot_source".to_owned(),
            Value::from("x_bookmarks_sync"),
        ),
    ]);
    if !included.is_empty() {
        snapshot.insert(
            "tweet_snapshot_included".to_owned(),
            serde_json::to_value(included)?,
        );
    }
    Ok(Value::Object(snapshot))
}

fn canonical_tweet_url(tweet_id: &str) -> String {
    format!("https://x.com/i/status/{tweet_id}")
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

#[derive(Debug, Default)]
struct PersistedBookmarkSummary {
    created: usize,
    reused: usize,
}

#[derive(Debug, Error)]
enum XSyncFinalizeError {
    #[error(transparent)]
    Repository(#[from] XSyncRepositoryError),
    #[error(transparent)]
    Submission(#[from] ContentSubmissionRepositoryError),
    #[error(transparent)]
    Queue(#[from] QueueError),
    #[error("X bookmark snapshot serialization failed")]
    Snapshot(#[from] serde_json::Error),
    #[error("X bookmark content {0} disappeared during fenced finalization")]
    MissingContent(i64),
}
