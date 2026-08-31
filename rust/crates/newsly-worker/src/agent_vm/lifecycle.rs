use std::collections::{BTreeMap, HashMap};
use std::path::PathBuf;
use std::sync::Arc;
use std::time::{Duration, SystemTime};

use newsly_db::{
    AgentVmNamespaceLeaseGrant, AgentVmPersistentState, AgentVmRepositoryError,
    AgentVmStateReplacement, acquire_agent_vm_namespace_lease, prepare_agent_corpus_transfer,
    release_agent_vm_namespace_lease, renew_agent_vm_namespace_lease,
    replace_agent_vm_persistent_state,
};
use newsly_e2b::{
    CommandLeaseState, CommandRequest, CorpusHydrationResult, DirectE2bProvider, E2bError,
    ExecutionTag, ExitStatus, NamespaceLease, NetworkPolicy, OutputLimits, RuntimeOwner,
    SandboxHandle, SandboxId, SandboxProvider, SandboxRequest, SandboxSession, SandboxUser,
    SnapshotId, VmBootstrapProvider, VmCapabilities, VmNamespace,
};
use sqlx::PgPool;
use thiserror::Error;
use tokio::sync::RwLock;
use tokio::time::Instant;
use tokio_util::sync::CancellationToken;
use uuid::Uuid;

use super::corpus::{
    AgentCorpusArchiveError, MaterializedAgentCorpusArchive, materialize_agent_corpus_archive,
};

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

#[derive(Debug, Clone)]
pub struct AgentVmLifecycleConfig {
    pub template_id: String,
    pub template_revision: String,
    pub sandbox_timeout: Duration,
    pub namespace_lease_duration: Duration,
    pub agent_data_mirror_root: PathBuf,
}

impl AgentVmLifecycleConfig {
    pub fn validate(&self) -> Result<(), AgentVmLifecycleError> {
        validate_identity(&self.template_id, "template id")?;
        validate_identity(&self.template_revision, "template revision")?;
        if self.sandbox_timeout.is_zero()
            || self.namespace_lease_duration <= self.sandbox_timeout
            || self.namespace_lease_duration.as_secs() > 7_200
        {
            return Err(AgentVmLifecycleError::Configuration(
                "namespace lease must be longer than the positive sandbox deadline and at most two hours"
                    .to_owned(),
            ));
        }
        if !self.agent_data_mirror_root.is_absolute()
            || self.agent_data_mirror_root == std::path::Path::new("/")
        {
            return Err(AgentVmLifecycleError::Configuration(
                "AGENT_DATA_MIRROR_ROOT must be an absolute non-root directory".to_owned(),
            ));
        }
        Ok(())
    }
}

#[derive(Debug, Clone)]
pub struct AgentVmLifecycle {
    pool: PgPool,
    provider: Arc<DirectE2bProvider>,
    config: AgentVmLifecycleConfig,
    capabilities: Arc<RwLock<HashMap<String, VmCapabilities>>>,
}

impl AgentVmLifecycle {
    pub fn new(
        pool: PgPool,
        provider: Arc<DirectE2bProvider>,
        config: AgentVmLifecycleConfig,
    ) -> Result<Self, AgentVmLifecycleError> {
        config.validate()?;
        Ok(Self {
            pool,
            provider,
            config,
            capabilities: Arc::new(RwLock::new(HashMap::new())),
        })
    }

