use std::future::Future;
use std::path::{Path, PathBuf};
use std::pin::Pin;
use std::sync::Arc;
use std::time::Duration;

use async_openai::Client;
use async_openai::config::OpenAIConfig;
use async_openai::types::audio::{AudioInput, AudioResponseFormat, CreateTranscriptionRequestArgs};
use secrecy::{ExposeSecret, SecretString};
use serde::Deserialize;
use tempfile::{Builder as TempFileBuilder, TempDir};
use thiserror::Error;
use tokio::process::Command;

const MODEL: &str = "gpt-transcribe";
const MAX_PROVIDER_FILE_BYTES: u64 = 25 * 1024 * 1024;
const CHUNK_DURATION_SECONDS: u64 = 10 * 60;
const VOICE_DICTATION_PROMPT: &str = "This recording is a short voice dictation that may contain names, numbers, URLs, or specialized terms.";
const CONTINUATION_SUFFIX: &str = "This is a continuation of the previous segment.";

#[derive(Debug, Clone)]
pub struct OpenAiTranscriptionGateway {
    client: Client<OpenAIConfig>,
    request_timeout: Duration,
    standard_pricing: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TranscriptionResult {
    pub transcript: String,
    pub language: Option<String>,
    pub chunk_count: usize,
    pub model: String,
    pub prompt_chars: usize,
    pub audio_duration_ms: Option<u64>,
    pub audio_duration_estimate_ms: Option<u64>,
    pub audio_duration_source: AudioDurationSource,
    pub standard_pricing: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TranscriptionChunkUsage {
    pub chunk_index: usize,
    pub model: &'static str,
    pub prompt_chars: usize,
    pub audio_size_bytes: u64,
    pub audio_duration_ms: Option<u64>,
    pub audio_duration_estimate_ms: Option<u64>,
    pub audio_duration_source: AudioDurationSource,
    pub standard_pricing: bool,
}

pub trait TranscriptionUsageObserver: Send + Sync {
    fn observe(
        &self,
        usage: TranscriptionChunkUsage,
    ) -> Pin<Box<dyn Future<Output = Result<(), String>> + Send + '_>>;
}

#[derive(Debug)]
struct NoTranscriptionUsageObserver;

impl TranscriptionUsageObserver for NoTranscriptionUsageObserver {
    fn observe(
        &self,
        _usage: TranscriptionChunkUsage,
    ) -> Pin<Box<dyn Future<Output = Result<(), String>> + Send + '_>> {
        Box::pin(async { Ok(()) })
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AudioDurationSource {
    Ffprobe,
    EstimatedFromBytes,
}

impl AudioDurationSource {
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Ffprobe => "ffprobe",
            Self::EstimatedFromBytes => "estimated_from_bytes",
        }
    }

    #[must_use]
    pub const fn is_measured(self) -> bool {
        matches!(self, Self::Ffprobe)
    }
}

impl OpenAiTranscriptionGateway {
    /// Creates the backend-managed `OpenAI` transcription client.
    ///
    /// # Errors
    ///
    /// Returns [`OpenAiTranscriptionError::InvalidConfiguration`] for an empty key or timeout.
    pub fn new(
        api_key: &SecretString,
        api_base: Option<&str>,
        request_timeout: Duration,
    ) -> Result<Self, OpenAiTranscriptionError> {
        if api_key.expose_secret().trim().is_empty() {
            return Err(OpenAiTranscriptionError::InvalidConfiguration(
                "OpenAI API key must not be empty".to_owned(),
            ));
        }
        if request_timeout.is_zero() {
            return Err(OpenAiTranscriptionError::InvalidConfiguration(
                "OpenAI transcription timeout must be greater than zero".to_owned(),
            ));
        }
        let mut config = OpenAIConfig::new().with_api_key(api_key.expose_secret());
        let standard_pricing = api_base.is_none_or(is_standard_openai_api_base);
        if let Some(api_base) = api_base {
            config = config.with_api_base(api_base);
        }
        Ok(Self {
            client: Client::with_config(config),
            request_timeout,
            standard_pricing,
        })
    }

