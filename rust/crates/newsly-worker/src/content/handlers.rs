use std::sync::Arc;
use std::time::Duration;

use newsly_providers::{ContentAnalysisGateway, XLookupGateway};
use newsly_queue::{OwnedWorkPlan, QueueKernel, TaskType};
use secrecy::SecretString;
use sqlx::PgPool;

use crate::{HandlerFuture, LeaseHealth, TaskHandler};

use super::extraction::ContentExtractionRuntime;
use super::storage::LocalContentBodyStore;

mod analysis;
mod process;
mod support;
mod tweet;

use analysis::execute_analyze_url;
use process::execute_process_content;

#[cfg(test)]
use analysis::instruction_link_plan;
#[cfg(test)]
use support::{
    classify_known_url, extraction_failure_is_terminal, feed_candidates_from_metadata,
    resolve_article_url, should_run_structured_analysis,
};
#[cfg(test)]
use tweet::{extract_tweet_id, resolve_from_known_tweets};

#[derive(Debug, Clone)]
pub struct ContentWorkerServices {
    pool: PgPool,
    queue: QueueKernel,
    extraction: ContentExtractionRuntime,
    body_store: LocalContentBodyStore,
    content_analysis: ContentAnalysisGateway,
    x_lookup: XLookupGateway,
    x_app_bearer_token: Option<SecretString>,
    extraction_timeout: Duration,
    max_retries: i32,
}

impl ContentWorkerServices {
    #[allow(clippy::too_many_arguments)]
    pub const fn new(
        pool: PgPool,
        queue: QueueKernel,
        extraction: ContentExtractionRuntime,
        body_store: LocalContentBodyStore,
        content_analysis: ContentAnalysisGateway,
        x_lookup: XLookupGateway,
        x_app_bearer_token: Option<SecretString>,
        extraction_timeout: Duration,
        max_retries: i32,
    ) -> Self {
        Self {
            pool,
            queue,
            extraction,
            body_store,
            content_analysis,
            x_lookup,
            x_app_bearer_token,
            extraction_timeout,
            max_retries,
        }
    }
}

#[derive(Debug, Clone)]
pub struct AnalyzeUrlHandler {
    services: Arc<ContentWorkerServices>,
}

impl AnalyzeUrlHandler {
    pub fn new(services: Arc<ContentWorkerServices>) -> Self {
        Self { services }
    }
}

impl TaskHandler for AnalyzeUrlHandler {
    fn task_type(&self) -> TaskType {
        TaskType::AnalyzeUrl
    }

    fn execute(&self, plan: Arc<OwnedWorkPlan>, lease: LeaseHealth) -> HandlerFuture<'_> {
        let services = Arc::clone(&self.services);
        Box::pin(async move { execute_analyze_url(&services, &plan, lease).await })
    }
}

#[derive(Debug, Clone)]
pub struct ProcessContentHandler {
    services: Arc<ContentWorkerServices>,
}

impl ProcessContentHandler {
    pub fn new(services: Arc<ContentWorkerServices>) -> Self {
        Self { services }
    }
}

impl TaskHandler for ProcessContentHandler {
    fn task_type(&self) -> TaskType {
        TaskType::ProcessContent
    }