    #[allow(clippy::too_many_arguments)]
    pub async fn acquire_for_task(
        &self,
        user_id: i64,
        vm_namespace: &str,
        task_id: i64,
        feature: &str,
        absolute_deadline: Instant,
        cancellation: CancellationToken,
    ) -> Result<AcquiredAgentVmSession, AgentVmLifecycleError> {
        if task_id <= 0 || feature.trim().is_empty() || feature.len() > 255 {
            return Err(AgentVmLifecycleError::Configuration(
                "agent VM task identity is invalid".to_owned(),
            ));
        }
        let holder = format!("{feature}:{task_id}:{}", Uuid::new_v4().simple());
        let mut grant = acquire_agent_vm_namespace_lease(
            &self.pool,
            user_id,
            vm_namespace,
            &holder,
            Some(task_id),
            &self.config.template_revision,
            self.config.namespace_lease_duration,
        )
        .await?;

        let acquired = self
            .acquire_external(
                user_id,
                task_id,
                feature,
                absolute_deadline,
                cancellation,
                &grant,
            )
            .await;
        let acquired = match acquired {
            Ok(acquired) => acquired,
            Err(error) => {
                if let Err(release_error) =
                    release_agent_vm_namespace_lease(&self.pool, &grant).await
                {
                    tracing::error!(
                        vm_namespace = %grant.vm_namespace,
                        error = %release_error,
                        "failed to release agent VM namespace after acquisition error"
                    );
                }
                return Err(error);
            }
        };

        // Refill the complete task window after acquisition/hydration. The token and ownership
        // generation stay unchanged, while the E2B-facing immutable lease receives the new expiry.
        grant.expires_at = match renew_agent_vm_namespace_lease(
            &self.pool,
            &grant,
            self.config.namespace_lease_duration,
        )
        .await
        {
            Ok(expires_at) => expires_at,
            Err(error) => {
                if let Err(release_error) =
                    release_agent_vm_namespace_lease(&self.pool, &grant).await
                {
                    tracing::error!(
                        vm_namespace = %grant.vm_namespace,
                        error = %release_error,
                        "failed to release agent VM namespace after renewal error"
                    );
                }
                return Err(error.into());
            }
        };
        let session = match sandbox_session(&grant, acquired.sandbox.clone()) {
            Ok(session) => session,
            Err(error) => {
                if let Err(release_error) =
                    release_agent_vm_namespace_lease(&self.pool, &grant).await
                {
                    tracing::error!(
                        vm_namespace = %grant.vm_namespace,
                        error = %release_error,
                        "failed to release agent VM namespace after session validation error"
                    );
                }
                return Err(error);
            }
        };
        Ok(AcquiredAgentVmSession {
            session,
            created: acquired.created,
            restored_snapshot: acquired.restored_snapshot,
            hydration: acquired.hydration,
            capabilities: acquired.capabilities,
            template_revision: self.config.template_revision.clone(),
            pool: self.pool.clone(),
            grant: Some(grant),
        })
    }