    pub const fn model_name() -> &'static str {
        MODEL
    }

    /// Transcribes one completed upload without retaining the file after the call.
    ///
    /// Files above `OpenAI`'s 25 MiB boundary are split into ten-minute chunks with ffmpeg. Provider
    /// calls are retried at most three times, matching the legacy service's bounded retry policy.
    ///
    /// # Errors
    ///
    /// Returns a typed error for local media failures, provider failures, timeouts, or malformed
    /// provider responses.
    pub async fn transcribe_upload(
        &self,
        path: &Path,
        original_filename: &str,
    ) -> Result<TranscriptionResult, OpenAiTranscriptionError> {
        self.transcribe_upload_observed(
            path,
            original_filename,
            Arc::new(NoTranscriptionUsageObserver),
        )
        .await
    }

    /// Transcribes an upload and reports each successful provider response before decoding it.
    ///
    /// # Errors
    ///
    /// Returns a typed error for local media failures, provider failures, timeouts, malformed
    /// provider responses, or a rejected usage observation.
    pub async fn transcribe_upload_observed(
        &self,
        path: &Path,
        original_filename: &str,
        usage_observer: Arc<dyn TranscriptionUsageObserver>,
    ) -> Result<TranscriptionResult, OpenAiTranscriptionError> {
        let metadata = tokio::fs::metadata(path).await?;
        if metadata.len() == 0 {
            return Err(OpenAiTranscriptionError::InvalidAudio(
                "Uploaded audio file is empty".to_owned(),
            ));
        }
        let duration = audio_duration(path).await?;
        if metadata.len() <= MAX_PROVIDER_FILE_BYTES {
            let (transcript, language) = self
                .transcribe_one(
                    path,
                    original_filename,
                    VOICE_DICTATION_PROMPT,
                    0,
                    duration,
                    usage_observer,
                )
                .await?;
            return Ok(TranscriptionResult {
                transcript,
                language,
                chunk_count: 1,
                model: MODEL.to_owned(),
                prompt_chars: VOICE_DICTATION_PROMPT.chars().count(),
                audio_duration_ms: duration.observed_milliseconds(),
                audio_duration_estimate_ms: duration.estimated_milliseconds(),
                audio_duration_source: duration.source,
                standard_pricing: self.standard_pricing,
            });
        }

        ensure_ffmpeg().await?;
        let chunks = split_audio(path, original_filename, duration.duration).await?;
        let chunk_count = chunks.paths.len();
        if chunk_count == 0 {
            return Err(OpenAiTranscriptionError::Media(
                "ffmpeg produced no audio chunks".to_owned(),
            ));
        }
        let mut chunk_durations = Vec::with_capacity(chunk_count);
        for chunk in &chunks.paths {
            chunk_durations.push(audio_duration(chunk).await?);
        }

        let mut transcripts = Vec::with_capacity(chunk_count);
        let mut language = None;
        for (index, chunk) in chunks.paths.iter().enumerate() {
            let prompt = if index == 0 {
                VOICE_DICTATION_PROMPT.to_owned()
            } else {
                format!("{VOICE_DICTATION_PROMPT} {CONTINUATION_SUFFIX}")
            };
            let filename = chunk
                .file_name()
                .and_then(|value| value.to_str())
                .unwrap_or("audio.mp3");
            let (text, detected_language) = self
                .transcribe_one(
                    chunk,
                    filename,
                    &prompt,
                    index,
                    chunk_durations[index],
                    Arc::clone(&usage_observer),
                )
                .await?;
            transcripts.push(text);
            if language.is_none() {
                language = detected_language;
            }
        }

        let all_measured = chunk_durations
            .iter()
            .all(|duration| duration.source.is_measured());
        let total_duration_ms = chunk_durations.iter().fold(0_u64, |total, duration| {
            total.saturating_add(duration.milliseconds())
        });
        Ok(TranscriptionResult {
            transcript: transcripts.join(" "),
            language,
            chunk_count,
            model: MODEL.to_owned(),
            prompt_chars: VOICE_DICTATION_PROMPT.chars().count(),
            audio_duration_ms: all_measured.then_some(total_duration_ms),
            audio_duration_estimate_ms: (!all_measured).then_some(total_duration_ms),
            audio_duration_source: if all_measured {
                AudioDurationSource::Ffprobe
            } else {
                AudioDurationSource::EstimatedFromBytes
            },
            standard_pricing: self.standard_pricing,
        })
    }

