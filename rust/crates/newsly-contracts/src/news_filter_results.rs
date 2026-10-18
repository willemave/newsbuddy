use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use utoipa::ToSchema;

use crate::NewsItemSummaryResponse;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct NewsFilterNewsItem {
    pub news: NewsItemSummaryResponse,
    /// Open status: matched, `filtered_out`, uncertain, unavailable, pending,
    /// `outside_window`, deferred, or off.
    pub filter_status: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct NewsFilterNewsResponse {
    pub items: Vec<NewsFilterNewsItem>,
    pub next_cursor: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct NewsFilterPreviewRequest {
    #[schemars(length(max = 8_000))]
    #[schema(max_length = 8_000)]
    pub text: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct NewsFilterPreviewResponse {
    pub items: Vec<NewsFilterNewsItem>,
}