    #[allow(clippy::too_many_arguments)]
    async fn acquire_external(
        &self,
        user_id: i64,
        task_id: i64,
        feature: &str,
        deadline: Instant,
        cancellation: CancellationToken,
        grant: &AgentVmNamespaceLeaseGrant,
    ) -> Result<ExternalAcquisition, AgentVmLifecycleError> {
        require_time(deadline)?;
        let expected = grant.persistent_state.clone();
        let mut state = expected.clone();
        self.retire_stale_template_state(&mut state, deadline, cancellation.child_token())
            .await?;

        let mut candidate = self
            .resolve_candidate(
                user_id,
                task_id,
                feature,
                &mut state,
                deadline,
                cancellation.child_token(),
            )
            .await?;
        let mut created_snapshot: Option<SnapshotId> = None;

        let prepared = self
            .prepare_candidate(
                user_id,
                grant,
                &candidate,
                deadline,
                cancellation.child_token(),
            )
            .await;
        let (capabilities, hydration) = match prepared {
            Ok(result) => result,
            Err(AgentVmLifecycleError::Repository(AgentVmRepositoryError::RemoteCorpusAhead {
                ..
            })) => {
                // A remote-ahead manifest is corrupt. Destroy both its live sandbox and any clean
                // checkpoint that could recreate it, then retry once from the canonical template.
                self.kill_sandbox(&candidate.sandbox.sandbox_id).await?;
                state.sandbox_id = None;
                state.sandbox_template_revision = None;
                if let Some(snapshot_id) = state.snapshot_id.take() {
                    self.delete_snapshot(&SnapshotId::parse(snapshot_id)?)
                        .await?;
                }
                state.snapshot_template_revision = None;
                let replacement = self
                    .create_canonical(
                        user_id,
                        task_id,
                        feature,
                        deadline,
                        cancellation.child_token(),
                    )
                    .await?;
                let prepared = self
                    .prepare_candidate(
                        user_id,
                        grant,
                        &replacement,
                        deadline,
                        cancellation.child_token(),
                    )
                    .await;
                match prepared {
                    Ok(prepared) => {
                        candidate = replacement;
                        prepared
                    }
                    Err(error) => {
                        self.cleanup_unpublished(&replacement.sandbox.sandbox_id, None)
                            .await;
                        return Err(error);
                    }
                }
            }
            Err(error) => {
                if candidate.created {
                    self.cleanup_unpublished(&candidate.sandbox.sandbox_id, None)
                        .await;
                }
                return Err(error);
            }
        };

        let should_snapshot =
            candidate.canonical_create && state.snapshot_id.is_none() && user_id > 0;
        if should_snapshot {
            match self
                .create_recovery_snapshot(
                    user_id,
                    &candidate.sandbox,
                    deadline,
                    cancellation.child_token(),
                )
                .await
            {
                Ok((snapshot_id, reconnected)) => {
                    candidate.sandbox = reconnected;
                    if let Some(snapshot_id) = snapshot_id {
                        created_snapshot = Some(snapshot_id.clone());
                        state.snapshot_id = Some(snapshot_id.as_str().to_owned());
                        state.snapshot_template_revision =
                            Some(self.config.template_revision.clone());
                    }
                }
                Err(error) => {
                    self.cleanup_unpublished(
                        &candidate.sandbox.sandbox_id,
                        created_snapshot.as_ref(),
                    )
                    .await;
                    return Err(error);
                }
            }
        }

        state.sandbox_id = Some(candidate.sandbox.sandbox_id.as_str().to_owned());
        state.sandbox_template_revision = Some(self.config.template_revision.clone());
        let replacement = AgentVmStateReplacement {
            sandbox_id: state.sandbox_id.clone(),
            sandbox_template_revision: state.sandbox_template_revision.clone(),
            snapshot_id: state.snapshot_id.clone(),
            snapshot_template_revision: state.snapshot_template_revision.clone(),
        };
        if let Err(error) =
            replace_agent_vm_persistent_state(&self.pool, user_id, grant, &expected, &replacement)
                .await
        {
            if candidate.created {
                self.cleanup_unpublished(&candidate.sandbox.sandbox_id, created_snapshot.as_ref())
                    .await;
            }
            return Err(error.into());
        }

        Ok(ExternalAcquisition {
            sandbox: candidate.sandbox,
            created: candidate.created,
            restored_snapshot: candidate.restored_snapshot,
            hydration,
            capabilities,
        })
    }

    async fn retire_stale_template_state(
        &self,
        state: &mut AgentVmPersistentState,
        deadline: Instant,
        cancellation: CancellationToken,
    ) -> Result<(), AgentVmLifecycleError> {
        require_time(deadline)?;
        if state.sandbox_id.is_some()
            && state.sandbox_template_revision.as_deref()
                != Some(self.config.template_revision.as_str())
        {
            let value = state.sandbox_id.take().ok_or_else(|| {
                AgentVmLifecycleError::Configuration(
                    "stale sandbox state disappeared during retirement".to_owned(),
                )
            })?;
            let id = SandboxId::parse(value)?;
            match cancelled(cancellation.child_token(), self.provider.kill_sandbox(&id)).await? {
                Ok(_) | Err(E2bError::NotFound { .. }) => {}
                Err(error) => return Err(error.into()),
            }
            state.sandbox_template_revision = None;
        }
        if state.snapshot_id.is_some()
            && state.snapshot_template_revision.as_deref()
                != Some(self.config.template_revision.as_str())
        {
            let value = state.snapshot_id.take().ok_or_else(|| {
                AgentVmLifecycleError::Configuration(
                    "stale snapshot state disappeared during retirement".to_owned(),
                )
            })?;
            let id = SnapshotId::parse(value)?;
            match cancelled(cancellation, self.provider.delete_snapshot(&id)).await? {
                Ok(_) | Err(E2bError::NotFound { .. }) => {}
                Err(error) => return Err(error.into()),
            }
            state.snapshot_template_revision = None;
        }
        Ok(())
    }

