use super::{normalize_arxiv_listing, normalize_hf_daily_papers};
use crate::scraping::{ScrapeGatewayError, ScrapedItem, ScrapedNewsItem};

const ARXIV_LISTING: &str = r#"<?xml version='1.0' encoding='UTF-8'?>
<rss xmlns:arxiv="http://arxiv.org/schemas/atom" xmlns:dc="http://purl.org/dc/elements/1.1/" version="2.0">
  <channel>
    <title>cs.CL updates on arXiv.org</title>
    <link>http://rss.arxiv.org/rss/cs.CL</link>
    <description>cs.CL updates on the arXiv.org e-print archive.</description>
    <item>
      <title>FD-VAD: Semantic Endpoint Detection</title>
      <link>https://arxiv.org/abs/2609.35791</link>
      <description>arXiv:2609.35791v1 Announce Type: new
Abstract: Natural turn-taking   requires
semantic endpointing.</description>
      <guid isPermaLink="false">oai:arXiv.org:2609.35791v1</guid>
      <category>cs.CL</category>
      <category>eess.AS</category>
      <pubDate>Thu, 01 Oct 2026 00:00:00 -0400</pubDate>
      <dc:creator>Puneet Mathur, Dinesh Manocha</dc:creator>
    </item>
    <item>
      <title>A cross-listed paper</title>
      <link>https://arxiv.org/abs/2609.30000</link>
      <description>arXiv:2609.30000v1 Announce Type: cross
Abstract: Primary category elsewhere.</description>
      <category>cs.LG</category>
    </item>
    <item>
      <title>A revised paper</title>
      <link>https://arxiv.org/abs/2501.00001</link>
      <description>arXiv:2501.00001v3 Announce Type: replace
Abstract: Old news.</description>
      <category>cs.CL</category>
    </item>
    <item>
      <title>Versioned link</title>
      <link>https://arxiv.org/abs/2609.35800v2</link>
      <description>arXiv:2609.35800v2 Announce Type: new
Abstract: Version in link.</description>
      <category>cs.CL</category>
    </item>
    <item>
      <title>Not an arXiv link</title>
      <link>https://example.com/abs/2609.35801</link>
      <description>arXiv:2609.35801v1 Announce Type: new
Abstract: Wrong host.</description>
    </item>
  </channel>
</rss>"#;

fn news(item: &ScrapedItem) -> &ScrapedNewsItem {
    match item {
        ScrapedItem::News(item) => item,
        ScrapedItem::Content(_) => panic!("paper aggregators publish news items"),
    }
}

#[test]
fn arxiv_listing_keeps_only_first_announcements_in_primary_category() {
    let items = normalize_arxiv_listing("cs.CL", ARXIV_LISTING.as_bytes()).expect("valid listing");

    let urls = items
        .iter()
        .map(|item| news(item).url.as_str())
        .collect::<Vec<_>>();
    assert_eq!(
        urls,
        [
            "https://arxiv.org/abs/2609.35791",
            "https://arxiv.org/abs/2609.35800"
        ]
    );
    let first = news(&items[0]);
    assert_eq!(first.platform, "arxiv");
    assert_eq!(first.visibility_scope, "global");
    assert_eq!(first.source_external_id.as_deref(), Some("2609.35791"));
    assert_eq!(first.discussion_url, None);
    assert_eq!(
        first.canonical_item_url.as_deref(),
        Some("https://arxiv.org/abs/2609.35791")
    );
    assert!(first.published_at.is_some());
    let metadata = &first.raw_metadata;
    assert_eq!(metadata["aggregator"]["topic"], "cs.CL");
    assert_eq!(
        metadata["excerpt"],
        "Natural turn-taking requires semantic endpointing."
    );
    assert_eq!(
        metadata["aggregator"]["author"],
        "Puneet Mathur, Dinesh Manocha"
    );
    assert_eq!(
        metadata["aggregator"]["metadata"]["categories"],
        serde_json::json!(["cs.CL", "eess.AS"])
    );
    assert_eq!(
        metadata["aggregator"]["metadata"]["pdf_url"],
        "https://arxiv.org/pdf/2609.35791"
    );
}

#[test]
fn empty_weekend_listing_is_a_successful_empty_check() {
    let listing = r#"<?xml version='1.0'?><rss version="2.0"><channel>
        <title>cs.CL updates on arXiv.org</title><link>http://rss.arxiv.org/rss/cs.CL</link>
        <description>none</description></channel></rss>"#;
    let items =
        normalize_arxiv_listing("cs.CL", listing.as_bytes()).expect("empty listing is valid");
    assert!(items.is_empty());
}

