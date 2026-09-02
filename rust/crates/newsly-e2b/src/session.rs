//! Database-independent E2B provider and session boundary.

use std::future::Future;
use std::time::Duration;

use async_trait::async_trait;
use bytes::Bytes;
use futures_util::StreamExt;
use serde::Deserialize;
use tokio_util::sync::CancellationToken;

use crate::bootstrap::{VmBootstrapClient, VmBootstrapLimits};
use crate::control_plane::{ControlPlaneClient, ControlPlaneConfig, SandboxHealth};
use crate::envd_process::{CommandEventStream, EnvdProcessClient, ProcessSignal};
use crate::error::E2bError;
use crate::files::{BoxByteStream, EnvdFileClient, FileLimits};
use crate::network::NetworkPolicy;
use crate::types::{
    CommandOutput, CommandRequest, CommandResult, ExecutionTag, ExitStatus, OutputLimits,
    ProcessInfo, ProcessSelector, SandboxHandle, SandboxId, SandboxRequest, WorkspacePath,
};

const RESULT_MANIFEST_LIMIT_BYTES: usize = 1024 * 1024;

/// Optional terminal-result fallback used only after reconnect-by-tag finds no live process.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ResultManifestLocation {
    pub path: WorkspacePath,
    pub username: String,
}

#[derive(Debug)]
pub enum RecoveredCommand {
    Live {
        stream: CommandEventStream,
        terminal_manifest: Option<ResultManifestLocation>,
    },
    Terminal(CommandResult),
}

/// Newsly-owned E2B surface. Executables depend on this trait rather than an E2B SDK.
#[async_trait]
pub trait SandboxProvider: Send + Sync {
    async fn create_sandbox(&self, request: &SandboxRequest) -> Result<SandboxHandle, E2bError>;

    async fn kill_sandbox(&self, sandbox_id: &SandboxId) -> Result<bool, E2bError>;

    async fn check_sandbox_health(
        &self,
        sandbox: &SandboxHandle,
    ) -> Result<SandboxHealth, E2bError>;

    async fn update_network(
        &self,
        sandbox_id: &SandboxId,
        policy: &NetworkPolicy,
    ) -> Result<(), E2bError>;

    async fn reset_network(&self, sandbox_id: &SandboxId) -> Result<(), E2bError>;

    async fn upload_file(
        &self,
        sandbox: &SandboxHandle,
        path: &WorkspacePath,
        username: &str,
        content_length: Option<u64>,
        source: BoxByteStream,
    ) -> Result<(), E2bError>;

    async fn download_file(
        &self,
        sandbox: &SandboxHandle,
        path: &WorkspacePath,
        username: &str,
    ) -> Result<BoxByteStream, E2bError>;

    async fn start_process(
        &self,
        sandbox: &SandboxHandle,
        request: CommandRequest,
        cancellation: CancellationToken,
    ) -> Result<CommandEventStream, E2bError>;

    #[allow(clippy::too_many_arguments)]
    async fn connect_process(
        &self,
        sandbox: &SandboxHandle,
        selector: ProcessSelector,
        execution_tag: ExecutionTag,
        absolute_deadline: tokio::time::Instant,
        idle_timeout: Duration,
        output_limits: OutputLimits,
        cancellation: CancellationToken,
    ) -> Result<CommandEventStream, E2bError>;

    #[allow(clippy::too_many_arguments)]
    async fn recover_process(
        &self,
        sandbox: &SandboxHandle,
        execution_tag: ExecutionTag,
        absolute_deadline: tokio::time::Instant,
        idle_timeout: Duration,
        output_limits: OutputLimits,
        cancellation: CancellationToken,
        result_manifest: Option<&ResultManifestLocation>,
    ) -> Result<RecoveredCommand, E2bError>;

    async fn read_command_result_manifest(
        &self,
        sandbox: &SandboxHandle,
        location: &ResultManifestLocation,
        expected_tag: &ExecutionTag,
    ) -> Result<CommandResult, E2bError>;

    async fn list_processes(&self, sandbox: &SandboxHandle) -> Result<Vec<ProcessInfo>, E2bError>;

    async fn signal_process(
        &self,
        sandbox: &SandboxHandle,
        selector: ProcessSelector,
        signal: ProcessSignal,
    ) -> Result<(), E2bError>;

