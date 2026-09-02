//! Client-visible vocabulary that remains part of the public compatibility surface.
//!
//! These enums are emitted into `OpenAPI` even when a current route carries the value through a
//! legacy string field. Keeping the values in Rust prevents the client generator from becoming a
//! second wire-schema authority.

use std::borrow::Cow;

use schemars::{JsonSchema, Schema, SchemaGenerator};
use serde::{Deserialize, Deserializer, Serialize, Serializer};
use utoipa::ToSchema;

macro_rules! string_enum {
    ($name:ident { $($variant:ident),+ $(,)? }) => {
        #[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, ToSchema)]
        #[serde(rename_all = "snake_case")]
        pub enum $name {
            $($variant),+
        }
    };
}

string_enum!(TaskType {
    Scrape,
    BackfillFeeds,
    AnalyzeUrl,
    ProcessContent,
    EnrichNewsItemArticle,
    ProcessNewsItem,
    ProcessPodcastMedia,
    DownloadTweetVideoAudio,
    TranscribeTweetVideo,
    Summarize,
    FetchNewsItemDiscussion,
    GenerateImage,
    DiscoverFeeds,
    OnboardingDiscover,
    DigDeeper,
    ChatTurn,
    SyncIntegration,
    GenerateAudioEpisode,
    RunLlmTask,
    BriefingRefresh,
    DeleteUserAccount,
});

string_enum!(TaskStatus {
    Pending,
    Processing,
    Completed,
    Failed,
});

#[derive(Debug, Clone, Copy, PartialEq, Eq, ToSchema)]
#[repr(i32)]
pub enum SummaryVersion {
    V1 = 1,
    V2 = 2,
}

impl SummaryVersion {
    pub const fn from_i32(value: i32) -> Option<Self> {
        match value {
            1 => Some(Self::V1),
            2 => Some(Self::V2),
            _ => None,
        }
    }

    pub const fn as_i32(self) -> i32 {
        self as i32
    }
}

impl Serialize for SummaryVersion {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: Serializer,
    {
        serializer.serialize_i32(self.as_i32())
    }
}

impl<'de> Deserialize<'de> for SummaryVersion {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        let value = i32::deserialize(deserializer)?;
        Self::from_i32(value).ok_or_else(|| {
            <D::Error as serde::de::Error>::custom(format_args!(
                "unsupported summary version {value}; expected 1 or 2"
            ))
        })
    }
}

impl JsonSchema for SummaryVersion {
    fn schema_name() -> Cow<'static, str> {
        "SummaryVersion".into()
    }

    fn schema_id() -> Cow<'static, str> {
        concat!(module_path!(), "::SummaryVersion").into()
    }

    fn json_schema(_generator: &mut SchemaGenerator) -> Schema {
        schemars::json_schema!({
            "type": "integer",
            "enum": [1, 2]
        })
    }
}

string_enum!(NewsItemVisibilityScope { Global, User });

string_enum!(NewsItemStatus {
    New,
    Processing,
    Ready,
    Failed,
});

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    #[test]
    fn summary_version_round_trips_as_numeric_json() {
        for (version, wire_value) in [(SummaryVersion::V1, 1), (SummaryVersion::V2, 2)] {
            let serialized = serde_json::to_value(version).expect("serialize summary version");
            assert_eq!(serialized, json!(wire_value));
            assert_eq!(
                serde_json::from_value::<SummaryVersion>(serialized)
                    .expect("deserialize summary version"),
                version
            );
        }
        assert!(serde_json::from_value::<SummaryVersion>(json!(3)).is_err());
        assert!(serde_json::from_value::<SummaryVersion>(json!("V1")).is_err());

        let schema = serde_json::to_value(schemars::schema_for!(SummaryVersion))
            .expect("serialize summary version schema");
        assert_eq!(schema["type"], "integer");
        assert_eq!(schema["enum"], json!([1, 2]));
    }
}
