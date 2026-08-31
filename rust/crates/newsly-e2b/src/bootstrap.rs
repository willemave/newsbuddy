//! Host-side adapter for the credential-free `newsly-vm-bootstrap` executable.

use std::collections::BTreeMap;
use std::time::{Duration, SystemTime};

use async_trait::async_trait;
use futures_util::StreamExt;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use tokio::time::Instant;
use tokio_util::sync::CancellationToken;
use uuid::Uuid;

use crate::control_plane::ControlPlaneClient;
use crate::envd_process::EnvdProcessClient;
use crate::error::E2bError;
use crate::files::{BoxByteStream, EnvdFileClient, FileLimits};
use crate::session::{DirectE2bProvider, SandboxSession};
use crate::types::{
    CommandRequest, CommandResult, ExecutionTag, ExitStatus, OutputLimits, SandboxPath, SandboxUser,
};

pub const VM_BOOTSTRAP_EXECUTABLE: &str = "/usr/local/bin/newsly-vm-bootstrap";
pub const MAX_CORPUS_ARCHIVE_BYTES: u64 = 128 * 1024 * 1024;
pub const MAX_CORPUS_MANIFEST_BYTES: u64 = 64 * 1024;

const CORPUS_MANIFEST_PATH: &str = "/data/manifest.json";
const REQUIRED_TOOL_CAPABILITIES: [&str; 7] = ["bash", "python", "node", "git", "curl", "jq", "rg"];
const REQUIRED_BROWSER_CAPABILITIES: [&str; 2] = ["chromium", "playwright"];
const CLEANUP_TIMEOUT: Duration = Duration::from_secs(5);

#[derive(Clone, Copy, Debug)]
pub struct VmBootstrapLimits {
    pub capability_timeout: Duration,
    pub corpus_timeout: Duration,
    pub command_idle_timeout: Duration,
    pub archive_bytes: u64,
    pub manifest_bytes: u64,
}

impl Default for VmBootstrapLimits {
    fn default() -> Self {
        Self {
            capability_timeout: Duration::from_secs(30),
            corpus_timeout: Duration::from_secs(300),
            command_idle_timeout: Duration::from_secs(60),
            archive_bytes: MAX_CORPUS_ARCHIVE_BYTES,
            manifest_bytes: MAX_CORPUS_MANIFEST_BYTES,
        }
    }
}

impl VmBootstrapLimits {
    pub fn validate(self) -> Result<(), E2bError> {
        if self.capability_timeout.is_zero()
            || self.corpus_timeout.is_zero()
            || self.command_idle_timeout.is_zero()
            || self.archive_bytes == 0
            || self.manifest_bytes == 0
        {
            return Err(E2bError::InvalidInput(
                "VM bootstrap limits must be greater than zero".to_owned(),
            ));
        }
        if self.archive_bytes > MAX_CORPUS_ARCHIVE_BYTES
            || self.manifest_bytes > MAX_CORPUS_MANIFEST_BYTES
        {
            return Err(E2bError::InvalidInput(
                "VM bootstrap byte limits exceed the installed helper contract".to_owned(),
            ));
        }
        Ok(())
    }
}

/// Validated capability manifest returned by the template helper.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(transparent)]
pub struct VmCapabilities(BTreeMap<String, Value>);

impl VmCapabilities {
    fn parse(stdout: &str) -> Result<Self, E2bError> {
        let values: BTreeMap<String, Value> = serde_json::from_str(stdout).map_err(|error| {
            E2bError::Protocol(format!("invalid VM capability manifest: {error}"))
        })?;
        let mut missing = REQUIRED_TOOL_CAPABILITIES
            .iter()
            .filter(|name| {
                values
                    .get(**name)
                    .and_then(Value::as_str)
                    .is_none_or(|path| path.trim().is_empty())
            })
            .copied()
            .collect::<Vec<_>>();
        missing.extend(
            REQUIRED_BROWSER_CAPABILITIES
                .iter()
                .filter(|name| values.get(**name).and_then(Value::as_bool) != Some(true))
                .copied(),
        );
        if !missing.is_empty() {
            missing.sort_unstable();
            return Err(E2bError::MissingVmCapabilities {
                capabilities: missing.join(", "),
            });
        }
        if values
            .get("browser_validation_error")
            .is_some_and(|value| !value.is_string())
        {
            return Err(E2bError::Protocol(
                "VM capability browser_validation_error must be a string".to_owned(),
            ));
        }
        Ok(Self(values))
    }

