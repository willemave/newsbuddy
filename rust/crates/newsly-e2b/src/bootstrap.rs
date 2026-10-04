//! Host-side adapter for the credential-free sandbox capability probe.

use std::collections::BTreeMap;
use std::time::Duration;

use serde::{Deserialize, Serialize};
use serde_json::Value;
use tokio::time::Instant;
use tokio_util::sync::CancellationToken;

use crate::control_plane::ControlPlaneClient;
use crate::envd_process::EnvdProcessClient;
use crate::error::E2bError;
use crate::session::DirectE2bProvider;
use crate::types::{
    CommandRequest, ExecutionTag, ExitStatus, OutputLimits, SandboxHandle, SandboxUser,
};

pub const VM_BOOTSTRAP_EXECUTABLE: &str = "/usr/local/bin/newsly-vm-bootstrap";
const REQUIRED_TOOL_CAPABILITIES: [&str; 7] = ["bash", "python", "node", "git", "curl", "jq", "rg"];
const REQUIRED_BROWSER_CAPABILITIES: [&str; 2] = ["chromium", "playwright"];

#[derive(Clone, Copy, Debug)]
pub struct VmBootstrapLimits {
    pub command_idle_timeout: Duration,
}

impl Default for VmBootstrapLimits {
    fn default() -> Self {
        Self {
            command_idle_timeout: Duration::from_secs(30),
        }
    }
}

impl VmBootstrapLimits {
    fn validate(self) -> Result<(), E2bError> {
        if self.command_idle_timeout.is_zero() {
            return Err(E2bError::InvalidInput(
                "VM command idle timeout must be greater than zero".to_owned(),
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
            let diagnostic = values
                .get("browser_validation_error")
                .and_then(Value::as_str)
                .filter(|detail| !detail.trim().is_empty())
                .map(|detail| {
                    format!(
                        "; browser validation: {}",
                        detail.trim().chars().take(1_000).collect::<String>()
                    )
                })
                .unwrap_or_default();
            return Err(E2bError::MissingVmCapabilities {
                capabilities: format!("{}{diagnostic}", missing.join(", ")),
            });
        }
        Ok(Self(values))
    }

    #[must_use]
    pub fn values(&self) -> &BTreeMap<String, Value> {
        &self.0
    }
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
                    absolute_deadline: requested_deadline,
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
}

impl DirectE2bProvider {
    pub async fn probe_vm_capabilities(
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

    #[test]
    fn capability_manifest_preserves_browser_failure_detail() {
        let invalid = r#"{"bash":"/bin/bash","python":"/usr/bin/python3","node":"/usr/bin/node","git":"/usr/bin/git","curl":"/usr/bin/curl","jq":"/usr/bin/jq","rg":"/usr/bin/rg","chromium":false,"playwright":false,"browser_validation_error":"Chromium executable is unavailable"}"#;
        let error = VmCapabilities::parse(invalid).expect_err("browser must be required");

        assert!(
            error
                .to_string()
                .contains("Chromium executable is unavailable")
        );
    }
}
