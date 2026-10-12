use super::{
    apple_show_id, decode_anchor_redirect, is_apple_podcasts_url, is_terminal_ytdlp_error,
    is_youtube_url, media_extension, percent_decode, reject_html_media,
};
use reqwest::Url;

#[test]
fn html_player_responses_are_rejected_before_download() {
    let mut headers = reqwest::header::HeaderMap::new();
    for value in [
        "text/html; charset=utf-8",
        "Text/HTML",
        "application/xhtml+xml",
    ] {
        headers.insert(reqwest::header::CONTENT_TYPE, value.parse().unwrap());
        assert!(reject_html_media(&headers).is_err());
    }
    for value in ["audio/mpeg", "application/octet-stream", "video/mp4"] {
        headers.insert(reqwest::header::CONTENT_TYPE, value.parse().unwrap());
        assert!(reject_html_media(&headers).is_ok());
    }
    headers.clear();
    assert!(reject_html_media(&headers).is_ok());
}

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
