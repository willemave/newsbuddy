use std::env;
use std::fmt::{self, Debug, Formatter};
use std::ops::Deref;
use std::path::{Component, Path, PathBuf};
use std::time::Duration;

use secrecy::SecretString;
use thiserror::Error;
use url::Url;

use crate::queue_process_config::{
    QueueWorkerProcessConfig, QueueWorkerProcessConfigError, QueueWorkerProcessEnv,
};

const MAX_MEDIA_BYTES: u64 = 500_000_000;

#[derive(Clone)]
pub struct MediaWorkerProcessConfig {
    pub process: QueueWorkerProcessConfig,
    pub openai_api_key: SecretString,
    pub openai_api_base: Option<String>,
    pub transcription_timeout: Duration,
    pub request_timeout: Duration,
    pub yt_dlp_timeout: Duration,
    pub ffmpeg_timeout: Duration,
    pub max_media_bytes: u64,
    pub max_redirects: usize,
    pub yt_dlp_binary: PathBuf,
    pub ffmpeg_binary: PathBuf,
    pub youtube_cookie_file: Option<PathBuf>,
    pub youtube_player_client: Option<String>,
    pub youtube_po_token_provider: Option<String>,
    pub youtube_po_token_base_url: Option<String>,
    pub itunes_country: Option<String>,
    pub podcast_scratch_root: PathBuf,
    pub tweet_media_root: PathBuf,
    pub content_body_local_root: PathBuf,
    pub content_body_storage_prefix: PathBuf,
    pub tweet_video_enabled: bool,
}

impl Debug for MediaWorkerProcessConfig {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("MediaWorkerProcessConfig")
            .field("process", &self.process)
            .field("openai_api_key", &"[REDACTED]")
            .field("openai_api_base", &self.openai_api_base)
            .field("transcription_timeout", &self.transcription_timeout)
            .field("request_timeout", &self.request_timeout)
            .field("yt_dlp_timeout", &self.yt_dlp_timeout)
            .field("ffmpeg_timeout", &self.ffmpeg_timeout)
            .field("max_media_bytes", &self.max_media_bytes)
            .field("max_redirects", &self.max_redirects)
            .field("yt_dlp_binary", &self.yt_dlp_binary)
            .field("ffmpeg_binary", &self.ffmpeg_binary)
            .field("youtube_cookie_file", &self.youtube_cookie_file)
            .field("youtube_player_client", &self.youtube_player_client)
            .field("youtube_po_token_provider", &self.youtube_po_token_provider)
            .field("youtube_po_token_base_url", &self.youtube_po_token_base_url)
            .field("itunes_country", &self.itunes_country)
            .field("podcast_scratch_root", &self.podcast_scratch_root)
            .field("tweet_media_root", &self.tweet_media_root)
            .field("content_body_local_root", &self.content_body_local_root)
            .field(
                "content_body_storage_prefix",
                &self.content_body_storage_prefix,
            )
            .field("tweet_video_enabled", &self.tweet_video_enabled)
            .finish()
    }
}

