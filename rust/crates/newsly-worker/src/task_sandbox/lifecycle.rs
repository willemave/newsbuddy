use std::collections::BTreeMap;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

use newsly_db::{
    TaskSandboxCleanupCandidate, TaskSandboxRepositoryError, clear_task_sandbox,
    find_recorded_task_sandbox, list_task_sandbox_cleanup_candidates,
    mark_task_sandbox_cleanup_required, record_task_sandbox,
};
use newsly_e2b::{
    CommandRequest, DirectE2bProvider, E2bError, ExecutionTag, ExitStatus, NetworkPolicy,
    OutputLimits, SandboxHandle, SandboxId, SandboxProvider, SandboxRequest, SandboxUser,
    VmBootstrapProvider, VmCapabilities,
};
use sqlx::PgPool;
use thiserror::Error;
use tokio::time::Instant;
use tokio_util::sync::CancellationToken;

const HARDEN_DEFAULT_USER: &str = r"set -eu
sed -i '/^user[[:space:]].*NOPASSWD:[[:space:]]*ALL[[:space:]]*$/d' /etc/sudoers
if id -nG user | tr ' ' '\n' | grep -qx sudo; then
  gpasswd -d user sudo >/dev/null
fi
if su -s /bin/sh user -c 'sudo -n true' >/dev/null 2>&1; then
  echo 'default user still has passwordless sudo' >&2
  exit 1
fi
";

/// Configuration for disposable compute used by one LLM task attempt.
#[derive(Debug, Clone)]
pub struct TaskSandboxConfig {
    pub template_id: String,
    pub template_revision: String,
    pub sandbox_timeout: Duration,
}

impl TaskSandboxConfig {
    fn validate(&self) -> Result<(), TaskSandboxError> {
        validate_identity(&self.template_id, "template id")?;
        validate_identity(&self.template_revision, "template revision")?;
        if self.sandbox_timeout.is_zero() || self.sandbox_timeout.as_secs() > 3_600 {
            return Err(TaskSandboxError::Configuration(
                "sandbox timeout must be between one second and one hour".to_owned(),
            ));
        }
        Ok(())
    }
}

/// Creates a fresh, credential-free sandbox for every task attempt.
#[derive(Debug, Clone)]
pub struct TaskSandboxOwner {
    pool: PgPool,
    provider: Arc<DirectE2bProvider>,
    config: TaskSandboxConfig,
    cleanup_sweep_active: Arc<AtomicBool>,
}

impl TaskSandboxOwner {
    pub fn new(
        pool: PgPool,
        provider: Arc<DirectE2bProvider>,
        config: TaskSandboxConfig,
    ) -> Result<Self, TaskSandboxError> {
        config.validate()?;
        Ok(Self {
            pool,
            provider,
            config,
            cleanup_sweep_active: Arc::new(AtomicBool::new(false)),
        })
    }

