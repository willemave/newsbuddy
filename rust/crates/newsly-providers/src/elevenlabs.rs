use std::time::Instant;

use futures_util::StreamExt;
use reqwest::{StatusCode, Url};
use secrecy::{ExposeSecret, SecretString};
use serde::Serialize;

pub(crate) struct ElevenLabsSynthesisRequest<'a> {
    pub(crate) api_base: &'a Url,
    pub(crate) api_key: &'a SecretString,
    pub(crate) voice_id: &'a str,
    pub(crate) model_id: &'a str,
    pub(crate) output_format: &'a str,
    pub(crate) speed: f32,
    pub(crate) text: &'a str,
    pub(crate) max_response_bytes: usize,
}

#[derive(Debug)]
pub(crate) enum ElevenLabsSynthesisError {
    InvalidApiBase,
    Http(reqwest::Error),
    Status { status: StatusCode, detail: String },
    EmptyAudio,
    AudioTooLarge,
}

pub(crate) async fn synthesize(
    client: &reqwest::Client,
    request: ElevenLabsSynthesisRequest<'_>,
) -> Result<Vec<u8>, ElevenLabsSynthesisError> {
    let endpoint = synthesis_endpoint(request.api_base, request.voice_id, request.output_format)?;
    let request_started = Instant::now();
    let response = client
        .post(endpoint)
        .header("xi-api-key", request.api_key.expose_secret())
        .header(reqwest::header::ACCEPT, "audio/mpeg")
        .json(&ElevenLabsRequest {
            text: request.text,
            model_id: request.model_id,
            voice_settings: ElevenLabsVoiceSettings {
                speed: request.speed,
            },
        })
        .send()
        .await
        .map_err(ElevenLabsSynthesisError::Http);
    tracing::info!(
        stage = "tts_response_headers",
        elapsed_ms = request_started.elapsed().as_millis(),
        succeeded = response
            .as_ref()
            .is_ok_and(|response| response.status().is_success()),
        status = response
            .as_ref()
            .ok()
            .map(|response| response.status().as_u16()),
        "Audio timing"
    );
    let response = response?;
    let status = response.status();
    let body_started = Instant::now();
    let audio = read_bounded(response, request.max_response_bytes).await;
    tracing::info!(
        stage = "tts_response_body",
        elapsed_ms = body_started.elapsed().as_millis(),
        request_elapsed_ms = request_started.elapsed().as_millis(),
        succeeded = audio.is_ok(),
        "Audio timing"
    );
    let audio = audio?;
    if !status.is_success() {
        return Err(ElevenLabsSynthesisError::Status {
            status,
            detail: String::from_utf8_lossy(&audio)
                .chars()
                .take(1_000)
                .collect(),
        });
    }
    if audio.is_empty() {
        return Err(ElevenLabsSynthesisError::EmptyAudio);
    }
    Ok(audio)
}

pub(crate) fn is_standard_api_base(value: &Url) -> bool {
    value.scheme() == "https"
        && value.host_str() == Some("api.elevenlabs.io")
        && matches!(value.path(), "" | "/")
        && value.query().is_none()
        && value.fragment().is_none()
}

fn synthesis_endpoint(
    api_base: &Url,
    voice_id: &str,
    output_format: &str,
) -> Result<Url, ElevenLabsSynthesisError> {
    let mut endpoint = api_base.clone();
    {
        let mut path = endpoint
            .path_segments_mut()
            .map_err(|()| ElevenLabsSynthesisError::InvalidApiBase)?;
        path.pop_if_empty();
        path.extend(["v1", "text-to-speech", voice_id]);
    }
    endpoint
        .query_pairs_mut()
        .append_pair("output_format", output_format);
    Ok(endpoint)
}

async fn read_bounded(
    response: reqwest::Response,
    limit: usize,
) -> Result<Vec<u8>, ElevenLabsSynthesisError> {
    if response
        .content_length()
        .is_some_and(|length| length > u64::try_from(limit).unwrap_or(u64::MAX))
    {
        return Err(ElevenLabsSynthesisError::AudioTooLarge);
    }
    let mut bytes = Vec::new();
    let mut chunks = response.bytes_stream();
    while let Some(chunk) = chunks.next().await {
        let chunk = chunk.map_err(ElevenLabsSynthesisError::Http)?;
        if bytes.len().saturating_add(chunk.len()) > limit {
            return Err(ElevenLabsSynthesisError::AudioTooLarge);
        }
        bytes.extend_from_slice(&chunk);
    }
    Ok(bytes)
}

#[derive(Debug, Serialize)]
struct ElevenLabsRequest<'a> {
    text: &'a str,
    model_id: &'a str,
    voice_settings: ElevenLabsVoiceSettings,
}

#[derive(Debug, Serialize)]
struct ElevenLabsVoiceSettings {
    speed: f32,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn endpoint_and_pricing_detection_preserve_provider_contract() {
        let base = Url::parse("https://api.elevenlabs.io").unwrap();
        let endpoint = synthesis_endpoint(&base, "voice/id", "mp3_44100_128").unwrap();
        assert_eq!(
            endpoint.as_str(),
            "https://api.elevenlabs.io/v1/text-to-speech/voice%2Fid?output_format=mp3_44100_128"
        );
        assert!(is_standard_api_base(&base));
        assert!(!is_standard_api_base(
            &Url::parse("https://api.elevenlabs.io/proxy").unwrap()
        ));
    }
}
