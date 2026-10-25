//! Shared Apple show directory transport; every caller validates returned feeds separately.
use serde::Deserialize;
use std::time::Duration;

#[derive(Debug, Deserialize)]
struct ItunesResponse {
    #[serde(default)]
    results: Vec<ItunesPodcast>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct ItunesPodcast {
    pub(crate) collection_id: Option<u64>,
    pub(crate) collection_name: Option<String>,
    pub(crate) artist_name: Option<String>,
    pub(crate) feed_url: Option<String>,
    pub(crate) collection_view_url: Option<String>,
    pub(crate) primary_genre_name: Option<String>,
    pub(crate) track_count: Option<u64>,
}

pub(crate) async fn search(
    client: &reqwest::Client,
    term: &str,
    limit: usize,
) -> Result<Vec<ItunesPodcast>, reqwest::Error> {
    client
        .get("https://itunes.apple.com/search")
        .timeout(Duration::from_secs(8))
        .query(&[
            ("media", "podcast"),
            ("entity", "podcast"),
            ("limit", &limit.clamp(1, 25).to_string()),
            ("term", term),
        ])
        .send()
        .await?
        .error_for_status()?
        .json::<ItunesResponse>()
        .await
        .map(|r| r.results)
}

pub(crate) async fn lookup(
    client: &reqwest::Client,
    ids: &str,
) -> Result<Vec<ItunesPodcast>, reqwest::Error> {
    client
        .get("https://itunes.apple.com/lookup")
        .timeout(Duration::from_secs(8))
        .query(&[("id", ids), ("entity", "podcast")])
        .send()
        .await?
        .error_for_status()?
        .json::<ItunesResponse>()
        .await
        .map(|r| r.results)
}

pub(crate) fn apple_podcast_id(url: &str) -> Option<String> {
    let url = reqwest::Url::parse(url).ok()?;
    if url.host_str()? != "podcasts.apple.com" {
        return None;
    }
    url.path_segments()?.find_map(|segment| {
        segment
            .strip_prefix("id")
            .filter(|v| !v.is_empty() && v.bytes().all(|b| b.is_ascii_digit()))
            .map(str::to_owned)
    })
}