    pub async fn acquire_for_task(
        &self,
        user_id: i64,
        task_id: i64,
        feature: &str,
        absolute_deadline: Instant,
        cancellation: CancellationToken,
    ) -> Result<AcquiredTaskSandbox, TaskSandboxError> {
        if user_id <= 0 || task_id <= 0 || feature.trim().is_empty() || feature.len() > 255 {
            return Err(TaskSandboxError::Configuration(
                "task sandbox identity is invalid".to_owned(),
            ));
        }
        require_time(absolute_deadline)?;
        self.schedule_pending_cleanups();
        if let Some(previous) = find_recorded_task_sandbox(&self.pool, task_id, user_id).await? {
            SandboxCleanup::recorded(
                Arc::clone(&self.provider),
                self.pool.clone(),
                task_id,
                user_id,
                SandboxId::parse(previous)?,
            )
            .run()
            .await?;
        }
        let timeout = u32::try_from(self.config.sandbox_timeout.as_secs()).map_err(|_| {
            TaskSandboxError::Configuration("sandbox timeout is too large".to_owned())
        })?;
        let request = SandboxRequest {
            template_id: self.config.template_id.clone(),
            timeout,
            auto_pause: false,
            auto_pause_memory: false,
            secure: true,
            allow_internet_access: false,
            metadata: BTreeMap::from([
                ("feature".to_owned(), feature.to_owned()),
                ("user_id".to_owned(), user_id.to_string()),
                ("llm_task_id".to_owned(), task_id.to_string()),
                (
                    "template_revision".to_owned(),
                    self.config.template_revision.clone(),
                ),
                ("reuse_scope".to_owned(), "task_attempt".to_owned()),
            ]),
            env_vars: BTreeMap::new(),
            network: Some(NetworkPolicy::deny_all()),
        };
        // Once dispatched, creation must be observed to completion. Dropping this future on
        // cancellation loses the remote sandbox ID and makes cleanup impossible.
        let sandbox = self.provider.create_sandbox(&request).await?;
        let mut pending = PendingSandbox::new(
            Arc::clone(&self.provider),
            self.pool.clone(),
            task_id,
            user_id,
            sandbox,
        );
        record_task_sandbox(
            &self.pool,
            task_id,
            user_id,
            pending.sandbox().sandbox_id.as_str(),
        )
        .await?;
        pending.mark_recorded();
        if cancellation.is_cancelled() {
            pending.cleanup().await?;
            return Err(TaskSandboxError::Cancelled);
        }
        self.harden(
            pending.sandbox(),
            absolute_deadline,
            cancellation.child_token(),
        )
        .await?;

        let capabilities = self
            .provider
            .probe_vm_capabilities(
                pending.sandbox(),
                absolute_deadline,
                cancellation.child_token(),
            )
            .await
            .map_err(|source| TaskSandboxError::ProviderOperation {
                operation: "probe_vm_capabilities",
                source,
            })?;
        let (sandbox, cleanup) = pending.disarm();
        Ok(AcquiredTaskSandbox {
            sandbox,
            capabilities,
            template_revision: self.config.template_revision.clone(),
            cleanup: Some(cleanup),
        })
    }

    async fn reap_pending_cleanups(&self) {
        let candidates = match list_task_sandbox_cleanup_candidates(&self.pool, 8).await {
            Ok(candidates) => candidates,
            Err(error) => {
                tracing::warn!(error = %error, "failed to load pending task sandbox cleanups");
                return;
            }
        };
        for candidate in candidates {
            let TaskSandboxCleanupCandidate {
                task_id,
                user_id,
                sandbox_id,
            } = candidate;
            let sandbox_id = match SandboxId::parse(sandbox_id) {
                Ok(sandbox_id) => sandbox_id,
                Err(error) => {
                    tracing::error!(task_id, user_id, error = %error, "stored task sandbox ID is invalid");
                    continue;
                }
            };
            let cleanup = SandboxCleanup::recorded(
                Arc::clone(&self.provider),
                self.pool.clone(),
                task_id,
                user_id,
                sandbox_id,
            );
            if let Err(error) = cleanup.run().await {
                tracing::warn!(task_id, user_id, error = %error, "pending task sandbox cleanup failed");
            }
        }
    }

    fn schedule_pending_cleanups(&self) {
        if self
            .cleanup_sweep_active
            .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .is_err()
        {
            return;
        }
        let owner = self.clone();
        tokio::spawn(async move {
            owner.reap_pending_cleanups().await;
            owner.cleanup_sweep_active.store(false, Ordering::Release);
        });
    }