impl MediaWorkerProcessConfig {
    /// Loads the isolated media worker's bounded provider, filesystem, queue, and database
    /// configuration. `YouTube` defaults mirror the checked-in legacy client configuration while
    /// each value remains independently overridable for production.
    ///
    /// # Errors
    ///
    /// Returns a field-specific error for missing secrets or unsafe and out-of-range values.
    #[allow(clippy::too_many_lines)]
    pub fn from_env() -> Result<Self, MediaWorkerConfigError> {
        let process = QueueWorkerProcessConfig::from_env_with(
            "newsly-media-worker",
            "rust-media",
            QueueWorkerProcessEnv {
                database_max_connections: "NEWSLY_MEDIA_DATABASE_MAX_CONNECTIONS",
                database_min_connections: "NEWSLY_MEDIA_DATABASE_MIN_CONNECTIONS",
                database_acquire_timeout_ms: "NEWSLY_MEDIA_DATABASE_ACQUIRE_TIMEOUT_MS",
                database_bounds: "NEWSLY_MEDIA_DATABASE connection bounds",
                default_max_connections: 8,
                worker_id: "NEWSLY_MEDIA_WORKER_ID",
                lease_seconds: "NEWSLY_MEDIA_WORKER_LEASE_SECONDS",
                default_log_filter: "newsly_worker=info,newsly_queue=info,newsly_providers=info",
                trim_database_url: true,
                empty_database_url_is_missing: false,
            },
        )?;

        let transcription_timeout = bounded_duration(
            "OPENAI_TRANSCRIPTION_TIMEOUT_SECONDS",
            600,
            Duration::from_secs(1),
            Duration::from_secs(1_800),
        )?;
        let request_timeout = bounded_duration(
            "NEWSLY_MEDIA_REQUEST_TIMEOUT_SECONDS",
            600,
            Duration::from_secs(1),
            Duration::from_secs(1_800),
        )?;
        let yt_dlp_timeout = bounded_duration(
            "NEWSLY_MEDIA_YT_DLP_TIMEOUT_SECONDS",
            600,
            Duration::from_secs(1),
            Duration::from_secs(1_800),
        )?;
        let ffmpeg_timeout = bounded_duration(
            "NEWSLY_MEDIA_FFMPEG_TIMEOUT_SECONDS",
            600,
            Duration::from_secs(1),
            Duration::from_secs(1_800),
        )?;
        let max_media_bytes = parse_u64("NEWSLY_MEDIA_MAX_BYTES", MAX_MEDIA_BYTES)?;
        if !(1..=MAX_MEDIA_BYTES).contains(&max_media_bytes) {
            return Err(MediaWorkerConfigError::InvalidRange(
                "NEWSLY_MEDIA_MAX_BYTES",
            ));
        }
        let max_redirects = parse_usize("NEWSLY_MEDIA_MAX_REDIRECTS", 10)?;
        if max_redirects > 10 {
            return Err(MediaWorkerConfigError::InvalidRange(
                "NEWSLY_MEDIA_MAX_REDIRECTS",
            ));
        }

        let youtube_player_client = optional_setting("YOUTUBE_PLAYER_CLIENT", Some("mweb"))?;
        if youtube_player_client
            .as_deref()
            .is_some_and(|value| value.len() > 32)
        {
            return Err(MediaWorkerConfigError::InvalidRange(
                "YOUTUBE_PLAYER_CLIENT",
            ));
        }
        let youtube_po_token_provider =
            optional_setting("YOUTUBE_PO_TOKEN_PROVIDER", Some("bgutilhttp"))?;
        if youtube_po_token_provider
            .as_deref()
            .is_some_and(|value| !matches!(value, "bgutilhttp" | "webpoclient"))
        {
            return Err(MediaWorkerConfigError::InvalidValue {
                name: "YOUTUBE_PO_TOKEN_PROVIDER",
                expected: "bgutilhttp, webpoclient, or none",
            });
        }
        let youtube_po_token_base_url = if youtube_po_token_provider.is_some() {
            let raw = env::var("YOUTUBE_PO_TOKEN_BASE_URL")
                .unwrap_or_else(|_| "http://127.0.0.1:4416".to_owned());
            let parsed = parse_http_url("YOUTUBE_PO_TOKEN_BASE_URL", &raw)?;
            Some(parsed.to_string().trim_end_matches('/').to_owned())
        } else {
            None
        };
        let itunes_country = optional_setting("DISCOVERY_ITUNES_COUNTRY", Some("us"))?
            .map(|value| value.to_ascii_lowercase());
        if itunes_country.as_deref().is_some_and(|value| {
            value.len() != 2
                || !value
                    .chars()
                    .all(|character| character.is_ascii_alphabetic())
        }) {
            return Err(MediaWorkerConfigError::InvalidValue {
                name: "DISCOVERY_ITUNES_COUNTRY",
                expected: "a two-letter country code or none",
            });
        }

        let storage_provider =
            env::var("CONTENT_BODY_STORAGE_PROVIDER").unwrap_or_else(|_| "local".to_owned());
        if storage_provider != "local" {
            return Err(MediaWorkerConfigError::UnsupportedStorageProvider(
                storage_provider,
            ));
        }
        let media_root = env_path("MEDIA_BASE_DIR", "./data/media")?;
        let content_body_storage_prefix = PathBuf::from(
            env::var("CONTENT_BODY_STORAGE_PREFIX").unwrap_or_else(|_| "content".to_owned()),
        );
        validate_relative_prefix(&content_body_storage_prefix)?;
        let openai_api_base = optional_setting("OPENAI_BASE_URL", None)?
            .map(|value| {
                parse_http_url("OPENAI_BASE_URL", &value)
                    .map(|url| url.to_string().trim_end_matches('/').to_owned())
            })
            .transpose()?;

        Ok(Self {
            process,
            openai_api_key: required_secret("OPENAI_API_KEY")?,
            openai_api_base,
            transcription_timeout,
            request_timeout,
            yt_dlp_timeout,
            ffmpeg_timeout,
            max_media_bytes,
            max_redirects,
            yt_dlp_binary: executable_path("YT_DLP_BINARY", "yt-dlp")?,
            ffmpeg_binary: executable_path("FFMPEG_BINARY", "ffmpeg")?,
            youtube_cookie_file: optional_path(
                "YOUTUBE_COOKIES_PATH",
                Some("secrets/youtube_cookies.txt"),
            )?,
            youtube_player_client,
            youtube_po_token_provider,
            youtube_po_token_base_url,
            itunes_country,
            podcast_scratch_root: env_path("PODCAST_SCRATCH_DIR", "./data/scratch")?,
            tweet_media_root: media_root.join("tweet_videos"),
            content_body_local_root: env_path("CONTENT_BODY_LOCAL_ROOT", "./data/content_bodies")?,
            content_body_storage_prefix,
            tweet_video_enabled: parse_bool("TWEET_VIDEO_ENABLED", true)?,
        })
    }
}

