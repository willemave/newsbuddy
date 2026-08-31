use chrono::{DateTime, Utc};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use utoipa::ToSchema;

/// Admin-visible API-key metadata. Secret hashes are never part of this wire
/// contract.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct ApiKeySummaryResponse {
    pub id: i64,
    pub user_id: i64,
    pub key_prefix: String,
    #[schemars(with = "String")]
    #[schema(value_type = String)]
    pub created_at: DateTime<Utc>,
    #[schemars(with = "Option<String>")]
    #[schema(value_type = Option<String>)]
    pub revoked_at: Option<DateTime<Utc>>,
    #[schemars(with = "Option<String>")]
    #[schema(value_type = Option<String>)]
    pub last_used_at: Option<DateTime<Utc>>,
    pub created_by_admin_user_id: Option<i64>,
}

/// One-time create result. Both plaintext aliases are retained for exact
/// compatibility with the existing generated contract.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct ApiKeyCreateResponse {
    pub api_key: String,
    pub key: String,
    pub key_prefix: String,
    pub record: ApiKeySummaryResponse,
}
