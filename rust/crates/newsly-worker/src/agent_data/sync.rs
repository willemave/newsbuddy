use std::collections::{BTreeMap, BTreeSet};
use std::error::Error;
use std::sync::Arc;

use chrono::{DateTime, Duration, Utc};
use newsly_queue::{EnqueueRequest, OwnedWorkPlan, QueueKernel, TaskResult, TaskType};
use serde_json::{Map, Value, json};
use sqlx::{FromRow, PgPool, Postgres, Transaction};

use crate::{
    HandlerAfterCommitFuture, HandlerExecution, HandlerFinalizerFuture, HandlerFuture, LeaseHealth,
    TaskFinalizer, TaskFinalizerResult, TaskHandler,
};

use super::documents::{
    AgentDataDocumentSnapshot, AgentDataSelection, collect_agent_data_documents,
};
use super::storage::{AgentDataMirrorStore, StagedAgentDataDocument, StagedDocumentPublication};

#[derive(Debug, Clone)]
pub struct AgentDataSyncServices {
    pool: PgPool,
    queue: QueueKernel,
    store: AgentDataMirrorStore,
    max_document_bytes: usize,
    index_debounce_seconds: i64,
}

impl AgentDataSyncServices {
    pub const fn new(
        pool: PgPool,
        queue: QueueKernel,
        store: AgentDataMirrorStore,
        max_document_bytes: usize,
        index_debounce_seconds: i64,
    ) -> Self {
        Self {
            pool,
            queue,
            store,
            max_document_bytes,
            index_debounce_seconds,
        }
    }

    pub(super) const fn pool(&self) -> &PgPool {
        &self.pool
    }

    pub(super) const fn queue(&self) -> &QueueKernel {
        &self.queue
    }
}

#[derive(Debug, Clone)]
pub struct SyncAgentDataHandler {
    services: Arc<AgentDataSyncServices>,
}

impl SyncAgentDataHandler {
    pub fn new(services: Arc<AgentDataSyncServices>) -> Self {
        Self { services }
    }
}

impl TaskHandler for SyncAgentDataHandler {
    fn task_type(&self) -> TaskType {
        TaskType::SyncAgentData
    }

    fn execute(&self, plan: Arc<OwnedWorkPlan>, _lease: LeaseHealth) -> HandlerFuture<'_> {
        let services = Arc::clone(&self.services);
        Box::pin(async move { execute_sync(&services, &plan).await })
    }
}

pub(super) async fn execute_sync(
    services: &AgentDataSyncServices,
    plan: &OwnedWorkPlan,
) -> HandlerExecution {
    let (user_id, selection) = match selection_from_plan(plan) {
        Ok(value) => value,
        Err(message) => {
            return HandlerExecution::from_result(TaskResult::fail(Some(message), false));
        }
    };
    prepare_sync_execution(
        services,
        plan,
        user_id,
        selection,
        AgentDataSyncContinuation::Incremental,
    )
    .await
}

pub(super) async fn prepare_sync_execution(
    services: &AgentDataSyncServices,
    plan: &OwnedWorkPlan,
    user_id: i64,
    selection: AgentDataSelection,
    continuation: AgentDataSyncContinuation,
) -> HandlerExecution {
    let snapshot = match collect_agent_data_documents(
        &services.pool,
        &services.store,
        user_id,
        &selection,
        services.max_document_bytes,
    )
    .await
    {
        Ok(Some(snapshot)) => snapshot,
        Ok(None) => {
            return HandlerExecution::from_result(TaskResult::fail(
                Some(format!("active user {user_id} does not exist")),
                false,
            ));
        }
        Err(error) => {
            return HandlerExecution::from_result(TaskResult::fail(Some(error.to_string()), true));
        }
    };
    let staged = match services
        .store
        .stage_documents(user_id, plan.task_id, plan.retry_count, &snapshot.documents)
        .await
    {
        Ok(staged) => staged,
        Err(error) => {
            return HandlerExecution::from_result(TaskResult::fail(Some(error.to_string()), true));
        }
    };
    let manifest_state = services.store.manifest_state(user_id).await;
    HandlerExecution::with_finalizer(
        TaskResult::ok(),
        AgentDataSyncFinalizer {
            queue: services.queue.clone(),
            store: services.store.clone(),
            snapshot,
            staged,
            manifest_state,
            index_debounce_seconds: services.index_debounce_seconds,
            task_id: plan.task_id,
            continuation,
        },
    )
}

