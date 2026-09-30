use newsly_agent_runtime::AgentRuntimeError;
use rig_core::completion::{CompletionRequest, CompletionResponse, NormalizeCompletionResponse};
use rig_core::http_client::HttpClientExt;
use rig_core::providers::openai;
use rig_core::providers::openai::responses_api::{
    ResponsesProviderExt, SystemInstructionsPlacement,
};
use serde_json::Value;

pub(super) async fn complete(
    client: &openai::Client,
    model: &str,
    request: CompletionRequest,
) -> Result<CompletionResponse, AgentRuntimeError> {
    let (_, request) = client
        .ext()
        .create_responses_request(
            model.to_owned(),
            request,
            &[],
            false,
            SystemInstructionsPlacement::default(),
            false,
        )
        .map_err(provider_error)?;
    let body = serde_json::to_vec(&request).map_err(provider_error)?;
    let request = client
        .post(<openai::client::OpenAIResponsesExt as ResponsesProviderExt>::RESPONSES_PATH)
        .map_err(provider_error)?
        .body(body)
        .map_err(provider_error)?;
    let response = client
        .send::<_, Vec<u8>>(request)
        .await
        .map_err(provider_error)?;
    let (parts, body) = response.into_parts();
    let provider_request_id = parts
        .headers
        .get("x-request-id")
        .and_then(|value| value.to_str().ok())
        .filter(|value| !value.is_empty())
        .map(str::to_owned);
    let body = body.await.map_err(provider_error)?;
    let raw = serde_json::from_slice(&body).map_err(provider_error)?;
    normalize(raw, provider_request_id)
}

fn normalize(
    raw: Value,
    provider_request_id: Option<String>,
) -> Result<CompletionResponse, AgentRuntimeError> {
    let response: openai::responses_api::CompletionResponse =
        serde_json::from_value(raw.clone()).map_err(provider_error)?;
    response
        .normalize("openai")
        .map(|response| {
            response
                .with_raw(raw)
                .with_optional_provider_request_id(provider_request_id)
        })
        .map_err(provider_error)
}

fn provider_error(error: impl std::fmt::Display) -> AgentRuntimeError {
    AgentRuntimeError::Provider(error.to_string())
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::normalize;
    use crate::rig_engine::provider_usage;

    #[test]
    fn raw_cache_writes_and_transport_identity_survive_sdk_normalization() {
        let response = normalize(
            json!({
                "id": "resp_cache_write",
                "object": "response",
                "created_at": 0,
                "status": "completed",
                "model": "gpt-test",
                "usage": {
                    "input_tokens": 500,
                    "output_tokens": 10,
                    "total_tokens": 510,
                    "input_tokens_details": {
                        "cached_tokens": 400,
                        "cache_write_tokens": 50
                    }
                },
                "output": [{
                    "type": "message",
                    "id": "msg_cache_write",
                    "status": "completed",
                    "role": "assistant",
                    "content": [{
                        "type": "output_text",
                        "annotations": [],
                        "text": "done"
                    }]
                }],
                "tools": []
            }),
            Some("req_cache_write".to_owned()),
        )
        .expect("raw OpenAI response should normalize");

        assert_eq!(provider_usage(&response).cache_write_tokens, 50);
        assert_eq!(
            response.provider_request_id.as_deref(),
            Some("req_cache_write")
        );
        assert_eq!(
            response.raw["usage"]["input_tokens_details"]["cache_write_tokens"],
            json!(50)
        );
    }
}
