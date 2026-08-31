use std::io::SeekFrom;

use axum::body::Body;
use axum::http::header::{
    ACCEPT_RANGES, CACHE_CONTROL, CONTENT_DISPOSITION, CONTENT_LENGTH, CONTENT_RANGE, CONTENT_TYPE,
};
use axum::http::{HeaderValue, StatusCode};
use axum::response::{IntoResponse, Response};
use tokio::io::{AsyncReadExt as _, AsyncSeekExt as _};

use crate::AppState;
use crate::audio_storage::AudioStorageError;
use crate::error::ApiError;
use crate::write_support::{internal_error, not_found};

pub(super) const AUDIO_CHUNK_BYTES: usize = 256 * 1_024;

pub(super) async fn stream_stored_audio(
    state: &AppState,
    stored_path: &str,
    episode_id: i64,
    content_type: &str,
    requested_range: Option<&str>,
    request_id: &str,
) -> Result<Response, ApiError> {
    let mut file = state
        .audio_storage
        .open(stored_path)
        .await
        .map_err(|error| match &error {
            AudioStorageError::File { source, .. }
                if source.kind() == std::io::ErrorKind::NotFound =>
            {
                not_found("Audio file", request_id)
            }
            _ => internal_error(error, request_id),
        })?;
    let content_length = file
        .metadata()
        .await
        .map_err(|error| internal_error(error, request_id))?
        .len();
    let Ok(selection) = resolve_byte_selection(requested_range, content_length) else {
        return Ok(range_not_satisfiable_response(
            episode_id,
            content_type,
            content_length,
        ));
    };
    if selection.start > 0 {
        file.seek(SeekFrom::Start(selection.start))
            .await
            .map_err(|error| internal_error(error, request_id))?;
    }
    let stream =
        tokio_util::io::ReaderStream::with_capacity(file.take(selection.length), AUDIO_CHUNK_BYTES);
    Ok(stored_audio_response(
        Body::from_stream(stream),
        episode_id,
        content_type,
        selection,
        content_length,
    ))
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct ByteSelection {
    start: u64,
    length: u64,
    partial: bool,
}

fn resolve_byte_selection(
    requested_range: Option<&str>,
    content_length: u64,
) -> Result<ByteSelection, ()> {
    let Some(requested_range) = requested_range else {
        return Ok(ByteSelection {
            start: 0,
            length: content_length,
            partial: false,
        });
    };
    let Some(specification) = requested_range.trim().strip_prefix("bytes=") else {
        return Err(());
    };
    if specification.contains(',') {
        return Err(());
    }
    let Some((raw_start, raw_end)) = specification.split_once('-') else {
        return Err(());
    };
    let raw_start = raw_start.trim();
    let raw_end = raw_end.trim();
    if content_length == 0 || (raw_start.is_empty() && raw_end.is_empty()) {
        return Err(());
    }

    if raw_start.is_empty() {
        let suffix_length = raw_end.parse::<u64>().map_err(|_| ())?;
        if suffix_length == 0 {
            return Err(());
        }
        let length = suffix_length.min(content_length);
        return Ok(ByteSelection {
            start: content_length - length,
            length,
            partial: true,
        });
    }

    let start = raw_start.parse::<u64>().map_err(|_| ())?;
    if start >= content_length {
        return Err(());
    }
    let end = if raw_end.is_empty() {
        content_length - 1
    } else {
        raw_end
            .parse::<u64>()
            .map_err(|_| ())?
            .min(content_length - 1)
    };
    if end < start {
        return Err(());
    }
    Ok(ByteSelection {
        start,
        length: end - start + 1,
        partial: true,
    })
}

fn stored_audio_response(
    body: Body,
    episode_id: i64,
    content_type: &str,
    selection: ByteSelection,
    content_length: u64,
) -> Response {
    let mut response =
        audio_stream_response(body, episode_id, content_type, Some(selection.length));
    response
        .headers_mut()
        .insert(ACCEPT_RANGES, HeaderValue::from_static("bytes"));
    if selection.partial {
        *response.status_mut() = StatusCode::PARTIAL_CONTENT;
        let end = selection.start + selection.length - 1;
        if let Ok(value) =
            HeaderValue::from_str(&format!("bytes {}-{end}/{content_length}", selection.start))
        {
            response.headers_mut().insert(CONTENT_RANGE, value);
        }
    }
    response
}

fn range_not_satisfiable_response(
    episode_id: i64,
    content_type: &str,
    content_length: u64,
) -> Response {
    let mut response = audio_stream_response(Body::empty(), episode_id, content_type, Some(0));
    *response.status_mut() = StatusCode::RANGE_NOT_SATISFIABLE;
    response
        .headers_mut()
        .insert(ACCEPT_RANGES, HeaderValue::from_static("bytes"));
    if let Ok(value) = HeaderValue::from_str(&format!("bytes */{content_length}")) {
        response.headers_mut().insert(CONTENT_RANGE, value);
    }
    response
}

pub(super) fn audio_stream_response(
    body: Body,
    episode_id: i64,
    content_type: &str,
    content_length: Option<u64>,
) -> Response {
    let mut response = body.into_response();
    let (content_type, filename_extension) = audio_response_metadata(content_type);
    response.headers_mut().insert(CONTENT_TYPE, content_type);
    response
        .headers_mut()
        .insert(CACHE_CONTROL, HeaderValue::from_static("no-store"));
    if let Some(content_length) = content_length
        && let Ok(value) = HeaderValue::from_str(&content_length.to_string())
    {
        response.headers_mut().insert(CONTENT_LENGTH, value);
    }
    if let Ok(value) = HeaderValue::from_str(&format!(
        "attachment; filename=\"audio-episode-{episode_id}.{filename_extension}\""
    )) {
        response.headers_mut().insert(CONTENT_DISPOSITION, value);
    }
    response
}

fn audio_response_metadata(content_type: &str) -> (HeaderValue, &'static str) {
    let media_type = content_type.split(';').next().unwrap_or_default().trim();
    let is_audio = media_type
        .get(.."audio/".len())
        .is_some_and(|prefix| prefix.eq_ignore_ascii_case("audio/"));
    let Ok(header) = HeaderValue::from_str(content_type) else {
        return (HeaderValue::from_static("audio/mpeg"), "mp3");
    };
    if !is_audio {
        return (HeaderValue::from_static("audio/mpeg"), "mp3");
    }

    let extension = if media_type.eq_ignore_ascii_case("audio/mpeg")
        || media_type.eq_ignore_ascii_case("audio/mp3")
    {
        "mp3"
    } else if media_type.eq_ignore_ascii_case("audio/mp4")
        || media_type.eq_ignore_ascii_case("audio/x-m4a")
    {
        "m4a"
    } else if media_type.eq_ignore_ascii_case("audio/aac") {
        "aac"
    } else if media_type.eq_ignore_ascii_case("audio/wav")
        || media_type.eq_ignore_ascii_case("audio/x-wav")
        || media_type.eq_ignore_ascii_case("audio/vnd.wave")
    {
        "wav"
    } else if media_type.eq_ignore_ascii_case("audio/ogg") {
        "ogg"
    } else if media_type.eq_ignore_ascii_case("audio/webm") {
        "webm"
    } else if media_type.eq_ignore_ascii_case("audio/flac") {
        "flac"
    } else {
        "bin"
    };
    (header, extension)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stored_wav_response_declares_length_and_matching_filename() {
        let response = audio_stream_response(Body::empty(), 3, "audio/wav", Some(16_044));

        assert_eq!(response.headers()[CONTENT_TYPE], "audio/wav");
        assert_eq!(response.headers()[CONTENT_LENGTH], "16044");
        assert_eq!(
            response.headers()[CONTENT_DISPOSITION],
            "attachment; filename=\"audio-episode-3.wav\""
        );
    }

    #[test]
    fn full_stored_response_advertises_byte_ranges() {
        let selection = resolve_byte_selection(None, 100).expect("full selection");
        let response = stored_audio_response(Body::empty(), 3, "audio/wav", selection, 100);

        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(response.headers()[ACCEPT_RANGES], "bytes");
        assert_eq!(response.headers()[CONTENT_LENGTH], "100");
        assert!(!response.headers().contains_key(CONTENT_RANGE));
    }

    #[test]
    fn open_ended_range_returns_partial_headers() {
        let selection =
            resolve_byte_selection(Some("bytes=40-"), 100).expect("open-ended selection");
        let response = stored_audio_response(Body::empty(), 3, "audio/wav", selection, 100);

        assert_eq!(selection.start, 40);
        assert_eq!(selection.length, 60);
        assert_eq!(response.status(), StatusCode::PARTIAL_CONTENT);
        assert_eq!(response.headers()[CONTENT_LENGTH], "60");
        assert_eq!(response.headers()[CONTENT_RANGE], "bytes 40-99/100");
    }

    #[test]
    fn suffix_range_is_resolved_from_end() {
        let selection = resolve_byte_selection(Some("bytes=-12"), 100).expect("suffix selection");
        let response = stored_audio_response(Body::empty(), 3, "audio/wav", selection, 100);

        assert_eq!(selection.start, 88);
        assert_eq!(selection.length, 12);
        assert_eq!(response.status(), StatusCode::PARTIAL_CONTENT);
        assert_eq!(response.headers()[CONTENT_RANGE], "bytes 88-99/100");
    }

    #[test]
    fn unsatisfiable_range_returns_416_contract() {
        assert!(resolve_byte_selection(Some("bytes=100-"), 100).is_err());
        let response = range_not_satisfiable_response(3, "audio/wav", 100);

        assert_eq!(response.status(), StatusCode::RANGE_NOT_SATISFIABLE);
        assert_eq!(response.headers()[ACCEPT_RANGES], "bytes");
        assert_eq!(response.headers()[CONTENT_LENGTH], "0");
        assert_eq!(response.headers()[CONTENT_RANGE], "bytes */100");
    }

    #[test]
    fn follow_stream_response_remains_lengthless() {
        let response = audio_stream_response(Body::empty(), 7, "audio/mpeg", None);

        assert_eq!(response.headers()[CONTENT_TYPE], "audio/mpeg");
        assert!(!response.headers().contains_key(CONTENT_LENGTH));
        assert_eq!(
            response.headers()[CONTENT_DISPOSITION],
            "attachment; filename=\"audio-episode-7.mp3\""
        );
    }

    #[test]
    fn unsafe_non_audio_content_type_falls_back_consistently() {
        let response = audio_stream_response(Body::empty(), 11, "text/plain", Some(4));

        assert_eq!(response.headers()[CONTENT_TYPE], "audio/mpeg");
        assert_eq!(response.headers()[CONTENT_LENGTH], "4");
        assert_eq!(
            response.headers()[CONTENT_DISPOSITION],
            "attachment; filename=\"audio-episode-11.mp3\""
        );
    }
}
