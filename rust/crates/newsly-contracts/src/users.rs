use chrono::{DateTime, Utc};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use utoipa::ToSchema;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
#[serde(rename_all = "snake_case")]
pub enum ReadingExperience {
    Classic,
    Briefing,
}

impl ReadingExperience {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Classic => "classic",
            Self::Briefing => "briefing",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
#[serde(deny_unknown_fields)]
pub struct CouncilPersonaConfig {
    pub id: String,
    pub display_name: String,
    #[serde(default)]
    pub instruction_prompt: String,
    pub sort_order: i32,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, JsonSchema, ToSchema)]
#[serde(deny_unknown_fields)]
pub struct CouncilPersonaInput {
    pub id: String,
    pub display_name: String,
    #[serde(default)]
    pub instruction_prompt: String,
    pub sort_order: i32,
}

impl From<CouncilPersonaInput> for CouncilPersonaConfig {
    fn from(value: CouncilPersonaInput) -> Self {
        Self {
            id: value.id,
            display_name: value.display_name,
            instruction_prompt: value.instruction_prompt,
            sort_order: value.sort_order,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema, ToSchema)]
// Account, entitlement, and onboarding flags are independent public wire facts.
#[allow(clippy::struct_excessive_bools)]
pub struct UserResponse {
    pub email: String,
    pub full_name: Option<String>,
    pub id: i64,
    pub apple_id: String,
    pub is_admin: bool,
    pub is_active: bool,
    pub twitter_username: Option<String>,
    #[serde(default)]
    pub council_personas: Vec<CouncilPersonaConfig>,
    #[serde(default)]
    pub has_x_bookmark_sync: bool,
    pub has_completed_onboarding: bool,
    pub has_completed_new_user_tutorial: bool,
    #[serde(default = "default_reading_experience")]
    pub reading_experience: ReadingExperience,
    pub timezone: Option<String>,
    #[serde(default)]
    pub timezone_revision: Option<i64>,
    #[schemars(with = "String")]
    #[schema(value_type = String, format = DateTime)]
    pub created_at: DateTime<Utc>,
    #[schemars(with = "String")]
    #[schema(value_type = String, format = DateTime)]
    pub updated_at: DateTime<Utc>,
}

const fn default_reading_experience() -> ReadingExperience {
    ReadingExperience::Briefing
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Deserialize, JsonSchema, ToSchema)]
#[serde(deny_unknown_fields)]
pub struct UpdateUserProfileRequest {
    pub full_name: Option<String>,
    pub twitter_username: Option<String>,
    pub council_personas: Option<Vec<CouncilPersonaInput>>,
    pub reading_experience: Option<ReadingExperience>,
    #[serde(default, deserialize_with = "deserialize_optional_timezone")]
    pub timezone: Option<String>,
    pub timezone_revision: Option<i64>,
}

fn deserialize_optional_timezone<'de, D>(deserializer: D) -> Result<Option<String>, D::Error>
where
    D: serde::Deserializer<'de>,
{
    String::deserialize(deserializer).map(Some)
}

#[cfg(test)]
mod tests {
    use super::UpdateUserProfileRequest;

    #[test]
    fn omitted_timezone_preserves_existing_value() {
        let request: UpdateUserProfileRequest = serde_json::from_str("{}").expect("valid patch");
        assert_eq!(request.timezone, None);
    }

    #[test]
    fn timezone_accepts_a_string() {
        let request: UpdateUserProfileRequest =
            serde_json::from_str(r#"{"timezone":"America/Los_Angeles","timezone_revision":3}"#)
                .expect("valid timezone patch");
        assert_eq!(request.timezone.as_deref(), Some("America/Los_Angeles"));
        assert_eq!(request.timezone_revision, Some(3));
    }

    #[test]
    fn explicit_null_timezone_is_rejected() {
        let error = serde_json::from_str::<UpdateUserProfileRequest>(r#"{"timezone":null}"#)
            .expect_err("null must not behave like omission");
        assert!(error.to_string().contains("expected a string"));
    }
}