    #[allow(clippy::too_many_arguments)]
    async fn resolve_candidate(
        &self,
        user_id: i64,
        task_id: i64,
        feature: &str,
        state: &mut AgentVmPersistentState,
        deadline: Instant,
        cancellation: CancellationToken,
    ) -> Result<SandboxCandidate, AgentVmLifecycleError> {
        if state.sandbox_template_revision.as_deref()
            == Some(self.config.template_revision.as_str())
            && let Some(value) = &state.sandbox_id
        {
            let id = SandboxId::parse(value.clone())?;
            let timeout = remaining(deadline)?;
            match cancelled(
                cancellation.child_token(),
                self.provider.connect_sandbox(&id, timeout),
            )
            .await?
            {
                Ok(sandbox) => {
                    return Ok(SandboxCandidate {
                        sandbox,
                        created: false,
                        canonical_create: false,
                        restored_snapshot: false,
                    });
                }
                Err(E2bError::NotFound { .. }) => {
                    state.sandbox_id = None;
                    state.sandbox_template_revision = None;
                }
                Err(error) => return Err(error.into()),
            }
        }

        if state.snapshot_template_revision.as_deref()
            == Some(self.config.template_revision.as_str())
            && let Some(snapshot_id) = &state.snapshot_id
        {
            match self
                .create(
                    snapshot_id,
                    user_id,
                    task_id,
                    feature,
                    false,
                    deadline,
                    cancellation.child_token(),
                )
                .await
            {
                Ok(sandbox) => {
                    return Ok(SandboxCandidate {
                        sandbox,
                        created: true,
                        canonical_create: false,
                        restored_snapshot: true,
                    });
                }
                Err(AgentVmLifecycleError::E2b(E2bError::NotFound { .. })) => {
                    state.snapshot_id = None;
                    state.snapshot_template_revision = None;
                }
                Err(error) => return Err(error),
            }
        }
        self.create_canonical(user_id, task_id, feature, deadline, cancellation)
            .await
    }

    async fn create_canonical(
        &self,
        user_id: i64,
        task_id: i64,
        feature: &str,
        deadline: Instant,
        cancellation: CancellationToken,
    ) -> Result<SandboxCandidate, AgentVmLifecycleError> {
        let sandbox = self
            .create(
                &self.config.template_id,
                user_id,
                task_id,
                feature,
                true,
                deadline,
                cancellation.child_token(),
            )
            .await?;
        if let Err(error) = self
            .harden(&sandbox, deadline, cancellation.child_token())
            .await
        {
            self.cleanup_unpublished(&sandbox.sandbox_id, None).await;
            return Err(error);
        }
        Ok(SandboxCandidate {
            sandbox,
            created: true,
            canonical_create: true,
            restored_snapshot: false,
        })
    }

    #[allow(clippy::too_many_arguments)]
    async fn create(
        &self,
        template_id: &str,
        user_id: i64,
        task_id: i64,
        feature: &str,
        canonical: bool,
        deadline: Instant,
        cancellation: CancellationToken,
    ) -> Result<SandboxHandle, AgentVmLifecycleError> {
        require_time(deadline)?;
        let timeout = u32::try_from(self.config.sandbox_timeout.as_secs()).map_err(|_| {
            AgentVmLifecycleError::Configuration("sandbox timeout is too large".to_owned())
        })?;
        let request = SandboxRequest {
            template_id: template_id.to_owned(),
            timeout,
            auto_pause: true,
            auto_pause_memory: true,
            secure: true,
            allow_internet_access: false,
            metadata: BTreeMap::from([
                ("feature".to_owned(), feature.to_owned()),
                ("user_id".to_owned(), user_id.to_string()),
                ("vm_namespace".to_owned(), format!("user:{user_id}")),
                ("llm_task_id".to_owned(), task_id.to_string()),
                (
                    "template_revision".to_owned(),
                    self.config.template_revision.clone(),
                ),
                ("canonical_create".to_owned(), canonical.to_string()),
            ]),
            env_vars: BTreeMap::from([("NEWSLY_USER_ID".to_owned(), user_id.to_string())]),
            network: Some(NetworkPolicy::deny_all()),
        };
        let created = cancelled(cancellation, self.provider.create_sandbox(&request)).await?;
        created.map_err(|source| AgentVmLifecycleError::ProviderOperation {
            operation: "create_sandbox",
            source,
        })
    }

