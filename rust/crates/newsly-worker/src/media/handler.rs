use std::future::Future;
use std::path::{Path, PathBuf};
use std::sync::Arc;

use chrono::Utc;
use newsly_db::{
    MediaContentSnapshot, MediaMutation, MediaTranscriptionUsage, prepare_media_content,
};
use newsly_providers::{
    MediaGateway, OpenAiTranscriptionError, OpenAiTranscriptionGateway, TranscriptionResult,
    YtDlpTarget, is_apple_podcasts_url, is_terminal_ytdlp_error, is_youtube_url,
};
use newsly_queue::{OwnedWorkPlan, QueueKernel, TaskResult, TaskType};
use serde_json::{Map, Value};
use sqlx::PgPool;
use tracing::warn;
use url::Url;

use crate::{HandlerExecution, HandlerFuture, LeaseHealth, TaskHandler};

use super::finalizer::MediaFinalizer;
use super::model::MediaFinalizationPlan;
use super::storage::{MediaFileStore, MediaFileStoreError};

#[derive(Debug, Clone)]
pub struct MediaWorkerServices {
    pool: PgPool,
    queue: QueueKernel,
    media_gateway: MediaGateway,
    transcription_gateway: OpenAiTranscriptionGateway,
    file_store: MediaFileStore,
    tweet_video_enabled: bool,
}

impl MediaWorkerServices {
    pub const fn new(
        pool: PgPool,
        queue: QueueKernel,
        media_gateway: MediaGateway,
        transcription_gateway: OpenAiTranscriptionGateway,
        file_store: MediaFileStore,
        tweet_video_enabled: bool,
    ) -> Self {
        Self {
            pool,
            queue,
            media_gateway,
            transcription_gateway,
            file_store,
            tweet_video_enabled,
        }
    }
}

#[derive(Debug, Clone)]
pub struct ProcessPodcastMediaHandler {
    services: Arc<MediaWorkerServices>,
}

impl ProcessPodcastMediaHandler {
    pub fn new(services: Arc<MediaWorkerServices>) -> Self {
        Self { services }
    }
}

impl TaskHandler for ProcessPodcastMediaHandler {
    fn task_type(&self) -> TaskType {
        TaskType::ProcessPodcastMedia
    }

    fn execute(&self, plan: Arc<OwnedWorkPlan>, lease: LeaseHealth) -> HandlerFuture<'_> {
        let services = Arc::clone(&self.services);
        Box::pin(async move { execute_podcast(&services, &plan, lease).await })
    }
}

#[derive(Debug, Clone)]
pub struct DownloadTweetVideoAudioHandler {
    services: Arc<MediaWorkerServices>,
}

impl DownloadTweetVideoAudioHandler {
    pub fn new(services: Arc<MediaWorkerServices>) -> Self {
        Self { services }
    }
}

impl TaskHandler for DownloadTweetVideoAudioHandler {
    fn task_type(&self) -> TaskType {
        TaskType::DownloadTweetVideoAudio
    }

    fn execute(&self, plan: Arc<OwnedWorkPlan>, lease: LeaseHealth) -> HandlerFuture<'_> {
        let services = Arc::clone(&self.services);
        Box::pin(async move { execute_tweet_download(&services, &plan, lease).await })
    }
}

#[derive(Debug, Clone)]
pub struct TranscribeTweetVideoHandler {
    services: Arc<MediaWorkerServices>,
}

impl TranscribeTweetVideoHandler {
    pub fn new(services: Arc<MediaWorkerServices>) -> Self {
        Self { services }
    }
}

impl TaskHandler for TranscribeTweetVideoHandler {
    fn task_type(&self) -> TaskType {
        TaskType::TranscribeTweetVideo
    }

    fn execute(&self, plan: Arc<OwnedWorkPlan>, lease: LeaseHealth) -> HandlerFuture<'_> {
        let services = Arc::clone(&self.services);
        Box::pin(async move { execute_tweet_transcription(&services, &plan, lease).await })
    }
}

