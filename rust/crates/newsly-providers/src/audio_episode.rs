use std::collections::BTreeSet;
use std::fmt::{self, Debug, Formatter};
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use futures_util::future::try_join_all;
use newsly_agent_runtime::{
    AgentEngine, AgentEvent, AgentEventSink, AgentLimits, AgentRequest, AgentRuntimeError,
    BoxToolFuture, NewslyTranscript, ProviderUsage, ResponseContract, ToolCall, ToolExecutor,
    ToolPolicy,
};
use reqwest::{StatusCode, Url};
use schemars::{JsonSchema, schema_for};
use secrecy::{ExposeSecret, SecretString};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use tempfile::TempDir;
use thiserror::Error;
use tokio::process::Command;
use tokio::sync::Semaphore;

use crate::{OpenRouterPrivacyPolicy, ProviderCredentials, RigAgentEngine, RigAgentEngineError};

const SCRIPT_SYSTEM_PROMPT: &str = "You write concise, natural podcast scripts for Newsly. Create spoken dialogue, not an essay. The format should feel like a smart tech and business podcast roundtable: quick context, clear stakes, grounded analysis, and a brisk close. Do not mention or imitate any specific real podcast, host, or brand. Do not invent facts outside the supplied source material. Do not emit stage directions, music cues, sponsor reads, or markdown.";
const ELEVENLABS_FLASH_MAX_INPUT_CHARS: usize = 40_000;
const TTS_CHUNK_TARGET_CHARS: usize = 36_000;

#[derive(Debug, Clone)]
pub struct AudioEpisodeGatewayConfig {
    pub credentials: ProviderCredentials,
    pub openrouter_policy: OpenRouterPrivacyPolicy,
    pub script_model: String,
    pub script_timeout: Duration,
    pub elevenlabs_api_base: Url,
    pub elevenlabs_api_key: SecretString,
    pub host_voice_id: String,
    pub guest_voice_id: String,
    pub tts_model: String,
    pub output_format: String,
    pub voice_speed: f32,
    pub max_parallel_tts_requests: usize,
    pub max_tts_response_bytes: usize,
    pub ffmpeg_binary: PathBuf,
}

#[derive(Clone)]
pub struct AudioEpisodeGateway {
    client: reqwest::Client,
    script_engine: RigAgentEngine,
    config: AudioEpisodeGatewayConfig,
    tts_slots: Arc<Semaphore>,
}

impl Debug for AudioEpisodeGateway {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("AudioEpisodeGateway")
            .field("script_model", &self.config.script_model)
            .field("script_timeout", &self.config.script_timeout)
            .field("elevenlabs_api_base", &self.config.elevenlabs_api_base)
            .field("elevenlabs_api_key", &"[REDACTED]")
            .field("host_voice_id", &"[REDACTED]")
            .field("guest_voice_id", &"[REDACTED]")
            .field("tts_model", &self.config.tts_model)
            .field("output_format", &self.config.output_format)
            .field("voice_speed", &self.config.voice_speed)
            .field(
                "max_parallel_tts_requests",
                &self.config.max_parallel_tts_requests,
            )
            .field(
                "max_tts_response_bytes",
                &self.config.max_tts_response_bytes,
            )
            .field("ffmpeg_binary", &self.config.ffmpeg_binary)
            .finish_non_exhaustive()
    }
}

impl AudioEpisodeGateway {
    /// Creates an audio-episode gateway from validated provider and synthesis configuration.
    ///
    /// # Errors
    ///
    /// Returns a configuration or provider-initialization error when the supplied settings are
    /// invalid or the HTTP/model clients cannot be constructed.
    pub fn new(config: AudioEpisodeGatewayConfig) -> Result<Self, AudioEpisodeGatewayError> {
        validate_config(&config)?;
        let client = reqwest::Client::builder()
            .connect_timeout(Duration::from_secs(15))
            .timeout(Duration::from_secs(120))
            .build()?;
        let script_engine =
            RigAgentEngine::new(config.credentials.clone(), config.openrouter_policy.clone())?;
        let max_parallel = config.max_parallel_tts_requests;
        Ok(Self {
            client,
            script_engine,
            config,
            tts_slots: Arc::new(Semaphore::new(max_parallel)),
        })
    }

