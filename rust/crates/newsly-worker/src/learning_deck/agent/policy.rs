use std::collections::BTreeSet;
use std::net::IpAddr;
use std::time::Duration;

use newsly_agent_runtime::{AgentLimits, ToolDefinition};
use newsly_db::LearningDeckTaskSnapshot;
use serde_json::{Map, Value};

pub(super) fn allowed_tools(
    task: &LearningDeckTaskSnapshot,
    definitions: &[ToolDefinition],
) -> BTreeSet<String> {
    let files = task.tool_policy.get("files");
    let files_enabled = policy_enabled(files, true);
    let write_enabled = files_enabled
        && !files
            .and_then(Value::as_str)
            .is_some_and(|value| matches!(value, "read" | "read_only" | "readonly"));
    definitions
        .iter()
        .filter(|tool| match tool.name.as_str() {
            "execute_bash" => policy_enabled(task.tool_policy.get("execute_bash"), true),
            "web_search" => policy_enabled(task.tool_policy.get("web_search"), true),
            "read_file" | "list_files" => files_enabled,
            "write_file" | "edit_file" | "write_knowledge_items" => write_enabled,
            "search_knowledge" | "read_knowledge_item" => true,
            _ => false,
        })
        .map(|tool| tool.name.clone())
        .collect()
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

pub(super) fn source_provider_parameters(_source: &Map<String, Value>) -> Map<String, Value> {
    Map::new()
}

pub(super) fn learning_deck_agent_limits(tool_call_limit: u32, deadline: Duration) -> AgentLimits {
    AgentLimits {
        request_limit: None,
        tool_call_limit,
        output_token_limit: None,
        deadline,
    }
}

pub(super) fn ip_selector(address: IpAddr) -> String {
    let prefix = if address.is_ipv4() { 32 } else { 128 };
    format!("{address}/{prefix}")
}
