use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use utoipa::ToSchema;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct UnreadCountsResponse {
    pub article: i64,
    pub podcast: i64,
    pub news: i64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct ProcessingCountResponse {
    pub processing_count: i64,
    pub long_form_count: i64,
    pub news_count: i64,
    pub news_crawl_count: i64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct BadgeStatsResponse {
    pub unread: UnreadCountsResponse,
    pub processing: ProcessingCountResponse,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct LongFormStatsResponse {
    pub unread_count: i64,
}
