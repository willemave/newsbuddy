use std::collections::BTreeSet;
use std::path::{Path, PathBuf};
use std::time::Duration;

use futures_util::StreamExt;
use newsly_extraction::PublicUrl;
use reqwest::Url;
use reqwest::header::{ACCEPT, LOCATION, USER_AGENT};
use serde::Deserialize;
use thiserror::Error;
use tokio::fs::{self, File};
use tokio::io::AsyncWriteExt;
use tokio::process::Command;
use uuid::Uuid;

const DEFAULT_MAX_MEDIA_BYTES: u64 = 500_000_000;
const APPLE_LOOKUP_MAX_BYTES: u64 = 2_000_000;
const APPLE_FEED_MAX_BYTES: u64 = 20_000_000;
const MAX_PROVIDER_ERROR_CHARS: usize = 500;
const STOPWORDS: [&str; 10] = [
    "the", "and", "of", "a", "an", "to", "in", "on", "for", "with",
];

#[derive(Debug, Clone)]
pub struct MediaGatewayConfig {
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
}

impl Default for MediaGatewayConfig {
    fn default() -> Self {
        Self {
            request_timeout: Duration::from_secs(600),
            yt_dlp_timeout: Duration::from_secs(600),
            ffmpeg_timeout: Duration::from_secs(600),
            max_media_bytes: DEFAULT_MAX_MEDIA_BYTES,
            max_redirects: 10,
            yt_dlp_binary: PathBuf::from("yt-dlp"),
            ffmpeg_binary: PathBuf::from("ffmpeg"),
            youtube_cookie_file: None,
            youtube_player_client: Some("mweb".to_owned()),
            youtube_po_token_provider: None,
            youtube_po_token_base_url: None,
            itunes_country: Some("us".to_owned()),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum YtDlpTarget {
    YouTube,
    Tweet,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DownloadedMedia {
    pub path: PathBuf,
    pub size_bytes: u64,
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct ApplePodcastResolution {
    pub feed_url: Option<String>,
    pub episode_title: Option<String>,
    pub audio_url: Option<String>,
}

#[derive(Debug, Clone)]
pub struct MediaGateway {
    client: reqwest::Client,
    config: MediaGatewayConfig,
}

impl MediaGateway {
    /// Creates the bounded media gateway. Redirects are handled manually so every target is
    /// revalidated as a public-network URL before the next request is sent.
    ///
    /// # Errors
    ///
    /// Returns an error when limits are invalid or the bounded HTTP client cannot be constructed.
    pub fn new(config: MediaGatewayConfig) -> Result<Self, MediaGatewayError> {
        if config.request_timeout.is_zero()
            || config.yt_dlp_timeout.is_zero()
            || config.ffmpeg_timeout.is_zero()
        {
            return Err(MediaGatewayError::InvalidConfiguration(
                "media timeouts must be greater than zero".to_owned(),
            ));
        }
        if config.max_media_bytes == 0 || config.max_media_bytes > DEFAULT_MAX_MEDIA_BYTES {
            return Err(MediaGatewayError::InvalidConfiguration(format!(
                "max_media_bytes must be between 1 and {DEFAULT_MAX_MEDIA_BYTES}"
            )));
        }
        if config.max_redirects > 10 {
            return Err(MediaGatewayError::InvalidConfiguration(
                "max_redirects must be at most 10".to_owned(),
            ));
        }
        if config
            .youtube_player_client
            .as_deref()
            .is_some_and(|value| value.trim().is_empty() || value.len() > 32)
        {
            return Err(MediaGatewayError::InvalidConfiguration(
                "youtube_player_client must contain between 1 and 32 characters".to_owned(),
            ));
        }
        let client = reqwest::Client::builder()
            .connect_timeout(Duration::from_secs(15))
            .timeout(config.request_timeout)
            .redirect(reqwest::redirect::Policy::none())
            .no_proxy()
            .build()?;
        Ok(Self { client, config })
    }

    pub const fn max_media_bytes(&self) -> u64 {
        self.config.max_media_bytes
    }

    /// Downloads a public media URL to a unique partial file and atomically publishes it after
    /// the response completes within the configured byte bound.
    ///
    /// # Errors
    ///
    /// Returns an error when URL validation, bounded transport, or destination file I/O fails.
    pub async fn download_public_media(
        &self,
        raw_url: &str,
        destination_dir: &Path,
        output_stem: &str,
    ) -> Result<DownloadedMedia, MediaGatewayError> {
        validate_output_stem(output_stem)?;
        fs::create_dir_all(destination_dir).await?;
        let normalized = decode_anchor_redirect(raw_url)?;
        let extension = media_extension(&normalized);
        let destination = destination_dir.join(format!("{output_stem}.{extension}"));
        if let Some(existing) = reusable_file(&destination, self.config.max_media_bytes).await? {
            return Ok(existing);
        }
        let partial = destination_dir.join(format!(
            ".{output_stem}.{}.partial",
            Uuid::new_v4().simple()
        ));
        let result = self
            .download_public_media_inner(&normalized, &partial, &destination)
            .await;
        if result.is_err() {
            let _ = fs::remove_file(&partial).await;
        }
        result
    }

    async fn download_public_media_inner(
        &self,
        raw_url: &str,
        partial: &Path,
        destination: &Path,
    ) -> Result<DownloadedMedia, MediaGatewayError> {
        let response = self
            .send_public_get(raw_url, Some("audio/*,application/octet-stream"))
            .await?;
        if response
            .content_length()
            .is_some_and(|length| length > self.config.max_media_bytes)
        {
            return Err(MediaGatewayError::MediaTooLarge {
                limit: self.config.max_media_bytes,
            });
        }
        let mut output = File::create(partial).await?;
        let mut received = 0_u64;
        let mut stream = response.bytes_stream();
        while let Some(chunk) = stream.next().await {
            let chunk = chunk?;
            received = received.checked_add(chunk.len() as u64).ok_or(
                MediaGatewayError::MediaTooLarge {
                    limit: self.config.max_media_bytes,
                },
            )?;
            if received > self.config.max_media_bytes {
                return Err(MediaGatewayError::MediaTooLarge {
                    limit: self.config.max_media_bytes,
                });
            }
            output.write_all(&chunk).await?;
        }
        output.flush().await?;
        drop(output);
        if received == 0 {
            return Err(MediaGatewayError::EmptyMedia);
        }
        fs::rename(partial, destination).await?;
        Ok(DownloadedMedia {
            path: destination.to_path_buf(),
            size_bytes: received,
        })
    }

    /// Runs the installed yt-dlp executable for the two explicitly supported media families.
    /// Arbitrary extractor URLs are rejected before the subprocess is started.
    ///
    /// # Errors
    ///
    /// Returns an error when URL validation, subprocess execution, output discovery, or file I/O
    /// fails.
    pub async fn download_with_ytdlp(
        &self,
        raw_url: &str,
        destination_dir: &Path,
        output_stem: &str,
        target: YtDlpTarget,
    ) -> Result<DownloadedMedia, MediaGatewayError> {
        validate_output_stem(output_stem)?;
        let public_url = PublicUrl::parse(raw_url)?;
        public_url.validate_dns().await?;
        validate_ytdlp_host(public_url.as_url(), target)?;
        fs::create_dir_all(destination_dir).await?;
        if let Some(existing) =
            find_ytdlp_output(destination_dir, output_stem, self.config.max_media_bytes).await?
        {
            return Ok(existing);
        }

        remove_ytdlp_outputs(destination_dir, output_stem).await?;
        let output_template = destination_dir.join(format!("{output_stem}.%(ext)s"));
        let mut command = Command::new(&self.config.yt_dlp_binary);
        command
            .kill_on_drop(true)
            .arg("--quiet")
            .arg("--no-warnings")
            .arg("--no-playlist")
            .arg("--format")
            .arg("bestaudio/best")
            .arg("--max-filesize")
            .arg(self.config.max_media_bytes.to_string())
            .arg("--socket-timeout")
            .arg("30")
            .arg("--output")
            .arg(&output_template)
            .arg("--print")
            .arg("after_move:filepath");
        if target == YtDlpTarget::YouTube {
            if let Some(cookie_file) = self
                .config
                .youtube_cookie_file
                .as_deref()
                .filter(|path| path.is_file())
            {
                command.arg("--cookies").arg(cookie_file);
            }
            let mut extractor_args = Vec::new();
            if let Some(player_client) = self
                .config
                .youtube_player_client
                .as_deref()
                .map(str::trim)
                .filter(|value| !value.is_empty())
            {
                extractor_args.push(format!(
                    "youtube:player_client={player_client};player_skip=configs"
                ));
            }
            if let Some(provider) = self
                .config
                .youtube_po_token_provider
                .as_deref()
                .map(str::trim)
                .filter(|value| !value.is_empty())
            {
                let value = self
                    .config
                    .youtube_po_token_base_url
                    .as_deref()
                    .map(str::trim)
                    .filter(|value| !value.is_empty())
                    .map_or_else(
                        || format!("youtubepot-{provider}:"),
                        |base| format!("youtubepot-{provider}:base_url={base}"),
                    );
                extractor_args.push(value);
            }
            for value in extractor_args {
                command.arg("--extractor-args").arg(value);
            }
        }
        command.arg("--").arg(public_url.as_str());

        let output = tokio::select! {
            result = command.output() => result?,
            () = tokio::time::sleep(self.config.yt_dlp_timeout) => {
                remove_ytdlp_outputs(destination_dir, output_stem).await?;
                return Err(MediaGatewayError::YtDlpTimeout);
            }
            error = wait_for_ytdlp_limit(
                destination_dir,
                output_stem,
                self.config.max_media_bytes,
            ) => {
                remove_ytdlp_outputs(destination_dir, output_stem).await?;
                return Err(error);
            }
        };
        if !output.status.success() {
            remove_ytdlp_outputs(destination_dir, output_stem).await?;
            return Err(MediaGatewayError::YtDlpFailed(truncate_chars(
                String::from_utf8_lossy(&output.stderr).trim(),
                MAX_PROVIDER_ERROR_CHARS,
            )));
        }
        find_ytdlp_output(destination_dir, output_stem, self.config.max_media_bytes)
            .await?
            .ok_or(MediaGatewayError::YtDlpOutputMissing)
    }

    /// Attempts mono/16kHz normalization and falls back to the downloaded media when ffmpeg is
    /// unavailable or rejects the source, matching the legacy worker's tolerant behavior.
    ///
    /// # Errors
    ///
    /// Returns an error when the source is unsafe or missing, or when file I/O fails outside the
    /// deliberately tolerated ffmpeg fallback.
    pub async fn normalize_audio(
        &self,
        input: &Path,
    ) -> Result<DownloadedMedia, MediaGatewayError> {
        let source = reusable_file(input, self.config.max_media_bytes)
            .await?
            .ok_or(MediaGatewayError::MediaFileMissing)?;
        let output_path = input.with_extension("normalized.wav");
        let mut command = Command::new(&self.config.ffmpeg_binary);
        command
            .kill_on_drop(true)
            .arg("-nostdin")
            .arg("-y")
            .arg("-loglevel")
            .arg("error")
            .arg("-i")
            .arg(input)
            .arg("-ac")
            .arg("1")
            .arg("-ar")
            .arg("16000")
            .arg(&output_path);
        let completed = tokio::time::timeout(self.config.ffmpeg_timeout, command.output()).await;
        let Ok(Ok(result)) = completed else {
            let _ = fs::remove_file(&output_path).await;
            return Ok(source);
        };
        if !result.status.success() {
            let _ = fs::remove_file(&output_path).await;
            return Ok(source);
        }
        match reusable_file(&output_path, self.config.max_media_bytes).await {
            Ok(Some(normalized)) => Ok(normalized),
            Ok(None) | Err(MediaGatewayError::MediaTooLarge { .. }) => {
                let _ = fs::remove_file(&output_path).await;
                Ok(source)
            }
            Err(error) => Err(error),
        }
    }

    /// Resolves an Apple Podcasts episode URL through the public iTunes lookup and publisher RSS
    /// feed. Every lookup/feed redirect is independently public-network validated.
    ///
    /// # Errors
    ///
    /// Returns an error when the input is invalid or a bounded lookup, feed request, or response
    /// parse fails.
    pub async fn resolve_apple_podcast_episode(
        &self,
        raw_url: &str,
    ) -> Result<ApplePodcastResolution, MediaGatewayError> {
        let apple_url = PublicUrl::parse(raw_url)?;
        let host = apple_url
            .as_url()
            .host_str()
            .ok_or(MediaGatewayError::InvalidApplePodcastUrl)?;
        if !is_domain_or_subdomain(host, "podcasts.apple.com")
            && !is_domain_or_subdomain(host, "itunes.apple.com")
        {
            return Err(MediaGatewayError::InvalidApplePodcastUrl);
        }
        let show_id =
            apple_show_id(apple_url.as_url()).ok_or(MediaGatewayError::InvalidApplePodcastUrl)?;
        let episode_id = apple_url
            .as_url()
            .query_pairs()
            .find_map(|(key, value)| (key == "i").then(|| value.into_owned()))
            .filter(|value| value.chars().all(|character| character.is_ascii_digit()));
        let mut lookup = Url::parse("https://itunes.apple.com/lookup").map_err(|error| {
            MediaGatewayError::InvalidConfiguration(format!(
                "Apple lookup endpoint is invalid: {error}"
            ))
        })?;
        {
            let mut query = lookup.query_pairs_mut();
            query
                .append_pair("id", &show_id)
                .append_pair("entity", "podcastEpisode")
                .append_pair("limit", "200");
            if let Some(country) = self
                .config
                .itunes_country
                .as_deref()
                .map(str::trim)
                .filter(|value| !value.is_empty())
            {
                query.append_pair("country", &country.to_ascii_lowercase());
            }
        }
        let lookup_bytes = self
            .fetch_public_bytes(lookup.as_str(), APPLE_LOOKUP_MAX_BYTES, "application/json")
            .await?;
        let lookup: ItunesLookupResponse = serde_json::from_slice(&lookup_bytes)?;
        let feed_url = lookup
            .results
            .iter()
            .find(|item| item.kind.as_deref() == Some("podcast"))
            .and_then(|item| clean(item.feed_url.as_deref()));
        let episode_title = episode_id.as_deref().and_then(|episode_id| {
            lookup
                .results
                .iter()
                .find(|item| {
                    item.kind.as_deref() == Some("podcast-episode")
                        && item
                            .track_id
                            .is_some_and(|track_id| track_id.to_string() == episode_id)
                })
                .and_then(|item| clean(item.track_name.as_deref()))
        });
        let audio_url = match (&feed_url, &episode_title) {
            (Some(feed_url), Some(episode_title)) => {
                let feed_bytes = self
                    .fetch_public_bytes(feed_url, APPLE_FEED_MAX_BYTES, "application/rss+xml")
                    .await?;
                resolve_feed_audio(&feed_bytes, episode_title)?
            }
            _ => None,
        };
        Ok(ApplePodcastResolution {
            feed_url,
            episode_title,
            audio_url,
        })
    }

    async fn fetch_public_bytes(
        &self,
        raw_url: &str,
        max_bytes: u64,
        accept: &str,
    ) -> Result<Vec<u8>, MediaGatewayError> {
        let response = self.send_public_get(raw_url, Some(accept)).await?;
        if response
            .content_length()
            .is_some_and(|length| length > max_bytes)
        {
            return Err(MediaGatewayError::ResponseTooLarge { limit: max_bytes });
        }
        let mut body = Vec::new();
        let mut stream = response.bytes_stream();
        while let Some(chunk) = stream.next().await {
            let chunk = chunk?;
            if u64::try_from(body.len().saturating_add(chunk.len())).unwrap_or(u64::MAX) > max_bytes
            {
                return Err(MediaGatewayError::ResponseTooLarge { limit: max_bytes });
            }
            body.extend_from_slice(&chunk);
        }
        Ok(body)
    }

    async fn send_public_get(
        &self,
        raw_url: &str,
        accept: Option<&str>,
    ) -> Result<reqwest::Response, MediaGatewayError> {
        let mut current = PublicUrl::parse(raw_url)?;
        for redirect_count in 0..=self.config.max_redirects {
            current.validate_dns().await?;
            let mut request = self.client.get(current.as_url().clone()).header(
                USER_AGENT,
                "Mozilla/5.0 (compatible; Newsly/1.0; Media Worker)",
            );
            if let Some(accept) = accept {
                request = request.header(ACCEPT, accept);
            }
            let response = request.send().await?;
            if !response.status().is_redirection() {
                return response
                    .error_for_status()
                    .map_err(MediaGatewayError::Transport);
            }
            if redirect_count == self.config.max_redirects {
                return Err(MediaGatewayError::TooManyRedirects);
            }
            let location = response
                .headers()
                .get(LOCATION)
                .and_then(|value| value.to_str().ok())
                .ok_or(MediaGatewayError::RedirectLocationMissing)?;
            let next = current
                .as_url()
                .join(location)
                .map_err(|error| MediaGatewayError::InvalidRedirect(error.to_string()))?;
            current = PublicUrl::parse(next.as_str())?;
        }
        Err(MediaGatewayError::TooManyRedirects)
    }
}

#[derive(Debug, Deserialize)]
struct ItunesLookupResponse {
    #[serde(default)]
    results: Vec<ItunesLookupItem>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ItunesLookupItem {
    #[serde(default)]
    kind: Option<String>,
    #[serde(default)]
    feed_url: Option<String>,
    #[serde(default)]
    track_id: Option<u64>,
    #[serde(default)]
    track_name: Option<String>,
}

fn resolve_feed_audio(
    bytes: &[u8],
    target_title: &str,
) -> Result<Option<String>, MediaGatewayError> {
    let feed = feed_rs::parser::parse(bytes)
        .map_err(|error| MediaGatewayError::Feed(error.to_string()))?;
    let target_normalized = normalized_title(target_title);
    let target_tokens = title_tokens(target_title);
    let target_token_set = target_tokens.iter().collect::<BTreeSet<_>>();
    let mut best_url = None;
    let mut best_score = 0_usize;
    for entry in feed.entries {
        let Some(entry_title) = entry.title.as_ref().map(|value| value.content.trim()) else {
            continue;
        };
        let Some(audio_url) = entry_audio_url(&entry) else {
            continue;
        };
        if normalized_title(entry_title) == target_normalized {
            return Ok(Some(audio_url));
        }
        let tokens = title_tokens(entry_title);
        let entry_token_set = tokens.iter().collect::<BTreeSet<_>>();
        let score = entry_token_set.intersection(&target_token_set).count();
        if score > best_score {
            best_score = score;
            best_url = Some(audio_url);
        }
    }
    let minimum_score = if target_tokens.is_empty() {
        0
    } else {
        3.max(target_tokens.len() / 2)
    };
    Ok((best_score >= minimum_score).then_some(best_url).flatten())
}

fn entry_audio_url(entry: &feed_rs::model::Entry) -> Option<String> {
    entry.links.iter().find_map(|link| {
        let media_type = link.media_type.as_deref().unwrap_or_default();
        let is_enclosure = link.rel.as_deref() == Some("enclosure");
        let looks_like_audio = media_type.starts_with("audio/")
            || matches!(
                media_extension(&link.href).as_str(),
                "aac" | "flac" | "m4a" | "mp3" | "mpga" | "oga" | "ogg" | "opus" | "wav" | "webm"
            );
        (is_enclosure || looks_like_audio)
            .then(|| clean(Some(link.href.as_str())))
            .flatten()
    })
}

fn apple_show_id(url: &Url) -> Option<String> {
    url.path_segments()
        .into_iter()
        .flatten()
        .find_map(|segment| {
            let candidate = segment.strip_prefix("id")?;
            (!candidate.is_empty()
                && candidate
                    .chars()
                    .all(|character| character.is_ascii_digit()))
            .then(|| candidate.to_owned())
        })
        .or_else(|| {
            url.query_pairs()
                .find_map(|(key, value)| (key == "id").then(|| value.into_owned()))
                .filter(|value| value.chars().all(|character| character.is_ascii_digit()))
        })
}

fn title_tokens(value: &str) -> Vec<String> {
    value
        .split(|character: char| !character.is_ascii_alphanumeric())
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_ascii_lowercase)
        .filter(|value| !STOPWORDS.contains(&value.as_str()))
        .collect()
}

fn normalized_title(value: &str) -> String {
    title_tokens(value).join(" ")
}

fn clean(value: Option<&str>) -> Option<String> {
    value
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
}

fn validate_ytdlp_host(url: &Url, target: YtDlpTarget) -> Result<(), MediaGatewayError> {
    let host = url
        .host_str()
        .ok_or_else(|| MediaGatewayError::UnsupportedYtDlpUrl("URL has no host".to_owned()))?;
    let supported = match target {
        YtDlpTarget::YouTube => {
            is_domain_or_subdomain(host, "youtube.com") || is_domain_or_subdomain(host, "youtu.be")
        }
        YtDlpTarget::Tweet => {
            is_domain_or_subdomain(host, "x.com") || is_domain_or_subdomain(host, "twitter.com")
        }
    };
    if supported {
        Ok(())
    } else {
        Err(MediaGatewayError::UnsupportedYtDlpUrl(host.to_owned()))
    }
}

fn is_domain_or_subdomain(host: &str, expected: &str) -> bool {
    let host = host.trim_end_matches('.').to_ascii_lowercase();
    let expected = expected.trim_end_matches('.').to_ascii_lowercase();
    host == expected || host.ends_with(&format!(".{expected}"))
}

fn media_extension(raw_url: &str) -> String {
    Url::parse(raw_url)
        .ok()
        .and_then(|url| {
            Path::new(url.path())
                .extension()
                .and_then(|value| value.to_str())
                .map(str::to_ascii_lowercase)
        })
        .filter(|extension| {
            matches!(
                extension.as_str(),
                "aac" | "flac" | "m4a" | "mp3" | "mpga" | "oga" | "ogg" | "opus" | "wav" | "webm"
            )
        })
        .unwrap_or_else(|| "mp3".to_owned())
}

fn validate_output_stem(value: &str) -> Result<(), MediaGatewayError> {
    if value.is_empty()
        || value.len() > 128
        || value
            .chars()
            .any(|character| !character.is_ascii_alphanumeric() && !matches!(character, '-' | '_'))
    {
        return Err(MediaGatewayError::InvalidOutputStem);
    }
    Ok(())
}

async fn reusable_file(
    path: &Path,
    max_bytes: u64,
) -> Result<Option<DownloadedMedia>, MediaGatewayError> {
    let metadata = match fs::symlink_metadata(path).await {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error.into()),
    };
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err(MediaGatewayError::UnsafeMediaPath);
    }
    if metadata.len() == 0 {
        let _ = fs::remove_file(path).await;
        return Ok(None);
    }
    if metadata.len() > max_bytes {
        return Err(MediaGatewayError::MediaTooLarge { limit: max_bytes });
    }
    Ok(Some(DownloadedMedia {
        path: path.to_path_buf(),
        size_bytes: metadata.len(),
    }))
}

async fn find_ytdlp_output(
    directory: &Path,
    output_stem: &str,
    max_bytes: u64,
) -> Result<Option<DownloadedMedia>, MediaGatewayError> {
    let mut entries = match fs::read_dir(directory).await {
        Ok(entries) => entries,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error.into()),
    };
    while let Some(entry) = entries.next_entry().await? {
        let file_name = entry.file_name();
        let Some(file_name) = file_name.to_str() else {
            continue;
        };
        let temporary_extension = Path::new(file_name)
            .extension()
            .and_then(|extension| extension.to_str())
            .is_some_and(|extension| {
                extension.eq_ignore_ascii_case("part") || extension.eq_ignore_ascii_case("ytdl")
            });
        if !file_name.starts_with(&format!("{output_stem}."))
            || temporary_extension
            || file_name.ends_with(".normalized.wav")
        {
            continue;
        }
        if let Some(file) = reusable_file(&entry.path(), max_bytes).await? {
            return Ok(Some(file));
        }
    }
    Ok(None)
}

async fn wait_for_ytdlp_limit(
    directory: &Path,
    output_stem: &str,
    max_bytes: u64,
) -> MediaGatewayError {
    loop {
        tokio::time::sleep(Duration::from_millis(100)).await;
        match ytdlp_output_bytes(directory, output_stem).await {
            Ok(bytes) if bytes > max_bytes => {
                return MediaGatewayError::MediaTooLarge { limit: max_bytes };
            }
            Ok(_) => {}
            Err(error) => return error,
        }
    }
}

async fn ytdlp_output_bytes(directory: &Path, output_stem: &str) -> Result<u64, MediaGatewayError> {
    let mut entries = match fs::read_dir(directory).await {
        Ok(entries) => entries,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(0),
        Err(error) => return Err(error.into()),
    };
    let mut total = 0_u64;
    while let Some(entry) = entries.next_entry().await? {
        let name = entry.file_name();
        if !name
            .to_str()
            .is_some_and(|name| name.starts_with(&format!("{output_stem}.")))
        {
            continue;
        }
        let metadata = fs::symlink_metadata(entry.path()).await?;
        if metadata.file_type().is_symlink() || !metadata.is_file() {
            return Err(MediaGatewayError::UnsafeMediaPath);
        }
        total = total
            .checked_add(metadata.len())
            .ok_or(MediaGatewayError::MediaTooLarge { limit: u64::MAX })?;
    }
    Ok(total)
}

async fn remove_ytdlp_outputs(
    directory: &Path,
    output_stem: &str,
) -> Result<(), MediaGatewayError> {
    let mut entries = match fs::read_dir(directory).await {
        Ok(entries) => entries,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(()),
        Err(error) => return Err(error.into()),
    };
    while let Some(entry) = entries.next_entry().await? {
        let name = entry.file_name();
        if name
            .to_str()
            .is_some_and(|name| name.starts_with(&format!("{output_stem}.")))
        {
            let metadata = entry.metadata().await?;
            if metadata.is_file() {
                fs::remove_file(entry.path()).await?;
            }
        }
    }
    Ok(())
}

fn decode_anchor_redirect(raw_url: &str) -> Result<String, MediaGatewayError> {
    let parsed = Url::parse(raw_url)
        .map_err(|error| MediaGatewayError::InvalidRedirect(error.to_string()))?;
    if !parsed
        .host_str()
        .is_some_and(|host| is_domain_or_subdomain(host, "anchor.fm"))
        || !raw_url.contains("https%3A%2F%2F")
    {
        return Ok(raw_url.to_owned());
    }
    for segment in parsed.path_segments().into_iter().flatten() {
        if segment.contains("https%3A%2F%2F") {
            return percent_decode(segment);
        }
    }
    Ok(raw_url.to_owned())
}

fn percent_decode(value: &str) -> Result<String, MediaGatewayError> {
    let bytes = value.as_bytes();
    let mut decoded = Vec::with_capacity(bytes.len());
    let mut index = 0;
    while index < bytes.len() {
        if bytes[index] == b'%' {
            if index + 2 >= bytes.len() {
                return Err(MediaGatewayError::InvalidPercentEncoding);
            }
            let high =
                hex_value(bytes[index + 1]).ok_or(MediaGatewayError::InvalidPercentEncoding)?;
            let low =
                hex_value(bytes[index + 2]).ok_or(MediaGatewayError::InvalidPercentEncoding)?;
            decoded.push((high << 4) | low);
            index += 3;
        } else {
            decoded.push(bytes[index]);
            index += 1;
        }
    }
    String::from_utf8(decoded).map_err(|_| MediaGatewayError::InvalidPercentEncoding)
}

const fn hex_value(value: u8) -> Option<u8> {
    match value {
        b'0'..=b'9' => Some(value - b'0'),
        b'a'..=b'f' => Some(value - b'a' + 10),
        b'A'..=b'F' => Some(value - b'A' + 10),
        _ => None,
    }
}

fn truncate_chars(value: &str, limit: usize) -> String {
    value.chars().take(limit).collect()
}

pub fn is_youtube_url(raw_url: &str) -> bool {
    let Ok(url) = Url::parse(raw_url) else {
        return false;
    };
    let Some(host) = url.host_str() else {
        return false;
    };
    if is_domain_or_subdomain(host, "youtu.be") {
        return !url.path().trim_matches('/').is_empty();
    }
    if !is_domain_or_subdomain(host, "youtube.com") {
        return false;
    }
    let path = url.path();
    if path.trim_end_matches('/') == "/watch" {
        return url
            .query_pairs()
            .any(|(key, value)| key == "v" && !value.trim().is_empty());
    }
    ["/embed/", "/v/", "/shorts/"]
        .iter()
        .any(|prefix| path.starts_with(prefix))
}

pub fn is_apple_podcasts_url(raw_url: &str) -> bool {
    Url::parse(raw_url).ok().is_some_and(|url| {
        url.host_str().is_some_and(|host| {
            is_domain_or_subdomain(host, "podcasts.apple.com")
                || is_domain_or_subdomain(host, "itunes.apple.com")
        })
    })
}

pub fn is_terminal_ytdlp_error(message: &str) -> bool {
    let message = message.to_ascii_lowercase();
    [
        "sign in to confirm",
        "requires authentication",
        "cookies not found",
        "private video",
        "video unavailable",
    ]
    .iter()
    .any(|marker| message.contains(marker))
}

#[derive(Debug, Error)]
pub enum MediaGatewayError {
    #[error("invalid media gateway configuration: {0}")]
    InvalidConfiguration(String),
    #[error("public media URL validation failed")]
    PublicUrl(#[from] newsly_extraction::ExtractionClientError),
    #[error("media HTTP request failed")]
    Transport(#[from] reqwest::Error),
    #[error("media file I/O failed")]
    Io(#[from] std::io::Error),
    #[error("media response exceeded the {limit}-byte limit")]
    ResponseTooLarge { limit: u64 },
    #[error("media download exceeded the {limit}-byte limit")]
    MediaTooLarge { limit: u64 },
    #[error("media response was empty")]
    EmptyMedia,
    #[error("media file is missing")]
    MediaFileMissing,
    #[error("media path is not a regular file")]
    UnsafeMediaPath,
    #[error("media redirect did not include a valid Location header")]
    RedirectLocationMissing,
    #[error("media redirect target was invalid: {0}")]
    InvalidRedirect(String),
    #[error("media request exceeded the redirect limit")]
    TooManyRedirects,
    #[error("yt-dlp URL host {0:?} is not supported by this task")]
    UnsupportedYtDlpUrl(String),
    #[error("yt-dlp download exceeded its deadline")]
    YtDlpTimeout,
    #[error("yt-dlp failed: {0}")]
    YtDlpFailed(String),
    #[error("yt-dlp completed without a media file")]
    YtDlpOutputMissing,
    #[error("Apple Podcasts URL is invalid")]
    InvalidApplePodcastUrl,
    #[error("Apple Podcasts lookup returned invalid JSON")]
    AppleLookup(#[from] serde_json::Error),
    #[error("podcast feed could not be parsed: {0}")]
    Feed(String),
    #[error("output stem is invalid")]
    InvalidOutputStem,
    #[error("encoded Anchor media URL is invalid")]
    InvalidPercentEncoding,
}

#[cfg(test)]
mod tests {
    use super::{
        apple_show_id, decode_anchor_redirect, is_apple_podcasts_url, is_terminal_ytdlp_error,
        is_youtube_url, media_extension, percent_decode,
    };
    use reqwest::Url;

    #[test]
    fn parses_apple_show_ids_and_audio_extensions() {
        let url =
            Url::parse("https://podcasts.apple.com/us/podcast/example/id123456789?i=1000123456789")
                .unwrap();
        assert_eq!(apple_show_id(&url).as_deref(), Some("123456789"));
        assert!(is_apple_podcasts_url(url.as_str()));
        assert_eq!(
            media_extension("https://cdn.example.test/audio.M4A?x=1"),
            "m4a"
        );
    }

    #[test]
    fn decodes_anchor_redirects_and_classifies_terminal_provider_errors() {
        assert_eq!(
            percent_decode("https%3A%2F%2Fcdn.example.test%2Faudio.mp3").unwrap(),
            "https://cdn.example.test/audio.mp3"
        );
        assert_eq!(
            decode_anchor_redirect(
                "https://anchor.fm/s/show/https%3A%2F%2Fcdn.example.test%2Faudio.mp3"
            )
            .unwrap(),
            "https://cdn.example.test/audio.mp3"
        );
        assert!(is_terminal_ytdlp_error("Video unavailable"));
        assert!(!is_terminal_ytdlp_error("temporary network failure"));
    }

    #[test]
    fn recognizes_only_supported_youtube_video_shapes() {
        assert!(is_youtube_url("https://youtu.be/abc"));
        assert!(is_youtube_url("https://www.youtube.com/watch?v=abc"));
        assert!(is_youtube_url("https://youtube.com/shorts/abc"));
        assert!(!is_youtube_url("https://youtube.com/channel/abc"));
        assert!(!is_youtube_url("https://youtu.be/"));
    }
}
