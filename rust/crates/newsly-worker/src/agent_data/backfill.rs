use std::error::Error;
use std::sync::Arc;

use newsly_queue::{EnqueueRequest, OwnedWorkPlan, TaskResult, TaskType};
use serde_json::{Value, json};
use sqlx::{PgPool, Postgres, Transaction};

use crate::{
    HandlerExecution, HandlerFinalizerFuture, HandlerFuture, LeaseHealth, TaskFinalizer,
    TaskFinalizerResult, TaskHandler,
};

use super::documents::AgentDataSelection;
use super::index::{AgentDataIndexServices, execute_index};
use super::sync::{
    AgentDataSyncContinuation, AgentDataSyncServices, backfill_request, prepare_sync_execution,
};

#[derive(Debug, Clone)]
pub struct AgentDataBackfillServices {
    sync: Arc<AgentDataSyncServices>,
    index: Arc<AgentDataIndexServices>,
    batch_size: i64,
}

impl AgentDataBackfillServices {
    pub const fn new(
        sync: Arc<AgentDataSyncServices>,
        index: Arc<AgentDataIndexServices>,
        batch_size: i64,
    ) -> Self {
        Self {
            sync,
            index,
            batch_size,
        }
    }
}

#[derive(Debug, Clone)]
pub struct BackfillAgentDataHandler {
    services: Arc<AgentDataBackfillServices>,
}

impl BackfillAgentDataHandler {
    pub fn new(services: Arc<AgentDataBackfillServices>) -> Self {
        Self { services }
    }
}

impl TaskHandler for BackfillAgentDataHandler {
    fn task_type(&self) -> TaskType {
        TaskType::BackfillAgentData
    }

    fn execute(&self, plan: Arc<OwnedWorkPlan>, _lease: LeaseHealth) -> HandlerFuture<'_> {
        let services = Arc::clone(&self.services);
        Box::pin(async move { execute_backfill(&services, &plan).await })
    }
}

#[derive(Debug, Clone)]
pub struct ReconcileAgentDataHandler {
    services: Arc<AgentDataBackfillServices>,
}

impl ReconcileAgentDataHandler {
    pub fn new(services: Arc<AgentDataBackfillServices>) -> Self {
        Self { services }
    }
}

impl TaskHandler for ReconcileAgentDataHandler {
    fn task_type(&self) -> TaskType {
        TaskType::ReconcileAgentData
    }

    fn execute(&self, plan: Arc<OwnedWorkPlan>, _lease: LeaseHealth) -> HandlerFuture<'_> {
        let services = Arc::clone(&self.services);
        Box::pin(async move { execute_reconcile(&services, &plan).await })
    }
}

async fn execute_backfill(
    services: &AgentDataBackfillServices,
    plan: &OwnedWorkPlan,
) -> HandlerExecution {
    let user_id = match task_user_id(plan, "backfill_agent_data") {
        Ok(value) => value,
        Err(message) => {
            return HandlerExecution::from_result(TaskResult::fail(Some(message), false));
        }
    };
    let Some(stage) = BackfillStage::parse(
        plan.payload
            .get("stage")
            .and_then(Value::as_str)
            .unwrap_or("knowledge"),
    ) else {
        return HandlerExecution::from_result(TaskResult::fail(
            Some("backfill_agent_data stage is invalid".to_owned()),
            false,
        ));
    };
    let before_id = match optional_positive_id(plan.payload.get("before_id")) {
        Ok(value) => value,
        Err(message) => {
            return HandlerExecution::from_result(TaskResult::fail(Some(message), false));
        }
    };
    let page = match next_backfill_page(
        services.sync.pool(),
        user_id,
        stage,
        before_id,
        services.batch_size.max(1),
    )
    .await
    {
        Ok(page) => page,
        Err(error) => {
            return HandlerExecution::from_result(TaskResult::fail(Some(error.to_string()), true));
        }
    };
    let Some(page) = page else {
        return execute_index(&services.index, plan, true).await;
    };
    prepare_sync_execution(
        &services.sync,
        plan,
        user_id,
        page.selection,
        AgentDataSyncContinuation::BackfillPage {
            stage: page.stage.as_str().to_owned(),
            before_id: page.next_before_id,
        },
    )
    .await
}

