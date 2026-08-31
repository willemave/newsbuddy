use std::error::Error;
use std::sync::Arc;

use newsly_queue::{OwnedWorkPlan, TaskResult, TaskType};
use serde_json::Value;
use sqlx::{FromRow, PgPool, Postgres, Transaction};

use crate::{
    HandlerAfterCommitFuture, HandlerExecution, HandlerFinalizerFuture, HandlerFuture, LeaseHealth,
    TaskFinalizer, TaskFinalizerResult, TaskHandler,
};

use super::storage::{AgentDataMirrorStore, StagedIndexPublication};

#[derive(Debug, Clone)]
pub struct AgentDataIndexServices {
    pool: PgPool,
    store: AgentDataMirrorStore,
}

impl AgentDataIndexServices {
    pub const fn new(pool: PgPool, store: AgentDataMirrorStore) -> Self {
        Self { pool, store }
    }
}

#[derive(Debug, Clone)]
pub struct IndexAgentDataHandler {
    services: Arc<AgentDataIndexServices>,
}

impl IndexAgentDataHandler {
    pub fn new(services: Arc<AgentDataIndexServices>) -> Self {
        Self { services }
    }
}

impl TaskHandler for IndexAgentDataHandler {
    fn task_type(&self) -> TaskType {
        TaskType::IndexAgentData
    }

    fn execute(&self, plan: Arc<OwnedWorkPlan>, _lease: LeaseHealth) -> HandlerFuture<'_> {
        let services = Arc::clone(&self.services);
        Box::pin(async move { execute_index(&services, &plan, false).await })
    }
}

#[derive(Debug, FromRow)]
struct IndexRecordRow {
    path: String,
    index_record: Value,
}

pub(super) async fn execute_index(
    services: &AgentDataIndexServices,
    plan: &OwnedWorkPlan,
    mark_complete: bool,
) -> HandlerExecution {
    let Some(user_id) = plan
        .owner_user_id
        .or_else(|| plan.payload.get("user_id").and_then(Value::as_i64))
        .filter(|value| *value > 0)
    else {
        return HandlerExecution::from_result(TaskResult::fail(
            Some("index_agent_data requires a positive user_id".to_owned()),
            false,
        ));
    };
    let snapshot = match prepare_index_snapshot(&services.pool, user_id).await {
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
        .stage_index(
            user_id,
            plan.task_id,
            plan.retry_count,
            snapshot.revision,
            &snapshot.records,
            mark_complete,
        )
        .await
    {
        Ok(staged) => staged,
        Err(error) => {
            return HandlerExecution::from_result(TaskResult::fail(Some(error.to_string()), true));
        }
    };
    HandlerExecution::with_finalizer(
        TaskResult::ok(),
        AgentDataIndexFinalizer {
            store: services.store.clone(),
            expected_revision: snapshot.revision,
            staged,
        },
    )
}

#[derive(Debug)]
struct IndexSnapshot {
    revision: i64,
    records: Vec<(String, Value)>,
}

async fn prepare_index_snapshot(
    pool: &PgPool,
    user_id: i64,
) -> Result<Option<IndexSnapshot>, sqlx::Error> {
    let mut transaction = pool.begin().await?;
    let revision = sqlx::query_scalar::<_, i64>(
        r"
        SELECT agent_data_revision
        FROM users
        WHERE id::bigint = $1 AND is_active IS TRUE
        FOR SHARE
        ",
    )
    .bind(user_id)
    .fetch_optional(&mut *transaction)
    .await?;
    let Some(revision) = revision else {
        transaction.rollback().await?;
        return Ok(None);
    };
    let rows = sqlx::query_as::<_, IndexRecordRow>(
        r"
        SELECT path, index_record::jsonb AS index_record
        FROM agent_data_files
        WHERE user_id::bigint = $1 AND deleted_at IS NULL
        ORDER BY path
        ",
    )
    .bind(user_id)
    .fetch_all(&mut *transaction)
    .await?;
    transaction.commit().await?;
    Ok(Some(IndexSnapshot {
        revision,
        records: rows
            .into_iter()
            .map(|row| (row.path, row.index_record))
            .collect(),
    }))
}

#[derive(Debug)]
struct AgentDataIndexFinalizer {
    store: AgentDataMirrorStore,
    expected_revision: i64,
    staged: StagedIndexPublication,
}

impl TaskFinalizer for AgentDataIndexFinalizer {
    fn apply<'a>(
        &'a self,
        transaction: &'a mut Transaction<'static, Postgres>,
    ) -> HandlerFinalizerFuture<'a> {
        Box::pin(async move {
            let revision = sqlx::query_scalar::<_, i64>(
                r"
                SELECT agent_data_revision
                FROM users
                WHERE id::bigint = $1 AND is_active IS TRUE
                FOR UPDATE
                ",
            )
            .bind(self.staged.user_id)
            .fetch_optional(&mut **transaction)
            .await?;
            if revision != Some(self.expected_revision) {
                return Ok(TaskFinalizerResult::Override(TaskResult::fail(
                    Some("agent-data revision changed before index publication".to_owned()),
                    true,
                )));
            }
            self.store
                .publish_index(&self.staged)
                .await
                .map_err(|error| Box::new(error) as Box<dyn Error + Send + Sync>)?;
            Ok(TaskFinalizerResult::Keep)
        })
    }

    fn after_commit(&self) -> HandlerAfterCommitFuture<'_> {
        Box::pin(async move {
            self.store.cleanup_staging(&self.staged).await;
        })
    }
}