fn selection_from_plan(plan: &OwnedWorkPlan) -> Result<(i64, AgentDataSelection), String> {
    let payload_user_id = plan.payload.get("user_id").and_then(Value::as_i64);
    let user_id = plan
        .owner_user_id
        .or(payload_user_id)
        .filter(|value| *value > 0)
        .ok_or_else(|| "sync_agent_data requires a positive user_id".to_owned())?;
    if payload_user_id != Some(user_id) || plan.owner_user_id != Some(user_id) {
        return Err("sync_agent_data owner and payload user_id must match".to_owned());
    }
    Ok((
        user_id,
        AgentDataSelection {
            content_ids: positive_ids(&plan.payload, "content_ids")?,
            news_item_ids: positive_ids(&plan.payload, "news_item_ids")?,
            chat_session_ids: positive_ids(&plan.payload, "chat_session_ids")?,
            briefing_dates: strings(&plan.payload, "briefing_dates")?,
        },
    ))
}

fn positive_ids(payload: &Map<String, Value>, key: &str) -> Result<BTreeSet<i64>, String> {
    payload
        .get(key)
        .and_then(Value::as_array)
        .ok_or_else(|| format!("sync_agent_data {key} must be an array"))?
        .iter()
        .map(|value| {
            value
                .as_i64()
                .filter(|value| *value > 0)
                .ok_or_else(|| format!("sync_agent_data {key} contains an invalid id"))
        })
        .collect()
}

fn strings(payload: &Map<String, Value>, key: &str) -> Result<BTreeSet<String>, String> {
    payload
        .get(key)
        .and_then(Value::as_array)
        .ok_or_else(|| format!("sync_agent_data {key} must be an array"))?
        .iter()
        .map(|value| {
            value
                .as_str()
                .filter(|value| !value.is_empty())
                .map(ToOwned::to_owned)
                .ok_or_else(|| format!("sync_agent_data {key} contains an invalid value"))
        })
        .collect()
}

#[derive(Debug, FromRow)]
struct AgentDataLedgerRow {
    id: i64,
    document_kind: String,
    document_key: String,
    path: String,
    stale_paths: Value,
    checksum_sha256: String,
    revision: i64,
    deleted_at: Option<DateTime<Utc>>,
}

#[derive(Debug)]
struct AgentDataSyncFinalizer {
    queue: QueueKernel,
    store: AgentDataMirrorStore,
    snapshot: AgentDataDocumentSnapshot,
    staged: StagedDocumentPublication,
    manifest_state: Option<(i64, bool)>,
    index_debounce_seconds: i64,
    task_id: i64,
    continuation: AgentDataSyncContinuation,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) enum AgentDataSyncContinuation {
    Incremental,
    BackfillPage { stage: String, before_id: i64 },
    ReconcilePage { before_id: i64 },
}