async fn execute_reconcile(
    services: &AgentDataBackfillServices,
    plan: &OwnedWorkPlan,
) -> HandlerExecution {
    let user_id = match task_user_id(plan, "reconcile_agent_data") {
        Ok(value) => value,
        Err(message) => {
            return HandlerExecution::from_result(TaskResult::fail(Some(message), false));
        }
    };
    let before_id = match optional_positive_id(plan.payload.get("before_id")) {
        Ok(value) => value,
        Err(message) => {
            return HandlerExecution::from_result(TaskResult::fail(Some(message), false));
        }
    };
    let page = match next_reconcile_page(
        services.sync.pool(),
        user_id,
        before_id,
        services.batch_size.max(1),
    )
    .await
    {
        Ok(page) => page,
        Err(error) => {
            return HandlerExecution::from_result(TaskResult::fail(Some(error.to_string()), true));
        }
    };
    let Some(page) = page else {
        return HandlerExecution::with_finalizer(
            TaskResult::ok(),
            ReconcileTerminalFinalizer {
                queue: services.sync.queue().clone(),
                user_id,
                task_id: plan.task_id,
            },
        );
    };
    prepare_sync_execution(
        &services.sync,
        plan,
        user_id,
        page.selection,
        AgentDataSyncContinuation::ReconcilePage {
            before_id: page.next_before_id,
        },
    )
    .await
}

fn task_user_id(plan: &OwnedWorkPlan, task: &str) -> Result<i64, String> {
    let payload_user_id = plan.payload.get("user_id").and_then(Value::as_i64);
    let user_id = plan
        .owner_user_id
        .filter(|value| *value > 0)
        .ok_or_else(|| format!("{task} requires a positive owner user_id"))?;
    if payload_user_id != Some(user_id) {
        return Err(format!("{task} owner and payload user_id must match"));
    }
    Ok(user_id)
}

fn optional_positive_id(value: Option<&Value>) -> Result<Option<i64>, String> {
    value
        .map(|value| {
            value
                .as_i64()
                .filter(|value| *value > 0)
                .ok_or_else(|| "agent-data before_id must be a positive integer".to_owned())
        })
        .transpose()
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum BackfillStage {
    Knowledge,
    Content,
    News,
    Chats,
    Briefings,
}

impl BackfillStage {
    const fn as_str(self) -> &'static str {
        match self {
            Self::Knowledge => "knowledge",
            Self::Content => "content",
            Self::News => "news",
            Self::Chats => "chats",
            Self::Briefings => "briefings",
        }
    }

    fn parse(value: &str) -> Option<Self> {
        match value {
            "knowledge" => Some(Self::Knowledge),
            "content" => Some(Self::Content),
            "news" => Some(Self::News),
            "chats" => Some(Self::Chats),
            "briefings" => Some(Self::Briefings),
            _ => None,
        }
    }

    const fn next(self) -> Option<Self> {
        match self {
            Self::Knowledge => Some(Self::Content),
            Self::Content => Some(Self::News),
            Self::News => Some(Self::Chats),
            Self::Chats => Some(Self::Briefings),
            Self::Briefings => None,
        }
    }
}

#[derive(Debug)]
struct BackfillPage {
    stage: BackfillStage,
    selection: AgentDataSelection,
    next_before_id: i64,
}

async fn next_backfill_page(
    pool: &PgPool,
    user_id: i64,
    mut stage: BackfillStage,
    mut before_id: Option<i64>,
    limit: i64,
) -> Result<Option<BackfillPage>, sqlx::Error> {
    let mut transaction = pool.begin().await?;
    let active = sqlx::query_scalar::<_, bool>(
        "SELECT EXISTS(SELECT 1 FROM users WHERE id::bigint = $1 AND is_active IS TRUE)",
    )
    .bind(user_id)
    .fetch_one(&mut *transaction)
    .await?;
    if !active {
        transaction.rollback().await?;
        return Ok(None);
    }
    loop {
        let ids = backfill_ids(&mut transaction, user_id, stage, before_id, limit).await?;
        if let Some(next_before_id) = ids.iter().copied().min() {
            let selection = if stage == BackfillStage::Briefings {
                let dates = sqlx::query_scalar::<_, String>(
                    r"
                    SELECT DISTINCT to_char(created_at, 'YYYY-MM-DD')
                    FROM briefing_segments
                    WHERE user_id::bigint = $1 AND id::bigint = ANY($2::bigint[])
                    ORDER BY 1
                    ",
                )
                .bind(user_id)
                .bind(&ids)
                .fetch_all(&mut *transaction)
                .await?
                .into_iter()
                .collect();
                AgentDataSelection {
                    briefing_dates: dates,
                    ..AgentDataSelection::default()
                }
            } else {
                selection_for_ids(stage, ids)
            };
            transaction.commit().await?;
            return Ok(Some(BackfillPage {
                stage,
                selection,
                next_before_id,
            }));
        }
        let Some(next) = stage.next() else {
            transaction.commit().await?;
            return Ok(None);
        };
        stage = next;
        before_id = None;
    }
}