    pub const fn script_model(&self) -> &str {
        self.config.script_model.as_str()
    }

    pub const fn tts_model(&self) -> &str {
        self.config.tts_model.as_str()
    }

    /// Generates and validates a structured two-speaker script for the supplied source snapshot.
    ///
    /// # Errors
    ///
    /// Returns an error when prompt serialization, provider execution, response decoding, or
    /// script validation fails.
    pub async fn generate_script(
        &self,
        kind: &str,
        source_snapshot: &Value,
    ) -> Result<GeneratedAudioEpisodeScript, AudioEpisodeGatewayError> {
        let schema = schema_for!(AudioEpisodeScript);
        let request = AgentRequest {
            feature: "audio_episode_script".to_owned(),
            model_spec: self.config.script_model.clone(),
            system_prompt: SCRIPT_SYSTEM_PROMPT.to_owned(),
            user_prompt: script_user_prompt(kind, source_snapshot)?,
            transcript: NewslyTranscript::default(),
            response_contract: ResponseContract::JsonSchema {
                name: "audio_episode_script".to_owned(),
                schema,
                strict: true,
                validation_retries: 1,
            },
            tools: Vec::new(),
            tool_policy: ToolPolicy {
                allowed: BTreeSet::new(),
                require_tool: false,
                allow_parallel_calls: false,
            },
            limits: AgentLimits {
                request_limit: Some(2),
                tool_call_limit: 0,
                output_token_limit: Some(3_000),
                deadline: self.config.script_timeout,
            },
            provider_parameters: Map::new(),
        };
        let outcome = self
            .script_engine
            .run(request, Arc::new(NoTools), Arc::new(NoEvents))
            .await?;
        let payload = outcome
            .structured_output
            .unwrap_or(Value::String(outcome.output_text));
        let mut script: AudioEpisodeScript = match payload {
            Value::String(text) => serde_json::from_str(&text)?,
            value => serde_json::from_value(value)?,
        };
        normalize_and_validate_script(&mut script)?;
        Ok(GeneratedAudioEpisodeScript {
            script,
            model: outcome.model_name,
            usage: outcome.usage,
        })
    }

    /// Synthesizes all normalized dialogue turns and stitches them into one MP3 payload.
    ///
    /// # Errors
    ///
    /// Returns an error when the dialogue is invalid, a synthesis request fails, or ffmpeg cannot
    /// assemble the generated audio.
    pub async fn synthesize_dialogue(
        &self,
        turns: &[AudioEpisodeTurn],
    ) -> Result<SynthesizedDialogue, AudioEpisodeGatewayError> {
        let chunks = normalize_tts_chunks(turns)?;
        let request_count = i32::try_from(chunks.len()).unwrap_or(i32::MAX);
        let text_chars = chunks.iter().fold(0_i32, |total, chunk| {
            total.saturating_add(i32::try_from(chunk.text.chars().count()).unwrap_or(i32::MAX))
        });
        let calls = chunks.iter().map(|chunk| self.synthesize_chunk(chunk));
        let audio_chunks = try_join_all(calls).await?;
        let audio_bytes = self.stitch_mp3(&audio_chunks).await?;
        Ok(SynthesizedDialogue {
            audio_bytes,
            request_count,
            text_chars,
        })
    }

