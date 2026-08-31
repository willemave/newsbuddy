use newsly_contracts::{ShareActionAgentResult, ShareActionBriefingTarget, ShareActionCandidate};
use newsly_db::ShareActionAgentSnapshot;
use newsly_e2b::ValidatedFeed;
use newsly_queue::python_canonical_json;
use reqwest::Url;
use serde::Deserialize;
use serde_json::{Map, Value};
use sha2::{Digest, Sha256};
use thiserror::Error;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum ShareActionWorkflow {
    AddContent,
    AddToBriefing,
    AddLinks,
    AddFeed,
    Chat,
    Presentation,
    BookmarkOnly,
}

impl ShareActionWorkflow {
    pub(super) fn parse(value: &str) -> Result<Self, ShareActionWorkflowError> {
        match value {
            "add_content" => Ok(Self::AddContent),
            "add_to_briefing" => Ok(Self::AddToBriefing),
            "add_links" => Ok(Self::AddLinks),
            "add_feed" => Ok(Self::AddFeed),
            "chat" => Ok(Self::Chat),
            "presentation" => Ok(Self::Presentation),
            "bookmark_only" => Ok(Self::BookmarkOnly),
            other => Err(ShareActionWorkflowError::UnsupportedMode(other.to_owned())),
        }
    }