async fn execute_podcast(
    services: &MediaWorkerServices,
    plan: &OwnedWorkPlan,
    mut lease: LeaseHealth,
) -> HandlerExecution {
    let Some(content_id) = content_id(plan) else {
        return plain_failure("No content_id provided", false);
    };
    let snapshot = match load_snapshot(services, content_id).await {
        Ok(Some(snapshot)) => snapshot,
        Ok(None) => return plain_failure("Content not found", false),
        Err(error) => return plain_failure(error, true),
    };
    if is_terminal_content(&snapshot.status) {
        return HandlerExecution::from_result(TaskResult::ok());
    }
    if snapshot.content_type != "podcast" {
        return plain_failure("process_podcast_media requires podcast content", false);
    }

    let metadata = runtime_metadata(&snapshot.content_metadata);
    if metadata_truthy(&metadata, "youtube_video")
        && let Some(transcript) = metadata_string(&metadata, "transcript")
            .or_else(|| metadata_string(&metadata, "content_to_summarize"))
    {
        if lease.ownership_lost() {
            return lease_lost_failure();
        }
        let body = match services
            .file_store
            .stage_transcript(content_id, &transcript)
            .await
        {
            Ok(body) => body,
            Err(error) => {
                return podcast_failed(services, content_id, error.to_string(), true, true);
            }
        };
        return completed_media(
            services,
            content_id,
            MediaMutation::PodcastCompleted {
                body,
                transcript,
                transcription_at: Utc::now(),
                transcription_service: "youtube".to_owned(),
                detected_language: None,
                resolved_audio_url: None,
                resolved_feed_url: None,
                resolved_episode_title: None,
            },
            None,
        );
    }

    let mut resolved_audio_url = metadata_string(&metadata, "audio_url");
    let mut resolved_feed_url = None;
    let mut resolved_episode_title = None;
    if resolved_audio_url.is_none() && is_apple_podcast(&snapshot, &metadata) {
        match lease_aware(
            &mut lease,
            services
                .media_gateway
                .resolve_apple_podcast_episode(&snapshot.url),
        )
        .await
        {
            Ok(Ok(resolution)) => {
                resolved_feed_url = resolution.feed_url;
                resolved_episode_title = resolution.episode_title;
                resolved_audio_url = resolution.audio_url;
            }
            Ok(Err(error)) => warn!(
                content_id,
                error = %error,
                "Apple Podcasts resolution failed; continuing to direct-audio fallback"
            ),
            Err(LeaseLost) => return lease_lost_failure(),
        }
    }
    if resolved_audio_url.is_none() {
        resolved_audio_url = direct_audio_url(&snapshot);
    }
    let Some(audio_url) = resolved_audio_url.clone() else {
        return podcast_failed(
            services,
            content_id,
            "No audio URL found".to_owned(),
            true,
            false,
        );
    };

    let scratch = match services
        .file_store
        .podcast_attempt_dir(content_id, plan.task_id)
        .await
    {
        Ok(path) => path,
        Err(error) => return podcast_failed(services, content_id, error.to_string(), true, true),
    };
    let downloaded = if is_youtube_url(&audio_url) {
        lease_aware(
            &mut lease,
            services.media_gateway.download_with_ytdlp(
                &audio_url,
                &scratch,
                &format!("podcast-{content_id}"),
                YtDlpTarget::YouTube,
            ),
        )
        .await
    } else {
        lease_aware(
            &mut lease,
            services.media_gateway.download_public_media(
                &audio_url,
                &scratch,
                &format!("podcast-{content_id}"),
            ),
        )
        .await
    };
    let downloaded = match downloaded {
        Ok(Ok(downloaded)) => downloaded,
        Ok(Err(error)) => {
            services.file_store.cleanup_podcast_attempt(&scratch).await;
            let message = error.to_string();
            return podcast_failed(
                services,
                content_id,
                message.clone(),
                !is_terminal_ytdlp_error(&message),
                true,
            );
        }
        Err(LeaseLost) => {
            services.file_store.cleanup_podcast_attempt(&scratch).await;
            return lease_lost_failure();
        }
    };
    let normalized = match lease_aware(
        &mut lease,
        services.media_gateway.normalize_audio(&downloaded.path),
    )
    .await
    {
        Ok(Ok(normalized)) => normalized,
        Ok(Err(error)) => {
            services.file_store.cleanup_podcast_attempt(&scratch).await;
            return podcast_failed(services, content_id, error.to_string(), true, true);
        }
        Err(LeaseLost) => {
            services.file_store.cleanup_podcast_attempt(&scratch).await;
            return lease_lost_failure();
        }
    };
    let filename = normalized
        .path
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or("podcast-audio.mp3");
    let transcription = match lease_aware(
        &mut lease,
        services
            .transcription_gateway
            .transcribe_upload(&normalized.path, filename),
    )
    .await
    {
        Ok(Ok(transcription)) => transcription,
        Ok(Err(error)) => {
            services.file_store.cleanup_podcast_attempt(&scratch).await;
            return podcast_failed(services, content_id, error.to_string(), true, true);
        }
        Err(LeaseLost) => {
            services.file_store.cleanup_podcast_attempt(&scratch).await;
            return lease_lost_failure();
        }
    };
    if transcription.transcript.trim().is_empty() {
        services.file_store.cleanup_podcast_attempt(&scratch).await;
        return podcast_failed(
            services,
            content_id,
            "OpenAI returned an empty podcast transcript".to_owned(),
            true,
            true,
        );
    }
    let transcript_text = transcription.transcript.trim().to_owned();
    let body = match services
        .file_store
        .stage_transcript(content_id, &transcript_text)
        .await
    {
        Ok(body) => body,
        Err(error) => {
            services.file_store.cleanup_podcast_attempt(&scratch).await;
            return podcast_failed(services, content_id, error.to_string(), true, true);
        }
    };
    services.file_store.cleanup_podcast_attempt(&scratch).await;
    if lease.ownership_lost() {
        return lease_lost_failure();
    }
    let usage = transcription_usage(
        plan,
        &snapshot,
        "podcast",
        &transcription,
        normalized.size_bytes,
    );
    completed_media(
        services,
        content_id,
        MediaMutation::PodcastCompleted {
            body,
            transcript: transcript_text,
            transcription_at: Utc::now(),
            transcription_service: "openai".to_owned(),
            detected_language: transcription.language,
            resolved_audio_url: Some(audio_url),
            resolved_feed_url,
            resolved_episode_title,
        },
        Some(usage),
    )
}

