use newsly_agent_runtime::AgentRuntimeError;
use newsly_e2b::OutputLimits;

pub(super) fn bounded_query(value: &str) -> Result<&str, AgentRuntimeError> {
    let value = value.trim();
    if value.is_empty() || value.chars().count() > 2_000 {
        Err(AgentRuntimeError::Tool(
            "query must contain 1-2000 characters".to_owned(),
        ))
    } else {
        Ok(value)
    }
}

pub(super) fn chat_output_limits(maximum_chars: usize) -> OutputLimits {
    let channel = maximum_chars.saturating_mul(4).clamp(4_000, 800_000);
    OutputLimits {
        stdout_bytes: channel,
        stderr_bytes: channel,
        combined_bytes: channel.saturating_mul(2),
        event_bytes: channel,
        channel_capacity: 32,
    }
}

pub(super) fn tail_chars(value: &str, maximum: usize) -> String {
    let count = value.chars().count();
    if count <= maximum {
        value.to_owned()
    } else {
        value.chars().skip(count - maximum).collect()
    }
}

pub(super) fn valid_url(value: &str) -> Result<String, AgentRuntimeError> {
    let parsed = reqwest::Url::parse(value.trim())
        .map_err(|_| AgentRuntimeError::Tool("URL is invalid".to_owned()))?;
    if !matches!(parsed.scheme(), "http" | "https") || parsed.host_str().is_none() {
        return Err(AgentRuntimeError::Tool(
            "URL must use http or https".to_owned(),
        ));
    }
    Ok(parsed.to_string())
}

pub(super) fn clean(value: Option<String>) -> Option<String> {
    value.and_then(|value| {
        let value = value.trim();
        (!value.is_empty()).then(|| value.chars().take(500).collect())
    })
}
