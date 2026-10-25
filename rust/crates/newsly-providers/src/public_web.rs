//! Bounded inert public-page reading for host tools. No downloaded code executes.
use std::{collections::HashSet, time::Duration};

use futures_util::StreamExt;
use newsly_extraction::PublicUrl;
use scraper::{ElementRef, Html, Selector};
use serde::Serialize;
use thiserror::Error;

use crate::public_http::{PublicHttpError, send_public};

const PAGE_BYTES: usize = 2 * 1024 * 1024;
const PAGE_TIMEOUT: Duration = Duration::from_secs(15);

#[derive(Debug, Clone, Serialize)]
pub struct PublicWebLink {
    pub url: String,
    pub title: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct PublicWebPage {
    pub url: String,
    pub title: Option<String>,
    pub text: String,
    pub links: Vec<PublicWebLink>,
    pub feed_urls: Vec<String>,
    pub truncated: bool,
}

#[derive(Debug, Error)]
#[error("public page could not be read: {0}")]
pub struct PublicWebPageError(String);

/// Read bounded public text and links under the same DNS/redirect policy as feed validation.
///
/// # Errors
/// Returns a safe error for invalid URLs, transport failures, non-text responses or body limits.
pub async fn read_public_web_page(
    url: &str,
    max_chars: usize,
) -> Result<PublicWebPage, PublicWebPageError> {
    tokio::time::timeout(PAGE_TIMEOUT, async {
        let response = send_public(
            url,
            PAGE_TIMEOUT,
            Duration::from_secs(5),
            "newsly-page-reader/1.0",
            Some("text/html, text/plain;q=0.9, application/xml;q=0.5"),
            5,
            false,
        )
        .await
        .map_err(|error| page_error(&error))?;
        if !response.response.status().is_success() {
            return Err(PublicWebPageError(format!(
                "HTTP {}",
                response.response.status()
            )));
        }
        let mime = response
            .response
            .headers()
            .get(reqwest::header::CONTENT_TYPE)
            .and_then(|v| v.to_str().ok())
            .unwrap_or("")
            .split(';')
            .next()
            .unwrap_or("")
            .trim()
            .to_ascii_lowercase();
        if !matches!(
            mime.as_str(),
            "text/html"
                | "application/xhtml+xml"
                | "text/plain"
                | "text/xml"
                | "application/xml"
                | "application/rss+xml"
                | "application/atom+xml"
        ) {
            return Err(PublicWebPageError(
                "response is not a supported text document".to_owned(),
            ));
        }
        if response
            .response
            .content_length()
            .is_some_and(|n| n > PAGE_BYTES as u64)
        {
            return Err(PublicWebPageError(
                "response exceeded the body limit".to_owned(),
            ));
        }
        let url = response.effective_url.to_string();
        let mut bytes = Vec::new();
        let mut stream = response.response.bytes_stream();
        while let Some(chunk) = stream.next().await {
            let chunk =
                chunk.map_err(|_| PublicWebPageError("response download failed".to_owned()))?;
            if bytes.len().saturating_add(chunk.len()) > PAGE_BYTES {
                return Err(PublicWebPageError(
                    "response exceeded the body limit".to_owned(),
                ));
            }
            bytes.extend_from_slice(&chunk);
        }
        let body = String::from_utf8_lossy(&bytes);
        Ok(parse_page(
            &url,
            &body,
            max_chars.clamp(1, 40_000),
            mime.contains("html"),
        ))
    })
    .await
    .map_err(|_| PublicWebPageError("deadline exceeded".to_owned()))?
}

fn page_error(error: &PublicHttpError) -> PublicWebPageError {
    // Do not include URLs, IPs, request headers or provider diagnostics in tool errors.
    PublicWebPageError(
        match error {
            PublicHttpError::Url(_) => "URL is not an allowed public address",
            PublicHttpError::Deadline => "deadline exceeded",
            PublicHttpError::TooLarge => "response exceeded the body limit",
            _ => "public HTTP request failed",
        }
        .to_owned(),
    )
}

fn selector(value: &str) -> Selector {
    Selector::parse(value).expect("static selector")
}

fn normalize_text<'a>(nodes: impl IntoIterator<Item = &'a str>) -> String {
    nodes
        .into_iter()
        .flat_map(str::split_whitespace)
        .collect::<Vec<_>>()
        .join(" ")
}

