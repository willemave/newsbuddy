use std::error::Error;

use newsly_db::{
    CreateLearningDeckOutcome, LearningDeckSourceProjection, ShareActionAgentSnapshot,
    ShareActionFinalizationTask, find_prepared_share_content, finish_share_action_task,
    get_or_create_share_chat_session, load_submitted_content_learning_deck_source,
    lock_share_action_for_finalization, mark_share_action_applied, mark_share_action_applying,
    mark_share_action_failed, persist_share_action_agent_output, request_share_action,
};
use newsly_queue::{EnqueueRequest, QueueKernel, TaskResult, TaskType};
use reqwest::Url;
use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};
use sqlx::{Postgres, Transaction};
use thiserror::Error;

use crate::{HandlerFinalizerFuture, TaskFinalizer, TaskFinalizerResult};

use super::agent::ShareActionAgentRunResult;
use super::submission::{
    ShareSubmissionPolicy, apply_validated_feed_action, enqueue_chat_session_sync,
    submit_content_action,
};
use super::workflows::{
    BriefingActionInput, ContentActionInput, LearningDeckActionInput, PreparedHostAction,
    ShareActionHostInput,
};

#[derive(Debug)]
pub struct ShareActionSuccessFinalizer {
    queue: QueueKernel,
    sandbox_root: String,
    snapshot: ShareActionAgentSnapshot,
    agent: Option<ShareActionAgentRunResult>,
    host_action: Option<PreparedHostAction>,
}

impl ShareActionSuccessFinalizer {
    pub fn agent(
        queue: QueueKernel,
        sandbox_root: String,
        snapshot: ShareActionAgentSnapshot,
        agent: ShareActionAgentRunResult,
        host_action: Option<PreparedHostAction>,
    ) -> Self {
        Self {
            queue,
            sandbox_root,
            snapshot,
            agent: Some(agent),
            host_action,
        }
    }

    pub fn deterministic_chat(
        queue: QueueKernel,
        sandbox_root: String,
        snapshot: ShareActionAgentSnapshot,
        host_action: PreparedHostAction,
    ) -> Self {
        Self {
            queue,
            sandbox_root,
            snapshot,
            agent: None,
            host_action: Some(host_action),
        }
    }

    async fn apply_inner(
        &self,
        transaction: &mut Transaction<'_, Postgres>,
    ) -> Result<TaskFinalizerResult, ShareActionFinalizationError> {
        let task = lock_share_action_for_finalization(
            transaction,
            self.snapshot.id,
            self.snapshot.user_id,
        )
        .await?;
        if matches!(task.status.as_str(), "completed" | "failed" | "cancelled") {
            return Ok(TaskFinalizerResult::Keep);
        }

        if let Some(agent) = &self.agent {
            let output = serde_json::to_value(&agent.result)?;
            let usage = json!({
                "provider_usage": agent.outcome.usage,
                "request_count": agent.outcome.request_count,
                "tool_call_count": agent.outcome.tool_call_count,
                "provider_response_id": agent.outcome.provider_response_id,
                "events": agent.events,
            });
            persist_share_action_agent_output(
                transaction,
                task.id,
                &output,
                &usage,
                &agent.model_provider,
                &agent.outcome.model_name,
                &agent.sandbox_provider,
                Some(&agent.sandbox_id),
            )
            .await?;
        }

        let Some(action) = &self.host_action else {
            finish_share_action_task(transaction, task.id).await?;
            return Ok(TaskFinalizerResult::Keep);
        };
        let requested = request_share_action(
            transaction,
            &task,
            &action.action_name,
            &action.action_input,
            action.rationale.as_deref(),
            &action.idempotency_key,
        )
        .await?;
        if requested.status != newsly_db::RequestedActionStatus::Approved {
            finish_share_action_task(transaction, task.id).await?;
            return Ok(TaskFinalizerResult::Keep);
        }

        mark_share_action_applying(transaction, requested.id).await?;
        sqlx::query("SAVEPOINT share_action_application")
            .execute(&mut **transaction)
            .await?;
        let result = apply_share_action_host_action(
            transaction,
            &self.queue,
            &self.sandbox_root,
            &task,
            &action.action_name,
            &action.typed_input,
        )
        .await;
        match result {
            Ok(result) if result.get("outcome").and_then(Value::as_str) != Some("failed") => {
                sqlx::query("RELEASE SAVEPOINT share_action_application")
                    .execute(&mut **transaction)
                    .await?;
                mark_share_action_applied(transaction, requested.id, &result).await?;
                finish_share_action_task(transaction, task.id).await?;
                Ok(TaskFinalizerResult::Keep)
            }
            Ok(result) => {
                let message = result
                    .get("error")
                    .and_then(Value::as_str)
                    .unwrap_or("Share Action applied no items")
                    .to_owned();
                sqlx::query("RELEASE SAVEPOINT share_action_application")
                    .execute(&mut **transaction)
                    .await?;
                mark_share_action_failed(transaction, requested.id, Some(&result), &message)
                    .await?;
                newsly_db::fail_share_action_task(
                    transaction,
                    task.id,
                    task.user_id,
                    "ShareActionApplicationError",
                    &message,
                    None,
                    None,
                )
                .await?;
                Ok(TaskFinalizerResult::Override(TaskResult::fail(
                    Some(message),
                    false,
                )))
            }
            Err(error) => {
                sqlx::query("ROLLBACK TO SAVEPOINT share_action_application")
                    .execute(&mut **transaction)
                    .await?;
                sqlx::query("RELEASE SAVEPOINT share_action_application")
                    .execute(&mut **transaction)
                    .await?;
                let message = error.to_string();
                mark_share_action_failed(transaction, requested.id, None, &message).await?;
                newsly_db::fail_share_action_task(
                    transaction,
                    task.id,
                    task.user_id,
                    "ShareActionApplicationError",
                    &message,
                    None,
                    None,
                )
                .await?;
                Ok(TaskFinalizerResult::Override(TaskResult::fail(
                    Some(message),
                    false,
                )))
            }
        }
    }
}

