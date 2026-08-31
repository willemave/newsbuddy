use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use utoipa::{IntoParams, ToSchema};

pub const CUSTOM_NARRATION_MAX_SOURCES: usize = 12;

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Deserialize, JsonSchema, ToSchema)]
#[serde(rename_all = "snake_case")]
pub enum AudioEpisodeDelivery {
    #[default]
    Background,
    Stream,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Deserialize, IntoParams)]
pub struct AudioEpisodeDeliveryQuery {
    #[serde(default)]
    pub delivery: AudioEpisodeDelivery,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, JsonSchema, ToSchema)]
pub struct CustomNarrationCreateRequest {
    #[serde(default)]
    pub content_ids: Vec<i64>,
    #[serde(default)]
    pub news_item_ids: Vec<i64>,
    pub title: Option<String>,
    #[serde(default)]
    pub mark_source_content_read_on_play: bool,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Deserialize, IntoParams)]
pub struct AudioEpisodeListQuery {
    #[serde(default = "default_list_limit")]
    pub limit: usize,
}

const fn default_list_limit() -> usize {
    20
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct AudioEpisodeShareResponse {
    pub share_enabled: bool,
    pub share_page_url: Option<String>,
    pub share_audio_url: Option<String>,
}
