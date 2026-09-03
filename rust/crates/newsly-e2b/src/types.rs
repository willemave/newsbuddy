//! Newsly-owned E2B identifiers and wire-independent domain types.

use std::collections::BTreeMap;
use std::fmt;
use std::path::{Component, Path};
use std::time::Duration;

use bytes::Bytes;
use secrecy::SecretString;
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::error::E2bError;

macro_rules! string_id {
    ($name:ident, $label:literal) => {
        #[derive(Clone, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
        #[serde(transparent)]
        pub struct $name(String);

        impl $name {
            pub fn parse(value: impl Into<String>) -> Result<Self, E2bError> {
                let value = value.into();
                let trimmed = value.trim();
                let safe = value.bytes().all(|byte| {
                    byte.is_ascii_alphanumeric() || b"._:/-".contains(&byte)
                });
                if trimmed.is_empty() || trimmed.len() > 256 || trimmed != value || !safe {
                    return Err(E2bError::InvalidInput(format!(
                        "{} must use 1-256 ASCII alphanumeric, dot, underscore, colon, slash, or dash characters",
                        $label
                    )));
                }
                Ok(Self(value))
            }

            #[must_use]
            pub fn as_str(&self) -> &str {
                &self.0
            }
        }

        impl fmt::Debug for $name {
            fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                f.debug_tuple(stringify!($name)).field(&self.0).finish()
            }
        }

        impl fmt::Display for $name {
            fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                f.write_str(&self.0)
            }
        }
    };
}

string_id!(SandboxId, "sandbox id");

/// Operating-system account selected for an envd operation.
#[derive(Clone, Debug, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
#[serde(transparent)]
pub struct SandboxUser(String);

impl SandboxUser {
    pub fn parse(value: impl Into<String>) -> Result<Self, E2bError> {
        let value = value.into();
        let valid = !value.is_empty()
            && value.len() <= 64
            && value
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_' || byte == b'-');
        if !valid {
            return Err(E2bError::InvalidInput(
                "sandbox username contains unsupported characters".to_owned(),
            ));
        }
        Ok(Self(value))
    }

    #[must_use]
    pub fn root() -> Self {
        Self("root".to_owned())
    }

    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

/// Unique, durable tag used to reconcile an ambiguously delivered process start.
#[derive(Clone, Debug, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
#[serde(transparent)]
pub struct ExecutionTag(String);

impl ExecutionTag {
    #[must_use]
    pub fn new() -> Self {
        Self(format!("newsly-{}", Uuid::new_v4().simple()))
    }