#[derive(Debug)]
struct ShareActionApplicator<'a> {
    queue: &'a QueueKernel,
    sandbox_root: &'a str,
}

impl ShareActionApplicator<'_> {
    async fn apply_host_action(
        &self,
        transaction: &mut Transaction<'_, Postgres>,
        task: &ShareActionFinalizationTask,
        action_name: &str,
        input: &ShareActionHostInput,
    ) -> Result<Map<String, Value>, ShareActionFinalizationError> {
        match (action_name, input) {
            ("add_content" | "save_to_knowledge", ShareActionHostInput::Content(input)) => {
                if let Some(content) = prepared_for_input(transaction, task, &input.url).await? {
                    newsly_db::enrich_prepared_share_content(
                        transaction,
                        &content,
                        input.title.as_deref(),
                        input.platform.as_deref(),
                    )
                    .await?;
                    return Ok(content_result(task, content.id));
                }
                let submitted = submit_content_action(
                    transaction,
                    self.queue,
                    task.user_id,
                    input,
                    ShareSubmissionPolicy::content_saved(),
                )
                .await?;
                Ok(Map::from_iter([
                    ("content_id".to_owned(), Value::from(submitted.content_id)),
                    ("task_id".to_owned(), option_i64(submitted.task_id)),
                ]))
            }
            ("subscribe_to_feed", ShareActionHostInput::Feed(input)) => {
                let subscription =
                    apply_validated_feed_action(transaction, self.queue, task.user_id, input)
                        .await?;
                Ok(Map::from_iter([
                    ("outcome".to_owned(), Value::from("completed")),
                    ("subscribed".to_owned(), Value::Bool(true)),
                    (
                        "subscription_outcome".to_owned(),
                        Value::from(subscription.outcome),
                    ),
                    ("config_id".to_owned(), Value::from(subscription.config_id)),
                    ("feed_url".to_owned(), Value::from(subscription.feed_url)),
                    ("feed_type".to_owned(), Value::from(subscription.feed_type)),
                    (
                        "feed_format".to_owned(),
                        Value::from(subscription.feed_format),
                    ),
                    (
                        "backfill_task_id".to_owned(),
                        option_i64(subscription.backfill_task_id),
                    ),
                ]))
            }
            ("add_to_briefing", ShareActionHostInput::Briefing(target)) => match target {
                BriefingActionInput::Feed(input) => {
                    let subscription =
                        apply_validated_feed_action(transaction, self.queue, task.user_id, input)
                            .await?;
                    Ok(Map::from_iter([
                        ("resolved_kind".to_owned(), Value::from("feed")),
                        (
                            "resolved_url".to_owned(),
                            Value::from(subscription.feed_url.clone()),
                        ),
                        ("subscribed".to_owned(), Value::Bool(true)),
                        (
                            "subscription_outcome".to_owned(),
                            Value::from(subscription.outcome),
                        ),
                        ("config_id".to_owned(), Value::from(subscription.config_id)),
                        ("feed_url".to_owned(), Value::from(subscription.feed_url)),
                        ("feed_type".to_owned(), Value::from(subscription.feed_type)),
                        (
                            "feed_format".to_owned(),
                            Value::from(subscription.feed_format),
                        ),
                        (
                            "backfill_task_id".to_owned(),
                            option_i64(subscription.backfill_task_id),
                        ),
                    ]))
                }
                BriefingActionInput::Content(input) => {
                    let submitted = submit_content_action(
                        transaction,
                        self.queue,
                        task.user_id,
                        input,
                        ShareSubmissionPolicy::content_inbox(),
                    )
                    .await?;
                    Ok(Map::from_iter([
                        ("resolved_kind".to_owned(), Value::from("content")),
                        ("resolved_url".to_owned(), Value::from(input.url.clone())),
                        ("content_id".to_owned(), Value::from(submitted.content_id)),
                        ("task_id".to_owned(), option_i64(submitted.task_id)),
                        (
                            "already_exists".to_owned(),
                            Value::Bool(submitted.already_exists),
                        ),
                    ]))
                }
            },
            ("enqueue_chat", ShareActionHostInput::Content(input)) => {
                let (content_id, content_task_id) = if let Some(content) =
                    prepared_for_input(transaction, task, &input.url).await?
                {
                    newsly_db::enrich_prepared_share_content(
                        transaction,
                        &content,
                        input.title.as_deref(),
                        input.platform.as_deref(),
                    )
                    .await?;
                    (content.id, task_input_id(task, "knowledge_task_id"))
                } else {
                    let submitted = submit_content_action(
                        transaction,
                        self.queue,
                        task.user_id,
                        input,
                        ShareSubmissionPolicy::chat(),
                    )
                    .await?;
                    (submitted.content_id, submitted.task_id)
                };
                let (session_id, created) =
                    get_or_create_share_chat_session(transaction, task.user_id, content_id).await?;
                if created {
                    enqueue_chat_session_sync(transaction, self.queue, task.user_id, session_id)
                        .await?;
                }
                Ok(Map::from_iter([
                    ("content_id".to_owned(), Value::from(content_id)),
                    ("task_id".to_owned(), option_i64(content_task_id)),
                    ("chat_session_id".to_owned(), Value::from(session_id)),
                ]))
            }
            ("add_links", ShareActionHostInput::AddLinks { candidates, .. }) => {
                self.apply_links(transaction, task, candidates).await
            }
            ("create_learning_deck", ShareActionHostInput::LearningDeck(input)) => {
                self.apply_learning_deck(transaction, task, input).await
            }
            (name, _) => Err(ShareActionFinalizationError::WrongInput(name.to_owned())),
        }
    }

    async fn apply_links(
        &self,
        transaction: &mut Transaction<'_, Postgres>,
        task: &ShareActionFinalizationTask,
        candidates: &[ContentActionInput],
    ) -> Result<Map<String, Value>, ShareActionFinalizationError> {
        let mut items = Vec::new();
        for candidate in candidates.iter().take(20) {
            sqlx::query("SAVEPOINT share_action_link")
                .execute(&mut **transaction)
                .await?;
            let applied = submit_content_action(
                transaction,
                self.queue,
                task.user_id,
                candidate,
                ShareSubmissionPolicy::content_saved(),
            )
            .await;
            match applied {
                Ok(submitted) => {
                    sqlx::query("RELEASE SAVEPOINT share_action_link")
                        .execute(&mut **transaction)
                        .await?;
                    items.push(json!({
                        "url": candidate.url,
                        "outcome": "submitted",
                        "content_id": submitted.content_id,
                        "task_id": submitted.task_id,
                    }));
                }
                Err(error) => {
                    sqlx::query("ROLLBACK TO SAVEPOINT share_action_link")
                        .execute(&mut **transaction)
                        .await?;
                    sqlx::query("RELEASE SAVEPOINT share_action_link")
                        .execute(&mut **transaction)
                        .await?;
                    items.push(json!({
                        "url": candidate.url,
                        "outcome": "failed",
                        "error": error.to_string(),
                    }));
                }
            }
        }
        let succeeded = items
            .iter()
            .filter(|item| item.get("outcome").and_then(Value::as_str) == Some("submitted"))
            .count();
        let failed = items.len().saturating_sub(succeeded);
        let outcome = if failed == 0 {
            "completed"
        } else if succeeded > 0 {
            "partial"
        } else {
            "failed"
        };
        let mut result = Map::from_iter([
            ("outcome".to_owned(), Value::from(outcome)),
            ("attempted_count".to_owned(), Value::from(items.len())),
            ("succeeded_count".to_owned(), Value::from(succeeded)),
            ("failed_count".to_owned(), Value::from(failed)),
            ("items".to_owned(), Value::Array(items)),
        ]);
        if outcome == "failed" {
            result.insert(
                "error".to_owned(),
                Value::from("All discovered links failed to submit"),
            );
        }
        Ok(result)
    }

    async fn apply_learning_deck(
        &self,
        transaction: &mut Transaction<'_, Postgres>,
        task: &ShareActionFinalizationTask,
        input: &LearningDeckActionInput,
    ) -> Result<Map<String, Value>, ShareActionFinalizationError> {
        let prepared = prepared_for_input(transaction, task, &input.source_url).await?;
        let mut content_id = task_input_id(task, "knowledge_content_id");
        let mut source = if let Some(github) = github_source(&input.source_url)? {
            github
        } else if let Some(content) = prepared {
            content_id = Some(content.id);
            load_submitted_content_learning_deck_source(transaction, content.id).await?
        } else {
            let submitted = submit_content_action(
                transaction,
                self.queue,
                task.user_id,
                &ContentActionInput {
                    url: input.source_url.clone(),
                    title: input.title.clone(),
                    platform: None,
                    content_type: None,
                    instruction: None,
                    chat_initial_message: None,
                },
                ShareSubmissionPolicy::content_saved(),
            )
            .await?;
            content_id = Some(submitted.content_id);
            load_submitted_content_learning_deck_source(transaction, submitted.content_id).await?
        };
        source.source_metadata.insert(
            "submission".to_owned(),
            json!({
                "submitted_via": "share_action",
                "share_action_task_id": task.id,
            }),
        );
        let outcome = newsly_db::create_or_rerun_learning_deck(
            transaction,
            task.user_id,
            &source,
            input.interests_prompt.as_deref(),
            self.sandbox_root,
        )
        .await?;
        let deck_id = match outcome {
            CreateLearningDeckOutcome::AttemptCreated { deck_id, task_id } => {
                let mut request = EnqueueRequest::new(TaskType::RunLlmTask);
                request.payload = json!({"llm_task_id": task_id, "user_id": task.user_id})
                    .as_object()
                    .cloned();
                request.owner_user_id = Some(task.user_id);
                self.queue
                    .enqueue_many_in_transaction(transaction, vec![request])
                    .await?;
                deck_id
            }
            CreateLearningDeckOutcome::ExistingActiveAttempt { deck_id } => deck_id,
            CreateLearningDeckOutcome::AnotherDeckActive => {
                return Err(ShareActionFinalizationError::AnotherDeckActive);
            }
        };
        Ok(Map::from_iter([
            ("learning_deck_id".to_owned(), Value::from(deck_id)),
            (
                "source_url".to_owned(),
                Value::from(input.source_url.clone()),
            ),
            ("content_id".to_owned(), option_i64(content_id)),
        ]))
    }
}