    async fn harden(
        &self,
        sandbox: &SandboxHandle,
        deadline: Instant,
        cancellation: CancellationToken,
    ) -> Result<(), TaskSandboxError> {
        let stream = self
            .provider
            .start_process(
                sandbox,
                CommandRequest {
                    command: "/bin/bash".to_owned(),
                    args: vec!["-lc".to_owned(), HARDEN_DEFAULT_USER.to_owned()],
                    env: BTreeMap::new(),
                    cwd: None,
                    username: Some(SandboxUser::root()),
                    tag: ExecutionTag::new(),
                    stdin_enabled: false,
                    absolute_deadline: deadline.min(Instant::now() + Duration::from_secs(30)),
                    idle_timeout: Duration::from_secs(30),
                    output_limits: OutputLimits {
                        stdout_bytes: 8 * 1024,
                        stderr_bytes: 8 * 1024,
                        combined_bytes: 16 * 1024,
                        event_bytes: 8 * 1024,
                        channel_capacity: 8,
                    },
                },
                cancellation,
            )
            .await
            .map_err(|source| TaskSandboxError::ProviderOperation {
                operation: "harden_default_user_start",
                source,
            })?;
        let result = stream.collect_result().await.map_err(|source| {
            TaskSandboxError::ProviderOperation {
                operation: "harden_default_user_stream",
                source,
            }
        })?;
        if result.status != ExitStatus::Exited || result.exit_code != 0 {
            return Err(TaskSandboxError::HardeningFailed(truncate(
                &format!(
                    "status={:?} exit_code={} stderr={:?} error={:?}",
                    result.status, result.exit_code, result.output.stderr, result.error
                ),
                1_000,
            )));
        }
        Ok(())
    }
}

#[derive(Debug)]
struct PendingSandbox {
    sandbox: Option<SandboxHandle>,
    cleanup: Option<SandboxCleanup>,
}

impl PendingSandbox {
    fn new(
        provider: Arc<DirectE2bProvider>,
        pool: PgPool,
        task_id: i64,
        user_id: i64,
        sandbox: SandboxHandle,
    ) -> Self {
        let cleanup = SandboxCleanup::unrecorded(
            provider,
            pool,
            task_id,
            user_id,
            sandbox.sandbox_id.clone(),
        );
        Self {
            sandbox: Some(sandbox),
            cleanup: Some(cleanup),
        }
    }

    fn sandbox(&self) -> &SandboxHandle {
        self.sandbox.as_ref().expect("pending sandbox is armed")
    }

    fn mark_recorded(&mut self) {
        self.cleanup
            .as_mut()
            .expect("pending sandbox cleanup is armed")
            .recorded = true;
    }

    async fn cleanup(&mut self) -> Result<(), TaskSandboxError> {
        let cleanup = self
            .cleanup
            .take()
            .expect("pending sandbox cleanup is armed");
        self.sandbox.take();
        cleanup.run().await
    }

    fn disarm(&mut self) -> (SandboxHandle, SandboxCleanup) {
        (
            self.sandbox.take().expect("pending sandbox is armed"),
            self.cleanup
                .take()
                .expect("pending sandbox cleanup is armed"),
        )
    }
}

impl Drop for PendingSandbox {
    fn drop(&mut self) {
        self.sandbox.take();
        let Some(cleanup) = self.cleanup.take() else {
            return;
        };
        schedule_cleanup(cleanup);
    }
}

#[derive(Debug)]
pub struct AcquiredTaskSandbox {
    pub sandbox: SandboxHandle,
    pub capabilities: VmCapabilities,
    pub template_revision: String,
    cleanup: Option<SandboxCleanup>,
}

impl AcquiredTaskSandbox {
    /// Destroys the attempt sandbox. Task sandboxes are never paused or retained.
    pub async fn release(mut self) -> Result<(), TaskSandboxError> {
        let Some(cleanup) = self.cleanup.take() else {
            return Ok(());
        };
        if let Err(error) = cleanup.run().await {
            self.cleanup = Some(cleanup);
            return Err(error);
        }
        Ok(())
    }
}

impl Drop for AcquiredTaskSandbox {
    fn drop(&mut self) {
        let Some(cleanup) = self.cleanup.take() else {
            return;
        };
        schedule_cleanup(cleanup);
    }
}

#[derive(Debug, Clone)]
struct SandboxCleanup {
    provider: Arc<DirectE2bProvider>,
    pool: PgPool,
    task_id: i64,
    user_id: i64,
    sandbox_id: SandboxId,
    recorded: bool,
}

impl SandboxCleanup {
    fn unrecorded(
        provider: Arc<DirectE2bProvider>,
        pool: PgPool,
        task_id: i64,
        user_id: i64,
        sandbox_id: SandboxId,
    ) -> Self {
        Self {
            provider,
            pool,
            task_id,
            user_id,
            sandbox_id,
            recorded: false,
        }
    }

