//! A committed action remains a fact when subsequent model execution fails.
use super::{ChatAgentError, ChatAgentRun};
use newsly_agent_runtime::{
    AgentOutcome, AssistantPart, MessagePart, MessageRole, NewslyMessage, NewslyTranscript,
    ProviderUsage, RequestPart,
};
use newsly_db::ChatTaskSnapshot;
use newsly_providers::ModelSpec;
use serde_json::{Map, Value};

pub(super) fn action_fallback(
    snapshot: &ChatTaskSnapshot,
    text: String,
    render_metadata: Option<Value>,
    tool_names: Vec<String>,
) -> Result<ChatAgentRun, ChatAgentError> {
    let model = ModelSpec::parse(&snapshot.context.session.model)?;
    let now = chrono::Utc::now();
    let message = |role, parts| NewslyMessage {
        id: None,
        role,
        parts,
        created_at: now,
        run_id: None,
        provider: None,
        model: None,
        finish_reason: None,
        usage: ProviderUsage::default(),
        metadata: Map::new(),
    };
    let transcript = NewslyTranscript {
        stream_generation: u64::try_from(snapshot.stream_generation).unwrap_or_default(),
        messages: vec![
            message(
                MessageRole::User,
                vec![MessagePart::Request(RequestPart::Text {
                    text: snapshot.context.user_prompt.clone(),
                })],
            ),
            message(
                MessageRole::Assistant,
                vec![MessagePart::Assistant(AssistantPart::Text {
                    text: text.clone(),
                })],
            ),
        ],
        ..NewslyTranscript::default()
    };
    Ok(ChatAgentRun {
        render_metadata,
        tool_names,
        turn_transcript: transcript.clone(),
        model_provider: model.provider.as_str().to_owned(),
        outcome: AgentOutcome {
            output_text: text,
            structured_output: None,
            transcript,
            usage: ProviderUsage::default(),
            provider_response_id: None,
            model_name: model.canonical(),
            request_count: 0,
            tool_call_count: 0,
        },
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use newsly_db::ChatTurnProcessingContext;
    pub(super) fn snapshot() -> ChatTaskSnapshot {
        let context: ChatTurnProcessingContext = serde_json::from_value(serde_json::json!({
            "version":1,"kind":"article","user_prompt":"Subscribe to More or Less","source":"chat",
            "session":{"user_id":1,"effective_session_id":2,"visible_session_id":2,"model":"openai:gpt-6.1-sol","provider":"openai"}
        })).unwrap();
        ChatTaskSnapshot {
            queue_task_id: 3,
            user_id: 1,
            session_id: 2,
            message_id: 4,
            stream_generation: 1,
            context,
            history: NewslyTranscript::default(),
            llm_task_id: Some(5),
            encrypted_provider_key: None,
            content: None,
            workspace_path: "/workspace/task".into(),
        }
    }
    #[test]
    fn confirmed_actions_publish_a_normal_turn_without_inventing_model_usage() {
        let snapshot = snapshot();
        let run = action_fallback(
            &snapshot,
            "Subscribed to More or Less.".into(),
            None,
            vec!["subscribe_to_source".into()],
        )
        .unwrap();
        assert_eq!(run.outcome.output_text, "Subscribed to More or Less.");
        assert!(run.outcome.provider_response_id.is_none());
        assert_eq!(run.outcome.usage, ProviderUsage::default());
        assert_eq!(run.turn_transcript.messages.len(), 2);
        assert_eq!(run.turn_transcript.messages[0].role, MessageRole::User);
        assert_eq!(run.turn_transcript.messages[1].role, MessageRole::Assistant);
        assert_eq!(run.turn_transcript.stream_generation, 1);
        run.turn_transcript.validate().unwrap();
        assert!(
            !serde_json::to_string(&run.turn_transcript)
                .unwrap()
                .contains("\"kind\":\"reasoning\"")
        );
    }
}

#[cfg(test)]
#[path = "fallback_publication_tests.rs"]
mod publication_tests;