/// Applies one already-validated Share Action host input using only bounded SQL and atomic queue
/// producer writes. This is shared by worker auto-approval and the synchronous HTTP approval
/// callback; it never performs provider or sandbox I/O.
pub async fn apply_share_action_host_action(
    transaction: &mut Transaction<'_, Postgres>,
    queue: &QueueKernel,
    sandbox_root: &str,
    task: &ShareActionFinalizationTask,
    action_name: &str,
    input: &ShareActionHostInput,
) -> Result<Map<String, Value>, ShareActionFinalizationError> {
    ShareActionApplicator {
        queue,
        sandbox_root,
    }
    .apply_host_action(transaction, task, action_name, input)
    .await
}

impl TaskFinalizer for ShareActionSuccessFinalizer {
    fn apply<'a>(
        &'a self,
        transaction: &'a mut Transaction<'static, Postgres>,
    ) -> HandlerFinalizerFuture<'a> {
        Box::pin(async move {
            self.apply_inner(transaction)
                .await
                .map_err(|error| Box::new(error) as Box<dyn Error + Send + Sync>)
        })
    }
}

#[derive(Debug)]
pub struct ShareActionFailureFinalizer {
    pub task_id: i64,
    pub user_id: i64,
    pub error_type: String,
    pub message: String,
    pub sandbox_provider: Option<String>,
    pub sandbox_id: Option<String>,
}