fn parse_page(url: &str, body: &str, max_chars: usize, html: bool) -> PublicWebPage {
    let mut page = PublicWebPage {
        url: url.to_owned(),
        title: None,
        text: String::new(),
        links: Vec::new(),
        feed_urls: Vec::new(),
        truncated: false,
    };
    let text = if html {
        let document = Html::parse_document(body);
        page.title = document
            .select(&selector("title"))
            .next()
            .map(|e| normalize_text(e.text()));
        let base = reqwest::Url::parse(url).expect("validated URL");
        let mut seen = HashSet::new();
        for link in document.select(&selector("a[href], link[href]")) {
            let Some(target) = link
                .value()
                .attr("href")
                .and_then(|v| base.join(v).ok())
                .filter(|v| PublicUrl::parse(v.as_str()).is_ok())
            else {
                continue;
            };
            let mut target = target;
            target.set_fragment(None);
            let target = target.to_string();
            if link.value().name() == "link"
                && link.value().attr("rel").is_some_and(|v| {
                    v.split_whitespace()
                        .any(|v| v.eq_ignore_ascii_case("alternate"))
                })
                && link.value().attr("type").is_some_and(|v| {
                    matches!(
                        v.to_ascii_lowercase().as_str(),
                        "application/rss+xml" | "application/atom+xml"
                    )
                })
                && page.feed_urls.len() < 16
                && !page.feed_urls.contains(&target)
            {
                page.feed_urls.push(target.clone());
            }
            if page.links.len() < 64 && seen.insert(target.clone()) {
                let title = normalize_text(link.text()).chars().take(240).collect();
                page.links.push(PublicWebLink { url: target, title });
            }
        }
        normalize_text(document.root_element().descendants().filter_map(|node| {
            let text = node.value().as_text()?;
            let hidden = node.ancestors().filter_map(ElementRef::wrap).any(|e| {
                matches!(
                    e.value().name(),
                    "script" | "style" | "noscript" | "template" | "head"
                )
            });
            (!hidden).then_some(text.as_ref())
        }))
    } else {
        normalize_text([body])
    };
    page.truncated = text.chars().count() > max_chars;
    page.text = text.chars().take(max_chars).collect();
    page
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn inert_html_extracts_relative_feed_and_links_without_scripts() {
        let page = parse_page(
            "https://example.com/pod/",
            "<html><head><title> A show </title><link rel='alternate' type='application/rss+xml' href='../rss'/></head><body>Hello<script>secret()</script><a href='/listen'>Listen</a><a href='file:///etc/passwd'>Bad</a></body></html>",
            100,
            true,
        );
        assert_eq!(page.feed_urls, ["https://example.com/rss"]);
        assert_eq!(page.title.as_deref(), Some("A show"));
        assert!(page.text.contains("Hello"));
        assert!(!page.text.contains("secret"));
        assert!(!page.links.iter().any(|l| l.url.starts_with("file:")));
    }
    #[test]
    fn text_limits_are_unicode_safe() {
        let page = parse_page("https://example.com/", "ééé", 2, false);
        assert_eq!(page.text, "éé");
        assert!(page.truncated);
    }
    #[test]
    fn html_normalizes_whitespace_between_text_nodes() {
        let page = parse_page(
            "https://example.com/",
            "<title> A\n show </title><body> Hello <a href='/listen'>\tListen<span> now </span> please</a>\nworld </body>",
            100,
            true,
        );
        assert_eq!(page.title.as_deref(), Some("A show"));
        assert_eq!(page.links[0].title, "Listen now please");
        assert_eq!(page.text, "Hello Listen now please world");
    }
}