async fn backfill_ids(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    stage: BackfillStage,
    before_id: Option<i64>,
    limit: i64,
) -> Result<Vec<i64>, sqlx::Error> {
    match stage {
        BackfillStage::Knowledge => sqlx::query_scalar::<_, i64>(
            r"
            SELECT save.content_id::bigint
            FROM content_knowledge_saves AS save
            JOIN contents AS content ON content.id = save.content_id
            WHERE save.user_id::bigint = $1 AND content.status = 'completed'
              AND ($2::bigint IS NULL OR save.content_id::bigint < $2)
            ORDER BY save.content_id DESC
            LIMIT $3
            ",
        )
        .bind(user_id)
        .bind(before_id)
        .bind(limit)
        .fetch_all(&mut **transaction)
        .await,
        BackfillStage::Content => sqlx::query_scalar::<_, i64>(
            r"
            SELECT content.id::bigint
            FROM contents AS content
            WHERE content.status = 'completed'
              AND ($2::bigint IS NULL OR content.id::bigint < $2)
              AND (
                  EXISTS (SELECT 1 FROM content_status WHERE user_id::bigint = $1 AND content_id = content.id)
                  OR EXISTS (SELECT 1 FROM chat_sessions WHERE user_id::bigint = $1 AND content_id = content.id)
              )
              AND NOT EXISTS (
                  SELECT 1 FROM content_knowledge_saves
                  WHERE user_id::bigint = $1 AND content_id = content.id
              )
            ORDER BY content.id DESC
            LIMIT $3
            ",
        )
        .bind(user_id)
        .bind(before_id)
        .bind(limit)
        .fetch_all(&mut **transaction)
        .await,
        BackfillStage::News => sqlx::query_scalar::<_, i64>(
            r"
            SELECT news.id::bigint
            FROM news_items AS news
            WHERE news.status = 'ready' AND news.representative_news_item_id IS NULL
              AND ($2::bigint IS NULL OR news.id::bigint < $2)
              AND (
                  (news.visibility_scope = 'user' AND news.owner_user_id::bigint = $1)
                  OR (
                      news.visibility_scope = 'global'
                      AND EXISTS (
                          SELECT 1 FROM user_scraper_configs AS config
                          WHERE config.user_id::bigint = $1 AND config.is_active IS TRUE
                            AND config.scraper_type = 'aggregator'
                            AND lower(config.config::jsonb ->> 'key') = lower(news.platform)
                      )
                  )
              )
            ORDER BY news.id DESC
            LIMIT $3
            ",
        )
        .bind(user_id)
        .bind(before_id)
        .bind(limit)
        .fetch_all(&mut **transaction)
        .await,
        BackfillStage::Chats => sqlx::query_scalar::<_, i64>(
            r"
            SELECT id::bigint FROM chat_sessions
            WHERE user_id::bigint = $1 AND ($2::bigint IS NULL OR id::bigint < $2)
            ORDER BY id DESC LIMIT $3
            ",
        )
        .bind(user_id)
        .bind(before_id)
        .bind(limit)
        .fetch_all(&mut **transaction)
        .await,
        BackfillStage::Briefings => sqlx::query_scalar::<_, i64>(
            r"
            SELECT id::bigint FROM briefing_segments
            WHERE user_id::bigint = $1 AND status IN ('active', 'degraded')
              AND ($2::bigint IS NULL OR id::bigint < $2)
            ORDER BY id DESC LIMIT $3
            ",
        )
        .bind(user_id)
        .bind(before_id)
        .bind(limit)
        .fetch_all(&mut **transaction)
        .await,
    }
}

