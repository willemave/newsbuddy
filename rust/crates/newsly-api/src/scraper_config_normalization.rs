use std::collections::BTreeSet;

use newsly_contracts::ScraperType;
use newsly_db::canonicalize_feed_url;
use serde_json::{Map, Number, Value};
use thiserror::Error;

pub(super) const DEFAULT_NEW_FEED_LIMIT: i64 = 1;
const AGGREGATOR_FEED_URL_PREFIX: &str = "aggregator://";
const SUPPORTED_AGGREGATOR_KEYS: [&str; 7] = [
    "brutalist",
    "finurls",
    "hackernews",
    "mediagazer",
    "memeorandum",
    "sciurls",
    "techmeme",
];

pub(super) fn validate_display_name(value: Option<&str>) -> Result<(), ConfigValidationError> {
    if value.is_some_and(|value| value.chars().count() > 255) {
        return Err(ConfigValidationError::new(
            "display_name must contain at most 255 characters",
        ));
    }
    Ok(())
}

/// Reproduce the request-model validation that runs before the Python create handler.
pub(super) fn normalize_create_input(
    scraper_type: ScraperType,
    config: Map<String, Value>,
) -> Result<Map<String, Value>, ConfigValidationError> {
    let mut config = normalize_for_type(scraper_type.as_str(), config)?;
    config
        .entry("limit".to_owned())
        .or_insert_with(|| Value::Number(Number::from(DEFAULT_NEW_FEED_LIMIT)));
    Ok(config)
}

/// Reproduce the shape-directed Pydantic update validator before loading the database row.
pub(super) fn normalize_update_input(
    config: Map<String, Value>,
) -> Result<Map<String, Value>, ConfigValidationError> {
    let feed_url = config.get("feed_url");
    let channel_id = config.get("channel_id");
    let playlist_id = config.get("playlist_id");
    let subreddit = truthy(config.get("subreddit"))
        .then(|| config.get("subreddit"))
        .flatten()
        .or_else(|| {
            truthy(config.get("name"))
                .then(|| config.get("name"))
                .flatten()
        });
    let aggregator_key = config.get("key");

    if truthy(aggregator_key)
        && (!truthy(feed_url)
            || feed_url
                .and_then(Value::as_str)
                .is_some_and(|value| value.starts_with(AGGREGATOR_FEED_URL_PREFIX)))
    {
        return normalize_aggregator_config(config);
    }
    if !truthy(feed_url) && (truthy(channel_id) || truthy(playlist_id)) {
        return normalize_youtube_config(config);
    }
    if subreddit.is_some() && !truthy(feed_url) {
        return normalize_reddit_config(config);
    }
    normalize_feed_config(config)
}

pub(super) fn normalize_for_stored_type(
    scraper_type: &str,
    config: Map<String, Value>,
) -> Result<Map<String, Value>, ConfigValidationError> {
    normalize_for_type(scraper_type, config)
}

pub(super) fn requires_feed_probe(scraper_type: &str) -> bool {
    matches!(scraper_type, "substack" | "atom" | "podcast_rss")
}

pub(super) fn apply_validated_feed_url(config: &mut Map<String, Value>, effective_url: &str) {
    config.insert(
        "feed_url".to_owned(),
        Value::String(canonicalize_feed_url(effective_url)),
    );
}

pub(super) fn feed_url(config: &Map<String, Value>) -> &str {
    config
        .get("feed_url")
        .and_then(Value::as_str)
        .unwrap_or_default()
}

pub(super) fn response_limit(config: &Map<String, Value>) -> Option<i64> {
    match config.get("limit") {
        Some(Value::Bool(true)) => Some(1),
        Some(Value::Number(value)) => value.as_i64().filter(|value| (1..=100).contains(value)),
        _ => None,
    }
}

fn normalize_for_type(
    scraper_type: &str,
    config: Map<String, Value>,
) -> Result<Map<String, Value>, ConfigValidationError> {
    match scraper_type {
        "youtube" => normalize_youtube_config(config),
        "reddit" => normalize_reddit_config(config),
        "aggregator" => normalize_aggregator_config(config),
        "substack" | "atom" | "podcast_rss" => normalize_feed_config(config),
        _ => Err(ConfigValidationError::new("Unsupported scraper_type")),
    }
}

fn normalize_feed_config(
    mut config: Map<String, Value>,
) -> Result<Map<String, Value>, ConfigValidationError> {
    let feed_url = config
        .get("feed_url")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| ConfigValidationError::new("config.feed_url is required"))?;
    let feed_url = canonicalize_feed_url(feed_url);
    validate_limit(config.get("limit"))?;
    config.insert("feed_url".to_owned(), Value::String(feed_url));
    Ok(config)
}

fn normalize_youtube_config(
    mut config: Map<String, Value>,
) -> Result<Map<String, Value>, ConfigValidationError> {
    let channel_id = trimmed_string(config.get("channel_id"));
    let playlist_id = trimmed_string(config.get("playlist_id"));
    let feed_url = first_truthy(config.get("feed_url"), config.get("url"))
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
        .or_else(|| {
            playlist_id
                .as_ref()
                .filter(|value| !value.is_empty())
                .map(|value| format!("https://www.youtube.com/playlist?list={value}"))
        })
        .or_else(|| {
            channel_id
                .as_ref()
                .filter(|value| !value.is_empty())
                .map(|value| format!("https://www.youtube.com/channel/{value}"))
        })
        .ok_or_else(|| {
            ConfigValidationError::new(
                "youtube config requires feed_url, channel_id, or playlist_id",
            )
        })?;
    config.insert("feed_url".to_owned(), Value::String(feed_url));
    if let Some(channel_id) = channel_id.filter(|value| !value.is_empty()) {
        config.insert("channel_id".to_owned(), Value::String(channel_id));
    }
    if let Some(playlist_id) = playlist_id.filter(|value| !value.is_empty()) {
        config.insert("playlist_id".to_owned(), Value::String(playlist_id));
    }
    validate_limit(config.get("limit"))?;
    Ok(config)
}

