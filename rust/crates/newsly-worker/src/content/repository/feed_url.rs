use url::Url;

use super::{FeedCandidate, LockedContent, nonempty, truncate_chars};

pub(super) fn canonicalize_feed_url(value: &str) -> String {
    let Ok(mut url) = Url::parse(value.trim()) else {
        return value.trim().trim_end_matches('/').to_owned();
    };
    url.set_fragment(None);
    let path = url.path().trim_end_matches('/').to_owned();
    url.set_path(&path);
    url.to_string()
}

pub(super) fn feed_display_name(candidate: &FeedCandidate, content: &LockedContent) -> String {
    let host = Url::parse(&candidate.url)
        .ok()
        .and_then(|url| url.host_str().map(str::to_owned));
    let selected = candidate
        .title
        .as_deref()
        .and_then(nonempty)
        .or_else(|| content.title.as_deref().and_then(nonempty))
        .or(host.as_deref())
        .unwrap_or("Feed")
        .to_owned();
    truncate_chars(&selected, 255)
}
