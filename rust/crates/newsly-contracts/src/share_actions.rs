use std::collections::BTreeMap;

use chrono::{DateTime, Utc};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use utoipa::ToSchema;

use crate::{LlmTaskActionResponse, LlmTaskApprovalPolicy};

macro_rules! string_enum {
    ($name:ident { $($variant:ident => $value:literal),+ $(,)? }) => {
        #[derive(
            Debug,
            Clone,
            Copy,
            PartialEq,
            Eq,
            Serialize,
            Deserialize,
            JsonSchema,
            ToSchema,
        )]
        #[serde(rename_all = "snake_case")]
        pub enum $name {
            $($variant),+
        }

        impl $name {
            pub const fn as_str(self) -> &'static str {
                match self {
                    $(Self::$variant => $value),+
                }
            }
        }

        impl TryFrom<&str> for $name {
            type Error = String;

            fn try_from(value: &str) -> Result<Self, Self::Error> {
                match value {
                    $($value => Ok(Self::$variant)),+,
                    other => Err(format!(
                        "unsupported {} value {other:?}",
                        stringify!($name),
                    )),
                }
            }
        }
    };
}

// The API schema intentionally retains the complete Python LlmTaskMode enum even though the
// Share Actions endpoint accepts only the seven modes returned by `is_share_action`.
string_enum!(LlmTaskMode {
    AddContent => "add_content",
    AddToBriefing => "add_to_briefing",
    AddLinks => "add_links",
    AddFeed => "add_feed",
    Chat => "chat",
    Presentation => "presentation",
    BookmarkOnly => "bookmark_only",
    ArticleChat => "article_chat",
    ContextualAssistant => "contextual_assistant",
    LearningDeckPresentation => "learning_deck_presentation",
    Generic => "generic",
});

impl LlmTaskMode {
    pub const fn is_share_action(self) -> bool {
        matches!(
            self,
            Self::AddContent
                | Self::AddToBriefing
                | Self::AddLinks
                | Self::AddFeed
                | Self::Chat
                | Self::Presentation
                | Self::BookmarkOnly
        )
    }
}

string_enum!(LlmTaskStatus {
    Queued => "queued",
    Preparing => "preparing",
    Running => "running",
    AwaitingApproval => "awaiting_approval",
    Applying => "applying",
    Completed => "completed",
    Failed => "failed",
    Cancelled => "cancelled",
});

#[derive(Debug, Clone, PartialEq, Deserialize, JsonSchema, ToSchema)]
pub struct ShareActionCreateRequest {
    pub url: String,
    pub mode: LlmTaskMode,
    #[serde(alias = "note")]
    #[schemars(length(max = 4_000))]
    #[schema(max_length = 4_000)]
    pub instruction: Option<String>,
    #[schemars(length(max = 2_000))]
    #[schema(max_length = 2_000)]
    pub chat_initial_message: Option<String>,
    #[schemars(length(max = 4_000))]
    #[schema(max_length = 4_000)]
    pub interests_prompt: Option<String>,
    pub approval_policy: Option<BTreeMap<String, LlmTaskApprovalPolicy>>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct ShareActionResponse {
    pub task_id: i64,
    pub mode: LlmTaskMode,
    pub status: LlmTaskStatus,
    pub workflow_state: String,
    #[schemars(with = "String")]
    #[schema(value_type = String, format = DateTime)]
    pub created_at: DateTime<Utc>,
    #[serde(default)]
    pub actions: Vec<LlmTaskActionResponse>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ShareActionCandidate {
    pub url: String,
    pub title: Option<String>,
    pub platform: Option<String>,
    pub content_type: Option<String>,
    pub rationale: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ShareActionPresentationCandidate {
    pub source_url: Option<String>,
    pub title: Option<String>,
    pub interests_prompt: Option<String>,
    pub artifact_mode: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ShareActionChatCandidate {
    pub content_url: Option<String>,
    pub initial_message: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum ShareActionBriefingTarget {
    Feed {
        url: String,
        title: Option<String>,
        platform: Option<String>,
        rationale: Option<String>,
    },
    Content {
        url: String,
        title: Option<String>,
        platform: Option<String>,
        rationale: Option<String>,
        content_type: Option<String>,
    },
}

impl ShareActionBriefingTarget {
    pub const fn kind(&self) -> &'static str {
        match self {
            Self::Feed { .. } => "feed",
            Self::Content { .. } => "content",
        }
    }

    pub fn url(&self) -> &str {
        match self {
            Self::Feed { url, .. } | Self::Content { url, .. } => url,
        }
    }
}

/// Strictly decoded final artifact produced inside the Share Action VM.
///
/// Unlike the public request, this host-controlled artifact rejects unknown fields so a model
/// cannot silently smuggle an unreviewed action shape into finalization.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ShareActionAgentResult {
    pub action: String,
    pub primary_url: Option<String>,
    pub feed_url: Option<String>,
    #[serde(default)]
    pub content_urls: Vec<ShareActionCandidate>,
    pub presentation: Option<ShareActionPresentationCandidate>,
    pub chat: Option<ShareActionChatCandidate>,
    pub briefing_target: Option<ShareActionBriefingTarget>,
    pub title: Option<String>,
    pub platform: Option<String>,
    pub content_type: Option<String>,
    pub rationale: Option<String>,
    #[serde(default)]
    pub sources_used: Vec<Map<String, Value>>,
    pub confidence: Option<f64>,
}

impl ShareActionAgentResult {
    /// Verify that the optional model confidence is finite and normalized.
    ///
    /// # Errors
    ///
    /// Returns an error when confidence is outside the inclusive zero-to-one range.
    pub fn validate_confidence(&self) -> Result<(), String> {
        if self
            .confidence
            .is_some_and(|value| !value.is_finite() || !(0.0..=1.0).contains(&value))
        {
            return Err("share action confidence must be between 0 and 1".to_owned());
        }
        Ok(())
    }
}
