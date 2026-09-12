use std::collections::BTreeMap;

use serde_json::json;

use super::{
    html_redirect_url, map_tweet, normalize_external_url, strip_bearer_prefix, tweet_id_from_url,
};

#[test]
fn accepts_case_insensitive_bearer_prefix_without_forwarding_it_twice() {
    assert_eq!(strip_bearer_prefix("Bearer token"), "token");
    assert_eq!(strip_bearer_prefix("bearer token"), "token");
    assert_eq!(strip_bearer_prefix("Bearer "), "");
    assert_eq!(strip_bearer_prefix("token"), "token");
}

#[test]
fn tweet_id_parser_accepts_only_x_status_urls() {
    assert_eq!(
        tweet_id_from_url("https://x.com/newsly/status/1234567890?ref=share").as_deref(),
        Some("1234567890")
    );
    assert_eq!(
        tweet_id_from_url("https://example.com/newsly/status/1234567890"),
        None
    );
    assert_eq!(
        tweet_id_from_url("https://x.com/newsly/status/not-a-number"),
        None
    );
}

#[test]
fn external_url_normalization_rejects_non_http_and_x_hosts() {
    assert_eq!(normalize_external_url("ftp://example.com/file"), None);
    assert_eq!(normalize_external_url("https://x.com/i/status/123"), None);
    assert_eq!(
        normalize_external_url("http://example.com/story#discussion").as_deref(),
        Some("https://example.com/story#discussion")
    );
}

#[test]
fn note_tweet_entity_urls_are_available_to_target_resolution() {
    let tweet = json!({
        "id": "123",
        "text": "Truncated preview",
        "note_tweet": {
            "note_tweet_results": {
                "result": {
                    "text": "Read the full post https://t.co/story",
                    "entity_set": {
                        "urls": [{
                            "url": "https://t.co/story",
                            "expanded_url": "https://example.com/story"
                        }]
                    }
                }
            }
        }
    });
    let mapped = map_tweet(
        tweet.as_object().unwrap(),
        &BTreeMap::default(),
        &BTreeMap::default(),
    )
    .expect("tweet should map");
    assert_eq!(mapped.external_urls, ["https://example.com/story"]);
}

#[test]
fn short_link_html_redirects_are_extracted() {
    let meta = r#"<meta http-equiv="refresh" content="0;URL=https://example.com/story">"#;
    assert_eq!(
        html_redirect_url(meta).as_deref(),
        Some("https://example.com/story")
    );
    let script = r#"<script>location.replace("https:\/\/example.com\/fallback")</script>"#;
    assert_eq!(
        html_redirect_url(script).as_deref(),
        Some("https://example.com/fallback")
    );
}