    async fn harden(
        &self,
        sandbox: &SandboxHandle,
        deadline: Instant,
        cancellation: CancellationToken,
    ) -> Result<(), AgentVmLifecycleError> {
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
            .map_err(|source| AgentVmLifecycleError::ProviderOperation {
                operation: "harden_default_user_start",
                source,
            })?;
        let result = stream.collect_result().await.map_err(|source| {
            AgentVmLifecycleError::ProviderOperation {
                operation: "harden_default_user_stream",
                source,
            }
        })?;
        if result.status != ExitStatus::Exited || result.exit_code != 0 {
            return Err(AgentVmLifecycleError::HardeningFailed(truncate(
                &format!(
                    "status={:?} exit_code={} error={:?} stdout={:?} stderr={:?}",
                    result.status,
                    result.exit_code,
                    result.error,
                    result.output.stdout,
                    result.output.stderr
                ),
                1_000,
            )));
        }
        Ok(())
    }

    async fn prepare_candidate(
        &self,
        user_id: i64,
        grant: &AgentVmNamespaceLeaseGrant,
        candidate: &SandboxCandidate,
        deadline: Instant,
        cancellation: CancellationToken,
    ) -> Result<(VmCapabilities, Option<CorpusHydrationResult>), AgentVmLifecycleError> {
        let session = sandbox_session(grant, candidate.sandbox.clone())?;
        let capabilities = if let Some(cached) = self
            .capabilities
            .read()
            .await
            .get(&self.config.template_revision)
            .cloned()
        {
            cached
        } else {
            let probed = self
                .provider
                .probe_vm_capabilities(&session, deadline, cancellation.child_token())
                .await
                .map_err(|source| AgentVmLifecycleError::ProviderOperation {
                    operation: "probe_vm_capabilities",
                    source,
                })?;
            self.capabilities
                .write()
                .await
                .insert(self.config.template_revision.clone(), probed.clone());
            probed
        };
        let expected_user_id = u64::try_from(user_id).map_err(|_| {
            AgentVmLifecycleError::Configuration(
                "persistent agent VM user id must be positive".to_owned(),
            )
        })?;
        let remote = self
            .provider
            .inspect_remote_corpus(
                &session,
                expected_user_id,
                deadline,
                cancellation.child_token(),
            )
            .await
            .map_err(|source| AgentVmLifecycleError::ProviderOperation {
                operation: "inspect_remote_corpus",
                source,
            })?;
        let prepared = prepare_agent_corpus_transfer(
            &self.pool,
            user_id,
            grant,
            remote.revision,
            remote.force_full,
        )
        .await?;
        let hydration = match prepared {
            None => None,
            Some(prepared) => {
                let materialized = materialize_agent_corpus_archive(
                    self.config.agent_data_mirror_root.clone(),
                    prepared,
                )
                .await?;
                Some(
                    self.install_materialized(&session, materialized, deadline, cancellation)
                        .await?,
                )
            }
        };
        Ok((capabilities, hydration))
    }

    async fn install_materialized(
        &self,
        session: &SandboxSession,
        archive: MaterializedAgentCorpusArchive,
        deadline: Instant,
        cancellation: CancellationToken,
    ) -> Result<CorpusHydrationResult, AgentVmLifecycleError> {
        let transfer = archive.transfer();
        let source = archive.into_stream().await?;
        self.provider
            .install_corpus_archive(session, transfer, source, deadline, cancellation)
            .await
            .map_err(|source| AgentVmLifecycleError::ProviderOperation {
                operation: "install_corpus_archive",
                source,
            })
    }