#[test]
fn malformed_arxiv_listing_is_a_retryable_feed_error() {
    let error = normalize_arxiv_listing("cs.CL", b"<html>busy</html>").expect_err("not a feed");
    assert!(matches!(error, ScrapeGatewayError::Feed(_)));
    assert!(error.retryable());
}

const HF_DAILY_PAPERS: &str = r#"[
  {
    "paper": {
      "id": "2610.00314",
      "title": "Predictive Credit",
      "summary": "Research agents   explain planned experiments.",
      "upvotes": 12,
      "publishedAt": "2026-09-29T00:00:00.000Z",
      "authors": [
        {"name": "Jingjie Ning", "hidden": false},
        {"name": "Hidden Person", "hidden": true},
        {"name": "Xueqi Li", "hidden": false}
      ]
    },
    "publishedAt": "2026-09-28T20:00:00.000Z",
    "title": "Predictive Credit",
    "summary": "Fallback summary",
    "thumbnail": "https://cdn-thumbnails.huggingface.co/social-thumbnails/papers/2610.00314.png",
    "numComments": 3,
    "organization": {"name": "CarnegieMellonU", "fullname": "Carnegie Mellon University"}
  },
  {
    "paper": {"id": "2610.00999v2", "title": "Versioned", "authors": []},
    "title": "Versioned"
  },
  {
    "paper": {"id": "2610.01000", "upvotes": "many"},
    "title": "Schema drift"
  },
  {
    "paper": {"id": "not-an-arxiv-id", "title": "Hosted elsewhere", "authors": []},
    "title": "Hosted elsewhere"
  }
]"#;

#[test]
fn hf_daily_papers_point_at_arxiv_with_hugging_face_discussion() {
    let outcome = normalize_hf_daily_papers(HF_DAILY_PAPERS.as_bytes()).expect("valid response");
    assert_eq!(outcome.items.len(), 2);
    assert_eq!(outcome.item_errors.len(), 2, "{:?}", outcome.item_errors);
    assert_eq!(
        news(&outcome.items[1]).source_external_id.as_deref(),
        Some("2610.00999")
    );
    let item = news(&outcome.items[0]);
    assert_eq!(item.platform, "hfpapers");
    assert_eq!(item.url, "https://arxiv.org/abs/2610.00314");
    assert_eq!(
        item.canonical_story_url.as_deref(),
        Some("https://arxiv.org/abs/2610.00314")
    );
    assert_eq!(
        item.discussion_url.as_deref(),
        Some("https://huggingface.co/papers/2610.00314")
    );
    assert_eq!(item.source_external_id.as_deref(), Some("2610.00314"));
    assert_eq!(
        item.published_at.map(|value| value.to_rfc3339()).as_deref(),
        Some("2026-09-28T20:00:00+00:00")
    );
    let metadata = &item.raw_metadata;
    assert_eq!(
        metadata["excerpt"],
        "Research agents explain planned experiments."
    );
    assert_eq!(metadata["comment_count"], 3);
    assert_eq!(metadata["aggregator"]["author"], "Jingjie Ning, Xueqi Li");
    assert_eq!(metadata["aggregator"]["metadata"]["upvotes"], 12);
    assert_eq!(
        metadata["aggregator"]["metadata"]["organization"],
        "Carnegie Mellon University"
    );
    assert!(metadata["aggregator"].get("topic").is_none());
}

#[test]
fn hf_daily_papers_report_unusable_entries_without_failing_the_check() {
    let outcome = normalize_hf_daily_papers(br#"[{"paper": {"id": "x"}, "title": "x"}]"#)
        .expect("a list with only unusable entries is a reported, non-retryable outcome");
    assert!(outcome.items.is_empty());
    assert_eq!(outcome.item_errors.len(), 1);
    assert!(!outcome.retryable_failure);
    let error =
        normalize_hf_daily_papers(b"{\"error\": \"rate limited\"}").expect_err("not a list");
    assert!(matches!(error, ScrapeGatewayError::Feed(_)));
}

#[test]
fn empty_hf_daily_papers_list_is_a_successful_empty_check() {
    let outcome = normalize_hf_daily_papers(b"[]").expect("empty list is valid");
    assert!(outcome.items.is_empty());
    assert!(outcome.item_errors.is_empty());
}
