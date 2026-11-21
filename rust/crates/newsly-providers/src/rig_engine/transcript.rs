use newsly_agent_runtime::{
    AgentRuntimeError, AssistantPart, ImageMediaType, MessagePart, MessageRole, NewslyMessage,
    NewslyTranscript, ReasoningContentKind, RequestPart, ToolImage,
};
use rig_core::completion::{AssistantContent, Message};
use rig_core::message::{
    ImageMediaType as RigImageMediaType, ProviderCallId, Reasoning, ReasoningContent,
    ToolCall as RigToolCall, ToolCallId, ToolFunction, ToolResultContent, UserContent,
};
use serde_json::Value;

pub(super) fn rig_history_from_transcript(
    transcript: &NewslyTranscript,
) -> Result<Vec<Message>, AgentRuntimeError> {
    transcript
        .messages
        .iter()
        .map(rig_message)
        .collect::<Result<Vec<_>, _>>()
}

fn rig_message(message: &NewslyMessage) -> Result<Message, AgentRuntimeError> {
    match message.role {
        MessageRole::System => {
            let text = request_text(&message.parts)?;
            Ok(Message::system(text))
        }
        MessageRole::User | MessageRole::Tool => {
            let content = request_content(&message.parts)?;
            Ok(Message::User { content })
        }
        MessageRole::Assistant => {
            let content = assistant_content(&message.parts)?;
            Ok(Message::Assistant {
                id: message
                    .metadata
                    .get("message_id")
                    .and_then(Value::as_str)
                    .map(str::to_owned),
                content,
            })
        }
    }
}

fn request_text(parts: &[MessagePart]) -> Result<String, AgentRuntimeError> {
    parts
        .iter()
        .map(|part| match part {
            MessagePart::Request(RequestPart::Text { text }) => Ok(text.clone()),
            _ => Err(invalid_history("message role and part type do not agree")),
        })
        .collect::<Result<Vec<_>, _>>()
        .map(|parts| parts.join("\n"))
}

pub(super) fn rig_image(image: &ToolImage) -> UserContent {
    rig_image_part(image.media_type, &image.data_base64)
}

fn rig_image_part(media_type: ImageMediaType, data_base64: &str) -> UserContent {
    let media_type = match media_type {
        ImageMediaType::Jpeg => RigImageMediaType::JPEG,
        ImageMediaType::Png => RigImageMediaType::PNG,
    };
    UserContent::image_base64(data_base64.to_owned(), Some(media_type), None)
}

fn request_content(parts: &[MessagePart]) -> Result<Vec<UserContent>, AgentRuntimeError> {
    parts
        .iter()
        .map(|part| match part {
            MessagePart::Request(RequestPart::Text { text }) => Ok(UserContent::text(text.clone())),
            MessagePart::Request(RequestPart::Image {
                media_type,
                data_base64,
            }) => Ok(rig_image_part(*media_type, data_base64)),
            MessagePart::Request(RequestPart::Retry { message, .. }) => {
                Ok(UserContent::text(message.clone()))
            }
            MessagePart::Request(RequestPart::ToolResult {
                tool_call_id,
                provider_call_id,
                provider_item_id,
                tool_name,
                content,
                is_error,
            }) => {
                let call = nonempty_call_id(tool_call_id)?;
                let provider = provider_call_id
                    .as_ref()
                    .and_then(|id| ProviderCallId::new(id.clone()))
                    .map(|provider| match provider_item_id {
                        Some(item_id) => provider.with_item_id(item_id.clone()),
                        None => provider,
                    });
                let value = if *is_error {
                    serde_json::json!({"error": content})
                } else {
                    content.clone()
                };
                Ok(UserContent::tool_result_for(
                    call,
                    provider,
                    tool_name.clone(),
                    vec![ToolResultContent::json(value)],
                ))
            }
            MessagePart::Assistant(_) => Err(invalid_history(
                "request message contains assistant content",
            )),
        })
        .collect()
}