    async fn create_recovery_snapshot(
        &self,
        user_id: i64,
        sandbox: &SandboxHandle,
        deadline: Instant,
        cancellation: CancellationToken,
    ) -> Result<(Option<SnapshotId>, SandboxHandle), AgentVmLifecycleError> {
        require_time(deadline)?;
        let name = format!(
            "newsly-user-{user_id}-{}",
            self.config
                .template_revision
                .chars()
                .take(64)
                .collect::<String>()
        );
        let snapshot = match cancelled(
            cancellation.child_token(),
            self.provider.create_snapshot(
                &sandbox.sandbox_id,
                Some(&name),
                CommandLeaseState::Idle,
            ),
        )
        .await?
        {
            Ok(snapshot) => snapshot,
            Err(error) => {
                tracing::warn!(
                    sandbox_id = %sandbox.sandbox_id,
                    error = %error,
                    "unable to create clean agent VM recovery snapshot"
                );
                let reconnected = cancelled(
                    cancellation,
                    self.provider
                        .connect_sandbox(&sandbox.sandbox_id, remaining(deadline)?),
                )
                .await??;
                return Ok((None, reconnected));
            }
        };
        let reconnected = match cancelled(
            cancellation,
            self.provider
                .connect_sandbox(&sandbox.sandbox_id, remaining(deadline)?),
        )
        .await?
        {
            Ok(sandbox) => sandbox,
            Err(error) => {
                self.delete_snapshot(&snapshot.snapshot_id).await?;
                return Err(error.into());
            }
        };
        Ok((Some(snapshot.snapshot_id), reconnected))
    }

    async fn kill_sandbox(&self, sandbox_id: &SandboxId) -> Result<(), AgentVmLifecycleError> {
        match self.provider.kill_sandbox(sandbox_id).await {
            Ok(_) | Err(E2bError::NotFound { .. }) => Ok(()),
            Err(error) => Err(error.into()),
        }
    }

    async fn delete_snapshot(&self, snapshot_id: &SnapshotId) -> Result<(), AgentVmLifecycleError> {
        match self.provider.delete_snapshot(snapshot_id).await {
            Ok(_) | Err(E2bError::NotFound { .. }) => Ok(()),
            Err(error) => Err(error.into()),
        }
    }

    async fn cleanup_unpublished(&self, sandbox_id: &SandboxId, snapshot_id: Option<&SnapshotId>) {
        if let Some(snapshot_id) = snapshot_id
            && let Err(error) = self.provider.delete_snapshot(snapshot_id).await
        {
            tracing::warn!(snapshot_id = %snapshot_id, error = %error, "failed to delete unpublished agent VM snapshot");
        }
        if let Err(error) = self.provider.kill_sandbox(sandbox_id).await {
            tracing::warn!(sandbox_id = %sandbox_id, error = %error, "failed to kill unpublished agent VM sandbox");
        }
    }
}

#[derive(Debug)]
struct SandboxCandidate {
    sandbox: SandboxHandle,
    created: bool,
    canonical_create: bool,
    restored_snapshot: bool,
}

#[derive(Debug)]
struct ExternalAcquisition {
    sandbox: SandboxHandle,
    created: bool,
    restored_snapshot: bool,
    hydration: Option<CorpusHydrationResult>,
    capabilities: VmCapabilities,
}

#[derive(Debug)]
pub struct AcquiredAgentVmSession {
    pub session: SandboxSession,
    pub created: bool,
    pub restored_snapshot: bool,
    pub hydration: Option<CorpusHydrationResult>,
    pub capabilities: VmCapabilities,
    pub template_revision: String,
    pool: PgPool,
    grant: Option<AgentVmNamespaceLeaseGrant>,
}

impl AcquiredAgentVmSession {
    pub async fn release(mut self) -> Result<(), AgentVmLifecycleError> {
        let Some(grant) = self.grant.take() else {
            return Ok(());
        };
        if release_agent_vm_namespace_lease(&self.pool, &grant).await? {
            Ok(())
        } else {
            Err(AgentVmLifecycleError::LeaseReleaseLost)
        }
    }
}

impl Drop for AcquiredAgentVmSession {
    fn drop(&mut self) {
        let Some(grant) = self.grant.take() else {
            return;
        };
        let pool = self.pool.clone();
        let Ok(runtime) = tokio::runtime::Handle::try_current() else {
            tracing::error!(
                vm_namespace = %grant.vm_namespace,
                "cannot schedule agent VM namespace release outside Tokio runtime"
            );
            return;
        };
        runtime.spawn(async move {
            match release_agent_vm_namespace_lease(&pool, &grant).await {
                Ok(true) => {}
                Ok(false) => tracing::error!(
                    vm_namespace = %grant.vm_namespace,
                    "dropped agent VM namespace no longer held its exact lease"
                ),
                Err(error) => {
                    tracing::error!(
                        vm_namespace = %grant.vm_namespace,
                        error = %error,
                        "failed to release dropped agent VM namespace lease"
                    );
                }
            }
        });
    }
}

