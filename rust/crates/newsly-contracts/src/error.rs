use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use utoipa::ToSchema;

/// Stable error representation at every Newsly HTTP boundary.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct ErrorEnvelope {
    pub code: String,
    pub message: String,
    #[schemars(required)]
    #[schema(required = true)]
    pub details: Option<Map<String, Value>>,
    pub retryable: bool,
    pub request_id: String,
}