fn normalize_reddit_config(
    mut config: Map<String, Value>,
) -> Result<Map<String, Value>, ConfigValidationError> {
    let raw_subreddit = first_truthy(config.get("subreddit"), config.get("name"))
        .and_then(Value::as_str)
        .map(str::trim)
        .unwrap_or_default();
    let subreddit = raw_subreddit
        .strip_prefix("r/")
        .unwrap_or(raw_subreddit)
        .trim_matches('/')
        .to_owned();
    if subreddit.is_empty() {
        return Err(ConfigValidationError::new("config.subreddit is required"));
    }
    config.insert("subreddit".to_owned(), Value::String(subreddit.clone()));
    config.insert(
        "feed_url".to_owned(),
        Value::String(format!("https://www.reddit.com/r/{subreddit}/")),
    );
    validate_limit(config.get("limit"))?;
    Ok(config)
}

fn normalize_aggregator_config(
    mut config: Map<String, Value>,
) -> Result<Map<String, Value>, ConfigValidationError> {
    let key = python_string(config.get("key")).trim().to_lowercase();
    if key.is_empty() {
        return Err(ConfigValidationError::new(
            "config.key is required for aggregator subscriptions",
        ));
    }
    if !SUPPORTED_AGGREGATOR_KEYS.contains(&key.as_str()) {
        return Err(ConfigValidationError::new(format!(
            "unsupported aggregator key: {key}"
        )));
    }
    config.insert("key".to_owned(), Value::String(key.clone()));
    config.insert(
        "feed_url".to_owned(),
        Value::String(format!("{AGGREGATOR_FEED_URL_PREFIX}{key}")),
    );

    if let Some(raw_topics) = config.get("topics") {
        let topics = raw_topics
            .as_array()
            .ok_or_else(|| ConfigValidationError::new("config.topics must be a list of strings"))?;
        if topics.iter().any(|topic| !topic.is_string()) {
            return Err(ConfigValidationError::new(
                "config.topics must be a list of strings",
            ));
        }
        let topics = topics
            .iter()
            .filter_map(Value::as_str)
            .map(str::trim)
            .filter(|topic| !topic.is_empty())
            .map(str::to_lowercase)
            .collect::<BTreeSet<_>>();
        if topics.is_empty() {
            config.remove("topics");
        } else {
            config.insert(
                "topics".to_owned(),
                Value::Array(topics.into_iter().map(Value::String).collect()),
            );
        }
    }
    Ok(config)
}

fn validate_limit(value: Option<&Value>) -> Result<(), ConfigValidationError> {
    let valid = match value {
        None | Some(Value::Null) => true,
        Some(Value::Bool(value)) => *value,
        Some(Value::Number(value)) => value
            .as_i64()
            .is_some_and(|value| (1..=100).contains(&value)),
        _ => false,
    };
    if valid {
        Ok(())
    } else {
        Err(ConfigValidationError::new(
            "config.limit must be an integer between 1 and 100",
        ))
    }
}

fn trimmed_string(value: Option<&Value>) -> Option<String> {
    value
        .and_then(Value::as_str)
        .map(|value| value.trim().to_owned())
}

fn first_truthy<'a>(first: Option<&'a Value>, second: Option<&'a Value>) -> Option<&'a Value> {
    if truthy(first) { first } else { second }
}

fn truthy(value: Option<&Value>) -> bool {
    match value {
        None | Some(Value::Null) => false,
        Some(Value::Bool(value)) => *value,
        Some(Value::Number(value)) => value.as_i64().map_or_else(
            || value.as_f64().is_some_and(|value| value != 0.0),
            |value| value != 0,
        ),
        Some(Value::String(value)) => !value.is_empty(),
        Some(Value::Array(value)) => !value.is_empty(),
        Some(Value::Object(value)) => !value.is_empty(),
    }
}

fn python_string(value: Option<&Value>) -> String {
    match value {
        None | Some(Value::Null) => String::new(),
        Some(Value::String(value)) => value.clone(),
        Some(Value::Bool(true)) => "True".to_owned(),
        Some(Value::Bool(false)) => "False".to_owned(),
        Some(value) => value.to_string(),
    }
}

#[derive(Debug, Clone, Error)]
#[error("{message}")]
pub(super) struct ConfigValidationError {
    message: String,
}

impl ConfigValidationError {
    fn new(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
        }
    }
}

#[cfg(test)]
mod tests {
    use newsly_contracts::ScraperType;
    use serde_json::{Map, json};

    use super::{DEFAULT_NEW_FEED_LIMIT, normalize_create_input};

    #[test]
    fn create_normalizes_reddit_and_default_limit() {
        let config = serde_json::from_value::<Map<String, serde_json::Value>>(json!({
            "subreddit": " r/MachineLearning/ "
        }))
        .unwrap();
        let config = normalize_create_input(ScraperType::Reddit, config).unwrap();
        assert_eq!(config["subreddit"], "MachineLearning");
        assert_eq!(
            config["feed_url"],
            "https://www.reddit.com/r/MachineLearning/"
        );
        assert_eq!(config["limit"], DEFAULT_NEW_FEED_LIMIT);
    }
}
