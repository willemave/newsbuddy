use std::path::PathBuf;
use std::sync::Arc;

use newsly_queue::{OwnedWorkPlan, QueueKernel, TaskResult, TaskType};
use newsly_worker::{HandlerExecution, HandlerFuture, LeaseHealth, TaskHandler};
use sqlx::PgPool;
use tracing::{info, warn};

use crate::external::{AccountExternalServices, remove_local_files};
use crate::repository::{
    AccountCleanupPlan, AccountDeletionFinalizer, AccountRepositoryError, prepare_cleanup_plan,
};

const ACTIVE_WORK_DEFER_SECONDS: i64 = 30;

#[derive(Debug, Clone)]
pub struct AccountDeletionServices {
    pool: PgPool,
    queue: QueueKernel,
    external: AccountExternalServices,
    media_audio_root: PathBuf,
    personal_markdown_root: PathBuf,
    agent_data_mirror_root: PathBuf,
}

impl AccountDeletionServices {
    pub const fn new(
        pool: PgPool,
        queue: QueueKernel,
        external: AccountExternalServices,
        media_audio_root: PathBuf,
        personal_markdown_root: PathBuf,
        agent_data_mirror_root: PathBuf,
    ) -> Self {
        Self {
            pool,
            queue,
            external,
            media_audio_root,
            personal_markdown_root,
            agent_data_mirror_root,
        }
    }
}

#[derive(Debug, Clone)]
pub struct AccountDeletionHandler {
    services: Arc<AccountDeletionServices>,
}

impl AccountDeletionHandler {
    pub fn new(services: Arc<AccountDeletionServices>) -> Self {
        Self { services }
    }
}

impl TaskHandler for AccountDeletionHandler {
    fn task_type(&self) -> TaskType {
        TaskType::DeleteUserAccount
    }

    fn execute(&self, plan: Arc<OwnedWorkPlan>, lease: LeaseHealth) -> HandlerFuture<'_> {
        let services = Arc::clone(&self.services);
        Box::pin(async move { execute_account_deletion(&services, &plan, lease).await })
    }
}

async fn execute_account_deletion(
    services: &AccountDeletionServices,
    task: &OwnedWorkPlan,
    lease: LeaseHealth,
) -> HandlerExecution {
    let Some(user_id) = deletion_user_id(task) else {
        return terminal_failure("delete_user_account requires a positive payload user_id");
    };
    if task
        .owner_user_id
        .is_some_and(|owner_user_id| owner_user_id != user_id)
    {
        return terminal_failure("delete_user_account owner does not match payload user_id");
    }

    // This transaction deletes pending account-owned work and commits before any plan or external
    // call. A currently processing sibling causes a retry-neutral queue deferral.
    match services
        .queue
        .cancel_pending_for_user(user_id, task.task_id)
        .await
    {
        Ok(true) => {}
        Ok(false) => {
            info!(
                task_id = task.task_id,
                user_id, "account deletion deferred until active account-owned work finishes"
            );
            return HandlerExecution::from_result(TaskResult::defer(ACTIVE_WORK_DEFER_SECONDS));
        }
        Err(error) => return retryable_failure(&error.to_string()),
    }

    let cleanup_plan = match prepare_cleanup_plan(
        &services.pool,
        user_id,
        &services.media_audio_root,
        &services.personal_markdown_root,
        &services.agent_data_mirror_root,
    )
    .await
    {
        Ok(cleanup_plan) => cleanup_plan,
        Err(AccountRepositoryError::ActiveUser(_)) => {
            return terminal_failure("account deletion refused to purge an active user");
        }
        Err(error) => return retryable_failure(&error.to_string()),
    };

    if lease.ownership_lost() {
        return retryable_failure("account deletion lease was lost before external cleanup");
    }
    if let Some(cleanup_plan) = cleanup_plan.as_ref()
        && let Err(error) = run_external_cleanup(services, cleanup_plan, &lease).await
    {
        return retryable_failure(&error);
    }
    if lease.ownership_lost() {
        return retryable_failure("account deletion lease was lost before database finalization");
    }

    HandlerExecution::with_finalizer(
        TaskResult::ok(),
        AccountDeletionFinalizer::new(user_id, task.task_id),
    )
}

async fn run_external_cleanup(
    services: &AccountDeletionServices,
    plan: &AccountCleanupPlan,
    lease: &LeaseHealth,
) -> Result<(), String> {
    debug_assert!(plan.user_id > 0);
    services
        .external
        .vm
        .destroy(plan.sandbox_id.as_deref(), plan.snapshot_id.as_deref())
        .await
        .map_err(|error| error.to_string())?;
    if lease.ownership_lost() {
        return Err("account deletion lease was lost after E2B cleanup".to_owned());
    }

    // X revocation intentionally cannot block local account deletion. Credentials are removed by
    // the fenced database purge even when the provider, key, or remote endpoint is unavailable.
    for grant in &plan.x_grants {
        if let Err(error) = services
            .external
            .x
            .revoke(&grant.encrypted_token, grant.token_type_hint.as_str())
            .await
        {
            warn!(
                user_id = plan.user_id,
                error = %error,
                "unable to revoke X grant during account deletion; local credentials will be purged"
            );
        }
    }
    if lease.ownership_lost() {
        return Err("account deletion lease was lost after grant revocation".to_owned());
    }

    for key in &plan.object_keys {
        services
            .external
            .objects
            .delete(key)
            .await
            .map_err(|error| error.to_string())?;
        if lease.ownership_lost() {
            return Err("account deletion lease was lost during object cleanup".to_owned());
        }
    }
    remove_local_files(plan)
        .await
        .map_err(|error| error.to_string())?;
    Ok(())
}

fn deletion_user_id(task: &OwnedWorkPlan) -> Option<i64> {
    (task.task_type == TaskType::DeleteUserAccount)
        .then(|| {
            task.payload
                .get("user_id")
                .and_then(serde_json::Value::as_i64)
        })
        .flatten()
        .filter(|user_id| *user_id > 0)
}

fn terminal_failure(message: &str) -> HandlerExecution {
    HandlerExecution::from_result(TaskResult::fail(Some(message.to_owned()), false))
}

fn retryable_failure(message: &str) -> HandlerExecution {
    HandlerExecution::from_result(TaskResult::fail(Some(message.to_owned()), true))
}

#[cfg(test)]
mod tests {
    use newsly_domain::RuntimeOwner;
    use newsly_queue::{OwnedWorkPlan, TaskQueue, TaskType};
    use serde_json::{Map, Value};

    use super::deletion_user_id;

    #[test]
    fn deletion_identity_comes_only_from_typed_payload() {
        let mut payload = Map::new();
        payload.insert("user_id".to_owned(), Value::from(42));
        let task = OwnedWorkPlan {
            task_id: 5,
            owner_user_id: None,
            task_type: TaskType::DeleteUserAccount,
            content_id: None,
            payload,
            retry_count: 0,
            queue_name: TaskQueue::Backfill,
            executor_runtime: RuntimeOwner::Rust,
            executor_version: 1,
            executor_namespace: "delete_user_account".to_owned(),
        };
        assert_eq!(deletion_user_id(&task), Some(42));
    }
}
