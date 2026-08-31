use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use utoipa::ToSchema;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct CouncilStartRequest {
    #[schemars(length(min = 1, max = 10_000))]
    #[schema(min_length = 1, max_length = 10_000)]
    pub message: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct CouncilSelectRequest {
    #[schemars(range(min = 1))]
    #[schema(minimum = 1)]
    pub child_session_id: i64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct CouncilRetryRequest {
    #[schemars(range(min = 1))]
    #[schema(minimum = 1)]
    pub child_session_id: i64,
}