    async fn transcribe_one(
        &self,
        path: &Path,
        filename: &str,
        prompt: &str,
        chunk_index: usize,
        duration: AudioDurationMeasurement,
        usage_observer: Arc<dyn TranscriptionUsageObserver>,
    ) -> Result<(String, Option<String>), OpenAiTranscriptionError> {
        let bytes = tokio::fs::read(path).await?;
        let mut last_error = None;
        for attempt in 0..3_u32 {
            let request = CreateTranscriptionRequestArgs::default()
                .file(AudioInput::from_vec_u8(filename.to_owned(), bytes.clone()))
                .model(MODEL)
                .response_format(AudioResponseFormat::Json)
                .prompt(prompt)
                .build()
                .map_err(|error| OpenAiTranscriptionError::InvalidAudio(error.to_string()))?;
            let response = tokio::time::timeout(
                self.request_timeout,
                self.client.audio().transcription().create_raw(request),
            )
            .await;
            match response {
                Ok(Ok(body)) => {
                    observe_transcription_usage(
                        &usage_observer,
                        TranscriptionChunkUsage {
                            chunk_index,
                            model: MODEL,
                            prompt_chars: prompt.chars().count(),
                            audio_size_bytes: u64::try_from(bytes.len()).unwrap_or(u64::MAX),
                            audio_duration_ms: duration.observed_milliseconds(),
                            audio_duration_estimate_ms: duration.estimated_milliseconds(),
                            audio_duration_source: duration.source,
                            standard_pricing: self.standard_pricing,
                        },
                    )
                    .await?;
                    let parsed: FlexibleTranscriptionResponse = serde_json::from_slice(&body)
                        .map_err(|error| {
                            OpenAiTranscriptionError::InvalidResponse(error.to_string())
                        })?;
                    let language = parsed
                        .language
                        .filter(|value| !value.trim().is_empty())
                        .or_else(|| {
                            parsed
                                .languages
                                .into_iter()
                                .find_map(|language| language.code.filter(|code| !code.is_empty()))
                        });
                    return Ok((parsed.text, language));
                }
                Ok(Err(error)) => last_error = Some(OpenAiTranscriptionError::Provider(error)),
                Err(_) => last_error = Some(OpenAiTranscriptionError::Timeout),
            }
            if attempt < 2 {
                tokio::time::sleep(Duration::from_secs(4_u64 << attempt)).await;
            }
        }
        Err(last_error.unwrap_or_else(|| {
            OpenAiTranscriptionError::InvalidResponse("provider returned no result".to_owned())
        }))
    }
}

async fn observe_transcription_usage(
    observer: &Arc<dyn TranscriptionUsageObserver>,
    usage: TranscriptionChunkUsage,
) -> Result<(), OpenAiTranscriptionError> {
    observer
        .observe(usage)
        .await
        .map_err(OpenAiTranscriptionError::UsageObservation)
}

fn is_standard_openai_api_base(value: &str) -> bool {
    matches!(
        value.trim().trim_end_matches('/'),
        "https://api.openai.com" | "https://api.openai.com/v1"
    )
}

#[derive(Debug, Deserialize)]
struct FlexibleTranscriptionResponse {
    text: String,
    #[serde(default)]
    language: Option<String>,
    #[serde(default)]
    languages: Vec<DetectedLanguage>,
}

#[derive(Debug, Deserialize)]
struct DetectedLanguage {
    #[serde(default)]
    code: Option<String>,
}

#[derive(Debug)]
struct AudioChunks {
    _directory: TempDir,
    paths: Vec<PathBuf>,
}

