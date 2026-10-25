use std::time::Duration;

use newsly_agent_runtime::AgentLimits;
use newsly_providers::{ModelProvider, ModelSpec};
use serde_json::{Map, Value, json};
use tokio::time::Instant;

pub(super) const DEFAULT_GENERATION_SECONDS: u64 = 900;
pub(super) const SANDBOX_CLEANUP_GRACE: Duration = Duration::from_secs(60);
const INITIAL_RESERVE: Duration = Duration::from_secs(180);
const REPAIR_RESERVE: Duration = Duration::from_secs(120);
// Anthropic requires an explicit per-turn ceiling. Use the model maximum rather than an app-level
// cap: one turn may write a whole deck file.
const OUTPUT_TOKEN_LIMIT: u64 = 128_000;

pub(super) fn learning_deck_model_parameters(model: &ModelSpec) -> Map<String, Value> {
    if model.provider == ModelProvider::OpenRouter && model.model.starts_with("anthropic/") {
        // Keep Claude's high-effort thinking and signatures available for tool-result replay.
        Map::from_iter([(
            "reasoning".to_owned(),
            json!({"enabled": true, "effort": "high"}),
        )])
    } else {
        Map::new()
    }
}

/// One deadline, restored from the durable task clock on every queue delivery.
#[derive(Debug, Clone, Copy)]
pub(super) struct GenerationBudget {
    pub deadline: Instant,
}

impl GenerationBudget {
    pub(super) fn new(now: Instant, remaining: Duration) -> Self {
        Self {
            deadline: now + remaining,
        }
    }

    pub(super) fn initial_deadline(self) -> Instant {
        self.deadline - INITIAL_RESERVE
    }

    pub(super) fn repair_deadline(self) -> Instant {
        self.deadline - REPAIR_RESERVE
    }

    pub(super) fn remaining(deadline: Instant) -> Result<Duration, super::LearningDeckAgentError> {
        deadline
            .checked_duration_since(Instant::now())
            .filter(|value| !value.is_zero())
            .ok_or(super::LearningDeckAgentError::Deadline)
    }
}

pub(super) fn learning_deck_agent_limits(tool_call_limit: u32, deadline: Duration) -> AgentLimits {
    AgentLimits {
        request_limit: None,
        tool_call_limit,
        output_token_limit: Some(OUTPUT_TOKEN_LIMIT),
        deadline,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn claude_routing_enables_preserved_thinking_only_through_openrouter() {
        for model in [
            super::super::DEFAULT_MODEL_SPEC,
            "openrouter: anthropic/claude-sonnet-5.5 ",
        ] {
            let parameters = learning_deck_model_parameters(&ModelSpec::parse(model).unwrap());
            assert_eq!(
                parameters["reasoning"],
                json!({"enabled": true, "effort": "high"})
            );
            assert!(parameters["reasoning"].get("exclude").is_none());
        }
        for model in [
            "anthropic:claude-sonnet-5-5",
            "openrouter:deepseek/deepseek-v4-flash",
        ] {
            assert!(learning_deck_model_parameters(&ModelSpec::parse(model).unwrap()).is_empty());
        }
    }

    #[test]
    fn every_phase_uses_one_deadline_and_keeps_publication_time() {
        let now = Instant::now();
        let budget = GenerationBudget::new(now, Duration::from_secs(900));
        assert_eq!(budget.initial_deadline().duration_since(now).as_secs(), 720);
        assert_eq!(budget.repair_deadline().duration_since(now).as_secs(), 780);
        let restored =
            GenerationBudget::new(now + Duration::from_secs(500), Duration::from_secs(400));
        assert_eq!(restored.deadline, budget.deadline);
        assert_eq!(restored.initial_deadline(), budget.initial_deadline());
        assert_eq!(restored.repair_deadline(), budget.repair_deadline());
    }
}