pub(super) fn assistant_content(
    parts: &[MessagePart],
) -> Result<Vec<AssistantContent>, AgentRuntimeError> {
    let mut content = Vec::with_capacity(parts.len());
    for part in parts {
        let next = match part {
            MessagePart::Assistant(AssistantPart::Text { text }) => {
                Ok(AssistantContent::text(text.clone()))
            }
            MessagePart::Assistant(AssistantPart::ToolCall {
                tool_call_id,
                provider_call_id,
                provider_item_id,
                tool_name,
                arguments,
                signature,
                additional_params,
            }) => {
                let provider = provider_call_id
                    .as_ref()
                    .and_then(|id| ProviderCallId::new(id.clone()))
                    .map(|provider| match provider_item_id {
                        Some(item_id) => provider.with_item_id(item_id.clone()),
                        None => provider,
                    });
                Ok(AssistantContent::ToolCall(RigToolCall {
                    id: nonempty_call_id(tool_call_id)?,
                    provider,
                    function: ToolFunction::new(tool_name.clone(), arguments.clone()),
                    signature: signature.clone(),
                    additional_params: additional_params.clone(),
                }))
            }
            MessagePart::Assistant(AssistantPart::Reasoning {
                provider_item_id,
                content_kind,
                text,
                signature,
                encrypted_content,
            }) => {
                let reasoning_content = match content_kind {
                    Some(ReasoningContentKind::Text) => ReasoningContent::Text {
                        text: text
                            .clone()
                            .ok_or_else(|| invalid_history("reasoning text is missing"))?,
                        signature: signature.clone(),
                    },
                    Some(ReasoningContentKind::Encrypted) => ReasoningContent::Encrypted(
                        encrypted_content
                            .clone()
                            .ok_or_else(|| invalid_history("encrypted reasoning is missing"))?,
                    ),
                    Some(ReasoningContentKind::Redacted) => ReasoningContent::Redacted {
                        data: encrypted_content
                            .clone()
                            .ok_or_else(|| invalid_history("redacted reasoning is missing"))?,
                    },
                    Some(ReasoningContentKind::Summary) => ReasoningContent::Summary(
                        text.clone()
                            .ok_or_else(|| invalid_history("reasoning summary is missing"))?,
                    ),
                    None => match (text, encrypted_content, signature) {
                        (Some(text), _, _) => ReasoningContent::Text {
                            text: text.clone(),
                            signature: signature.clone(),
                        },
                        (_, Some(encrypted), _) => ReasoningContent::Encrypted(encrypted.clone()),
                        (_, _, Some(redacted)) => ReasoningContent::Redacted {
                            data: redacted.clone(),
                        },
                        _ => return Err(invalid_history("reasoning part has no durable content")),
                    },
                };
                if let Some(AssistantContent::Reasoning(previous)) = content.last_mut()
                    && previous.id == *provider_item_id
                {
                    previous.content.push(reasoning_content);
                    continue;
                }
                Ok(AssistantContent::Reasoning(Reasoning {
                    id: provider_item_id.clone(),
                    content: vec![reasoning_content],
                }))
            }
            MessagePart::Request(_) => Err(invalid_history(
                "assistant message contains request content",
            )),
        }?;
        content.push(next);
    }
    Ok(content)
}

pub(super) fn newsly_assistant_parts(content: &[AssistantContent]) -> Vec<MessagePart> {
    content
        .iter()
        .flat_map(|part| match part {
            AssistantContent::Text(text) => vec![MessagePart::Assistant(AssistantPart::Text {
                text: text.text.clone(),
            })],
            AssistantContent::ToolCall(call) => {
                vec![MessagePart::Assistant(AssistantPart::ToolCall {
                    tool_call_id: call.id.as_str().to_owned(),
                    provider_call_id: call
                        .provider
                        .as_ref()
                        .map(|provider| provider.call_id.clone()),
                    provider_item_id: call
                        .provider
                        .as_ref()
                        .and_then(|provider| provider.item_id.clone()),
                    tool_name: call.function.name.clone(),
                    arguments: call.function.arguments.clone(),
                    signature: call.signature.clone(),
                    additional_params: call.additional_params.clone(),
                })]
            }
            AssistantContent::Reasoning(reasoning) => reasoning
                .content
                .iter()
                .map(|content| match content {
                    ReasoningContent::Text { text, signature } => {
                        MessagePart::Assistant(AssistantPart::Reasoning {
                            provider_item_id: reasoning.id.clone(),
                            content_kind: Some(ReasoningContentKind::Text),
                            text: Some(text.clone()),
                            signature: signature.clone(),
                            encrypted_content: None,
                        })
                    }
                    ReasoningContent::Encrypted(content) => {
                        MessagePart::Assistant(AssistantPart::Reasoning {
                            provider_item_id: reasoning.id.clone(),
                            content_kind: Some(ReasoningContentKind::Encrypted),
                            text: None,
                            signature: None,
                            encrypted_content: Some(content.clone()),
                        })
                    }
                    ReasoningContent::Redacted { data } => {
                        MessagePart::Assistant(AssistantPart::Reasoning {
                            provider_item_id: reasoning.id.clone(),
                            content_kind: Some(ReasoningContentKind::Redacted),
                            text: None,
                            signature: None,
                            encrypted_content: Some(data.clone()),
                        })
                    }
                    ReasoningContent::Summary(text) => {
                        MessagePart::Assistant(AssistantPart::Reasoning {
                            provider_item_id: reasoning.id.clone(),
                            content_kind: Some(ReasoningContentKind::Summary),
                            text: Some(text.clone()),
                            signature: None,
                            encrypted_content: None,
                        })
                    }
                })
                .collect(),
            AssistantContent::Image(_) => Vec::new(),
        })
        .collect()
}

fn nonempty_call_id(value: &str) -> Result<ToolCallId, AgentRuntimeError> {
    ToolCallId::new(value.to_owned())
        .ok_or_else(|| invalid_history("tool call id must not be empty"))
}

fn invalid_history(message: &str) -> AgentRuntimeError {
    AgentRuntimeError::InvalidRequest(format!("invalid Newsly transcript: {message}"))
}
