use std::fmt::{self, Debug, Formatter};

use secrecy::SecretString;
use serde::{Deserialize, Serialize};
use thiserror::Error;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ModelProvider {
    OpenAi,
    Anthropic,
    Google,
    OpenRouter,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ModelSpec {
    pub provider: ModelProvider,
    pub model: String,
}

impl ModelSpec {
    /// Parses a canonical or provider-inferred model specification.
    ///
    /// # Errors
    ///
    /// Returns an error when the provider or model component is empty or unsupported.
    pub fn parse(value: &str) -> Result<Self, ModelSpecError> {
        let value = value.trim();
        if value.is_empty() {
            return Err(ModelSpecError::Empty);
        }
        let (provider, model) = match value.split_once(':') {
            Some(("openai", model)) => (ModelProvider::OpenAi, model),
            Some(("anthropic", model)) => (ModelProvider::Anthropic, model),
            Some(("google" | "google-gla", model)) => (ModelProvider::Google, model),
            Some(("openrouter", model)) => (ModelProvider::OpenRouter, model),
            Some((provider, _)) => {
                return Err(ModelSpecError::UnknownProvider(provider.to_owned()));
            }
            None if value.starts_with("gpt-") || value.starts_with("o3") => {
                (ModelProvider::OpenAi, value)
            }
            None if value.starts_with("claude-") => (ModelProvider::Anthropic, value),
            None if value.starts_with("gemini-") => (ModelProvider::Google, value),
            None => return Err(ModelSpecError::MissingProvider(value.to_owned())),
        };
        let model = model.trim();
        if model.is_empty() {
            return Err(ModelSpecError::MissingModel);
        }
        Ok(Self {
            provider,
            model: model.to_owned(),
        })
    }

    pub fn canonical(&self) -> String {
        let prefix = match self.provider {
            ModelProvider::OpenAi => "openai",
            ModelProvider::Anthropic => "anthropic",
            ModelProvider::Google => "google",
            ModelProvider::OpenRouter => "openrouter",
        };
        format!("{prefix}:{}", self.model)
    }
}

#[derive(Clone)]
pub struct ProviderCredentials {
    pub openai: Option<SecretString>,
    pub anthropic: Option<SecretString>,
    pub google: Option<SecretString>,
    pub openrouter: Option<SecretString>,
}

impl ProviderCredentials {
    pub fn key_for(&self, provider: ModelProvider) -> Option<&SecretString> {
        match provider {
            ModelProvider::OpenAi => self.openai.as_ref(),
            ModelProvider::Anthropic => self.anthropic.as_ref(),
            ModelProvider::Google => self.google.as_ref(),
            ModelProvider::OpenRouter => self.openrouter.as_ref(),
        }
    }
}

impl Debug for ProviderCredentials {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ProviderCredentials")
            .field("openai", &self.openai.as_ref().map(|_| "[REDACTED]"))
            .field("anthropic", &self.anthropic.as_ref().map(|_| "[REDACTED]"))
            .field("google", &self.google.as_ref().map(|_| "[REDACTED]"))
            .field(
                "openrouter",
                &self.openrouter.as_ref().map(|_| "[REDACTED]"),
            )
            .finish()
    }
}

#[derive(Debug, Error)]
pub enum ModelSpecError {
    #[error("model specification is empty")]
    Empty,
    #[error("model specification requires an explicit provider prefix: {0}")]
    MissingProvider(String),
    #[error("unknown model provider {0}")]
    UnknownProvider(String),
    #[error("model name is missing")]
    MissingModel,
}