    pub(super) const fn host_action_name(self) -> &'static str {
        match self {
            Self::AddContent => "add_content",
            Self::AddToBriefing => "add_to_briefing",
            Self::AddLinks => "add_links",
            Self::AddFeed => "subscribe_to_feed",
            Self::Chat => "enqueue_chat",
            Self::Presentation => "create_learning_deck",
            Self::BookmarkOnly => "save_to_knowledge",
        }
    }

    const fn result_action(self) -> &'static str {
        match self {
            Self::AddContent => "add_content",
            Self::AddToBriefing => "add_to_briefing",
            Self::AddLinks => "add_links",
            Self::AddFeed => "add_feed",
            Self::Chat => "chat",
            Self::Presentation => "presentation",
            Self::BookmarkOnly => "bookmark_only",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ContentActionInput {
    pub url: String,
    pub title: Option<String>,
    pub platform: Option<String>,
    pub content_type: Option<String>,
    pub instruction: Option<String>,
    pub chat_initial_message: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FeedActionInput {
    pub url: String,
    pub title: Option<String>,
    pub platform: Option<String>,
    pub instruction: Option<String>,
    #[serde(default)]
    pub feed_type: Option<String>,
    #[serde(default)]
    pub feed_format: Option<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub enum BriefingActionInput {
    Feed(FeedActionInput),
    Content(ContentActionInput),
}

#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LearningDeckActionInput {
    pub source_url: String,
    pub title: Option<String>,
    pub interests_prompt: Option<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub enum ShareActionHostInput {
    Content(ContentActionInput),
    Feed(FeedActionInput),
    AddLinks {
        url: String,
        candidates: Vec<ContentActionInput>,
    },
    Briefing(BriefingActionInput),
    LearningDeck(LearningDeckActionInput),
}

#[derive(Debug, Clone, PartialEq)]
pub struct PreparedHostAction {
    pub action_name: String,
    pub action_input: Map<String, Value>,
    pub typed_input: ShareActionHostInput,
    pub rationale: Option<String>,
    pub idempotency_key: String,
}

pub fn build_host_action(
    task: &ShareActionAgentSnapshot,
    result: &ShareActionAgentResult,
    validated_feed: Option<&ValidatedFeed>,
) -> Result<Option<PreparedHostAction>, ShareActionWorkflowError> {
    result
        .validate_confidence()
        .map_err(ShareActionWorkflowError::InvalidArtifact)?;
    validate_artifact_urls(result)?;
    if result.action == "no_action" {
        return Ok(None);
    }
    let workflow = ShareActionWorkflow::parse(&task.mode)?;
    if result.action != workflow.result_action() && result.action != workflow.host_action_name() {
        return Err(ShareActionWorkflowError::ActionModeMismatch {
            action: result.action.clone(),
            mode: task.mode.clone(),
        });
    }
    let input_url = required_input_url(task)?;
    let typed_input = match workflow {
        ShareActionWorkflow::AddContent | ShareActionWorkflow::BookmarkOnly => {
            ShareActionHostInput::Content(ContentActionInput {
                url: normalized_http_url(result.primary_url.as_deref().unwrap_or(&input_url))?,
                title: clean(result.title.clone()),
                platform: clean(result.platform.clone()),
                content_type: clean(result.content_type.clone()),
                instruction: clean(result.rationale.clone()),
                chat_initial_message: None,
            })
        }
        ShareActionWorkflow::AddFeed => {
            let validated = validated_feed.ok_or_else(|| {
                ShareActionWorkflowError::InvalidArtifact(
                    "add_feed action is missing host validation".to_owned(),
                )
            })?;
            let url = normalized_http_url(&validated.effective_url)?;
            ShareActionHostInput::Feed(FeedActionInput {
                feed_type: Some(validated_scraper_type(validated)),
                feed_format: Some(validated.format.as_str().to_owned()),
                url,
                title: clean(result.title.clone()),
                platform: clean(result.platform.clone()),
                instruction: clean(result.rationale.clone()),
            })
        }
        ShareActionWorkflow::AddLinks => ShareActionHostInput::AddLinks {
            url: normalized_http_url(result.primary_url.as_deref().unwrap_or(&input_url))?,
            candidates: result
                .content_urls
                .iter()
                .map(content_candidate)
                .collect::<Result<Vec<_>, _>>()?,
        },
        ShareActionWorkflow::AddToBriefing => {
            let target = result.briefing_target.as_ref().ok_or(
                ShareActionWorkflowError::InvalidArtifact(
                    "Add-to-Briefing result is missing briefing_target".to_owned(),
                ),
            )?;
            ShareActionHostInput::Briefing(match target {
                ShareActionBriefingTarget::Feed {
                    url: _,
                    title,
                    platform,
                    rationale,
                } => {
                    let validated = validated_feed.ok_or_else(|| {
                        ShareActionWorkflowError::InvalidArtifact(
                            "add_to_briefing feed is missing host validation".to_owned(),
                        )
                    })?;
                    let url = normalized_http_url(&validated.effective_url)?;
                    BriefingActionInput::Feed(FeedActionInput {
                        feed_type: Some(validated_scraper_type(validated)),
                        feed_format: Some(validated.format.as_str().to_owned()),
                        url,
                        title: clean(title.clone()),
                        platform: clean(platform.clone()),
                        instruction: clean(rationale.clone()),
                    })
                }
                ShareActionBriefingTarget::Content {
                    url,
                    title,
                    platform,
                    rationale,
                    content_type,
                } => BriefingActionInput::Content(ContentActionInput {
                    url: normalized_http_url(url)?,
                    title: clean(title.clone()),
                    platform: clean(platform.clone()),
                    content_type: clean(content_type.clone()),
                    instruction: clean(rationale.clone()),
                    chat_initial_message: None,
                }),
            })
        }
        ShareActionWorkflow::Chat => {
            let chat = result.chat.as_ref();
            let url = chat
                .and_then(|candidate| candidate.content_url.as_deref())
                .or(result.primary_url.as_deref())
                .unwrap_or(&input_url);
            ShareActionHostInput::Content(ContentActionInput {
                url: normalized_http_url(url)?,
                title: clean(result.title.clone()),
                platform: clean(result.platform.clone()),
                content_type: clean(result.content_type.clone()),
                instruction: clean(result.rationale.clone()),
                chat_initial_message: chat
                    .and_then(|candidate| clean(candidate.initial_message.clone()))
                    .or_else(|| input_optional_text(task, "chat_initial_message")),
            })
        }
        ShareActionWorkflow::Presentation => {
            let presentation = result.presentation.as_ref();
            let source_url = presentation
                .and_then(|candidate| candidate.source_url.as_deref())
                .or(result.primary_url.as_deref())
                .unwrap_or(&input_url);
            ShareActionHostInput::LearningDeck(LearningDeckActionInput {
                source_url: normalized_http_url(source_url)?,
                title: presentation
                    .and_then(|candidate| clean(candidate.title.clone()))
                    .or_else(|| clean(result.title.clone())),
                interests_prompt: input_optional_text(task, "interests_prompt").or_else(|| {
                    presentation.and_then(|candidate| clean(candidate.interests_prompt.clone()))
                }),
            })
        }
    };
    Ok(Some(prepare_host_action(
        workflow.host_action_name(),
        typed_input,
        result.rationale.clone(),
    )))
}

pub fn build_deterministic_chat_action(
    task: &ShareActionAgentSnapshot,
) -> Result<PreparedHostAction, ShareActionWorkflowError> {
    if task.mode != "chat" {
        return Err(ShareActionWorkflowError::UnsupportedMode(task.mode.clone()));
    }
    let input = ShareActionHostInput::Content(ContentActionInput {
        url: required_input_url(task)?,
        title: None,
        platform: None,
        content_type: None,
        instruction: None,
        chat_initial_message: input_optional_text(task, "chat_initial_message"),
    });
    Ok(prepare_host_action(
        "enqueue_chat",
        input,
        Some("Use the canonical content pipeline before starting chat".to_owned()),
    ))
}

/// Strictly decodes an already-approved durable action before applying it from the HTTP callback.
/// The action name must be the single host action owned by the task mode, and unknown input fields
/// are rejected rather than silently ignored.
pub fn parse_stored_host_input(
    mode: &str,
    action_name: &str,
    action_input: &Map<String, Value>,
) -> Result<ShareActionHostInput, ShareActionWorkflowError> {
    let workflow = ShareActionWorkflow::parse(mode)?;
    if action_name != workflow.host_action_name() {
        return Err(ShareActionWorkflowError::ActionModeMismatch {
            action: action_name.to_owned(),
            mode: mode.to_owned(),
        });
    }
    let input = match workflow {
        ShareActionWorkflow::AddContent
        | ShareActionWorkflow::Chat
        | ShareActionWorkflow::BookmarkOnly => {
            ShareActionHostInput::Content(decode_input(action_input, action_name)?)
        }
        ShareActionWorkflow::AddFeed => {
            ShareActionHostInput::Feed(decode_input(action_input, action_name)?)
        }
        ShareActionWorkflow::AddLinks => {
            let decoded: StoredAddLinksInput = decode_input(action_input, action_name)?;
            ShareActionHostInput::AddLinks {
                url: decoded.url,
                candidates: decoded.content_urls,
            }
        }
        ShareActionWorkflow::AddToBriefing => {
            let mut fields = action_input.clone();
            let kind = fields
                .remove("kind")
                .and_then(|value| value.as_str().map(str::to_owned))
                .ok_or_else(|| {
                    ShareActionWorkflowError::InvalidArtifact(
                        "add_to_briefing action input is missing kind".to_owned(),
                    )
                })?;
            if let Some(rationale) = fields.remove("rationale") {
                fields.insert("instruction".to_owned(), rationale);
            }
            ShareActionHostInput::Briefing(match kind.as_str() {
                "feed" => BriefingActionInput::Feed(decode_input(&fields, action_name)?),
                "content" => BriefingActionInput::Content(decode_input(&fields, action_name)?),
                _ => {
                    return Err(ShareActionWorkflowError::InvalidArtifact(format!(
                        "unsupported add_to_briefing kind {kind:?}"
                    )));
                }
            })
        }
        ShareActionWorkflow::Presentation => {
            ShareActionHostInput::LearningDeck(decode_input(action_input, action_name)?)
        }
    };
    validate_host_input_urls(&input)?;
    Ok(input)
}

fn prepare_host_action(
    action_name: &str,
    typed_input: ShareActionHostInput,
    rationale: Option<String>,
) -> PreparedHostAction {
    let action_input = host_input_json(&typed_input);
    let encoded = python_canonical_json(&Value::Object(action_input.clone()));
    let digest = Sha256::digest(encoded.as_bytes());
    PreparedHostAction {
        action_name: action_name.to_owned(),
        action_input,
        typed_input,
        rationale: clean(rationale),
        idempotency_key: format!("{action_name}:{}", hex_encode(&digest)),
    }
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct StoredAddLinksInput {
    url: String,
    content_urls: Vec<ContentActionInput>,
}

fn decode_input<T: for<'de> Deserialize<'de>>(
    input: &Map<String, Value>,
    action_name: &str,
) -> Result<T, ShareActionWorkflowError> {
    serde_json::from_value(Value::Object(input.clone())).map_err(|error| {
        ShareActionWorkflowError::InvalidArtifact(format!(
            "{action_name} action input is invalid: {error}"
        ))
    })
}

fn validate_host_input_urls(input: &ShareActionHostInput) -> Result<(), ShareActionWorkflowError> {
    let primary_url = match input {
        ShareActionHostInput::Content(ContentActionInput { url, .. })
        | ShareActionHostInput::Feed(FeedActionInput { url, .. })
        | ShareActionHostInput::AddLinks { url, .. }
        | ShareActionHostInput::Briefing(
            BriefingActionInput::Feed(FeedActionInput { url, .. })
            | BriefingActionInput::Content(ContentActionInput { url, .. }),
        )
        | ShareActionHostInput::LearningDeck(LearningDeckActionInput {
            source_url: url, ..
        }) => url,
    };
    normalized_http_url(primary_url)?;
    if let ShareActionHostInput::AddLinks { candidates, .. } = input {
        for candidate in candidates {
            normalized_http_url(&candidate.url)?;
        }
    }
    Ok(())
}

fn host_input_json(input: &ShareActionHostInput) -> Map<String, Value> {
    match input {
        ShareActionHostInput::Content(content) => content_json(content),
        ShareActionHostInput::Feed(feed) => feed_json(feed),
        ShareActionHostInput::AddLinks { url, candidates } => Map::from_iter([
            ("url".to_owned(), Value::from(url.clone())),
            (
                "content_urls".to_owned(),
                Value::Array(
                    candidates
                        .iter()
                        .map(|candidate| Value::Object(content_json(candidate)))
                        .collect(),
                ),
            ),
        ]),
        ShareActionHostInput::Briefing(target) => match target {
            BriefingActionInput::Feed(feed) => {
                let mut value = feed_json(feed);
                if let Some(rationale) = value.remove("instruction") {
                    value.insert("rationale".to_owned(), rationale);
                }
                value.insert("kind".to_owned(), Value::from("feed"));
                value
            }
            BriefingActionInput::Content(content) => {
                let mut value = content_json(content);
                if let Some(rationale) = value.remove("instruction") {
                    value.insert("rationale".to_owned(), rationale);
                }
                value.insert("kind".to_owned(), Value::from("content"));
                value
            }
        },
        ShareActionHostInput::LearningDeck(deck) => {
            let mut value = Map::from_iter([(
                "source_url".to_owned(),
                Value::from(deck.source_url.clone()),
            )]);
            insert_optional(&mut value, "title", deck.title.as_deref());
            insert_optional(
                &mut value,
                "interests_prompt",
                deck.interests_prompt.as_deref(),
            );
            value
        }
    }
}

fn content_json(input: &ContentActionInput) -> Map<String, Value> {
    let mut value = Map::from_iter([("url".to_owned(), Value::from(input.url.clone()))]);
    insert_optional(&mut value, "title", input.title.as_deref());
    insert_optional(&mut value, "platform", input.platform.as_deref());
    insert_optional(&mut value, "content_type", input.content_type.as_deref());
    insert_optional(&mut value, "instruction", input.instruction.as_deref());
    insert_optional(
        &mut value,
        "chat_initial_message",
        input.chat_initial_message.as_deref(),
    );
    value
}

fn feed_json(input: &FeedActionInput) -> Map<String, Value> {
    let mut value = Map::from_iter([("url".to_owned(), Value::from(input.url.clone()))]);
    insert_optional(&mut value, "title", input.title.as_deref());
    insert_optional(&mut value, "platform", input.platform.as_deref());
    insert_optional(&mut value, "instruction", input.instruction.as_deref());
    insert_optional(&mut value, "feed_type", input.feed_type.as_deref());
    insert_optional(&mut value, "feed_format", input.feed_format.as_deref());
    value
}

pub(crate) fn validated_scraper_type(validated: &ValidatedFeed) -> String {
    if validated.has_audio_entries {
        "podcast_rss".to_owned()
    } else if Url::parse(&validated.effective_url)
        .ok()
        .and_then(|url| url.host_str().map(str::to_ascii_lowercase))
        .is_some_and(|host| host == "substack.com" || host.ends_with(".substack.com"))
    {
        "substack".to_owned()
    } else {
        "atom".to_owned()
    }
}

fn content_candidate(
    candidate: &ShareActionCandidate,
) -> Result<ContentActionInput, ShareActionWorkflowError> {
    Ok(ContentActionInput {
        url: normalized_http_url(&candidate.url)?,
        title: clean(candidate.title.clone()),
        platform: clean(candidate.platform.clone()),
        content_type: clean(candidate.content_type.clone()),
        instruction: clean(candidate.rationale.clone()),
        chat_initial_message: None,
    })
}

fn validate_artifact_urls(result: &ShareActionAgentResult) -> Result<(), ShareActionWorkflowError> {
    for value in [result.primary_url.as_deref(), result.feed_url.as_deref()]
        .into_iter()
        .flatten()
    {
        normalized_http_url(value)?;
    }
    for candidate in &result.content_urls {
        normalized_http_url(&candidate.url)?;
    }
    if let Some(presentation) = &result.presentation
        && let Some(url) = &presentation.source_url
    {
        normalized_http_url(url)?;
    }
    if let Some(chat) = &result.chat
        && let Some(url) = &chat.content_url
    {
        normalized_http_url(url)?;
    }
    if let Some(target) = &result.briefing_target {
        normalized_http_url(target.url())?;
    }
    Ok(())
}

fn required_input_url(task: &ShareActionAgentSnapshot) -> Result<String, ShareActionWorkflowError> {
    task.input
        .get("url")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(normalized_http_url)
        .transpose()?
        .ok_or_else(|| {
            ShareActionWorkflowError::InvalidArtifact(
                "Share Action task input is missing url".to_owned(),
            )
        })
}

fn input_optional_text(task: &ShareActionAgentSnapshot, key: &str) -> Option<String> {
    task.input
        .get(key)
        .and_then(Value::as_str)
        .and_then(|value| {
            let trimmed = value.trim();
            (!trimmed.is_empty()).then(|| trimmed.to_owned())
        })
}

fn normalized_http_url(value: &str) -> Result<String, ShareActionWorkflowError> {
    let parsed =
        Url::parse(value).map_err(|_| ShareActionWorkflowError::InvalidUrl(value.to_owned()))?;
    if !matches!(parsed.scheme(), "http" | "https") || parsed.host().is_none() {
        return Err(ShareActionWorkflowError::InvalidUrl(value.to_owned()));
    }
    Ok(value.to_owned())
}

fn clean(value: Option<String>) -> Option<String> {
    value.and_then(|value| {
        let trimmed = value.trim();
        (!trimmed.is_empty()).then(|| trimmed.to_owned())
    })
}

fn insert_optional(target: &mut Map<String, Value>, key: &str, value: Option<&str>) {
    if let Some(value) = value {
        target.insert(key.to_owned(), Value::from(value));
    }
}

fn hex_encode(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut encoded = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        encoded.push(char::from(HEX[usize::from(byte >> 4)]));
        encoded.push(char::from(HEX[usize::from(byte & 0x0f)]));
    }
    encoded
}

#[derive(Debug, Error)]
pub enum ShareActionWorkflowError {
    #[error("unsupported Share Action mode: {0}")]
    UnsupportedMode(String),
    #[error("Share Action result action {action:?} does not match mode {mode:?}")]
    ActionModeMismatch { action: String, mode: String },
    #[error("Share Action result contains an invalid HTTP URL: {0}")]
    InvalidUrl(String),
    #[error("invalid Share Action result artifact: {0}")]
    InvalidArtifact(String),
}

#[cfg(test)]
mod tests;