impl AgentDataSyncFinalizer {
    async fn apply_inner(
        &self,
        transaction: &mut Transaction<'static, Postgres>,
    ) -> Result<TaskFinalizerResult, Box<dyn Error + Send + Sync>> {
        let revision = sqlx::query_scalar::<_, i64>(
            r"
            SELECT agent_data_revision
            FROM users
            WHERE id::bigint = $1 AND is_active IS TRUE
            FOR UPDATE
            ",
        )
        .bind(self.snapshot.user_id)
        .fetch_optional(&mut **transaction)
        .await?;
        if revision != Some(self.snapshot.expected_revision) {
            return Ok(TaskFinalizerResult::Override(TaskResult::fail(
                Some("agent-data revision changed before document publication".to_owned()),
                true,
            )));
        }

        let ledger_rows = selected_ledger_rows(
            transaction,
            self.snapshot.user_id,
            &self.snapshot.selected_identities,
        )
        .await?;
        let mut ledger_by_identity = ledger_rows
            .into_iter()
            .map(|row| ((row.document_kind.clone(), row.document_key.clone()), row))
            .collect::<BTreeMap<_, _>>();
        let desired_identities = self
            .staged
            .documents
            .iter()
            .map(|document| {
                (
                    document.document_kind.clone(),
                    document.document_key.clone(),
                )
            })
            .collect::<BTreeSet<_>>();
        let next_revision = self.snapshot.expected_revision + 1;
        let mut changed = false;

        for (identity, row) in &ledger_by_identity {
            if desired_identities.contains(identity) || row.deleted_at.is_some() {
                continue;
            }
            self.store
                .delete_document(self.snapshot.user_id, &row.path)
                .await?;
            sqlx::query(
                r"
                UPDATE agent_data_files
                SET deleted_at = timezone('UTC', now()), revision = $1, updated_at = timezone('UTC', now())
                WHERE id::bigint = $2
                ",
            )
            .bind(next_revision)
            .bind(row.id)
            .execute(&mut **transaction)
            .await?;
            changed = true;
        }

        for document in &self.staged.documents {
            let identity = (
                document.document_kind.clone(),
                document.document_key.clone(),
            );
            let existing = ledger_by_identity.remove(&identity);
            let path_changed = existing
                .as_ref()
                .is_some_and(|row| row.path != document.path);
            let document_changed = existing.as_ref().is_none_or(|row| {
                path_changed
                    || row.checksum_sha256 != document.checksum_sha256
                    || row.deleted_at.is_some()
                    || !document.filesystem_matches
            });
            let mut stale_paths = existing
                .as_ref()
                .map(|row| stale_paths(&row.stale_paths))
                .unwrap_or_default();
            if let Some(old_path) = existing
                .as_ref()
                .filter(|_| path_changed)
                .map(|row| row.path.clone())
            {
                self.store
                    .delete_document(self.snapshot.user_id, &old_path)
                    .await?;
                stale_paths.insert(old_path);
                stale_paths.remove(&document.path);
            }
            if document_changed {
                self.store
                    .publish_document(self.snapshot.user_id, document)
                    .await?;
                changed = true;
            }
            upsert_document(
                transaction,
                self.snapshot.user_id,
                document,
                &stale_paths,
                existing.as_ref().map_or(next_revision, |row| {
                    if document_changed {
                        next_revision
                    } else {
                        row.revision
                    }
                }),
            )
            .await?;
        }

        let revision = if changed {
            sqlx::query(
                r"
                UPDATE users
                SET agent_data_revision = $1, updated_at = timezone('UTC', now())
                WHERE id::bigint = $2
                ",
            )
            .bind(next_revision)
            .bind(self.snapshot.user_id)
            .execute(&mut **transaction)
            .await?;
            next_revision
        } else {
            self.snapshot.expected_revision
        };
        let requests = followup_requests(
            self.snapshot.user_id,
            revision,
            self.manifest_state,
            self.index_debounce_seconds,
            self.task_id,
            &self.continuation,
        );
        self.queue
            .enqueue_many_in_transaction(transaction, requests)
            .await?;
        Ok(TaskFinalizerResult::Keep)
    }
}

impl TaskFinalizer for AgentDataSyncFinalizer {
    fn apply<'a>(
        &'a self,
        transaction: &'a mut Transaction<'static, Postgres>,
    ) -> HandlerFinalizerFuture<'a> {
        Box::pin(async move { self.apply_inner(transaction).await })
    }

    fn after_commit(&self) -> HandlerAfterCommitFuture<'_> {
        Box::pin(async move {
            self.store.cleanup_document_staging(&self.staged).await;
        })
    }
}

async fn selected_ledger_rows(
    transaction: &mut Transaction<'static, Postgres>,
    user_id: i64,
    identities: &[(String, String)],
) -> Result<Vec<AgentDataLedgerRow>, sqlx::Error> {
    if identities.is_empty() {
        return Ok(Vec::new());
    }
    let content = identity_keys(identities, "content");
    let news = identity_keys(identities, "news");
    let chats = identity_keys(identities, "chat");
    let briefings = identity_keys(identities, "briefing");
    sqlx::query_as::<_, AgentDataLedgerRow>(
        r"
        SELECT id::bigint AS id, document_kind, document_key, path, stale_paths::jsonb AS stale_paths,
               checksum_sha256, revision, deleted_at
        FROM agent_data_files
        WHERE user_id::bigint = $1
          AND (
              (document_kind = 'content' AND document_key = ANY($2::text[]))
              OR (document_kind = 'news' AND document_key = ANY($3::text[]))
              OR (document_kind = 'chat' AND document_key = ANY($4::text[]))
              OR (document_kind = 'briefing' AND document_key = ANY($5::text[]))
          )
        ORDER BY id
        FOR UPDATE
        ",
    )
    .bind(user_id)
    .bind(content)
    .bind(news)
    .bind(chats)
    .bind(briefings)
    .fetch_all(&mut **transaction)
    .await
}

