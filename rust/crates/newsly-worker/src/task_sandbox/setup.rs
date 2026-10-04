use std::env;
use std::net::IpAddr;
use std::sync::Arc;
use std::time::Duration;

use newsly_agent_runtime::ToolDefinition;
use newsly_e2b::{
    CommandRequest, CommandResult, ExecutionTag, OutputLimits, SandboxHandle, SandboxUser,
};
use newsly_e2b::{ControlPlaneConfig, DirectE2bProvider, E2bError, FileLimits, NetworkPolicy};
use reqwest::Url;
use secrecy::SecretString;
use serde_json::{Map, Value};
use tokio::time::Instant;
use tokio_util::sync::CancellationToken;

const DEFAULT_EXA_API_BASE: &str = "https://api.exa.ai/search";
const PRIVATE_NETWORK_DENIALS: [&str; 9] = [
    "10.0.0.0/8",
    "100.64.0.0/10",
    "169.254.0.0/16",
    "172.16.0.0/12",
    "192.0.0.0/24",
    "192.168.0.0/16",
    "198.18.0.0/15",
    "fc00::/7",
    "fe80::/10",
];

pub(crate) fn secret_env(name: &str) -> Option<SecretString> {
    env::var(name)
        .ok()
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty())
        .map(SecretString::from)
}

pub(crate) fn secret_env_alias(names: &[&str]) -> Option<SecretString> {
    names.iter().find_map(|name| secret_env(name))
}

pub(crate) fn exa_endpoint() -> Result<Url, url::ParseError> {
    env::var("EXA_API_BASE_URL")
        .ok()
        .map(|value| value.trim_end_matches('/').to_owned())
        .map_or_else(
            || Url::parse(DEFAULT_EXA_API_BASE),
            |value| Url::parse(&format!("{value}/search")),
        )
}

pub(crate) fn direct_provider(
    api_key: SecretString,
    transfer_limit: u64,
) -> Result<Arc<DirectE2bProvider>, E2bError> {
    DirectE2bProvider::new(
        ControlPlaneConfig::production(api_key)?,
        FileLimits {
            upload_bytes: transfer_limit,
            download_bytes: transfer_limit,
        },
    )
    .map(Arc::new)
}

pub(crate) struct SandboxCommand {
    pub command: String,
    pub args: Vec<String>,
    pub cwd: Option<String>,
    pub deadline: Instant,
    pub idle_timeout: Duration,
    pub output_limits: OutputLimits,
}

pub(crate) async fn run_command(
    provider: &DirectE2bProvider,
    sandbox: &SandboxHandle,
    options: SandboxCommand,
    cancellation: CancellationToken,
) -> Result<CommandResult, E2bError> {
    let stream = provider
        .start_process(
            sandbox,
            CommandRequest {
                command: options.command,
                args: options.args,
                env: std::collections::BTreeMap::new(),
                cwd: options.cwd,
                username: Some(SandboxUser::parse("user")?),
                tag: ExecutionTag::new(),
                stdin_enabled: false,
                absolute_deadline: options.deadline,
                idle_timeout: options.idle_timeout,
                output_limits: options.output_limits,
            },
            cancellation,
        )
        .await?;
    stream.collect_result().await
}

pub(crate) fn allowed_tools(
    policy: &Map<String, Value>,
    definitions: &[ToolDefinition],
) -> std::collections::BTreeSet<String> {
    let files = policy.get("files");
    let files_enabled = policy_enabled(files, true);
    let write_enabled = files_enabled
        && !files
            .and_then(Value::as_str)
            .is_some_and(|value| matches!(value, "read" | "read_only" | "readonly"));
    definitions
        .iter()
        .filter(|tool| match tool.name.as_str() {
            "execute_bash" => policy_enabled(policy.get("execute_bash"), true),
            "web_search" => policy_enabled(policy.get("web_search"), true),
            "read_file" | "list_files" => files_enabled,
            "write_file" | "edit_file" | "write_knowledge_items" => write_enabled,
            "search_knowledge" | "read_knowledge_item" => true,
            _ => false,
        })
        .map(|tool| tool.name.clone())
        .collect()
}

pub(crate) async fn agent_network_policy(
    public_base_url: Option<&Url>,
    block_public_origin: bool,
    operation: &'static str,
) -> NetworkPolicy {
    let mut deny_out = PRIVATE_NETWORK_DENIALS
        .into_iter()
        .map(str::to_owned)
        .collect::<Vec<_>>();
    if block_public_origin
        && let Some(base_url) = public_base_url
        && let Some(host) = base_url.host_str()
    {
        let port = base_url.port_or_known_default().unwrap_or(443);
        match tokio::time::timeout(
            Duration::from_secs(5),
            tokio::net::lookup_host((host, port)),
        )
        .await
        {
            Ok(Ok(addresses)) => {
                deny_out.extend(addresses.map(|address| ip_selector(address.ip())));
            }
            Ok(Err(error)) => {
                tracing::warn!(host, error = %error, operation, "unable to resolve Newsly origin for VM egress denial");
            }
            Err(_) => {
                tracing::warn!(
                    host,
                    operation,
                    "timed out resolving Newsly origin for VM egress denial"
                );
            }
        }
    }
    deny_out.sort();
    deny_out.dedup();
    NetworkPolicy {
        deny_out,
        allow_internet_access: Some(true),
        ..NetworkPolicy::default()
    }
}

fn policy_enabled(value: Option<&Value>, default: bool) -> bool {
    match value {
        None | Some(Value::Null | Value::Array(_) | Value::Object(_)) => default,
        Some(Value::Bool(value)) => *value,
        Some(Value::String(value)) => !matches!(
            value.trim().to_ascii_lowercase().as_str(),
            "none" | "disabled" | "off" | "false" | "0"
        ),
        Some(Value::Number(value)) => value.as_i64() != Some(0),
    }
}

fn ip_selector(address: IpAddr) -> String {
    let prefix = if address.is_ipv4() { 32 } else { 128 };
    format!("{address}/{prefix}")
}