async fn execute_tweet_download(
    services: &MediaWorkerServices,
    plan: &OwnedWorkPlan,
    mut lease: LeaseHealth,
) -> HandlerExecution {
    let Some(content_id) = content_id(plan) else {
        return plain_failure("No content_id provided", false);
    };
    let snapshot = match load_snapshot(services, content_id).await {
        Ok(Some(snapshot)) => snapshot,
        Ok(None) => return plain_failure("Content not found", false),
        Err(error) => return plain_failure(error, true),
    };
    if is_terminal_content(&snapshot.status) {
        return HandlerExecution::from_result(TaskResult::ok());
    }
    let metadata = runtime_metadata(&snapshot.content_metadata);
    let (has_video, duration_ms) = tweet_video_metadata(&metadata);
    if !services.tweet_video_enabled || !has_video {
        return completed_media(
            services,
            content_id,
            MediaMutation::TweetSkipped {
                disabled: !services.tweet_video_enabled,
            },
            None,
        );
    }
    let tweet_url = metadata_string(&metadata, "tweet_url")
        .or_else(|| metadata_string(&metadata, "discussion_url"))
        .unwrap_or_else(|| snapshot.url.clone());
    let attempt_dir = match services
        .file_store
        .tweet_attempt_dir(content_id, plan.task_id)
        .await
    {
        Ok(path) => path,
        Err(error) => {
            return tweet_fallback(services, content_id, "download_tweet_video_audio", error);
        }
    };
    let downloaded = match lease_aware(
        &mut lease,
        services.media_gateway.download_with_ytdlp(
            &tweet_url,
            &attempt_dir,
            &format!("tweet-{content_id}"),
            YtDlpTarget::Tweet,
        ),
    )
    .await
    {
        Ok(Ok(downloaded)) => downloaded,
        Ok(Err(error)) => {
            services
                .file_store
                .cleanup_tweet_attempt(&attempt_dir)
                .await;
            return tweet_fallback(services, content_id, "download_tweet_video_audio", error);
        }
        Err(LeaseLost) => {
            services
                .file_store
                .cleanup_tweet_attempt(&attempt_dir)
                .await;
            return lease_lost_failure();
        }
    };
    if lease.ownership_lost() {
        services
            .file_store
            .cleanup_tweet_attempt(&attempt_dir)
            .await;
        return lease_lost_failure();
    }
    let Some(audio_path) = downloaded.path.to_str().map(str::to_owned) else {
        services
            .file_store
            .cleanup_tweet_attempt(&attempt_dir)
            .await;
        return tweet_fallback(
            services,
            content_id,
            "download_tweet_video_audio",
            MediaFileStoreError::UnsafeMediaPath,
        );
    };
    completed_media(
        services,
        content_id,
        MediaMutation::TweetDownloaded {
            audio_path,
            duration_ms,
            downloaded_at: Utc::now(),
        },
        None,
    )
}

