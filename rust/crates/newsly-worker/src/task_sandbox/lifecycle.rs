use std::collections::BTreeMap;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

use chrono::Utc;
use newsly_db::{
    NewTaskSandboxSession, TaskSandboxCleanupCandidate, TaskSandboxEnd, TaskSandboxProviderInfo,
    TaskSandboxRepositoryError, attach_task_sandbox, begin_task_sandbox_session,
    finalize_task_sandbox_session, find_recorded_task_sandbox,
    list_task_sandbox_cleanup_candidates, mark_task_sandbox_cleanup_required,
};
use newsly_e2b::{
    CommandRequest, DeliveryState, DirectE2bProvider, E2bError, ExecutionTag, ExitStatus,
    NetworkPolicy, OutputLimits, SandboxHandle, SandboxId, SandboxInfo, SandboxProvider,
    SandboxRequest, SandboxUser, VmBootstrapProvider, VmCapabilities,
};
use sqlx::PgPool;
use thiserror::Error;
use tokio::time::Instant;
use tokio_util::sync::CancellationToken;
use uuid::Uuid;

static PERIODIC_CLEANUP_STARTED: AtomicBool = AtomicBool::new(false);
const CLEANUP_INTERVAL: Duration = Duration::from_secs(60);

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
        let owner = Self {
            pool,
            provider,
            config,
            cleanup_sweep_active: Arc::new(AtomicBool::new(false)),
        };
        owner.start_periodic_cleanup();
        Ok(owner)
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
            let sandbox_id = SandboxId::parse(previous.sandbox_id)?;
            if let Some(session_id) = previous.session_id {
                let mut cleanup = SandboxCleanup::recorded(
                    Arc::clone(&self.provider),
                    self.pool.clone(),
                    session_id,
                    task_id,
                    user_id,
                    sandbox_id,
                );
                cleanup.run().await?;
            } else {
                kill(&self.provider, &sandbox_id).await?;
            }
        }
        let timeout = u32::try_from(self.config.sandbox_timeout.as_secs()).map_err(|_| {
            TaskSandboxError::Configuration("sandbox timeout is too large".to_owned())
        })?;
        let session_id = Uuid::new_v4();
        let requested_at = Utc::now();
        begin_task_sandbox_session(
            &self.pool,
            &NewTaskSandboxSession {
                id: session_id,
                task_id,
                user_id,
                feature,
                template_id: &self.config.template_id,
                template_revision: &self.config.template_revision,
                timeout_seconds: i32::try_from(timeout).map_err(|_| {
                    TaskSandboxError::Configuration("sandbox timeout is too large".to_owned())
                })?,
                requested_at,
            },
        )
        .await?;
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
                ("newsly_session_id".to_owned(), session_id.to_string()),
                (
                    "template_revision".to_owned(),
                    self.config.template_revision.clone(),
                ),
                ("reuse_scope".to_owned(), "task_attempt".to_owned()),
            ]),
            env_vars: BTreeMap::new(),
            network: Some(NetworkPolicy::deny_all()),
        };
        // The durable session and its metadata key let the periodic reconciler recover a sandbox
        // even if this future is dropped after E2B accepts the create request.
        let sandbox = match self.provider.create_sandbox(&request).await {
            Ok(sandbox) => sandbox,
            Err(source) => {
                if source.delivery_state() == DeliveryState::NotDelivered {
                    let _ = finalize_task_sandbox_session(
                        &self.pool,
                        session_id,
                        task_id,
                        user_id,
                        None,
                        TaskSandboxEnd::NotDelivered {
                            observed_at: Utc::now(),
                        },
                    )
                    .await;
                }
                return Err(source.into());
            }
        };
        let provider_info = match self.provider.get_sandbox_info(&sandbox.sandbox_id).await {
            Ok(info) => provider_info(&info),
            Err(error) => {
                tracing::warn!(
                    session_id = %session_id,
                    sandbox_id = %sandbox.sandbox_id,
                    error = %error,
                    "E2B sandbox resource details are unavailable; runtime cost will remain unpriced"
                );
                TaskSandboxProviderInfo {
                    sandbox_id: sandbox.sandbox_id.as_str().to_owned(),
                    started_at: None,
                    end_at: None,
                    cpu_count: None,
                    memory_mb: None,
                }
            }
        };
        let mut pending = PendingSandbox::new(
            Arc::clone(&self.provider),
            self.pool.clone(),
            session_id,
            task_id,
            user_id,
            sandbox,
        );
        let task_attached =
            attach_task_sandbox(&self.pool, session_id, task_id, user_id, &provider_info).await?;
        if !task_attached || cancellation.is_cancelled() {
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
                session_id,
                task_id,
                user_id,
                sandbox_id,
            } = candidate;
            if let Err(error) = self
                .reconcile_cleanup_candidate(session_id, task_id, user_id, sandbox_id)
                .await
            {
                tracing::warn!(session_id = %session_id, task_id, user_id, error = %error, "pending task sandbox cleanup failed");
            }
        }
    }

    async fn reconcile_cleanup_candidate(
        &self,
        session_id: Uuid,
        task_id: i64,
        user_id: i64,
        sandbox_id: Option<String>,
    ) -> Result<(), TaskSandboxError> {
        let sandbox_id = if let Some(sandbox_id) = sandbox_id {
            SandboxId::parse(sandbox_id)?
        } else {
            let matches = self
                .provider
                .list_sandboxes_by_metadata("newsly_session_id", &session_id.to_string(), 2)
                .await
                .map_err(|source| TaskSandboxError::ProviderOperation {
                    operation: "list_sandboxes_by_session",
                    source,
                })?;
            let Some(info) = matches.first() else {
                finalize_task_sandbox_session(
                    &self.pool,
                    session_id,
                    task_id,
                    user_id,
                    None,
                    TaskSandboxEnd::TimeoutBound {
                        observed_at: Utc::now(),
                    },
                )
                .await?;
                return Ok(());
            };
            if matches.len() != 1 {
                return Err(TaskSandboxError::Configuration(format!(
                    "session {session_id} matched multiple E2B sandboxes"
                )));
            }
            let provider_info = provider_info(info);
            attach_task_sandbox(&self.pool, session_id, task_id, user_id, &provider_info).await?;
            info.sandbox_id.clone()
        };
        let mut cleanup = SandboxCleanup::recorded(
            Arc::clone(&self.provider),
            self.pool.clone(),
            session_id,
            task_id,
            user_id,
            sandbox_id,
        );
        cleanup.run().await
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

    fn start_periodic_cleanup(&self) {
        if PERIODIC_CLEANUP_STARTED
            .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .is_err()
        {
            return;
        }
        let Ok(runtime) = tokio::runtime::Handle::try_current() else {
            PERIODIC_CLEANUP_STARTED.store(false, Ordering::Release);
            return;
        };
        let owner = self.clone();
        runtime.spawn(async move {
            let mut interval = tokio::time::interval(CLEANUP_INTERVAL);
            loop {
                interval.tick().await;
                owner.schedule_pending_cleanups();
            }
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
        session_id: Uuid,
        task_id: i64,
        user_id: i64,
        sandbox: SandboxHandle,
    ) -> Self {
        let cleanup = SandboxCleanup::recorded(
            provider,
            pool,
            session_id,
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

    async fn cleanup(&mut self) -> Result<(), TaskSandboxError> {
        let mut cleanup = self
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
        let Some(mut cleanup) = self.cleanup.take() else {
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
    session_id: Uuid,
    task_id: i64,
    user_id: i64,
    sandbox_id: SandboxId,
    observed_end: Option<TaskSandboxEnd>,
}

impl SandboxCleanup {
    fn recorded(
        provider: Arc<DirectE2bProvider>,
        pool: PgPool,
        session_id: Uuid,
        task_id: i64,
        user_id: i64,
        sandbox_id: SandboxId,
    ) -> Self {
        Self {
            provider,
            pool,
            session_id,
            task_id,
            user_id,
            sandbox_id,
            observed_end: None,
        }
    }

    async fn run(&mut self) -> Result<(), TaskSandboxError> {
        match mark_task_sandbox_cleanup_required(
            &self.pool,
            self.session_id,
            self.task_id,
            self.user_id,
            self.sandbox_id.as_str(),
        )
        .await
        {
            Ok(()) => {}
            Err(TaskSandboxRepositoryError::AttemptUnavailable) => return Ok(()),
            Err(error) => return Err(error.into()),
        }
        let end = if let Some(end) = self.observed_end {
            end
        } else {
            let end = kill(&self.provider, &self.sandbox_id).await?;
            self.observed_end = Some(end);
            end
        };
        finalize_task_sandbox_session(
            &self.pool,
            self.session_id,
            self.task_id,
            self.user_id,
            Some(self.sandbox_id.as_str()),
            end,
        )
        .await?;
        Ok(())
    }
}

async fn kill(
    provider: &DirectE2bProvider,
    sandbox_id: &SandboxId,
) -> Result<TaskSandboxEnd, TaskSandboxError> {
    match provider.kill_sandbox(sandbox_id).await {
        Ok(true) => Ok(TaskSandboxEnd::Confirmed {
            observed_at: Utc::now(),
        }),
        Ok(false) | Err(E2bError::NotFound { .. }) => Ok(TaskSandboxEnd::Missing {
            observed_at: Utc::now(),
        }),
        Err(source) => Err(TaskSandboxError::ProviderOperation {
            operation: "kill_sandbox",
            source,
        }),
    }
}

fn provider_info(info: &SandboxInfo) -> TaskSandboxProviderInfo {
    TaskSandboxProviderInfo {
        sandbox_id: info.sandbox_id.as_str().to_owned(),
        started_at: Some(info.started_at),
        end_at: Some(info.end_at),
        cpu_count: i32::try_from(info.cpu_count).ok(),
        memory_mb: i32::try_from(info.memory_mb).ok(),
    }
}

fn schedule_cleanup(mut cleanup: SandboxCleanup) {
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
