use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use utoipa::ToSchema;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct NewsFilterPreference {
    pub revision: i64,
    pub text: String,
    pub enabled: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct UpdateNewsFilterRequest {
    pub expected_revision: i64,
    #[schemars(length(max = 8_000))]
    #[schema(max_length = 8_000)]
    pub text: String,
    pub enabled: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct OnboardingNewsFilterSeed {
    pub text: String,
    pub enabled: bool,
}

/// An omitted field uses the compatibility default; explicit null is never a seed.
pub(crate) fn deserialize_news_filter_seed<'de, D>(
    deserializer: D,
) -> Result<Option<OnboardingNewsFilterSeed>, D::Error>
where
    D: serde::Deserializer<'de>,
{
    OnboardingNewsFilterSeed::deserialize(deserializer).map(Some)
}

#[cfg(test)]
mod tests {
    use crate::OnboardingCompleteRequest;
    #[test]
    fn seed_requires_object_and_both_members() {
        for invalid in [
            r#"{"news_filter_seed":null}"#,
            r#"{"news_filter_seed":{"text":"AI"}}"#,
            r#"{"news_filter_seed":{"enabled":false}}"#,
        ] {
            assert!(serde_json::from_str::<OnboardingCompleteRequest>(invalid).is_err());
        }
        assert!(
            serde_json::from_str::<OnboardingCompleteRequest>("{}")
                .unwrap()
                .news_filter_seed
                .is_none()
        );
        assert!(
            serde_json::from_str::<OnboardingCompleteRequest>(
                r#"{"news_filter_seed":{"text":"","enabled":true}}"#
            )
            .is_ok()
        );
    }
}