    fn execute(&self, plan: Arc<OwnedWorkPlan>, lease: LeaseHealth) -> HandlerFuture<'_> {
        let services = Arc::clone(&self.services);
        Box::pin(async move { execute_process_content(&services, &plan, lease).await })
    }
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeMap;

    use newsly_providers::{InstructionLink, XTweet};
    use serde_json::json;

    use super::{
        classify_known_url, extract_tweet_id, extraction_failure_is_terminal,
        feed_candidates_from_metadata, instruction_link_plan, resolve_article_url,
        resolve_from_known_tweets, should_run_structured_analysis,
    };
    use crate::content::model::ContentSnapshot;

    fn snapshot(url: &str, content_type: &str, metadata: serde_json::Value) -> ContentSnapshot {
        ContentSnapshot {
            content_type: content_type.to_owned(),
            url: url.to_owned(),
            title: None,
            status: "new".to_owned(),
            content_metadata: metadata,
            platform: None,
            body_storage_provider: None,
            body_storage_key: None,
        }
    }

    #[test]
    fn classifies_direct_youtube_video_for_podcast_handoff() {
        let snapshot = snapshot(
            "https://www.youtube.com/watch?v=fixture",
            "article",
            json!({}),
        );
        let classification = classify_known_url(&snapshot).unwrap();
        assert_eq!(classification.content_type, "podcast");
        assert_eq!(classification.platform.as_deref(), Some("youtube"));
        assert_eq!(
            classification.metadata_updates.get("audio_url"),
            Some(&json!(snapshot.url))
        );
    }

    #[test]
    fn structured_analysis_classifies_unknown_urls_and_honors_known_url_instructions() {
        let unknown = snapshot("https://example.com/story", "unknown", json!({}));
        assert!(should_run_structured_analysis(&unknown, None));

        let known = snapshot(
            "https://www.youtube.com/watch?v=fixture",
            "unknown",
            json!({}),
        );
        assert!(!should_run_structured_analysis(&known, None));
        assert!(should_run_structured_analysis(
            &known,
            Some("Extract the cited links")
        ));
    }

    #[test]
    fn extraction_failure_becomes_terminal_only_after_retry_budget_is_exhausted() {
        assert!(!extraction_failure_is_terminal(true, 0, 3));
        assert!(!extraction_failure_is_terminal(true, 2, 3));
        assert!(extraction_failure_is_terminal(true, 3, 3));
        assert!(extraction_failure_is_terminal(false, 0, 3));
    }

    #[test]
    fn resolves_non_url_news_identity_to_linked_article() {
        let snapshot = snapshot(
            "424242",
            "news",
            json!({
                "platform": "hackernews",
                "aggregator": {"metadata": {"hn_linked_url": "http://example.com/story"}}
            }),
        );
        assert_eq!(resolve_article_url(&snapshot), "https://example.com/story");
    }

    #[test]
    fn accepts_only_supported_typed_feed_metadata() {
        let valid = feed_candidates_from_metadata(&json!({
            "detected_feed": {
                "url": "https://example.com/feed.xml",
                "type": "atom",
                "title": "Example"
            }
        }));
        let invalid = feed_candidates_from_metadata(&json!({
            "detected_feed": {
                "url": "https://example.com/feed.xml",
                "type": "custom"
            }
        }));
        assert_eq!(valid.len(), 1);
        assert!(invalid.is_empty());
    }

    #[test]
    fn extracts_numeric_tweet_ids_from_supported_status_paths() {
        assert_eq!(
            extract_tweet_id("https://x.com/newsly/status/1234567890?ref=share").as_deref(),
            Some("1234567890")
        );
        assert_eq!(
            extract_tweet_id("https://twitter.com/i/status/987654321").as_deref(),
            Some("987654321")
        );
        assert_eq!(extract_tweet_id("https://x.com/newsly/status/"), None);
        assert_eq!(
            extract_tweet_id("https://example.com/newsly/status/123"),
            None
        );
    }

    #[test]
    fn normalizes_instruction_links_and_excludes_the_source() {
        let link = InstructionLink {
            url: "http://example.com/child#section".to_owned(),
            title: Some(" Child ".to_owned()),
            context: Some(" useful ".to_owned()),
            content_type: Some(" article ".to_owned()),
            platform: None,
            source: None,
        };
        let plan = instruction_link_plan(&link, "https://example.com/source").unwrap();
        assert_eq!(plan.url, "https://example.com/child");
        assert_eq!(plan.title.as_deref(), Some("Child"));
        assert_eq!(plan.context.as_deref(), Some("useful"));

        let source = InstructionLink {
            url: "http://example.com/source#duplicate".to_owned(),
            ..link
        };
        assert!(instruction_link_plan(&source, "https://example.com/source").is_none());
    }

    #[test]
    fn resolves_an_external_target_from_a_known_linked_tweet() {
        let root = tweet("10", Vec::new(), vec!["11".to_owned()]);
        let linked = tweet(
            "11",
            vec!["https://example.com/linked-story".to_owned()],
            Vec::new(),
        );
        let included = BTreeMap::from([(linked.id.clone(), linked)]);
        let resolution = resolve_from_known_tweets(&root, included).unwrap();
        assert_eq!(
            resolution.selected_article_url.as_deref(),
            Some("https://example.com/linked-story")
        );
        assert_eq!(resolution.resolution_source, "linked_tweet");
        assert_eq!(resolution.resolution_tweet_id, "11");
    }

    fn tweet(id: &str, external_urls: Vec<String>, linked_tweet_ids: Vec<String>) -> XTweet {
        XTweet {
            id: id.to_owned(),
            text: format!("post {id}"),
            author_id: Some("1".to_owned()),
            author_username: Some("newsly".to_owned()),
            author_name: Some("Newsly".to_owned()),
            created_at: Some("2026-08-31T00:00:00Z".to_owned()),
            like_count: None,
            retweet_count: None,
            reply_count: None,
            conversation_id: Some("10".to_owned()),
            in_reply_to_user_id: None,
            referenced_tweet_types: Vec::new(),
            article_title: None,
            article_text: None,
            note_tweet_text: None,
            external_urls,
            linked_tweet_ids,
            has_video: false,
            video_duration_ms: None,
        }
    }
}
