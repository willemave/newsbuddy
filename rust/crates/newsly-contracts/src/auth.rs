use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use utoipa::ToSchema;
use uuid::Uuid;

use crate::{ReadingExperience, UserResponse};

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, JsonSchema, ToSchema)]
pub struct AppleSignInRequest {
    pub id_token: String,
    pub full_name: Option<String>,
    pub nonce: Option<String>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Deserialize, JsonSchema, ToSchema)]
#[serde(deny_unknown_fields)]
pub struct DebugUserSessionRequest {
    pub user_id: Option<i64>,
    pub has_completed_onboarding: Option<bool>,
    pub has_completed_new_user_tutorial: Option<bool>,
    pub reading_experience: Option<ReadingExperience>,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, JsonSchema, ToSchema)]
pub struct DeleteAccountRequest {
    pub id_token: String,
    pub authorization_code: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct DeleteAccountResponse {
    #[serde(default = "default_deletion_status")]
    pub status: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, JsonSchema, ToSchema)]
#[serde(deny_unknown_fields)]
pub struct RefreshTokenRequest {
    pub refresh_token: String,
    #[schemars(with = "Option<String>")]
    #[schema(value_type = Option<String>, format = "uuid")]
    pub attempt_id: Option<Uuid>,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, JsonSchema, ToSchema)]
pub struct AdminLoginRequest {
    pub password: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct AdminLoginResponse {
    pub message: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct AccessTokenResponse {
    pub access_token: String,
    pub refresh_token: String,
    #[serde(default = "default_token_type")]
    pub token_type: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct TokenResponse {
    pub access_token: String,
    pub refresh_token: String,
    #[serde(default = "default_token_type")]
    pub token_type: String,
    pub user: UserResponse,
    #[serde(default)]
    pub is_new_user: bool,
}

fn default_token_type() -> String {
    "bearer".to_owned()
}

fn default_deletion_status() -> String {
    "deletion_scheduled".to_owned()
}

#[cfg(test)]
mod tests {
    use super::AppleSignInRequest;

    #[test]
    fn apple_sign_in_accepts_legacy_email_but_does_not_model_it() {
        let request: AppleSignInRequest = serde_json::from_value(serde_json::json!({
            "id_token": "token",
            "email": "untrusted@example.com",
            "full_name": "Ada Lovelace"
        }))
        .expect("legacy request remains decodable");

        assert_eq!(request.id_token, "token");
        assert_eq!(request.full_name.as_deref(), Some("Ada Lovelace"));
        assert_eq!(request.nonce, None);
    }

    #[test]
    fn apple_sign_in_preserves_the_raw_nonce() {
        let request: AppleSignInRequest = serde_json::from_value(serde_json::json!({
            "id_token": "token",
            "full_name": null,
            "nonce": "raw-nonce"
        }))
        .expect("nonce request decodes");

        assert_eq!(request.nonce.as_deref(), Some("raw-nonce"));
    }
}
