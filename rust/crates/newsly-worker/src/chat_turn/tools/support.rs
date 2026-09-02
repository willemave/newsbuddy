use newsly_agent_runtime::AgentRuntimeError;
use newsly_contracts::{AssistantFeedOption, FeedFormat, FeedType};
use newsly_e2b::OutputLimits;
use sha1::{Digest, Sha1};

pub(super) fn assistant_feed_option(
    suggestion: newsly_db::NewOnboardingSuggestion,
) -> Option<AssistantFeedOption> {
    let feed_url = suggestion
        .feed_url
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty() && value.chars().count() <= 2_048)?;
    let site_url = suggestion
        .site_url
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty() && value.chars().count() <= 2_048)
        .unwrap_or_else(|| feed_url.clone());
    let feed_type = match suggestion.suggestion_type.as_str() {
        "atom" => FeedType::Atom,
        "substack" => FeedType::Substack,
        "podcast_rss" => FeedType::PodcastRss,
        _ => return None,
    };
    let title = suggestion
        .title
        .as_deref()
        .and_then(|value| bounded_text(value, 300))
        .or_else(|| feed_host_label(&site_url))
        .unwrap_or_else(|| feed_url.clone());
    let rationale = suggestion
        .rationale
        .as_deref()
        .and_then(|value| bounded_text(value, 600));
    let digest = Sha1::digest(feed_url.as_bytes());
    let id = format!("{digest:x}").chars().take(16).collect();
    let feed_format =
        if feed_type == FeedType::Atom || feed_url.to_ascii_lowercase().contains("atom") {
            FeedFormat::Atom
        } else {
            FeedFormat::Rss
        };
    Some(AssistantFeedOption {
        id,
        title,
        site_url: site_url.clone(),
        feed_url,
        feed_type,
        feed_format,
        description: None,
        rationale,
        evidence_url: Some(site_url),
        is_subscribed: false,
    })
}

fn bounded_text(value: &str, maximum: usize) -> Option<String> {
    let value = value.trim();
    if value.is_empty() {
        return None;
    }
    let count = value.chars().count();
    if count <= maximum {
        Some(value.to_owned())
    } else if maximum > 3 {
        Some(format!(
            "{}...",
            value
                .chars()
                .take(maximum - 3)
                .collect::<String>()
                .trim_end()
        ))
    } else {
        Some(value.chars().take(maximum).collect())
    }
}

fn feed_host_label(value: &str) -> Option<String> {
    reqwest::Url::parse(value)
        .ok()?
        .host_str()
        .map(|host| host.trim_start_matches("www.").to_owned())
        .filter(|host| !host.is_empty())
}

pub(super) fn looks_like_podcast_query(value: &str) -> bool {
    let value = value.to_ascii_lowercase();
    [
        "podcast", "podcasts", "episode", "episodes", "show", "shows",
    ]
    .into_iter()
    .any(|hint| value.contains(hint))
}

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