fn selection_for_ids(stage: BackfillStage, ids: Vec<i64>) -> AgentDataSelection {
    match stage {
        BackfillStage::Knowledge | BackfillStage::Content => AgentDataSelection {
            content_ids: ids.into_iter().collect(),
            ..AgentDataSelection::default()
        },
        BackfillStage::News => AgentDataSelection {
            news_item_ids: ids.into_iter().collect(),
            ..AgentDataSelection::default()
        },
        BackfillStage::Chats => AgentDataSelection {
            chat_session_ids: ids.into_iter().collect(),
            ..AgentDataSelection::default()
        },
        BackfillStage::Briefings => unreachable!("briefing pages use date identities"),
    }
}

#[derive(Debug)]
struct ReconcilePage {
    selection: AgentDataSelection,
    next_before_id: i64,
}

async fn next_reconcile_page(
    pool: &PgPool,
    user_id: i64,
    before_id: Option<i64>,
    limit: i64,
) -> Result<Option<ReconcilePage>, ReconcilePageError> {
    let rows = sqlx::query_as::<_, (i64, String, String)>(
        r"
        SELECT id::bigint, document_kind, document_key
        FROM agent_data_files
        WHERE user_id::bigint = $1 AND deleted_at IS NULL
          AND ($2::bigint IS NULL OR id::bigint < $2)
        ORDER BY id DESC LIMIT $3
        ",
    )
    .bind(user_id)
    .bind(before_id)
    .bind(limit)
    .fetch_all(pool)
    .await?;
    if rows.is_empty() {
        return Ok(None);
    }
    let mut selection = AgentDataSelection::default();
    let mut next_before_id = i64::MAX;
    for (row_id, kind, key) in rows {
        next_before_id = next_before_id.min(row_id);
        match kind.as_str() {
            "content" => {
                selection
                    .content_ids
                    .insert(positive_document_key(&kind, &key)?);
            }
            "news" => {
                selection
                    .news_item_ids
                    .insert(positive_document_key(&kind, &key)?);
            }
            "chat" => {
                selection
                    .chat_session_ids
                    .insert(positive_document_key(&kind, &key)?);
            }
            "briefing" if !key.is_empty() => {
                selection.briefing_dates.insert(key);
            }
            _ => return Err(ReconcilePageError::InvalidKind(kind)),
        }
    }
    Ok(Some(ReconcilePage {
        selection,
        next_before_id,
    }))
}

fn positive_document_key(kind: &str, key: &str) -> Result<i64, ReconcilePageError> {
    key.parse::<i64>()
        .ok()
        .filter(|value| *value > 0)
        .ok_or_else(|| ReconcilePageError::InvalidKey {
            kind: kind.to_owned(),
            key: key.to_owned(),
        })
}

#[derive(Debug, thiserror::Error)]
enum ReconcilePageError {
    #[error("agent-data reconcile query failed")]
    Sqlx(#[from] sqlx::Error),
    #[error("agent-data ledger has unsupported document kind {0:?}")]
    InvalidKind(String),
    #[error("agent-data ledger has invalid {kind} document key {key:?}")]
    InvalidKey { kind: String, key: String },
}

#[derive(Debug)]
struct ReconcileTerminalFinalizer {
    queue: newsly_queue::QueueKernel,
    user_id: i64,
    task_id: i64,
}

impl TaskFinalizer for ReconcileTerminalFinalizer {
    fn apply<'a>(
        &'a self,
        transaction: &'a mut Transaction<'static, Postgres>,
    ) -> HandlerFinalizerFuture<'a> {
        Box::pin(async move {
            let mut index = EnqueueRequest::new(TaskType::IndexAgentData);
            index.payload = json!({"user_id": self.user_id}).as_object().cloned();
            index.owner_user_id = Some(self.user_id);
            index.dedupe = Some(true);
            index.dedupe_key = Some(format!("agent-index|user:{}|base", self.user_id));
            let backfill = backfill_request(
                self.user_id,
                "knowledge",
                None,
                format!("after:{}", self.task_id),
            );
            self.queue
                .enqueue_many_in_transaction(transaction, vec![index, backfill])
                .await
                .map_err(|error| Box::new(error) as Box<dyn Error + Send + Sync>)?;
            Ok(TaskFinalizerResult::Keep)
        })
    }
}