    #[must_use]
    pub fn values(&self) -> &BTreeMap<String, Value> {
        &self.0
    }

    #[must_use]
    pub fn into_values(self) -> BTreeMap<String, Value> {
        self.0
    }
}

/// The remote corpus revision used by the host materializer.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct RemoteCorpusState {
    pub revision: u64,
    pub force_full: bool,
}

impl RemoteCorpusState {
    const fn force_full() -> Self {
        Self {
            revision: 0,
            force_full: true,
        }
    }
}

/// Host-owned metadata for one already-materialized full or delta archive.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct CorpusTransfer {
    pub user_id: u64,
    pub from_revision: u64,
    pub to_revision: u64,
    pub full: bool,
    pub changed_file_count: u32,
    pub deleted_path_count: u32,
    pub archive_bytes: u64,
}

impl CorpusTransfer {
    fn validate(self, archive_limit: u64) -> Result<(), E2bError> {
        if self.user_id == 0 {
            return Err(E2bError::InvalidInput(
                "corpus transfer user id must be positive".to_owned(),
            ));
        }
        if self.to_revision < self.from_revision {
            return Err(E2bError::InvalidInput(
                "corpus target revision precedes source revision".to_owned(),
            ));
        }
        if self.archive_bytes == 0 {
            return Err(E2bError::InvalidInput(
                "corpus archive must not be empty".to_owned(),
            ));
        }
        if self.archive_bytes > archive_limit {
            return Err(E2bError::FileTooLarge {
                limit_bytes: usize::try_from(archive_limit).unwrap_or(usize::MAX),
                observed_bytes: self.archive_bytes,
            });
        }
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct CorpusHydrationResult {
    pub remote_revision: u64,
    pub applied_revision: u64,
    pub full: bool,
    pub changed_file_count: u32,
    pub deleted_path_count: u32,
    pub elapsed: Duration,
}

/// DB-independent lifecycle surface consumed by acquisition handlers.
#[async_trait]
pub trait VmBootstrapProvider: Send + Sync {
    async fn probe_vm_capabilities(
        &self,
        session: &SandboxSession,
        absolute_deadline: Instant,
        cancellation: CancellationToken,
    ) -> Result<VmCapabilities, E2bError>;

    async fn inspect_remote_corpus(
        &self,
        session: &SandboxSession,
        expected_user_id: u64,
        absolute_deadline: Instant,
        cancellation: CancellationToken,
    ) -> Result<RemoteCorpusState, E2bError>;

    async fn install_corpus_archive(
        &self,
        session: &SandboxSession,
        transfer: CorpusTransfer,
        source: BoxByteStream,
        absolute_deadline: Instant,
        cancellation: CancellationToken,
    ) -> Result<CorpusHydrationResult, E2bError>;
}

#[derive(Clone, Debug)]
pub struct VmBootstrapClient {
    process: EnvdProcessClient,
    files: EnvdFileClient,
    limits: VmBootstrapLimits,
}

impl VmBootstrapClient {
    pub fn new(control: ControlPlaneClient, limits: VmBootstrapLimits) -> Result<Self, E2bError> {
        limits.validate()?;
        let files = EnvdFileClient::new(
            control.clone(),
            FileLimits {
                upload_bytes: limits.archive_bytes,
                download_bytes: limits.manifest_bytes,
            },
        )?;
        Ok(Self {
            process: EnvdProcessClient::new(control),
            files,
            limits,
        })
    }

    async fn probe_capabilities(
        &self,
        session: &SandboxSession,
        requested_deadline: Instant,
        cancellation: CancellationToken,
    ) -> Result<VmCapabilities, E2bError> {
        session.require_active(SystemTime::now())?;
        let deadline = bounded_deadline(requested_deadline, self.limits.capability_timeout)?;
        let result = self
            .run_command(
                &session.sandbox,
                "probe_capabilities",
                VM_BOOTSTRAP_EXECUTABLE,
                vec!["capabilities".to_owned()],
                None,
                deadline,
                capability_output_limits(),
                cancellation,
            )
            .await?;
        VmCapabilities::parse(&result.output.stdout)
    }

    async fn inspect_corpus(
        &self,
        session: &SandboxSession,
        expected_user_id: u64,
        requested_deadline: Instant,
        cancellation: CancellationToken,
    ) -> Result<RemoteCorpusState, E2bError> {
        session.require_active(SystemTime::now())?;
        if expected_user_id == 0 {
            return Err(E2bError::InvalidInput(
                "expected corpus user id must be positive".to_owned(),
            ));
        }
        let deadline = bounded_deadline(requested_deadline, self.limits.corpus_timeout)?;
        self.inspect_corpus_until(&session.sandbox, expected_user_id, deadline, cancellation)
            .await
    }

    async fn inspect_corpus_until(
        &self,
        sandbox: &crate::types::SandboxHandle,
        expected_user_id: u64,
        deadline: Instant,
        cancellation: CancellationToken,
    ) -> Result<RemoteCorpusState, E2bError> {
        let manifest_path = SandboxPath::parse(CORPUS_MANIFEST_PATH)?;
        let root = SandboxUser::root();
        let download = self.files.download_sandbox_path(
            sandbox,
            &manifest_path,
            root.as_str(),
            remaining(deadline)?,
        );
        let mut stream = tokio::select! {
            () = cancellation.cancelled() => return Err(E2bError::Cancelled),
            result = tokio::time::timeout_at(deadline, download) => {
                match result {
                    Ok(Ok(stream)) => stream,
                    Ok(Err(E2bError::NotFound { .. } | E2bError::FileTooLarge { .. })) => {
                        return Ok(RemoteCorpusState::force_full());
                    }
                    Ok(Err(error)) => return Err(error),
                    Err(_) => return Err(E2bError::Deadline),
                }
            }
        };
        let mut bytes = Vec::new();
        loop {
            let item = tokio::select! {
                () = cancellation.cancelled() => return Err(E2bError::Cancelled),
                result = tokio::time::timeout_at(deadline, stream.next()) => {
                    result.map_err(|_| E2bError::Deadline)?
                }
            };
            let Some(chunk) = item else {
                break;
            };
            let chunk = match chunk {
                Ok(chunk) => chunk,
                Err(E2bError::FileTooLarge { .. }) => {
                    return Ok(RemoteCorpusState::force_full());
                }
                Err(error) => return Err(error),
            };
            bytes.extend_from_slice(&chunk);
        }
        Ok(parse_remote_manifest(&bytes, expected_user_id).map_or_else(
            RemoteCorpusState::force_full,
            |revision| RemoteCorpusState {
                revision,
                force_full: false,
            },
        ))
    }

    async fn install_corpus(
        &self,
        session: &SandboxSession,
        transfer: CorpusTransfer,
        source: BoxByteStream,
        requested_deadline: Instant,
        cancellation: CancellationToken,
    ) -> Result<CorpusHydrationResult, E2bError> {
        session.require_active(SystemTime::now())?;
        transfer.validate(self.limits.archive_bytes)?;
        let started = Instant::now();
        let deadline = bounded_deadline(requested_deadline, self.limits.corpus_timeout)?;
        let remote_archive = SandboxPath::parse(format!(
            "/tmp/newsly-agent-data-{}.tar.gz",
            Uuid::new_v4().simple()
        ))?;
        let root = SandboxUser::root();
        let upload = self.files.upload_sandbox_path(
            &session.sandbox,
            &remote_archive,
            root.as_str(),
            transfer.archive_bytes,
            source,
            remaining(deadline)?,
        );
        let upload_result = tokio::select! {
            () = cancellation.cancelled() => Err(E2bError::Cancelled),
            result = tokio::time::timeout_at(deadline, upload) => {
                result.map_err(|_| E2bError::Deadline)?
            }
        };
        if let Err(error) = upload_result {
            self.cleanup_remote_archive(&session.sandbox, &remote_archive)
                .await;
            return Err(error);
        }

        let install_result = self
            .run_command(
                &session.sandbox,
                "install_corpus",
                VM_BOOTSTRAP_EXECUTABLE,
                vec![
                    "corpus".to_owned(),
                    "install".to_owned(),
                    remote_archive.as_str().to_owned(),
                ],
                Some(root),
                deadline,
                install_output_limits(),
                // A command stream owns and cancels its token when it closes. Keep the parent
                // alive for the required post-install manifest verification below.
                cancellation.child_token(),
            )
            .await;
        self.cleanup_remote_archive(&session.sandbox, &remote_archive)
            .await;
        install_result?;

        let state = self
            .inspect_corpus_until(&session.sandbox, transfer.user_id, deadline, cancellation)
            .await?;
        if state.force_full || state.revision != transfer.to_revision {
            return Err(E2bError::Protocol(format!(
                "installed corpus manifest revision did not advance to {}",
                transfer.to_revision
            )));
        }
        Ok(CorpusHydrationResult {
            remote_revision: transfer.from_revision,
            applied_revision: transfer.to_revision,
            full: transfer.full,
            changed_file_count: transfer.changed_file_count,
            deleted_path_count: transfer.deleted_path_count,
            elapsed: started.elapsed(),
        })
    }

    #[allow(clippy::too_many_arguments)]
    pub(crate) async fn run_command(
        &self,
        sandbox: &crate::types::SandboxHandle,
        operation: &'static str,
        command: &str,
        args: Vec<String>,
        username: Option<SandboxUser>,
        deadline: Instant,
        output_limits: OutputLimits,
        cancellation: CancellationToken,
    ) -> Result<CommandResult, E2bError> {
        let execution_tag = ExecutionTag::new();
        let request = CommandRequest {
            command: command.to_owned(),
            args,
            env: BTreeMap::new(),
            cwd: None,
            username,
            tag: execution_tag.clone(),
            stdin_enabled: false,
            absolute_deadline: deadline,
            idle_timeout: self.limits.command_idle_timeout,
            output_limits,
        };
        let stream = match self
            .process
            .start(sandbox, request, cancellation.clone())
            .await
        {
            Ok(stream) => stream,
            Err(ambiguous @ E2bError::AmbiguousDelivery { .. }) => {
                match self
                    .process
                    .recover_by_tag(
                        sandbox,
                        execution_tag,
                        deadline,
                        self.limits.command_idle_timeout,
                        output_limits,
                        cancellation,
                    )
                    .await
                {
                    Ok(stream) => stream,
                    Err(E2bError::RecoveryUnavailable { .. }) => return Err(ambiguous),
                    Err(error) => return Err(error),
                }
            }
            Err(error) => return Err(error),
        };
        let result = stream.collect_result().await?;
        if result.exit_code != 0 || result.status != ExitStatus::Exited {
            let detail = result
                .error
                .as_deref()
                .filter(|value| !value.trim().is_empty())
                .or_else(|| {
                    (!result.output.stderr.trim().is_empty())
                        .then_some(result.output.stderr.as_str())
                })
                .or_else(|| {
                    (!result.output.stdout.trim().is_empty())
                        .then_some(result.output.stdout.as_str())
                })
                .unwrap_or("no diagnostic output");
            return Err(E2bError::VmBootstrapFailed {
                operation,
                exit_code: result.exit_code,
                message: truncate_chars(detail.trim(), 4_000),
            });
        }
        Ok(result)
    }

    async fn cleanup_remote_archive(
        &self,
        sandbox: &crate::types::SandboxHandle,
        remote_archive: &SandboxPath,
    ) {
        let Some(deadline) = Instant::now().checked_add(CLEANUP_TIMEOUT) else {
            return;
        };
        if let Err(error) = self
            .run_command(
                sandbox,
                "cleanup_corpus_archive",
                "/bin/rm",
                vec![
                    "-f".to_owned(),
                    "--".to_owned(),
                    remote_archive.as_str().to_owned(),
                ],
                Some(SandboxUser::root()),
                deadline,
                cleanup_output_limits(),
                CancellationToken::new(),
            )
            .await
        {
            tracing::warn!(
                sandbox_id = %sandbox.sandbox_id,
                path = remote_archive.as_str(),
                error = %error,
                "unable to remove staged E2B corpus archive"
            );
        }
    }
}

#[async_trait]
impl VmBootstrapProvider for DirectE2bProvider {
    async fn probe_vm_capabilities(
        &self,
        session: &SandboxSession,
        absolute_deadline: Instant,
        cancellation: CancellationToken,
    ) -> Result<VmCapabilities, E2bError> {
        self.vm_bootstrap_client()
            .probe_capabilities(session, absolute_deadline, cancellation)
            .await
    }

    async fn inspect_remote_corpus(
        &self,
        session: &SandboxSession,
        expected_user_id: u64,
        absolute_deadline: Instant,
        cancellation: CancellationToken,
    ) -> Result<RemoteCorpusState, E2bError> {
        self.vm_bootstrap_client()
            .inspect_corpus(session, expected_user_id, absolute_deadline, cancellation)
            .await
    }

    async fn install_corpus_archive(
        &self,
        session: &SandboxSession,
        transfer: CorpusTransfer,
        source: BoxByteStream,
        absolute_deadline: Instant,
        cancellation: CancellationToken,
    ) -> Result<CorpusHydrationResult, E2bError> {
        self.vm_bootstrap_client()
            .install_corpus(session, transfer, source, absolute_deadline, cancellation)
            .await
    }
}

fn bounded_deadline(requested: Instant, maximum: Duration) -> Result<Instant, E2bError> {
    let now = Instant::now();
    if requested <= now {
        return Err(E2bError::Deadline);
    }
    let maximum_deadline = now
        .checked_add(maximum)
        .ok_or_else(|| E2bError::InvalidInput("VM bootstrap timeout is too large".to_owned()))?;
    Ok(requested.min(maximum_deadline))
}

fn remaining(deadline: Instant) -> Result<Duration, E2bError> {
    deadline
        .checked_duration_since(Instant::now())
        .filter(|remaining| !remaining.is_zero())
        .ok_or(E2bError::Deadline)
}

fn parse_remote_manifest(bytes: &[u8], expected_user_id: u64) -> Option<u64> {
    let manifest: CorpusManifestWire = serde_json::from_slice(bytes).ok()?;
    (manifest.user_id == expected_user_id).then_some(manifest.revision)
}

#[derive(Debug, Deserialize)]
struct CorpusManifestWire {
    user_id: u64,
    revision: u64,
}

fn capability_output_limits() -> OutputLimits {
    OutputLimits {
        stdout_bytes: 64 * 1024,
        stderr_bytes: 32 * 1024,
        combined_bytes: 96 * 1024,
        event_bytes: 64 * 1024,
        channel_capacity: 16,
    }
}

fn install_output_limits() -> OutputLimits {
    OutputLimits {
        stdout_bytes: 32 * 1024,
        stderr_bytes: 64 * 1024,
        combined_bytes: 96 * 1024,
        event_bytes: 64 * 1024,
        channel_capacity: 16,
    }
}

fn cleanup_output_limits() -> OutputLimits {
    OutputLimits {
        stdout_bytes: 4 * 1024,
        stderr_bytes: 4 * 1024,
        combined_bytes: 8 * 1024,
        event_bytes: 8 * 1024,
        channel_capacity: 4,
    }
}

fn truncate_chars(value: &str, limit: usize) -> String {
    value.chars().take(limit).collect()
}

#[cfg(test)]
mod tests {
    use super::{CorpusTransfer, RemoteCorpusState, VmCapabilities, parse_remote_manifest};

    #[test]
    fn capability_manifest_requires_tools_and_browser() {
        let valid = r#"{
            "bash":"/bin/bash","python":"/usr/bin/python","node":"/usr/bin/node",
            "git":"/usr/bin/git","curl":"/usr/bin/curl","jq":"/usr/bin/jq",
            "rg":"/usr/bin/rg","chromium":true,"playwright":true
        }"#;
        assert!(VmCapabilities::parse(valid).is_ok());
        assert!(VmCapabilities::parse(r#"{"bash":"/bin/bash"}"#).is_err());
    }

    #[test]
    fn corrupt_or_foreign_manifests_force_a_full_transfer() {
        let valid = br#"{"user_id":42,"revision":9,"future_extension":true}"#;
        assert_eq!(parse_remote_manifest(valid, 42), Some(9));
        assert_eq!(parse_remote_manifest(valid, 7), None);
        assert_eq!(parse_remote_manifest(br#"{"revision":true}"#, 42), None);
        assert_eq!(
            RemoteCorpusState::force_full(),
            RemoteCorpusState {
                revision: 0,
                force_full: true,
            }
        );
    }

    #[test]
    fn corpus_transfer_enforces_archive_bounds() {
        let transfer = CorpusTransfer {
            user_id: 42,
            from_revision: 2,
            to_revision: 3,
            full: false,
            changed_file_count: 1,
            deleted_path_count: 0,
            archive_bytes: 8,
        };
        assert!(transfer.validate(8).is_ok());
        assert!(
            CorpusTransfer {
                archive_bytes: 9,
                ..transfer
            }
            .validate(8)
            .is_err()
        );
    }
}
