//! Host-side adapter for the credential-free sandbox capability probe.

use std::collections::BTreeMap;
use std::time::Duration;

use async_trait::async_trait;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use tokio::time::Instant;
use tokio_util::sync::CancellationToken;

use crate::control_plane::ControlPlaneClient;
use crate::envd_process::EnvdProcessClient;
use crate::error::E2bError;
use crate::session::DirectE2bProvider;
use crate::types::{
    CommandRequest, CommandResult, ExecutionTag, ExitStatus, OutputLimits, SandboxHandle,
    SandboxUser,
};

pub const VM_BOOTSTRAP_EXECUTABLE: &str = "/usr/local/bin/newsly-vm-bootstrap";
const REQUIRED_TOOL_CAPABILITIES: [&str; 7] = ["bash", "python", "node", "git", "curl", "jq", "rg"];
const REQUIRED_BROWSER_CAPABILITIES: [&str; 2] = ["chromium", "playwright"];

#[derive(Clone, Copy, Debug)]
pub struct VmBootstrapLimits {
    pub capability_timeout: Duration,
    pub command_idle_timeout: Duration,
}

impl Default for VmBootstrapLimits {
    fn default() -> Self {
        Self {
            capability_timeout: Duration::from_secs(30),
            command_idle_timeout: Duration::from_secs(30),
        }
    }
}

impl VmBootstrapLimits {
    fn validate(self) -> Result<(), E2bError> {
        if self.capability_timeout.is_zero() || self.command_idle_timeout.is_zero() {
            return Err(E2bError::InvalidInput(
                "VM capability limits must be greater than zero".to_owned(),
            ));
        }
        Ok(())
    }
}

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
        Ok(Self(values))
    }

    #[must_use]
    pub fn values(&self) -> &BTreeMap<String, Value> {
        &self.0
    }
}

#[async_trait]
pub trait VmBootstrapProvider: Send + Sync {
    async fn probe_vm_capabilities(
        &self,
        sandbox: &SandboxHandle,
        absolute_deadline: Instant,
        cancellation: CancellationToken,
    ) -> Result<VmCapabilities, E2bError>;
}

#[derive(Clone, Debug)]
pub struct VmBootstrapClient {
    process: EnvdProcessClient,
    limits: VmBootstrapLimits,
}

impl VmBootstrapClient {
    pub fn new(control: ControlPlaneClient, limits: VmBootstrapLimits) -> Result<Self, E2bError> {
        limits.validate()?;
        Ok(Self {
            process: EnvdProcessClient::new(control),
            limits,
        })
    }

    async fn probe(
        &self,
        sandbox: &SandboxHandle,
        requested_deadline: Instant,
        cancellation: CancellationToken,
    ) -> Result<VmCapabilities, E2bError> {
        let deadline = requested_deadline.min(
            Instant::now()
                .checked_add(self.limits.capability_timeout)
                .ok_or(E2bError::Deadline)?,
        );
        let stream = self
            .process
            .start(
                sandbox,
                CommandRequest {
                    command: VM_BOOTSTRAP_EXECUTABLE.to_owned(),
                    args: vec!["capabilities".to_owned()],
                    env: BTreeMap::new(),
                    cwd: None,
                    username: Some(SandboxUser::parse("user")?),
                    tag: ExecutionTag::new(),
                    stdin_enabled: false,
                    absolute_deadline: deadline,
                    idle_timeout: self.limits.command_idle_timeout,
                    output_limits: OutputLimits {
                        stdout_bytes: 64 * 1024,
                        stderr_bytes: 16 * 1024,
                        combined_bytes: 80 * 1024,
                        event_bytes: 64 * 1024,
                        channel_capacity: 8,
                    },
                },
                cancellation,
            )
            .await?;
        let result = stream.collect_result().await?;
        if result.status != ExitStatus::Exited || result.exit_code != 0 {
            let diagnostic = if result.output.stderr.trim().is_empty() {
                result.error.unwrap_or(result.output.stdout)
            } else {
                result.output.stderr
            };
            return Err(E2bError::Protocol(format!(
                "VM capability probe failed with exit {}: {}",
                result.exit_code,
                diagnostic.chars().take(1_000).collect::<String>()
            )));
        }
        VmCapabilities::parse(&result.output.stdout)
    }

    #[allow(clippy::too_many_arguments)]
    pub(crate) async fn run_command(
        &self,
        sandbox: &SandboxHandle,
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
            Err(ambiguous @ E2bError::AmbiguousDelivery { .. }) => match self
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
            },
            Err(error) => return Err(error),
        };
        let result = stream.collect_result().await?;
        if result.exit_code != 0 || result.status != ExitStatus::Exited {
            // Command stderr is the actionable bootstrap failure. Transport status is only a
            // fallback when the command produced no diagnostic.
            let detail = (!result.output.stderr.trim().is_empty())
                .then_some(result.output.stderr.as_str())
                .or_else(|| {
                    (!result.output.stdout.trim().is_empty())
                        .then_some(result.output.stdout.as_str())
                })
                .or_else(|| {
                    result
                        .error
                        .as_deref()
                        .filter(|value| !value.trim().is_empty())
                })
                .unwrap_or("no diagnostic output");
            return Err(E2bError::VmBootstrapFailed {
                operation,
                exit_code: result.exit_code,
                message: detail.trim().chars().take(4_000).collect(),
            });
        }
        Ok(result)
    }
}

#[async_trait]
impl VmBootstrapProvider for DirectE2bProvider {
    async fn probe_vm_capabilities(
        &self,
        sandbox: &SandboxHandle,
        absolute_deadline: Instant,
        cancellation: CancellationToken,
    ) -> Result<VmCapabilities, E2bError> {
        self.vm_bootstrap_client()
            .probe(sandbox, absolute_deadline, cancellation)
            .await
    }
}

#[cfg(test)]
mod tests {
    use super::VmCapabilities;

    #[test]
    fn capability_manifest_requires_tools_and_browser() {
        let valid = r#"{"bash":"/bin/bash","python":"/usr/bin/python3","node":"/usr/bin/node","git":"/usr/bin/git","curl":"/usr/bin/curl","jq":"/usr/bin/jq","rg":"/usr/bin/rg","chromium":true,"playwright":true}"#;
        assert!(VmCapabilities::parse(valid).is_ok());
        assert!(VmCapabilities::parse(r#"{"bash":"/bin/bash"}"#).is_err());
    }
}