async fn ensure_ffmpeg() -> Result<(), OpenAiTranscriptionError> {
    let status = tokio::time::timeout(
        Duration::from_secs(10),
        Command::new("ffmpeg").arg("-version").status(),
    )
    .await
    .map_err(|_| OpenAiTranscriptionError::Media("ffmpeg availability check timed out".to_owned()))?
    .map_err(|error| {
        OpenAiTranscriptionError::Media(format!(
            "Audio file exceeds 25MB but ffmpeg is unavailable: {error}"
        ))
    })?;
    if !status.success() {
        return Err(OpenAiTranscriptionError::Media(
            "Audio file exceeds 25MB but ffmpeg is unavailable".to_owned(),
        ));
    }
    Ok(())
}

async fn split_audio(
    input: &Path,
    original_filename: &str,
    duration: Duration,
) -> Result<AudioChunks, OpenAiTranscriptionError> {
    let duration_seconds = duration
        .as_secs()
        .saturating_add(u64::from(duration.subsec_nanos() > 0));
    let chunk_count = usize::try_from(duration_seconds.div_ceil(CHUNK_DURATION_SECONDS).max(1))
        .map_err(|_| {
            OpenAiTranscriptionError::Media("Audio requires too many chunks".to_owned())
        })?;
    let directory = TempFileBuilder::new()
        .prefix("newsly-audio-chunks-")
        .tempdir()?;
    let extension = provider_extension(original_filename);
    let mut paths = Vec::with_capacity(chunk_count);
    for index in 0..chunk_count {
        let output = directory
            .path()
            .join(format!("chunk_{index:03}.{extension}"));
        let result = Command::new("ffmpeg")
            .arg("-i")
            .arg(input)
            .arg("-ss")
            .arg((index as u64 * CHUNK_DURATION_SECONDS).to_string())
            .arg("-t")
            .arg(CHUNK_DURATION_SECONDS.to_string())
            .arg("-acodec")
            .arg("copy")
            .arg("-y")
            .arg(&output)
            .output()
            .await?;
        if !result.status.success() {
            return Err(OpenAiTranscriptionError::Media(format!(
                "ffmpeg failed: {}",
                String::from_utf8_lossy(&result.stderr).trim()
            )));
        }
        if tokio::fs::metadata(&output).await?.len() == 0 {
            return Err(OpenAiTranscriptionError::Media(
                "ffmpeg produced an empty audio chunk".to_owned(),
            ));
        }
        paths.push(output);
    }
    Ok(AudioChunks {
        _directory: directory,
        paths,
    })
}

#[derive(Debug, Clone, Copy)]
struct AudioDurationMeasurement {
    duration: Duration,
    source: AudioDurationSource,
}

impl AudioDurationMeasurement {
    fn milliseconds(self) -> u64 {
        u64::try_from(self.duration.as_millis()).unwrap_or(u64::MAX)
    }

    fn observed_milliseconds(self) -> Option<u64> {
        self.source.is_measured().then_some(self.milliseconds())
    }

    fn estimated_milliseconds(self) -> Option<u64> {
        (!self.source.is_measured()).then_some(self.milliseconds())
    }
}

async fn audio_duration(path: &Path) -> Result<AudioDurationMeasurement, OpenAiTranscriptionError> {
    if let Some(duration) = probe_audio_duration(path).await {
        return Ok(AudioDurationMeasurement {
            duration,
            source: AudioDurationSource::Ffprobe,
        });
    }
    let bytes = tokio::fs::metadata(path).await?.len();
    let whole_mebibytes = u32::try_from(bytes / (1024 * 1024)).map_err(|_| {
        OpenAiTranscriptionError::Media("Audio file is too large to estimate duration".to_owned())
    })?;
    let remainder_bytes =
        u32::try_from(bytes % (1024 * 1024)).expect("the remainder of one mebibyte fits u32");
    let estimated_seconds =
        (f64::from(whole_mebibytes) + f64::from(remainder_bytes) / (1024.0 * 1024.0)) * 60.0;
    let duration = Duration::try_from_secs_f64(estimated_seconds.max(0.001)).map_err(|error| {
        OpenAiTranscriptionError::Media(format!("Audio duration estimate is invalid: {error}"))
    })?;
    Ok(AudioDurationMeasurement {
        duration,
        source: AudioDurationSource::EstimatedFromBytes,
    })
}