async fn execute_tweet_transcription(
    services: &MediaWorkerServices,
    plan: &OwnedWorkPlan,
    mut lease: LeaseHealth,
) -> HandlerExecution {
    let Some(content_id) = content_id(plan) else {
        return plain_failure("No content_id provided", false);
    };
    let snapshot = match load_snapshot(services, content_id).await {
        Ok(Some(snapshot)) => snapshot,
        Ok(None) => return plain_failure("Content not found", false),
        Err(error) => return plain_failure(error, true),
    };
    if is_terminal_content(&snapshot.status) {
        return HandlerExecution::from_result(TaskResult::ok());
    }
    let metadata = runtime_metadata(&snapshot.content_metadata);
    let Some(raw_path) = metadata_string(&metadata, "video_audio_path") else {
        return tweet_fallback(
            services,
            content_id,
            "transcribe_tweet_video",
            MediaFileStoreError::MissingTweetAudioPath,
        );
    };
    let audio = match services.file_store.validate_tweet_audio(&raw_path).await {
        Ok(audio) => audio,
        Err(error) => {
            return tweet_fallback(services, content_id, "transcribe_tweet_video", error);
        }
    };
    let transcription = match lease_aware(
        &mut lease,
        services
            .transcription_gateway
            .transcribe_upload(&audio.path, &audio.filename),
    )
    .await
    {
        Ok(Ok(transcription)) => transcription,
        Ok(Err(error)) => {
            return tweet_fallback_with_cleanup(
                services,
                content_id,
                "transcribe_tweet_video",
                error,
                audio.path.parent().map(Path::to_path_buf),
            );
        }
        Err(LeaseLost) => return lease_lost_failure(),
    };
    if transcription.transcript.trim().is_empty() {
        return tweet_fallback_with_cleanup(
            services,
            content_id,
            "transcribe_tweet_video",
            OpenAiTranscriptionError::InvalidResponse(
                "provider returned an empty transcript".to_owned(),
            ),
            audio.path.parent().map(Path::to_path_buf),
        );
    }
    let transcript_text = transcription.transcript.trim().to_owned();
    if lease.ownership_lost() {
        return lease_lost_failure();
    }
    let usage = transcription_usage(
        plan,
        &snapshot,
        "tweet_video",
        &transcription,
        audio.size_bytes,
    );
    completed_media_with_cleanup(
        services,
        content_id,
        MediaMutation::TweetTranscribed {
            transcript: transcript_text,
            transcription_at: Utc::now(),
            transcription_service: "openai".to_owned(),
        },
        Some(usage),
        audio.path.parent().map(Path::to_path_buf),
    )
}

async fn load_snapshot(
    services: &MediaWorkerServices,
    content_id: i64,
) -> Result<Option<MediaContentSnapshot>, String> {
    let mut transaction = services
        .pool
        .begin()
        .await
        .map_err(|error| error.to_string())?;
    let snapshot = prepare_media_content(&mut transaction, content_id)
        .await
        .map_err(|error| error.to_string())?;
    transaction
        .commit()
        .await
        .map_err(|error| error.to_string())?;
    Ok(snapshot)
}

fn completed_media(
    services: &MediaWorkerServices,
    content_id: i64,
    mutation: MediaMutation,
    usage: Option<MediaTranscriptionUsage>,
) -> HandlerExecution {
    completed_media_with_cleanup(services, content_id, mutation, usage, None)
}