    async fn synthesize_chunk(
        &self,
        chunk: &TtsChunk,
    ) -> Result<Vec<u8>, AudioEpisodeGatewayError> {
        let _slot = self
            .tts_slots
            .acquire()
            .await
            .map_err(|_| AudioEpisodeGatewayError::ProviderClosed)?;
        let voice_id = if chunk.speaker == AudioEpisodeSpeaker::Host {
            &self.config.host_voice_id
        } else {
            &self.config.guest_voice_id
        };
        let mut endpoint = self.config.elevenlabs_api_base.clone();
        {
            let mut segments = endpoint.path_segments_mut().map_err(|()| {
                AudioEpisodeGatewayError::InvalidConfiguration(
                    "ElevenLabs API base URL cannot be a base URL".to_owned(),
                )
            })?;
            segments.pop_if_empty();
            segments.extend(["v1", "text-to-speech", voice_id]);
        }
        endpoint
            .query_pairs_mut()
            .append_pair("output_format", &self.config.output_format);
        let response = self
            .client
            .post(endpoint)
            .header("xi-api-key", self.config.elevenlabs_api_key.expose_secret())
            .header(reqwest::header::ACCEPT, "audio/mpeg")
            .json(&ElevenLabsRequest {
                text: &chunk.text,
                model_id: &self.config.tts_model,
                voice_settings: ElevenLabsVoiceSettings {
                    speed: self.config.voice_speed,
                },
            })
            .send()
            .await?;
        let status = response.status();
        if !status.is_success() {
            let detail = response
                .text()
                .await
                .unwrap_or_default()
                .chars()
                .take(1_000)
                .collect();
            return Err(AudioEpisodeGatewayError::ElevenLabsStatus { status, detail });
        }
        if response.content_length().is_some_and(|length| {
            length > u64::try_from(self.config.max_tts_response_bytes).unwrap_or(u64::MAX)
        }) {
            return Err(AudioEpisodeGatewayError::AudioTooLarge);
        }
        let bytes = response.bytes().await?;
        if bytes.is_empty() {
            return Err(AudioEpisodeGatewayError::EmptyAudio);
        }
        if bytes.len() > self.config.max_tts_response_bytes {
            return Err(AudioEpisodeGatewayError::AudioTooLarge);
        }
        Ok(bytes.to_vec())
    }