async fn probe_audio_duration(path: &Path) -> Option<Duration> {
    let output = Command::new("ffprobe")
        .arg("-i")
        .arg(path)
        .arg("-show_entries")
        .arg("format=duration")
        .arg("-v")
        .arg("quiet")
        .arg("-of")
        .arg("csv=p=0")
        .output()
        .await
        .ok()?;
    if output.status.success()
        && let Ok(text) = std::str::from_utf8(&output.stdout)
        && let Ok(duration) = text.trim().parse::<f64>()
        && duration.is_finite()
        && duration > 0.0
    {
        return Duration::try_from_secs_f64(duration).ok();
    }
    None
}

fn provider_extension(filename: &str) -> &'static str {
    match Path::new(filename)
        .extension()
        .and_then(|value| value.to_str())
        .map(str::to_ascii_lowercase)
        .as_deref()
    {
        Some("mp4" | "m4a") => "mp4",
        Some("wav") => "wav",
        Some("webm") => "webm",
        Some("ogg") => "ogg",
        Some("opus") => "opus",
        Some("flac") => "flac",
        _ => "mp3",
    }
}

#[derive(Debug, Error)]
pub enum OpenAiTranscriptionError {
    #[error("invalid OpenAI transcription configuration: {0}")]
    InvalidConfiguration(String),
    #[error("invalid audio upload: {0}")]
    InvalidAudio(String),
    #[error("audio media processing failed: {0}")]
    Media(String),
    #[error("OpenAI transcription timed out")]
    Timeout,
    #[error("OpenAI transcription usage observation failed: {0}")]
    UsageObservation(String),
    #[error("OpenAI transcription failed")]
    Provider(#[source] async_openai::error::OpenAIError),
    #[error("OpenAI returned an invalid transcription response: {0}")]
    InvalidResponse(String),
    #[error("audio file I/O failed")]
    Io(#[from] std::io::Error),
}

#[cfg(test)]
mod tests {
    use std::future::Future;
    use std::pin::Pin;
    use std::sync::{Arc, Mutex};

    use super::{
        AudioDurationSource, OpenAiTranscriptionError, TranscriptionChunkUsage,
        TranscriptionUsageObserver, observe_transcription_usage,
    };

    #[derive(Debug, Default)]
    struct RecordingObserver(Mutex<Vec<TranscriptionChunkUsage>>);

    impl TranscriptionUsageObserver for RecordingObserver {
        fn observe(
            &self,
            usage: TranscriptionChunkUsage,
        ) -> Pin<Box<dyn Future<Output = Result<(), String>> + Send + '_>> {
            Box::pin(async move {
                self.0.lock().unwrap().push(usage);
                Ok(())
            })
        }
    }

    #[tokio::test]
    async fn successful_response_usage_survives_later_decode_failure() {
        let observer = Arc::new(RecordingObserver::default());
        let sink: Arc<dyn TranscriptionUsageObserver> = observer.clone();
        observe_transcription_usage(
            &sink,
            TranscriptionChunkUsage {
                chunk_index: 2,
                model: "gpt-transcribe",
                prompt_chars: 17,
                audio_size_bytes: 4_096,
                audio_duration_ms: Some(12_000),
                audio_duration_estimate_ms: None,
                audio_duration_source: AudioDurationSource::Ffprobe,
                standard_pricing: true,
            },
        )
        .await
        .unwrap();
        let later_decode: Result<serde_json::Value, OpenAiTranscriptionError> =
            serde_json::from_slice(b"not-json")
                .map_err(|error| OpenAiTranscriptionError::InvalidResponse(error.to_string()));

        assert!(later_decode.is_err());
        let observations = observer.0.lock().unwrap();
        assert_eq!(observations.len(), 1);
        let chunk_usage = &observations[0];
        assert_eq!(chunk_usage.chunk_index, 2);
        assert_eq!(chunk_usage.audio_duration_ms, Some(12_000));
    }
}