fn completed_media_with_cleanup(
    services: &MediaWorkerServices,
    content_id: i64,
    mutation: MediaMutation,
    usage: Option<MediaTranscriptionUsage>,
    cleanup_tweet_attempt: Option<PathBuf>,
) -> HandlerExecution {
    HandlerExecution::with_finalizer(
        TaskResult::ok(),
        MediaFinalizer::new(
            services.queue.clone(),
            services.file_store.clone(),
            MediaFinalizationPlan {
                content_id,
                mutation,
                usage,
                cleanup_tweet_attempt,
            },
        ),
    )
}

fn podcast_failed(
    services: &MediaWorkerServices,
    content_id: i64,
    error_message: String,
    retryable: bool,
    increment_retry_count: bool,
) -> HandlerExecution {
    HandlerExecution::with_finalizer(
        TaskResult::fail(Some(error_message.clone()), retryable),
        MediaFinalizer::new(
            services.queue.clone(),
            services.file_store.clone(),
            MediaFinalizationPlan {
                content_id,
                mutation: MediaMutation::PodcastFailed {
                    error_message,
                    increment_retry_count,
                },
                usage: None,
                cleanup_tweet_attempt: None,
            },
        ),
    )
}

fn tweet_fallback(
    services: &MediaWorkerServices,
    content_id: i64,
    stage: &str,
    error: impl std::fmt::Display,
) -> HandlerExecution {
    tweet_fallback_with_cleanup(services, content_id, stage, error, None)
}

fn tweet_fallback_with_cleanup(
    services: &MediaWorkerServices,
    content_id: i64,
    stage: &str,
    error: impl std::fmt::Display,
    cleanup_tweet_attempt: Option<PathBuf>,
) -> HandlerExecution {
    completed_media_with_cleanup(
        services,
        content_id,
        MediaMutation::TweetFallback {
            stage: stage.to_owned(),
            error_message: error.to_string(),
            failed_at: Utc::now(),
        },
        None,
        cleanup_tweet_attempt,
    )
}

fn transcription_usage(
    plan: &OwnedWorkPlan,
    snapshot: &MediaContentSnapshot,
    media_kind: &str,
    result: &TranscriptionResult,
    audio_size_bytes: u64,
) -> MediaTranscriptionUsage {
    let metadata = runtime_metadata(&snapshot.content_metadata);
    MediaTranscriptionUsage {
        task_id: plan.task_id,
        content_id: snapshot.id,
        user_id: plan
            .owner_user_id
            .or_else(|| metadata_positive_integer(&metadata, "submitted_by_user_id")),
        request_id: format!("media-{}-attempt-{}", plan.task_id, plan.retry_count),
        model: result.model.clone(),
        media_kind: media_kind.to_owned(),
        language: result.language.clone(),
        chunk_count: bounded_i32(result.chunk_count),
        prompt_chars: bounded_i32(result.prompt_chars),
        audio_size_bytes: i64::try_from(audio_size_bytes).unwrap_or(i64::MAX),
    }
}

fn content_id(plan: &OwnedWorkPlan) -> Option<i64> {
    plan.content_id
        .or_else(|| plan.payload.get("content_id").and_then(Value::as_i64))
        .filter(|content_id| *content_id > 0)
}

fn runtime_metadata(value: &Value) -> Map<String, Value> {
    let stored = value.as_object().cloned().unwrap_or_default();
    let mut runtime = stored
        .get("domain")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    if let Some(processing) = stored.get("processing").and_then(Value::as_object) {
        runtime.extend(processing.clone());
    }
    for (key, value) in stored {
        if !matches!(key.as_str(), "domain" | "processing") {
            runtime.entry(key).or_insert(value);
        }
    }
    runtime
}

fn metadata_string(metadata: &Map<String, Value>, key: &str) -> Option<String> {
    metadata
        .get(key)
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
}

fn metadata_truthy(metadata: &Map<String, Value>, key: &str) -> bool {
    match metadata.get(key) {
        Some(Value::Bool(value)) => *value,
        Some(Value::String(value)) => !value.trim().is_empty(),
        Some(Value::Null) | None => false,
        Some(_) => true,
    }
}

fn metadata_bool(metadata: &Map<String, Value>, key: &str) -> bool {
    match metadata.get(key) {
        Some(Value::Bool(value)) => *value,
        Some(Value::String(value)) => value.trim().eq_ignore_ascii_case("true"),
        _ => false,
    }
}