    async fn stitch_mp3(&self, chunks: &[Vec<u8>]) -> Result<Vec<u8>, AudioEpisodeGatewayError> {
        if chunks.is_empty() {
            return Err(AudioEpisodeGatewayError::EmptyAudio);
        }
        if chunks.len() == 1 {
            return Ok(chunks[0].clone());
        }
        let temp_dir = TempDir::with_prefix("newsly-audio-episode-")?;
        let mut input_paths = Vec::with_capacity(chunks.len());
        for (index, chunk) in chunks.iter().enumerate() {
            let path = temp_dir.path().join(format!("turn-{index:03}.mp3"));
            tokio::fs::write(&path, chunk).await?;
            input_paths.push(path);
        }
        let concat_path = temp_dir.path().join("inputs.txt");
        let concat_manifest = input_paths
            .iter()
            .map(|path| ffmpeg_concat_line(path))
            .collect::<Vec<_>>()
            .join("\n");
        tokio::fs::write(&concat_path, concat_manifest).await?;
        let output_path = temp_dir.path().join("stitched.mp3");
        let mut command = Command::new(&self.config.ffmpeg_binary);
        command
            .kill_on_drop(true)
            .args([
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
            ])
            .arg(&concat_path)
            .args(["-vn", "-codec:a", "libmp3lame", "-b:a", "128k"])
            .arg(&output_path);
        let timeout = Duration::from_secs(
            u64::try_from(chunks.len())
                .unwrap_or(60)
                .saturating_mul(10)
                .clamp(30, 300),
        );
        let output = tokio::time::timeout(timeout, command.output())
            .await
            .map_err(|_| AudioEpisodeGatewayError::FfmpegTimeout)??;
        if !output.status.success() {
            let detail = String::from_utf8_lossy(&output.stderr)
                .chars()
                .rev()
                .take(1_000)
                .collect::<String>()
                .chars()
                .rev()
                .collect();
            return Err(AudioEpisodeGatewayError::FfmpegFailed(detail));
        }
        let bytes = tokio::fs::read(output_path).await?;
        if bytes.is_empty() {
            return Err(AudioEpisodeGatewayError::EmptyAudio);
        }
        Ok(bytes)
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum AudioEpisodeSpeaker {
    Host,
    Cohost,
    Expert,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct AudioEpisodeTurn {
    pub speaker: AudioEpisodeSpeaker,
    pub text: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct AudioEpisodeScript {
    pub title: String,
    pub estimated_duration_seconds: i32,
    pub turns: Vec<AudioEpisodeTurn>,
}

impl AudioEpisodeScript {
    pub fn render_text(&self) -> String {
        let mut lines = vec![self.title.trim().to_owned()];
        lines.extend(self.turns.iter().map(|turn| {
            let speaker = match turn.speaker {
                AudioEpisodeSpeaker::Host => "Host",
                AudioEpisodeSpeaker::Cohost => "Cohost",
                AudioEpisodeSpeaker::Expert => "Expert",
            };
            format!("{speaker}: {}", turn.text.trim())
        }));
        lines
            .into_iter()
            .filter(|line| !line.trim().is_empty())
            .collect::<Vec<_>>()
            .join("\n\n")
    }

    pub fn estimated_duration(&self) -> i32 {
        estimate_duration_seconds(&self.render_text())
    }
}

#[derive(Debug, Clone)]
pub struct GeneratedAudioEpisodeScript {
    pub script: AudioEpisodeScript,
    pub model: String,
    pub usage: ProviderUsage,
}

#[derive(Debug, Clone)]
pub struct SynthesizedDialogue {
    pub audio_bytes: Vec<u8>,
    pub request_count: i32,
    pub text_chars: i32,
}

#[derive(Debug, Clone)]
struct TtsChunk {
    speaker: AudioEpisodeSpeaker,
    text: String,
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

#[derive(Debug)]
struct NoEvents;

impl AgentEventSink for NoEvents {
    fn publish(&self, _event: AgentEvent) -> Result<(), AgentRuntimeError> {
        Ok(())
    }
}

#[derive(Debug)]
struct NoTools;

impl ToolExecutor for NoTools {
    fn execute(&self, call: ToolCall, _events: Arc<dyn AgentEventSink>) -> BoxToolFuture<'_> {
        Box::pin(async move {
            Err(AgentRuntimeError::Tool(format!(
                "audio script generation does not expose tool {}",
                call.name
            )))
        })
    }
}

fn script_user_prompt(kind: &str, snapshot: &Value) -> Result<String, AudioEpisodeGatewayError> {
    let source = serde_json::to_string_pretty(snapshot)?;
    let prompt = match kind {
        "fast_news_digest" => format!(
            "Create a roughly 60 second quick-hit episode from these unread Fast Reads. Curate the highest-signal highlights rather than reading every item. Use only the supplied summaries and key points. Mention concrete entities and numbers when present, group related items, start with the top two or three headlines and why they matter, and end with one short what-to-watch-next close. Use 110-150 spoken words and as many natural turns as needed.\n\nUnread Fast Reads JSON:\n{source}"
        ),
        "content_council_discussion" => {
            let label = if snapshot.get("content_type").and_then(Value::as_str) == Some("podcast") {
                "transcript"
            } else {
                "article"
            };
            format!(
                "Create a roughly 60 second council-of-experts discussion about this long-form {label}. Use the supplied excerpts and summary. Give the thesis, strongest evidence, implications, and weak spots or open questions. Keep it grounded. Use 110-150 spoken words. Use host for framing, cohost for synthesis, and expert for sharper analysis. End with a concise takeaway and why the piece is worth remembering.\n\nLong-form source JSON:\n{source}"
            )
        }
        "news_item_discussion" => format!(
            "Create a roughly 60 second podcast-style discussion about this single Fast Read. Use only the supplied summary, key points, and link metadata. Give the headline, context, stakes, and what to watch next. Use 110-150 spoken words. Use host for framing, cohost for synthesis, and expert for sharper analysis. End with a concise takeaway.\n\nFast Read source JSON:\n{source}"
        ),
        "custom_narration" => format!(
            "Create one cohesive podcast-style narration from the selected articles, podcast transcripts, and Fast Reads. Synthesize them as one episode, explaining shared themes, contradictions, evidence, and implications while preserving material source-specific details. Stay grounded in the selected sources. Use 500-700 spoken words and as many turns as needed. Use host for setup and transitions, cohost for synthesis, and expert for sharper analysis. Start by framing why the sources belong together and end with the concise takeaway the listener should remember.\n\nSelected source JSON:\n{source}"
        ),
        unsupported => {
            return Err(AudioEpisodeGatewayError::UnsupportedKind(
                unsupported.to_owned(),
            ));
        }
    };
    Ok(prompt)
}

fn normalize_and_validate_script(
    script: &mut AudioEpisodeScript,
) -> Result<(), AudioEpisodeGatewayError> {
    script.title = script.title.trim().chars().take(255).collect();
    script.turns.retain_mut(|turn| {
        turn.text = turn.text.trim().to_owned();
        !turn.text.is_empty()
    });
    if script.title.is_empty() {
        return Err(AudioEpisodeGatewayError::InvalidScript(
            "script title is empty".to_owned(),
        ));
    }
    if script.turns.is_empty() || script.turns.len() > 100 {
        return Err(AudioEpisodeGatewayError::InvalidScript(
            "script must contain between 1 and 100 non-empty turns".to_owned(),
        ));
    }
    let character_count = script.turns.iter().fold(0_usize, |total, turn| {
        total.saturating_add(turn.text.chars().count())
    });
    if character_count > 100_000 {
        return Err(AudioEpisodeGatewayError::InvalidScript(
            "script exceeds the maximum spoken character count".to_owned(),
        ));
    }
    script.estimated_duration_seconds = estimate_duration_seconds(&script.render_text()).max(1);
    Ok(())
}

fn normalize_tts_chunks(
    turns: &[AudioEpisodeTurn],
) -> Result<Vec<TtsChunk>, AudioEpisodeGatewayError> {
    let mut chunks = Vec::new();
    for turn in turns {
        let text = turn.text.trim();
        if text.is_empty() {
            continue;
        }
        for chunk in split_tts_text(text) {
            chunks.push(TtsChunk {
                speaker: turn.speaker.clone(),
                text: chunk,
            });
        }
    }
    if chunks.is_empty() {
        return Err(AudioEpisodeGatewayError::InvalidScript(
            "script contains no spoken text".to_owned(),
        ));
    }
    Ok(chunks)
}

fn split_tts_text(text: &str) -> Vec<String> {
    if text.chars().count() <= ELEVENLABS_FLASH_MAX_INPUT_CHARS {
        return vec![text.to_owned()];
    }
    let mut remaining = text;
    let mut chunks = Vec::new();
    while remaining.chars().count() > TTS_CHUNK_TARGET_CHARS {
        let byte_target = remaining
            .char_indices()
            .nth(TTS_CHUNK_TARGET_CHARS)
            .map_or(remaining.len(), |(index, _)| index);
        let window = &remaining[..byte_target];
        let sentence_cut = window
            .char_indices()
            .filter(|(index, character)| {
                matches!(character, '.' | '!' | '?')
                    && remaining[*index + character.len_utf8()..]
                        .chars()
                        .next()
                        .is_some_and(char::is_whitespace)
            })
            .map(|(index, character)| index + character.len_utf8())
            .next_back();
        let whitespace_cut = window
            .char_indices()
            .filter(|(_, character)| character.is_whitespace())
            .map(|(index, _)| index)
            .next_back();
        let cut = sentence_cut
            .or(whitespace_cut)
            .unwrap_or(byte_target)
            .max(1);
        chunks.push(remaining[..cut].trim().to_owned());
        remaining = remaining[cut..].trim_start();
    }
    if !remaining.trim().is_empty() {
        chunks.push(remaining.trim().to_owned());
    }
    chunks
}

fn estimate_duration_seconds(script_text: &str) -> i32 {
    let words = i64::try_from(script_text.split_whitespace().count()).unwrap_or(i64::MAX);
    if words == 0 {
        return 0;
    }
    i32::try_from((words.saturating_mul(60).saturating_add(144)) / 145).unwrap_or(i32::MAX)
}

fn ffmpeg_concat_line(path: &Path) -> String {
    let escaped = path.to_string_lossy().replace('\'', "'\\''");
    format!("file '{escaped}'")
}

fn validate_config(config: &AudioEpisodeGatewayConfig) -> Result<(), AudioEpisodeGatewayError> {
    if config.script_model.trim().is_empty()
        || config.host_voice_id.trim().is_empty()
        || config.guest_voice_id.trim().is_empty()
        || config.tts_model.trim().is_empty()
        || config.output_format.trim().is_empty()
        || config.script_timeout.is_zero()
        || config.max_parallel_tts_requests == 0
        || config.max_tts_response_bytes < 1_024
        || !(0.7..=1.2).contains(&config.voice_speed)
    {
        return Err(AudioEpisodeGatewayError::InvalidConfiguration(
            "audio provider settings are incomplete or outside supported bounds".to_owned(),
        ));
    }
    Ok(())
}

#[derive(Debug, Error)]
pub enum AudioEpisodeGatewayError {
    #[error("audio episode provider configuration is invalid: {0}")]
    InvalidConfiguration(String),
    #[error("audio episode kind is unsupported: {0}")]
    UnsupportedKind(String),
    #[error("audio episode script is invalid: {0}")]
    InvalidScript(String),
    #[error("audio episode script provider configuration failed")]
    ScriptConfiguration(#[from] RigAgentEngineError),
    #[error("audio episode script generation failed")]
    Script(#[from] AgentRuntimeError),
    #[error("audio episode JSON processing failed")]
    Json(#[from] serde_json::Error),
    #[error("audio episode provider HTTP request failed")]
    Http(#[from] reqwest::Error),
    #[error("ElevenLabs returned HTTP {status}: {detail}")]
    ElevenLabsStatus { status: StatusCode, detail: String },
    #[error("ElevenLabs returned empty audio")]
    EmptyAudio,
    #[error("ElevenLabs audio response exceeded the configured bound")]
    AudioTooLarge,
    #[error("audio provider concurrency gate closed")]
    ProviderClosed,
    #[error("audio episode file operation failed")]
    Io(#[from] std::io::Error),
    #[error("ffmpeg audio stitching timed out")]
    FfmpegTimeout,
    #[error("ffmpeg audio stitching failed: {0}")]
    FfmpegFailed(String),
}

impl AudioEpisodeGatewayError {
    pub fn retryable(&self) -> bool {
        match self {
            Self::Http(error) => error.is_timeout() || error.is_connect() || error.is_request(),
            Self::ElevenLabsStatus { status, .. } => {
                *status == StatusCode::TOO_MANY_REQUESTS || status.is_server_error()
            }
            Self::Script(error) => !matches!(
                error,
                AgentRuntimeError::InvalidRequest(_)
                    | AgentRuntimeError::Validation(_)
                    | AgentRuntimeError::Tool(_)
            ),
            Self::EmptyAudio | Self::FfmpegTimeout | Self::FfmpegFailed(_) => true,
            Self::InvalidConfiguration(_)
            | Self::UnsupportedKind(_)
            | Self::InvalidScript(_)
            | Self::ScriptConfiguration(_)
            | Self::Json(_)
            | Self::AudioTooLarge
            | Self::ProviderClosed
            | Self::Io(_) => false,
        }
    }
}