    async fn send_process_stdin(
        &self,
        sandbox: &SandboxHandle,
        selector: ProcessSelector,
        input: Bytes,
    ) -> Result<(), E2bError>;

    async fn close_process_stdin(
        &self,
        sandbox: &SandboxHandle,
        selector: ProcessSelector,
    ) -> Result<(), E2bError>;
}

#[derive(Clone, Debug)]
pub struct DirectE2bProvider {
    control: ControlPlaneClient,
    process: EnvdProcessClient,
    files: EnvdFileClient,
    vm_bootstrap: VmBootstrapClient,
}

impl DirectE2bProvider {
    pub fn new(config: ControlPlaneConfig, file_limits: FileLimits) -> Result<Self, E2bError> {
        Self::new_with_bootstrap_limits(config, file_limits, VmBootstrapLimits::default())
    }

    pub fn new_with_bootstrap_limits(
        config: ControlPlaneConfig,
        file_limits: FileLimits,
        bootstrap_limits: VmBootstrapLimits,
    ) -> Result<Self, E2bError> {
        let control = ControlPlaneClient::new(config)?;
        let process = EnvdProcessClient::new(control.clone());
        let files = EnvdFileClient::new(control.clone(), file_limits)?;
        let vm_bootstrap = VmBootstrapClient::new(control.clone(), bootstrap_limits)?;
        Ok(Self {
            control,
            process,
            files,
            vm_bootstrap,
        })
    }

    #[must_use]
    pub fn control_plane(&self) -> &ControlPlaneClient {
        &self.control
    }

    #[must_use]
    pub fn process_client(&self) -> &EnvdProcessClient {
        &self.process
    }

    #[must_use]
    pub fn file_client(&self) -> &EnvdFileClient {
        &self.files
    }

    #[must_use]
    pub fn vm_bootstrap_client(&self) -> &VmBootstrapClient {
        &self.vm_bootstrap
    }

    /// Applies a complete network policy for one operation and resets to deny-all afterward.
    /// The reset is attempted whether the operation succeeds or fails.
    pub async fn with_network_policy<T, F, Fut>(
        &self,
        sandbox_id: &SandboxId,
        policy: &NetworkPolicy,
        operation: F,
    ) -> Result<T, E2bError>
    where
        F: FnOnce() -> Fut,
        Fut: Future<Output = Result<T, E2bError>>,
    {
        let mut reset_guard = NetworkResetGuard::new(self.control.clone(), sandbox_id.clone());
        self.control.update_network(sandbox_id, policy).await?;
        let result = operation().await;
        let reset = reset_guard.reset().await;
        match (result, reset) {
            (Ok(value), Ok(())) => Ok(value),
            (Ok(_), Err(reset_error)) => Err(reset_error),
            (Err(operation_error), Ok(())) => Err(operation_error),
            (Err(operation_error), Err(reset_error)) => {
                tracing::error!(
                    sandbox_id = %sandbox_id,
                    error = %reset_error,
                    "failed to reset E2B network policy after operation failure"
                );
                Err(operation_error)
            }
        }
    }

    #[allow(clippy::too_many_arguments)]
    async fn recover_command(
        &self,
        sandbox: &SandboxHandle,
        execution_tag: ExecutionTag,
        absolute_deadline: tokio::time::Instant,
        idle_timeout: Duration,
        output_limits: OutputLimits,
        cancellation: CancellationToken,
        result_manifest: Option<&ResultManifestLocation>,
    ) -> Result<RecoveredCommand, E2bError> {
        if self.control.check_envd_health(sandbox).await? == SandboxHealth::Unavailable {
            return Err(E2bError::RecoveryUnavailable {
                execution_tag: execution_tag.to_string(),
            });
        }
        match self
            .process
            .recover_by_tag(
                sandbox,
                execution_tag.clone(),
                absolute_deadline,
                idle_timeout,
                output_limits,
                cancellation,
            )
            .await
        {
            Ok(stream) => Ok(RecoveredCommand::Live {
                stream,
                terminal_manifest: result_manifest.cloned(),
            }),
            Err(E2bError::RecoveryUnavailable { .. }) => {
                let location = result_manifest.ok_or_else(|| E2bError::RecoveryUnavailable {
                    execution_tag: execution_tag.to_string(),
                })?;
                match self
                    .read_result_manifest(sandbox, location, &execution_tag)
                    .await
                {
                    Ok(result) => Ok(RecoveredCommand::Terminal(result)),
                    Err(E2bError::NotFound { .. }) => Err(E2bError::RecoveryUnavailable {
                        execution_tag: execution_tag.to_string(),
                    }),
                    Err(error) => Err(error),
                }
            }
            Err(error) => Err(error),
        }
    }