    pub fn parse(value: impl Into<String>) -> Result<Self, E2bError> {
        let value = value.into();
        let valid = !value.is_empty()
            && value.len() <= 128
            && value
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || b"._:-".contains(&byte));
        if !valid {
            return Err(E2bError::InvalidInput(
                "execution tag must use 1-128 ASCII alphanumeric, dot, underscore, colon, or dash characters"
                    .to_owned(),
            ));
        }
        Ok(Self(value))
    }

    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl Default for ExecutionTag {
    fn default() -> Self {
        Self::new()
    }
}

impl fmt::Display for ExecutionTag {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

/// Relative path underneath the sandbox user's workspace.
#[derive(Clone, Debug, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
#[serde(transparent)]
pub struct WorkspacePath(String);

impl WorkspacePath {
    pub fn parse(value: impl Into<String>) -> Result<Self, E2bError> {
        let value = value.into();
        if value.is_empty() || value.len() > 4096 || value.contains('\0') {
            return Err(E2bError::InvalidInput("invalid workspace path".to_owned()));
        }
        let path = Path::new(&value);
        if path.is_absolute()
            || path.components().any(|component| {
                matches!(
                    component,
                    Component::ParentDir | Component::RootDir | Component::Prefix(_)
                )
            })
        {
            return Err(E2bError::InvalidInput("workspace_escape".to_owned()));
        }
        let normalized = path
            .components()
            .filter_map(|component| match component {
                Component::Normal(value) => value.to_str(),
                _ => None,
            })
            .collect::<Vec<_>>()
            .join("/");
        if normalized.is_empty() {
            return Err(E2bError::InvalidInput("invalid workspace path".to_owned()));
        }
        Ok(Self(normalized))
    }

    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

/// Absolute path used only by trusted host-side sandbox lifecycle operations.
///
/// Agent-authored paths remain [`WorkspacePath`] values. Keeping this type separate prevents a
/// corpus or lifecycle caller from accidentally weakening the workspace-escape boundary.
#[derive(Clone, Debug, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
#[serde(transparent)]
pub struct SandboxPath(String);

impl SandboxPath {
    pub fn parse(value: impl Into<String>) -> Result<Self, E2bError> {
        let value = value.into();
        if value.len() < 2
            || value.len() > 4096
            || value.contains('\0')
            || value.contains('\\')
            || value.chars().any(char::is_control)
        {
            return Err(E2bError::InvalidInput("invalid sandbox path".to_owned()));
        }
        let path = Path::new(&value);
        if !path.is_absolute()
            || path.components().any(|component| {
                matches!(
                    component,
                    Component::ParentDir | Component::CurDir | Component::Prefix(_)
                )
            })
        {
            return Err(E2bError::InvalidInput("sandbox_path_escape".to_owned()));
        }
        let normalized = format!(
            "/{}",
            path.components()
                .filter_map(|component| match component {
                    Component::Normal(value) => value.to_str(),
                    _ => None,
                })
                .collect::<Vec<_>>()
                .join("/")
        );
        if normalized == "/" || normalized != value {
            return Err(E2bError::InvalidInput("invalid sandbox path".to_owned()));
        }
        Ok(Self(normalized))
    }

    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

/// E2B sandbox connection details. Secret fields are redacted from `Debug`.
#[derive(Clone)]
pub struct SandboxHandle {
    pub sandbox_id: SandboxId,
    pub template_id: String,
    pub envd_version: String,
    pub sandbox_domain: String,
    pub envd_access_token: Option<SecretString>,
    pub traffic_access_token: Option<SecretString>,
}

impl fmt::Debug for SandboxHandle {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("SandboxHandle")
            .field("sandbox_id", &self.sandbox_id)
            .field("template_id", &self.template_id)
            .field("envd_version", &self.envd_version)
            .field("sandbox_domain", &self.sandbox_domain)
            .field(
                "envd_access_token",
                &self.envd_access_token.as_ref().map(|_| "[REDACTED]"),
            )
            .field(
                "traffic_access_token",
                &self.traffic_access_token.as_ref().map(|_| "[REDACTED]"),
            )
            .finish()
    }
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
#[allow(clippy::struct_excessive_bools)]
pub struct SandboxRequest {
    #[serde(rename = "templateID")]
    pub template_id: String,
    pub timeout: u32,
    pub auto_pause: bool,
    #[serde(skip_serializing_if = "is_false")]
    pub auto_pause_memory: bool,
    pub secure: bool,
    #[serde(rename = "allow_internet_access")]
    pub allow_internet_access: bool,
    #[serde(skip_serializing_if = "BTreeMap::is_empty")]
    pub metadata: BTreeMap<String, String>,
    #[serde(rename = "envVars", skip_serializing_if = "BTreeMap::is_empty")]
    pub env_vars: BTreeMap<String, String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub network: Option<crate::network::NetworkPolicy>,
}

#[expect(
    clippy::trivially_copy_pass_by_ref,
    reason = "Serde skip_serializing_if predicates receive a shared reference"
)]
const fn is_false(value: &bool) -> bool {
    !*value
}

impl SandboxRequest {
    pub fn validate(&self) -> Result<(), E2bError> {
        if self.template_id.trim().is_empty() {
            return Err(E2bError::InvalidInput("template id is required".to_owned()));
        }
        if self.timeout == 0 {
            return Err(E2bError::InvalidInput(
                "sandbox timeout must be greater than zero".to_owned(),
            ));
        }
        if let Some(network) = &self.network {
            network.validate()?;
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ProcessSelector {
    Pid(u32),
    Tag(ExecutionTag),
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ProcessInfo {
    pub pid: u32,
    pub tag: Option<ExecutionTag>,
    pub command: String,
    pub args: Vec<String>,
    pub cwd: Option<String>,
}

#[derive(Clone, Debug)]
pub struct CommandRequest {
    pub command: String,
    pub args: Vec<String>,
    pub env: BTreeMap<String, String>,
    pub cwd: Option<String>,
    pub username: Option<SandboxUser>,
    pub tag: ExecutionTag,
    pub stdin_enabled: bool,
    pub absolute_deadline: tokio::time::Instant,
    pub idle_timeout: Duration,
    pub output_limits: OutputLimits,
}

impl CommandRequest {
    pub fn validate(&self) -> Result<(), E2bError> {
        if self.command.trim().is_empty() {
            return Err(E2bError::InvalidInput("command is required".to_owned()));
        }
        if self.absolute_deadline <= tokio::time::Instant::now() {
            return Err(E2bError::Deadline);
        }
        if self.idle_timeout.is_zero() {
            return Err(E2bError::InvalidInput(
                "command idle timeout must be greater than zero".to_owned(),
            ));
        }
        self.output_limits.validate()
    }
}

#[derive(Clone, Copy, Debug)]
pub struct OutputLimits {
    pub stdout_bytes: usize,
    pub stderr_bytes: usize,
    pub combined_bytes: usize,
    pub event_bytes: usize,
    pub channel_capacity: usize,
}

impl Default for OutputLimits {
    fn default() -> Self {
        Self {
            stdout_bytes: 2 * 1024 * 1024,
            stderr_bytes: 2 * 1024 * 1024,
            combined_bytes: 4 * 1024 * 1024,
            event_bytes: 256 * 1024,
            channel_capacity: 32,
        }
    }
}

impl OutputLimits {
    pub fn validate(self) -> Result<(), E2bError> {
        if self.stdout_bytes == 0
            || self.stderr_bytes == 0
            || self.combined_bytes == 0
            || self.event_bytes == 0
            || self.channel_capacity == 0
        {
            return Err(E2bError::InvalidInput(
                "all command output limits must be greater than zero".to_owned(),
            ));
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum CommandEvent {
    Started {
        sequence: u64,
        sandbox_id: SandboxId,
        execution_tag: ExecutionTag,
        pid: u32,
    },
    Stdout {
        sequence: u64,
        bytes: Bytes,
        text: String,
    },
    Stderr {
        sequence: u64,
        bytes: Bytes,
        text: String,
    },
    Pty {
        sequence: u64,
        bytes: Bytes,
        text: String,
    },
    KeepAlive {
        sequence: u64,
    },
    Exited {
        sequence: u64,
        status: ExitStatus,
        exit_code: i32,
        error: Option<String>,
    },
    TransportDisconnected {
        sequence: u64,
    },
}

impl CommandEvent {
    #[must_use]
    pub fn sequence(&self) -> u64 {
        match self {
            Self::Started { sequence, .. }
            | Self::Stdout { sequence, .. }
            | Self::Stderr { sequence, .. }
            | Self::Pty { sequence, .. }
            | Self::KeepAlive { sequence }
            | Self::Exited { sequence, .. }
            | Self::TransportDisconnected { sequence } => *sequence,
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ExitStatus {
    Exited,
    Signalled,
    Failed,
    Unknown(String),
}

impl ExitStatus {
    #[must_use]
    pub fn from_wire(status: &str, exited: bool, exit_code: i32) -> Self {
        let normalized = status.trim().to_ascii_lowercase();
        if let Some(code) = normalized
            .strip_prefix("exit status ")
            .and_then(|value| value.parse::<i32>().ok())
        {
            return if code == 0 {
                Self::Exited
            } else {
                Self::Failed
            };
        }
        match normalized.as_str() {
            "exited" | "completed" | "done" | "finished" => Self::Exited,
            "signalled" | "signaled" | "killed" | "terminated" => Self::Signalled,
            "failed" | "error" => Self::Failed,
            "" => {
                if exited || exit_code == 0 {
                    Self::Exited
                } else {
                    Self::Failed
                }
            }
            other => Self::Unknown(other.to_owned()),
        }
    }
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct CommandOutput {
    pub stdout: String,
    pub stderr: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct CommandResult {
    pub execution_tag: ExecutionTag,
    pub pid: Option<u32>,
    pub output: CommandOutput,
    pub status: ExitStatus,
    pub exit_code: i32,
    pub error: Option<String>,
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeMap;

    use super::{ExitStatus, SandboxPath, SandboxRequest, SandboxUser, WorkspacePath};

    #[test]
    fn disabled_auto_pause_memory_is_omitted_from_the_control_plane_request() {
        let request = SandboxRequest {
            template_id: "template".to_owned(),
            timeout: 60,
            auto_pause: false,
            auto_pause_memory: false,
            secure: true,
            allow_internet_access: false,
            metadata: BTreeMap::new(),
            env_vars: BTreeMap::new(),
            network: None,
        };

        let value = serde_json::to_value(request).expect("sandbox request serializes");
        assert_eq!(value["autoPause"], false);
        assert_eq!(value["allow_internet_access"], false);
        assert!(value.get("allowInternetAccess").is_none());
        assert!(value.get("autoPauseMemory").is_none());
    }

    #[test]
    fn enabled_auto_pause_memory_is_sent_to_the_control_plane() {
        let request = SandboxRequest {
            template_id: "template".to_owned(),
            timeout: 60,
            auto_pause: true,
            auto_pause_memory: true,
            secure: true,
            allow_internet_access: false,
            metadata: BTreeMap::new(),
            env_vars: BTreeMap::new(),
            network: None,
        };

        let value = serde_json::to_value(request).expect("sandbox request serializes");
        assert_eq!(value["autoPause"], true);
        assert_eq!(value["autoPauseMemory"], true);
    }

    #[test]
    fn envd_go_style_exit_status_is_normalized() {
        assert_eq!(
            ExitStatus::from_wire("exit status 0", false, 0),
            ExitStatus::Exited
        );
        assert_eq!(
            ExitStatus::from_wire("exit status 17", true, 17),
            ExitStatus::Failed
        );
    }

    #[test]
    fn workspace_paths_match_the_command_stream_contract() {
        let fixture: serde_json::Value = serde_json::from_str(include_str!(
            "../../../../contracts/llm/e2b-command-stream.json"
        ))
        .expect("fixture must be valid JSON");
        for case in fixture["path_cases"].as_array().expect("path cases") {
            let input = case["input"].as_str().expect("input");
            let accepted = case["accepted"].as_bool().expect("accepted");
            let parsed = WorkspacePath::parse(input);
            assert_eq!(parsed.is_ok(), accepted, "path case {input}");
            if accepted {
                assert_eq!(parsed.expect("accepted").as_str(), case["resolved"]);
            }
        }
    }

    #[test]
    fn trusted_sandbox_paths_are_absolute_and_normalized() {
        assert_eq!(
            SandboxPath::parse("/data/manifest.json")
                .expect("normalized absolute path")
                .as_str(),
            "/data/manifest.json"
        );
        for rejected in [
            "data/manifest.json",
            "/",
            "/data/../etc/passwd",
            "/data//file",
        ] {
            assert!(SandboxPath::parse(rejected).is_err(), "accepted {rejected}");
        }
    }

    #[test]
    fn sandbox_users_reject_basic_auth_delimiters() {
        assert_eq!(SandboxUser::root().as_str(), "root");
        assert!(SandboxUser::parse("user:name").is_err());
        assert!(SandboxUser::parse("user\nroot").is_err());
    }
}