fn identity_keys(identities: &[(String, String)], kind: &str) -> Vec<String> {
    identities
        .iter()
        .filter(|(document_kind, _)| document_kind == kind)
        .map(|(_, key)| key.clone())
        .collect()
}

fn stale_paths(value: &Value) -> BTreeSet<String> {
    value
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(ToOwned::to_owned)
        .collect()
}

async fn upsert_document(
    transaction: &mut Transaction<'static, Postgres>,
    user_id: i64,
    document: &StagedAgentDataDocument,
    stale_paths: &BTreeSet<String>,
    revision: i64,
) -> Result<(), sqlx::Error> {
    sqlx::query(
        r"
        INSERT INTO agent_data_files (
            user_id, document_kind, document_key, path, stale_paths, checksum_sha256,
            index_record, byte_size, revision, deleted_at, updated_at
        )
        VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7::jsonb, $8, $9, NULL, timezone('UTC', now()))
        ON CONFLICT (user_id, document_kind, document_key) DO UPDATE
        SET path = EXCLUDED.path,
            stale_paths = EXCLUDED.stale_paths,
            checksum_sha256 = EXCLUDED.checksum_sha256,
            index_record = EXCLUDED.index_record,
            byte_size = EXCLUDED.byte_size,
            revision = EXCLUDED.revision,
            deleted_at = NULL,
            updated_at = timezone('UTC', now())
        ",
    )
    .bind(user_id)
    .bind(&document.document_kind)
    .bind(&document.document_key)
    .bind(&document.path)
    .bind(json!(stale_paths))
    .bind(&document.checksum_sha256)
    .bind(&document.index_record)
    .bind(document.byte_size)
    .bind(revision)
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

fn followup_requests(
    user_id: i64,
    revision: i64,
    manifest_state: Option<(i64, bool)>,
    index_debounce_seconds: i64,
    task_id: i64,
    continuation: &AgentDataSyncContinuation,
) -> Vec<EnqueueRequest> {
    let mut requests = Vec::new();
    if manifest_state.map(|state| state.0) != Some(revision) {
        let mut request = EnqueueRequest::new(TaskType::IndexAgentData);
        request.payload = json!({"user_id": user_id}).as_object().cloned();
        request.owner_user_id = Some(user_id);
        request.dedupe = Some(true);
        request.dedupe_key = Some(format!("agent-index|user:{user_id}|base"));
        let delay = if matches!(continuation, AgentDataSyncContinuation::Incremental) {
            index_debounce_seconds.max(0)
        } else {
            0
        };
        request.available_at = Some(Utc::now() + Duration::seconds(delay));
        requests.push(request);
    }
    match continuation {
        AgentDataSyncContinuation::Incremental => {
            if !manifest_state.is_some_and(|state| state.1) {
                requests.push(backfill_request(
                    user_id,
                    "knowledge",
                    None,
                    "initial".to_owned(),
                ));
            }
        }
        AgentDataSyncContinuation::BackfillPage { stage, before_id } => {
            requests.push(backfill_request(
                user_id,
                stage,
                Some(*before_id),
                format!("after:{task_id}"),
            ));
        }
        AgentDataSyncContinuation::ReconcilePage { before_id } => {
            let mut request = EnqueueRequest::new(TaskType::ReconcileAgentData);
            request.payload = json!({"user_id": user_id, "before_id": before_id})
                .as_object()
                .cloned();
            request.owner_user_id = Some(user_id);
            request.dedupe = Some(true);
            request.dedupe_key = Some(format!("agent-reconcile|user:{user_id}|after:{task_id}"));
            requests.push(request);
        }
    }
    requests
}

pub(super) fn backfill_request(
    user_id: i64,
    stage: &str,
    before_id: Option<i64>,
    chain_key: String,
) -> EnqueueRequest {
    let mut payload = json!({"user_id": user_id, "stage": stage});
    if let Some(before_id) = before_id {
        payload["before_id"] = Value::from(before_id);
    }
    let mut request = EnqueueRequest::new(TaskType::BackfillAgentData);
    request.payload = payload.as_object().cloned();
    request.owner_user_id = Some(user_id);
    request.dedupe = Some(true);
    request.dedupe_key = Some(format!("agent-backfill|user:{user_id}|{chain_key}"));
    request
}