    async fn read_result_manifest(
        &self,
        sandbox: &SandboxHandle,
        location: &ResultManifestLocation,
        expected_tag: &ExecutionTag,
    ) -> Result<CommandResult, E2bError> {
        let mut stream = self
            .files
            .download(sandbox, &location.path, &location.username)
            .await?;
        let mut bytes = Vec::new();
        while let Some(chunk) = stream.next().await {
            let chunk = chunk?;
            if bytes.len().saturating_add(chunk.len()) > RESULT_MANIFEST_LIMIT_BYTES {
                return Err(E2bError::FileTooLarge {
                    limit_bytes: RESULT_MANIFEST_LIMIT_BYTES,
                    observed_bytes: u64::try_from(bytes.len().saturating_add(chunk.len()))
                        .unwrap_or(u64::MAX),
                });
            }
            bytes.extend_from_slice(&chunk);
        }
        let manifest: ResultManifestWire = serde_json::from_slice(&bytes)
            .map_err(|error| E2bError::Protocol(format!("invalid result manifest: {error}")))?;
        let manifest_tag = ExecutionTag::parse(manifest.execution_tag)?;
        if &manifest_tag != expected_tag {
            return Err(E2bError::Protocol(format!(
                "result manifest tag {manifest_tag} does not match {expected_tag}"
            )));
        }
        let status = manifest
            .status
            .unwrap_or_else(|| ExitStatus::from_wire("", true, manifest.exit_code));
        Ok(CommandResult {
            execution_tag: manifest_tag,
            pid: manifest.pid,
            output: CommandOutput {
                stdout: manifest.stdout,
                stderr: manifest.stderr,
            },
            status,
            exit_code: manifest.exit_code,
            error: manifest.error,
        })
    }
}

#[derive(Debug)]
struct NetworkResetGuard {
    control: ControlPlaneClient,
    sandbox_id: SandboxId,
    armed: bool,
}

impl NetworkResetGuard {
    fn new(control: ControlPlaneClient, sandbox_id: SandboxId) -> Self {
        Self {
            control,
            sandbox_id,
            armed: true,
        }
    }

    async fn reset(&mut self) -> Result<(), E2bError> {
        self.control.reset_network(&self.sandbox_id).await?;
        self.armed = false;
        Ok(())
    }
}

impl Drop for NetworkResetGuard {
    fn drop(&mut self) {
        if !self.armed {
            return;
        }
        let control = self.control.clone();
        let sandbox_id = self.sandbox_id.clone();
        let Ok(runtime) = tokio::runtime::Handle::try_current() else {
            tracing::error!(
                sandbox_id = %sandbox_id,
                "cannot schedule E2B network reset outside a Tokio runtime"
            );
            return;
        };
        runtime.spawn(async move {
            if let Err(error) = control.reset_network(&sandbox_id).await {
                tracing::error!(
                    sandbox_id = %sandbox_id,
                    error = %error,
                    "failed to reset E2B network policy during cancellation cleanup"
                );
            }
        });
    }
}

#[derive(Debug, Deserialize)]
struct ResultManifestWire {
    execution_tag: String,
    #[serde(default)]
    pid: Option<u32>,
    #[serde(default)]
    stdout: String,
    #[serde(default)]
    stderr: String,
    #[serde(default)]
    status: Option<ExitStatus>,
    exit_code: i32,
    #[serde(default)]
    error: Option<String>,
}

#[async_trait]
impl SandboxProvider for DirectE2bProvider {
    async fn create_sandbox(&self, request: &SandboxRequest) -> Result<SandboxHandle, E2bError> {
        self.control.create(request).await
    }

    async fn kill_sandbox(&self, sandbox_id: &SandboxId) -> Result<bool, E2bError> {
        self.control.kill(sandbox_id).await
    }