impl TaskFinalizer for ShareActionFailureFinalizer {
    fn apply<'a>(
        &'a self,
        transaction: &'a mut Transaction<'static, Postgres>,
    ) -> HandlerFinalizerFuture<'a> {
        Box::pin(async move {
            newsly_db::fail_share_action_task(
                transaction,
                self.task_id,
                self.user_id,
                &self.error_type,
                &self.message,
                self.sandbox_provider.as_deref(),
                self.sandbox_id.as_deref(),
            )
            .await
            .map(|()| TaskFinalizerResult::Keep)
            .map_err(|error| Box::new(error) as Box<dyn Error + Send + Sync>)
        })
    }
}

async fn prepared_for_input(
    transaction: &mut Transaction<'_, Postgres>,
    task: &ShareActionFinalizationTask,
    action_url: &str,
) -> Result<Option<newsly_db::PreparedContentProjection>, ShareActionFinalizationError> {
    let Some(content) = find_prepared_share_content(transaction, task).await? else {
        return Ok(None);
    };
    let source_url = task.input.get("url").and_then(Value::as_str);
    if [
        source_url,
        Some(content.url.as_str()),
        content.source_url.as_deref(),
    ]
    .into_iter()
    .flatten()
    .any(|candidate| same_source(candidate, action_url))
    {
        Ok(Some(content))
    } else {
        Ok(None)
    }
}

