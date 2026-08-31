use rig_core::providers::openrouter::{DataCollection, ProviderPreferences, ProviderSortStrategy};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use thiserror::Error;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[allow(clippy::struct_excessive_bools)]
pub struct OpenRouterPrivacyPolicy {
    pub provider_order: Vec<String>,
    pub ignored_providers: Vec<String>,
    pub allow_fallbacks: bool,
    pub require_parameters: bool,
    pub zero_data_retention: bool,
    pub deny_data_collection: bool,
    pub reasoning_enabled: bool,
    pub exclude_reasoning: bool,
}

impl Default for OpenRouterPrivacyPolicy {
    fn default() -> Self {
        Self {
            provider_order: Vec::new(),
            ignored_providers: Vec::new(),
            allow_fallbacks: false,
            require_parameters: true,
            zero_data_retention: true,
            deny_data_collection: true,
            reasoning_enabled: false,
            exclude_reasoning: true,
        }
    }
}

impl OpenRouterPrivacyPolicy {
    /// Validates that routing and privacy controls do not contradict one another.
    ///
    /// # Errors
    ///
    /// Returns an error when a pinned route allows fallbacks or zero-retention routing permits
    /// provider data collection.
    pub fn validate(&self) -> Result<(), OpenRouterRoutingError> {
        if self.zero_data_retention && !self.deny_data_collection {
            return Err(OpenRouterRoutingError::ContradictoryPrivacyPolicy);
        }
        if !self.provider_order.is_empty() && self.allow_fallbacks {
            return Err(OpenRouterRoutingError::PinnedRouteAllowsFallbacks);
        }
        Ok(())
    }

    pub(crate) fn rig_preferences(&self) -> Result<ProviderPreferences, OpenRouterRoutingError> {
        self.validate()?;
        let mut preferences = ProviderPreferences::new()
            .allow_fallbacks(self.allow_fallbacks)
            .require_parameters(self.require_parameters)
            .zdr(self.zero_data_retention)
            .sort(ProviderSortStrategy::Throughput);
        if self.deny_data_collection {
            preferences = preferences.data_collection(DataCollection::Deny);
        }
        if !self.provider_order.is_empty() {
            preferences = preferences.order(self.provider_order.clone());
        }
        if !self.ignored_providers.is_empty() {
            preferences = preferences.ignore(self.ignored_providers.clone());
        }
        Ok(preferences)
    }

    /// Serializes the validated routing policy into `OpenRouter` request parameters.
    ///
    /// # Errors
    ///
    /// Returns an error when the policy is invalid or Rig cannot serialize its preferences.
    pub fn request_parameters(&self) -> Result<Map<String, Value>, OpenRouterRoutingError> {
        let preferences = self.rig_preferences()?;
        let mut parameters = preferences
            .to_json()
            .as_object()
            .ok_or(OpenRouterRoutingError::InvalidPreferencesSerialization)?
            .clone();
        parameters.insert(
            "reasoning".to_owned(),
            serde_json::json!({
                "enabled": self.reasoning_enabled,
                "exclude": self.exclude_reasoning,
            }),
        );
        Ok(parameters)
    }
}

#[derive(Debug, Error)]
pub enum OpenRouterRoutingError {
    #[error("zero-data-retention routing must also deny provider data collection")]
    ContradictoryPrivacyPolicy,
    #[error("a pinned provider route cannot allow fallbacks")]
    PinnedRouteAllowsFallbacks,
    #[error("OpenRouter preferences did not serialize to an object")]
    InvalidPreferencesSerialization,
}