    async fn check_sandbox_health(
        &self,
        sandbox: &SandboxHandle,
    ) -> Result<SandboxHealth, E2bError> {
        self.control.check_envd_health(sandbox).await
    }

    async fn update_network(
        &self,
        sandbox_id: &SandboxId,
        policy: &NetworkPolicy,
    ) -> Result<(), E2bError> {
        self.control.update_network(sandbox_id, policy).await
    }

    async fn reset_network(&self, sandbox_id: &SandboxId) -> Result<(), E2bError> {
        self.control.reset_network(sandbox_id).await
    }

    async fn upload_file(
        &self,
        sandbox: &SandboxHandle,
        path: &WorkspacePath,
        username: &str,
        content_length: Option<u64>,
        source: BoxByteStream,
    ) -> Result<(), E2bError> {
        self.files
            .upload(sandbox, path, username, content_length, source)
            .await
    }

    async fn download_file(
        &self,
        sandbox: &SandboxHandle,
        path: &WorkspacePath,
        username: &str,
    ) -> Result<BoxByteStream, E2bError> {
        self.files.download(sandbox, path, username).await
    }

    async fn start_process(
        &self,
        sandbox: &SandboxHandle,
        request: CommandRequest,
        cancellation: CancellationToken,
    ) -> Result<CommandEventStream, E2bError> {
        self.process.start(sandbox, request, cancellation).await
    }

    async fn connect_process(
        &self,
        sandbox: &SandboxHandle,
        selector: ProcessSelector,
        execution_tag: ExecutionTag,
        absolute_deadline: tokio::time::Instant,
        idle_timeout: Duration,
        output_limits: OutputLimits,
        cancellation: CancellationToken,
    ) -> Result<CommandEventStream, E2bError> {
        self.process
            .connect(
                sandbox,
                selector,
                execution_tag,
                absolute_deadline,
                idle_timeout,
                output_limits,
                cancellation,
            )
            .await
    }

    async fn recover_process(
        &self,
        sandbox: &SandboxHandle,
        execution_tag: ExecutionTag,
        absolute_deadline: tokio::time::Instant,
        idle_timeout: Duration,
        output_limits: OutputLimits,
        cancellation: CancellationToken,
        result_manifest: Option<&ResultManifestLocation>,
    ) -> Result<RecoveredCommand, E2bError> {
        self.recover_command(
            sandbox,
            execution_tag,
            absolute_deadline,
            idle_timeout,
            output_limits,
            cancellation,
            result_manifest,
        )
        .await
    }

    async fn read_command_result_manifest(
        &self,
        sandbox: &SandboxHandle,
        location: &ResultManifestLocation,
        expected_tag: &ExecutionTag,
    ) -> Result<CommandResult, E2bError> {
        self.read_result_manifest(sandbox, location, expected_tag)
            .await
    }

    async fn list_processes(&self, sandbox: &SandboxHandle) -> Result<Vec<ProcessInfo>, E2bError> {
        self.process.list(sandbox).await
    }

    async fn signal_process(
        &self,
        sandbox: &SandboxHandle,
        selector: ProcessSelector,
        signal: ProcessSignal,
    ) -> Result<(), E2bError> {
        self.process.signal(sandbox, selector, signal).await
    }

    async fn send_process_stdin(
        &self,
        sandbox: &SandboxHandle,
        selector: ProcessSelector,
        input: Bytes,
    ) -> Result<(), E2bError> {
        self.process.send_stdin(sandbox, selector, input).await
    }

    async fn close_process_stdin(
        &self,
        sandbox: &SandboxHandle,
        selector: ProcessSelector,
    ) -> Result<(), E2bError> {
        self.process.close_stdin(sandbox, selector).await
    }
}

#[cfg(test)]
mod tests {
    use super::ResultManifestWire;

    #[test]
    fn success_recording_manifest_matches_recovery_wire() {
        let fixture: serde_json::Value = serde_json::from_str(include_str!(
            "../../../../contracts/llm/e2b-command-stream.json"
        ))
        .expect("fixture must be valid JSON");
        let manifest = fixture["commands"][0]["result_manifest"].clone();
        let parsed: ResultManifestWire =
            serde_json::from_value(manifest).expect("manifest recording must fit recovery wire");
        assert_eq!(parsed.execution_tag, "fixture-exec-1");
        assert_eq!(parsed.exit_code, 0);
    }
}