fn same_source(first: &str, second: &str) -> bool {
    let first_tweet = tweet_id(first);
    let second_tweet = tweet_id(second);
    if first_tweet.is_some() || second_tweet.is_some() {
        return first_tweet.is_some() && first_tweet == second_tweet;
    }
    match (Url::parse(first), Url::parse(second)) {
        (Ok(mut first), Ok(mut second)) => {
            first.set_fragment(None);
            second.set_fragment(None);
            first.as_str().trim_end_matches('/') == second.as_str().trim_end_matches('/')
        }
        _ => false,
    }
}

fn tweet_id(value: &str) -> Option<String> {
    let url = Url::parse(value).ok()?;
    let host = url.host_str()?.trim_start_matches("www.");
    if !matches!(host, "x.com" | "twitter.com" | "mobile.twitter.com") {
        return None;
    }
    let parts = url.path_segments()?.collect::<Vec<_>>();
    let status = parts.iter().position(|part| *part == "status")?;
    parts
        .get(status + 1)
        .copied()
        .filter(|value| !value.is_empty() && value.bytes().all(|byte| byte.is_ascii_digit()))
        .map(str::to_owned)
}

fn content_result(task: &ShareActionFinalizationTask, content_id: i64) -> Map<String, Value> {
    Map::from_iter([
        ("content_id".to_owned(), Value::from(content_id)),
        (
            "task_id".to_owned(),
            option_i64(task_input_id(task, "knowledge_task_id")),
        ),
    ])
}

fn task_input_id(task: &ShareActionFinalizationTask, key: &str) -> Option<i64> {
    task.input
        .get(key)
        .and_then(Value::as_i64)
        .filter(|value| *value > 0)
}

fn option_i64(value: Option<i64>) -> Value {
    value.map_or(Value::Null, Value::from)
}