fn sandbox_session(
    grant: &AgentVmNamespaceLeaseGrant,
    sandbox: SandboxHandle,
) -> Result<SandboxSession, AgentVmLifecycleError> {
    let lease = NamespaceLease {
        namespace: VmNamespace::parse(grant.vm_namespace.clone())?,
        runtime_owner: RuntimeOwner::Rust,
        ownership_version: grant.ownership_version,
        token: grant.token,
        expires_at: SystemTime::from(grant.expires_at),
        template_revision: grant.template_revision.clone(),
    };
    lease.validate()?;
    Ok(SandboxSession { sandbox, lease })
}

async fn cancelled<F, T>(
    cancellation: CancellationToken,
    future: F,
) -> Result<T, AgentVmLifecycleError>
where
    F: std::future::Future<Output = T>,
{
    tokio::select! {
        () = cancellation.cancelled() => Err(AgentVmLifecycleError::Cancelled),
        value = future => Ok(value),
    }
}

fn remaining(deadline: Instant) -> Result<Duration, AgentVmLifecycleError> {
    deadline
        .checked_duration_since(Instant::now())
        .filter(|duration| !duration.is_zero())
        .ok_or(AgentVmLifecycleError::Deadline)
}

fn require_time(deadline: Instant) -> Result<(), AgentVmLifecycleError> {
    remaining(deadline).map(|_| ())
}

fn validate_identity(value: &str, label: &str) -> Result<(), AgentVmLifecycleError> {
    if value.is_empty()
        || value.trim() != value
        || value.len() > 255
        || value.chars().any(char::is_control)
    {
        return Err(AgentVmLifecycleError::Configuration(format!(
            "{label} must be non-empty, unpadded, and at most 255 bytes"
        )));
    }
    Ok(())
}

fn truncate(value: &str, maximum: usize) -> String {
    value.chars().take(maximum).collect()
}

#[derive(Debug, Error)]
pub enum AgentVmLifecycleError {
    #[error("agent VM lifecycle configuration is invalid: {0}")]
    Configuration(String),
    #[error("agent VM operation reached its deadline")]
    Deadline,
    #[error("agent VM operation was cancelled")]
    Cancelled,
    #[error("agent VM hardening failed: {0}")]
    HardeningFailed(String),
    #[error("agent VM namespace release lost its exact lease")]
    LeaseReleaseLost,
    #[error("agent VM repository failed")]
    Repository(#[from] AgentVmRepositoryError),
    #[error("agent VM corpus archive failed")]
    CorpusArchive(#[from] AgentCorpusArchiveError),
    #[error("agent VM provider failed: {0}")]
    E2b(#[from] E2bError),
    #[error("agent VM provider operation {operation} failed: {source}")]
    ProviderOperation {
        operation: &'static str,
        #[source]
        source: E2bError,
    },
}

impl AgentVmLifecycleError {
    /// Returns a short queue deferral for coordination races that should not consume retry budget.
    #[must_use]
    pub const fn deferral_seconds(&self) -> Option<i64> {
        match self {
            Self::Repository(
                AgentVmRepositoryError::OwnershipMissing { .. }
                | AgentVmRepositoryError::OwnershipTransition { .. }
                | AgentVmRepositoryError::WrongRuntimeOwner { .. },
            ) => Some(30),
            Self::Repository(
                AgentVmRepositoryError::NamespaceBusy { .. }
                | AgentVmRepositoryError::LeaseLost
                | AgentVmRepositoryError::LeaseOwnershipChanged
                | AgentVmRepositoryError::PersistentStateConflict
                | AgentVmRepositoryError::Sqlx(_),
            )
            | Self::CorpusArchive(AgentCorpusArchiveError::CorpusChanged { .. })
            | Self::Cancelled => Some(5),
            _ => None,
        }
    }
}
