//! Database-independent E2B provider and session boundary.

use tokio_util::sync::CancellationToken;

use crate::bootstrap::{VmBootstrapClient, VmBootstrapLimits};
use crate::control_plane::{ControlPlaneClient, ControlPlaneConfig, SandboxHealth};
use crate::envd_process::{CommandEventStream, EnvdProcessClient, ProcessSignal};
use crate::error::E2bError;
use crate::files::{BoxByteStream, EnvdFileClient, FileLimits};
use crate::network::NetworkPolicy;
use crate::types::{
    CommandRequest, ProcessSelector, SandboxHandle, SandboxId, SandboxInfo, SandboxRequest,
    WorkspacePath,
};

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
    pub fn file_client(&self) -> &EnvdFileClient {
        &self.files
    }

    #[must_use]
    pub fn vm_bootstrap_client(&self) -> &VmBootstrapClient {
        &self.vm_bootstrap
    }
}

impl DirectE2bProvider {
    pub async fn create_sandbox(
        &self,
        request: &SandboxRequest,
    ) -> Result<SandboxHandle, E2bError> {
        self.control.create(request).await
    }

    pub async fn kill_sandbox(&self, sandbox_id: &SandboxId) -> Result<bool, E2bError> {
        self.control.kill(sandbox_id).await
    }

    pub async fn get_sandbox_info(&self, sandbox_id: &SandboxId) -> Result<SandboxInfo, E2bError> {
        self.control.get_info(sandbox_id).await
    }

    pub async fn list_sandboxes_by_metadata(
        &self,
        key: &str,
        value: &str,
        limit: u8,
    ) -> Result<Vec<SandboxInfo>, E2bError> {
        self.control.list_by_metadata(key, value, limit).await
    }

    pub async fn check_sandbox_health(
        &self,
        sandbox: &SandboxHandle,
    ) -> Result<SandboxHealth, E2bError> {
        self.control.check_envd_health(sandbox).await
    }

    pub async fn update_network(
        &self,
        sandbox_id: &SandboxId,
        policy: &NetworkPolicy,
    ) -> Result<(), E2bError> {
        self.control.update_network(sandbox_id, policy).await
    }

    pub async fn reset_network(&self, sandbox_id: &SandboxId) -> Result<(), E2bError> {
        self.control.reset_network(sandbox_id).await
    }

    pub async fn upload_file(
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

    pub async fn download_file(
        &self,
        sandbox: &SandboxHandle,
        path: &WorkspacePath,
        username: &str,
    ) -> Result<BoxByteStream, E2bError> {
        self.files.download(sandbox, path, username).await
    }

    pub async fn start_process(
        &self,
        sandbox: &SandboxHandle,
        request: CommandRequest,
        cancellation: CancellationToken,
    ) -> Result<CommandEventStream, E2bError> {
        self.process.start(sandbox, request, cancellation).await
    }

    pub async fn signal_process(
        &self,
        sandbox: &SandboxHandle,
        selector: ProcessSelector,
        signal: ProcessSignal,
    ) -> Result<(), E2bError> {
        self.process.signal(sandbox, selector, signal).await
    }
}