    fn recorded(
        provider: Arc<DirectE2bProvider>,
        pool: PgPool,
        task_id: i64,
        user_id: i64,
        sandbox_id: SandboxId,
    ) -> Self {
        Self {
            recorded: true,
            ..Self::unrecorded(provider, pool, task_id, user_id, sandbox_id)
        }
    }

    async fn run(&self) -> Result<(), TaskSandboxError> {
        let tracked = if self.recorded {
            match mark_task_sandbox_cleanup_required(
                &self.pool,
                self.task_id,
                self.user_id,
                self.sandbox_id.as_str(),
            )
            .await
            {
                Ok(()) => true,
                // The exact record was deleted or replaced. The old remote ID still belongs to
                // this guard and must be killed, but clearing must not touch its replacement.
                Err(TaskSandboxRepositoryError::AttemptUnavailable) => false,
                Err(error) => return Err(error.into()),
            }
        } else {
            false
        };
        kill(&self.provider, &self.sandbox_id).await?;
        if tracked {
            match clear_task_sandbox(
                &self.pool,
                self.task_id,
                self.user_id,
                self.sandbox_id.as_str(),
            )
            .await
            {
                Ok(()) | Err(TaskSandboxRepositoryError::AttemptUnavailable) => {}
                Err(error) => return Err(error.into()),
            }
        }
        Ok(())
    }
}

async fn kill(
    provider: &DirectE2bProvider,
    sandbox_id: &SandboxId,
) -> Result<(), TaskSandboxError> {
    match provider.kill_sandbox(sandbox_id).await {
        Ok(_) | Err(E2bError::NotFound { .. }) => Ok(()),
        Err(source) => Err(TaskSandboxError::ProviderOperation {
            operation: "kill_sandbox",
            source,
        }),
    }
}

fn schedule_cleanup(cleanup: SandboxCleanup) {
    let Ok(runtime) = tokio::runtime::Handle::try_current() else {
        tracing::error!(sandbox_id = %cleanup.sandbox_id, "cannot schedule task sandbox cleanup outside Tokio");
        return;
    };
    runtime.spawn(async move {
        if let Err(error) = cleanup.run().await {
            tracing::error!(sandbox_id = %cleanup.sandbox_id, error = %error, "failed to destroy task sandbox");
        }
    });
}

fn require_time(deadline: Instant) -> Result<(), TaskSandboxError> {
    if deadline <= Instant::now() {
        Err(TaskSandboxError::Deadline)
    } else {
        Ok(())
    }
}

fn validate_identity(value: &str, label: &str) -> Result<(), TaskSandboxError> {
    if value.is_empty()
        || value.trim() != value
        || value.len() > 255
        || value.chars().any(char::is_control)
    {
        return Err(TaskSandboxError::Configuration(format!(
            "{label} must be non-empty, unpadded, and at most 255 bytes"
        )));
    }
    Ok(())
}

fn truncate(value: &str, maximum: usize) -> String {
    value.chars().take(maximum).collect()
}

#[derive(Debug, Error)]
pub enum TaskSandboxError {
    #[error("invalid task sandbox configuration: {0}")]
    Configuration(String),
    #[error("task sandbox operation {operation} failed")]
    ProviderOperation {
        operation: &'static str,
        #[source]
        source: E2bError,
    },
    #[error("task sandbox hardening failed: {0}")]
    HardeningFailed(String),
    #[error("task sandbox deadline exceeded")]
    Deadline,
    #[error("task sandbox operation was cancelled")]
    Cancelled,
    #[error(transparent)]
    Provider(#[from] E2bError),
    #[error(transparent)]
    Repository(#[from] TaskSandboxRepositoryError),
}

#[cfg(test)]
mod tests {
    use super::{TaskSandboxError, validate_identity};

    #[test]
    fn task_sandbox_identity_rejects_ambiguous_metadata() {
        assert!(validate_identity("newsly-agent", "template").is_ok());
        assert!(matches!(
            validate_identity(" newsly-agent", "template"),
            Err(TaskSandboxError::Configuration(_))
        ));
        assert!(validate_identity("line\nbreak", "template").is_err());
        assert!(validate_identity(&"x".repeat(256), "template").is_err());
    }
}