fn metadata_nonnegative_integer(metadata: &Map<String, Value>, key: &str) -> Option<i64> {
    metadata
        .get(key)
        .and_then(|value| match value {
            Value::Number(value) => value.as_i64(),
            Value::String(value) => value.trim().parse::<i64>().ok(),
            _ => None,
        })
        .filter(|value| *value >= 0)
}

fn metadata_positive_integer(metadata: &Map<String, Value>, key: &str) -> Option<i64> {
    metadata_nonnegative_integer(metadata, key).filter(|value| *value > 0)
}

fn tweet_video_metadata(metadata: &Map<String, Value>) -> (bool, Option<i64>) {
    let snapshot = metadata
        .get("tweet_snapshot")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    let has_video = metadata_bool(metadata, "has_video") || metadata_bool(&snapshot, "has_video");
    let duration_ms = metadata_nonnegative_integer(metadata, "video_duration_ms")
        .or_else(|| metadata_nonnegative_integer(&snapshot, "video_duration_ms"));
    (has_video, duration_ms)
}

fn is_apple_podcast(snapshot: &MediaContentSnapshot, metadata: &Map<String, Value>) -> bool {
    metadata_string(metadata, "platform")
        .or_else(|| snapshot.platform.clone())
        .is_some_and(|platform| platform.eq_ignore_ascii_case("apple_podcasts"))
        || is_apple_podcasts_url(&snapshot.url)
}

fn direct_audio_url(snapshot: &MediaContentSnapshot) -> Option<String> {
    if is_direct_audio_url(&snapshot.url) {
        return Some(snapshot.url.clone());
    }
    snapshot
        .source_url
        .as_ref()
        .filter(|url| is_direct_audio_url(url))
        .cloned()
}

fn is_direct_audio_url(raw_url: &str) -> bool {
    let Ok(url) = Url::parse(raw_url) else {
        return false;
    };
    if !matches!(url.scheme(), "http" | "https") || url.host_str().is_none() {
        return false;
    }
    Path::new(url.path())
        .extension()
        .and_then(|value| value.to_str())
        .is_some_and(|extension| {
            matches!(
                extension.to_ascii_lowercase().as_str(),
                "aac" | "flac" | "m4a" | "mp3" | "mpga" | "oga" | "ogg" | "opus" | "wav" | "webm"
            )
        })
}

fn is_terminal_content(status: &str) -> bool {
    matches!(status, "completed" | "skipped")
}

fn bounded_i32(value: usize) -> i32 {
    i32::try_from(value).unwrap_or(i32::MAX)
}

fn plain_failure(message: impl Into<String>, retryable: bool) -> HandlerExecution {
    HandlerExecution::from_result(TaskResult::fail(Some(message.into()), retryable))
}

fn lease_lost_failure() -> HandlerExecution {
    plain_failure("Media task lease was lost", true)
}

async fn lease_aware<F, T, E>(lease: &mut LeaseHealth, future: F) -> Result<Result<T, E>, LeaseLost>
where
    F: Future<Output = Result<T, E>>,
{
    tokio::pin!(future);
    tokio::select! {
        biased;
        () = lease.wait_for_ownership_loss() => Err(LeaseLost),
        result = &mut future => Ok(result),
    }
}

#[derive(Debug)]
struct LeaseLost;

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::{direct_audio_url, runtime_metadata, tweet_video_metadata};
    use newsly_db::MediaContentSnapshot;

    #[test]
    fn resolves_snapshot_video_metadata() {
        let metadata = runtime_metadata(&json!({
            "has_video": false,
            "tweet_snapshot": {"has_video": true, "video_duration_ms": "42"},
        }));
        assert_eq!(tweet_video_metadata(&metadata), (true, Some(42)));
    }

    #[test]
    fn direct_audio_falls_back_to_source_url() {
        let snapshot = MediaContentSnapshot {
            id: 1,
            content_type: "podcast".to_owned(),
            url: "https://example.test/show".to_owned(),
            source_url: Some("https://cdn.example.test/episode.mp3".to_owned()),
            title: None,
            source: None,
            platform: None,
            status: "processing".to_owned(),
            content_metadata: json!({}),
            error_message: None,
            retry_count: 0,
        };
        assert_eq!(
            direct_audio_url(&snapshot).as_deref(),
            Some("https://cdn.example.test/episode.mp3")
        );
    }
}