impl Deref for MediaWorkerProcessConfig {
    type Target = QueueWorkerProcessConfig;

    fn deref(&self) -> &Self::Target {
        &self.process
    }
}

fn required_secret(name: &'static str) -> Result<SecretString, MediaWorkerConfigError> {
    let value = env::var(name).map_err(|_| MediaWorkerConfigError::Missing(name))?;
    if value.trim().is_empty() {
        return Err(MediaWorkerConfigError::Empty(name));
    }
    Ok(SecretString::from(value))
}

fn bounded_duration(
    name: &'static str,
    default_seconds: u64,
    minimum: Duration,
    maximum: Duration,
) -> Result<Duration, MediaWorkerConfigError> {
    let duration = Duration::from_secs(parse_u64(name, default_seconds)?);
    if !(minimum..=maximum).contains(&duration) {
        return Err(MediaWorkerConfigError::InvalidRange(name));
    }
    Ok(duration)
}

fn parse_u64(name: &'static str, default: u64) -> Result<u64, MediaWorkerConfigError> {
    parse_number(name, default)
}

fn parse_usize(name: &'static str, default: usize) -> Result<usize, MediaWorkerConfigError> {
    parse_number(name, default)
}

fn parse_number<T>(name: &'static str, default: T) -> Result<T, MediaWorkerConfigError>
where
    T: std::str::FromStr,
{
    match env::var(name) {
        Ok(value) => value
            .parse()
            .map_err(|_| MediaWorkerConfigError::InvalidNumber(name)),
        Err(_) => Ok(default),
    }
}

fn parse_bool(name: &'static str, default: bool) -> Result<bool, MediaWorkerConfigError> {
    match env::var(name) {
        Ok(value) => match value.trim().to_ascii_lowercase().as_str() {
            "1" | "true" | "yes" | "on" => Ok(true),
            "0" | "false" | "no" | "off" => Ok(false),
            _ => Err(MediaWorkerConfigError::InvalidValue {
                name,
                expected: "a boolean",
            }),
        },
        Err(_) => Ok(default),
    }
}

fn optional_setting(
    name: &'static str,
    default: Option<&str>,
) -> Result<Option<String>, MediaWorkerConfigError> {
    let value = env::var(name).ok().or_else(|| default.map(str::to_owned));
    let Some(value) = value else {
        return Ok(None);
    };
    let value = value.trim();
    if value.is_empty() {
        return Err(MediaWorkerConfigError::Empty(name));
    }
    if value.eq_ignore_ascii_case("none") {
        return Ok(None);
    }
    Ok(Some(value.to_owned()))
}

fn optional_path(
    name: &'static str,
    default: Option<&str>,
) -> Result<Option<PathBuf>, MediaWorkerConfigError> {
    optional_setting(name, default).map(|value| value.map(PathBuf::from))
}

fn executable_path(
    name: &'static str,
    default: &'static str,
) -> Result<PathBuf, MediaWorkerConfigError> {
    optional_path(name, Some(default))?.ok_or(MediaWorkerConfigError::Empty(name))
}

fn env_path(name: &'static str, default: &'static str) -> Result<PathBuf, MediaWorkerConfigError> {
    executable_path(name, default)
}

fn parse_http_url(name: &'static str, value: &str) -> Result<Url, MediaWorkerConfigError> {
    let parsed = Url::parse(value).map_err(|_| MediaWorkerConfigError::InvalidValue {
        name,
        expected: "an absolute HTTP(S) URL",
    })?;
    if !matches!(parsed.scheme(), "http" | "https") || parsed.host_str().is_none() {
        return Err(MediaWorkerConfigError::InvalidValue {
            name,
            expected: "an absolute HTTP(S) URL",
        });
    }
    Ok(parsed)
}

fn validate_relative_prefix(prefix: &Path) -> Result<(), MediaWorkerConfigError> {
    if prefix.as_os_str().is_empty()
        || prefix.is_absolute()
        || prefix
            .components()
            .any(|component| !matches!(component, Component::Normal(_)))
    {
        return Err(MediaWorkerConfigError::InvalidPath(
            "CONTENT_BODY_STORAGE_PREFIX",
        ));
    }
    Ok(())
}

#[derive(Debug, Error)]
pub enum MediaWorkerConfigError {
    #[error("required environment variable {0} is missing")]
    Missing(&'static str),
    #[error("environment variable {0} cannot be empty")]
    Empty(&'static str),
    #[error("{name} has an invalid value; expected {expected}")]
    InvalidValue {
        name: &'static str,
        expected: &'static str,
    },
    #[error("{0} is not a valid number")]
    InvalidNumber(&'static str),
    #[error("{0} is outside its supported range")]
    InvalidRange(&'static str),
    #[error("{0} must be a safe relative path")]
    InvalidPath(&'static str),
    #[error("Rust media workers require local content-body storage, got {0:?}")]
    UnsupportedStorageProvider(String),
}

impl From<QueueWorkerProcessConfigError> for MediaWorkerConfigError {
    fn from(error: QueueWorkerProcessConfigError) -> Self {
        match error {
            QueueWorkerProcessConfigError::Missing(name)
            | QueueWorkerProcessConfigError::InvalidUnicode(name) => Self::Missing(name),
            QueueWorkerProcessConfigError::Empty(name) => Self::Empty(name),
            QueueWorkerProcessConfigError::InvalidNumber(name, _) => Self::InvalidNumber(name),
            QueueWorkerProcessConfigError::Invalid(name) => Self::InvalidValue {
                name,
                expected: "a supported value",
            },
            QueueWorkerProcessConfigError::Range(name) => Self::InvalidRange(name),
        }
    }
}