fn github_source(
    raw: &str,
) -> Result<Option<LearningDeckSourceProjection>, ShareActionFinalizationError> {
    let Ok(url) = Url::parse(raw) else {
        return Ok(None);
    };
    let host = url.host_str().unwrap_or_default().to_ascii_lowercase();
    let parts = url
        .path_segments()
        .into_iter()
        .flatten()
        .filter(|part| !part.is_empty())
        .map(str::to_owned)
        .collect::<Vec<_>>();
    let file = if matches!(host.as_str(), "github.com" | "www.github.com")
        && parts.len() >= 5
        && matches!(parts[2].as_str(), "blob" | "raw")
    {
        Some((
            parts[0].clone(),
            parts[1].trim_end_matches(".git").to_owned(),
            parts[3].clone(),
            parts[4..].join("/"),
        ))
    } else if host == "raw.githubusercontent.com" && parts.len() >= 4 {
        Some((
            parts[0].clone(),
            parts[1].trim_end_matches(".git").to_owned(),
            parts[2].clone(),
            parts[3..].join("/"),
        ))
    } else {
        None
    };
    if let Some((owner, repo, reference, path)) = file {
        let blob = github_url(
            "https://github.com",
            &owner,
            &repo,
            Some("blob"),
            &reference,
            &path,
        )?;
        let raw_url = github_url(
            "https://raw.githubusercontent.com",
            &owner,
            &repo,
            None,
            &reference,
            &path,
        )?;
        let filename = path.rsplit('/').next().unwrap_or(&path);
        let identity = bounded_github_identity(
            format!(
                "github:{}/{}:file:{reference}/{path}",
                owner.to_ascii_lowercase(),
                repo.to_ascii_lowercase()
            ),
            &owner,
            &repo,
        );
        let source_title = format!("{owner}/{repo}: {filename}");
        return Ok(Some(LearningDeckSourceProjection {
            source_kind: "github_repo".to_owned(),
            source_identity: identity,
            source_url: Some(blob.clone()),
            source_content_id: None,
            source_title: source_title.clone(),
            source_metadata: Map::from_iter([
                ("owner".to_owned(), Value::from(owner.clone())),
                ("repo".to_owned(), Value::from(repo.clone())),
                (
                    "repo_url".to_owned(),
                    Value::from(format!("https://github.com/{owner}/{repo}")),
                ),
                ("title".to_owned(), Value::from(source_title)),
                (
                    "linked_artifact".to_owned(),
                    json!({
                        "url": blob,
                        "raw_url": raw_url,
                        "path": path,
                        "filename": filename,
                        "ref": reference,
                        "content_type": filename.to_ascii_lowercase().ends_with(".pdf").then_some("pdf"),
                    }),
                ),
            ]),
        }));
    }
    if !matches!(host.as_str(), "github.com" | "www.github.com") {
        return Ok(None);
    }
    if parts.len() < 2 {
        return Err(ShareActionFinalizationError::InvalidGithubUrl);
    }
    let owner = parts[0].clone();
    let repo = parts[1].trim_end_matches(".git").to_owned();
    if owner.is_empty() || repo.is_empty() {
        return Err(ShareActionFinalizationError::InvalidGithubUrl);
    }
    Ok(Some(LearningDeckSourceProjection {
        source_kind: "github_repo".to_owned(),
        source_identity: format!(
            "github:{}/{}",
            owner.to_ascii_lowercase(),
            repo.to_ascii_lowercase()
        ),
        source_url: Some(format!("https://github.com/{owner}/{repo}")),
        source_content_id: None,
        source_title: format!("{owner}/{repo}"),
        source_metadata: Map::from_iter([
            ("owner".to_owned(), Value::from(owner)),
            ("repo".to_owned(), Value::from(repo)),
        ]),
    }))
}

fn github_url(
    origin: &str,
    owner: &str,
    repo: &str,
    marker: Option<&str>,
    reference: &str,
    path: &str,
) -> Result<String, ShareActionFinalizationError> {
    let mut url = Url::parse(origin).map_err(|_| ShareActionFinalizationError::InvalidGithubUrl)?;
    let mut segments = url
        .path_segments_mut()
        .map_err(|()| ShareActionFinalizationError::InvalidGithubUrl)?;
    segments.push(owner).push(repo);
    if let Some(marker) = marker {
        segments.push(marker);
    }
    segments.push(reference);
    for part in path.split('/') {
        segments.push(part);
    }
    drop(segments);
    Ok(url.to_string().trim_end_matches('/').to_owned())
}

fn bounded_github_identity(identity: String, owner: &str, repo: &str) -> String {
    if identity.len() <= 512 {
        return identity;
    }
    format!(
        "github:{}/{}:file:{}",
        owner.to_ascii_lowercase(),
        repo.to_ascii_lowercase(),
        hex_encode(&Sha256::digest(identity.as_bytes()))
    )
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
pub enum ShareActionFinalizationError {
    #[error(transparent)]
    Repository(#[from] newsly_db::ShareActionRepositoryError),
    #[error(transparent)]
    Submission(#[from] super::submission::ShareSubmissionError),
    #[error(transparent)]
    LearningDeck(#[from] newsly_db::LearningDeckRepositoryError),
    #[error(transparent)]
    Queue(#[from] newsly_queue::QueueError),
    #[error(transparent)]
    Sqlx(#[from] sqlx::Error),
    #[error(transparent)]
    Json(#[from] serde_json::Error),
    #[error("Share Action {0} has the wrong typed input")]
    WrongInput(String),
    #[error("another Learning Deck generation is active")]
    AnotherDeckActive,
    #[error("GitHub URL must include an owner and repository")]
    InvalidGithubUrl,
}
