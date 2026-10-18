//! Per-attempt, bounded podcast evidence shared by all onboarding lanes.
use std::collections::{HashMap, HashSet};
use std::sync::Mutex;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::Duration;

use futures_util::{StreamExt, stream};
use tokio::sync::Semaphore;
use tokio::time::Instant;

use super::WebResult;
use crate::feed_validation::FeedDiscoveryEvidence;
use crate::{FeedValidationError, FeedValidator, ValidatedFeed};

const MAX_FEEDS: usize = 16;
const MAX_CONCURRENT_FEEDS: usize = 8;
const EVIDENCE_BUDGET: Duration = Duration::from_secs(8);

pub(super) struct PodcastEnrichmentBudget {
    deadline: Instant,
    permits: Semaphore,
    per_group_limit: usize,
    claimed_urls: Mutex<HashSet<String>>,
    completed: Mutex<HashMap<String, Option<(ValidatedFeed, FeedDiscoveryEvidence)>>>,
}

impl PodcastEnrichmentBudget {
    pub(super) fn new(podcast_lanes: usize) -> Self {
        Self {
            deadline: Instant::now() + EVIDENCE_BUDGET,
            permits: Semaphore::new(MAX_CONCURRENT_FEEDS),
            per_group_limit: (MAX_FEEDS / (podcast_lanes.max(1) * 2))
                .clamp(1, MAX_CONCURRENT_FEEDS),
            claimed_urls: Mutex::new(HashSet::new()),
            completed: Mutex::new(HashMap::new()),
        }
    }

    pub(super) fn apply_to_results(
        &self,
        results: &mut Vec<WebResult>,
    ) -> HashMap<String, ValidatedFeed> {
        let completed = self
            .completed
            .lock()
            .expect("evidence state lock stays healthy");
        results.retain_mut(|result| {
            match result.feed_url.as_ref().and_then(|url| completed.get(url)) {
                Some(Some((_, evidence))) => {
                    result.feed_evidence = Some(evidence.clone());
                    true
                }
                Some(None) if result.feed_url.as_deref() == Some(result.url.as_str()) => false,
                Some(None) => {
                    // A rejected link inside a snippet does not invalidate the page itself.
                    result.feed_url = None;
                    result.feed_evidence = None;
                    true
                }
                None => true,
            }
        });
        completed
            .iter()
            .filter_map(|(url, value)| value.as_ref().map(|(feed, _)| (url.clone(), feed.clone())))
            .collect()
    }
}

pub(super) async fn enrich_podcast_results(
    results: Vec<WebResult>,
    budget: &PodcastEnrichmentBudget,
    label: &str,
) -> (Vec<WebResult>, HashMap<String, ValidatedFeed>) {
    enrich_podcast_results_with(results, budget, label, |url| async move {
        FeedValidator::new().validate_feed_with_evidence(&url).await
    })
    .await
}

async fn enrich_podcast_results_with<F, Fut>(
    mut results: Vec<WebResult>,
    budget: &PodcastEnrichmentBudget,
    label: &str,
    validate: F,
) -> (Vec<WebResult>, HashMap<String, ValidatedFeed>)
where
    F: Fn(String) -> Fut + Send + Sync,
    Fut: std::future::Future<
            Output = Result<Option<(ValidatedFeed, FeedDiscoveryEvidence)>, FeedValidationError>,
        >,
{
    let mut seen = HashSet::new();
    let urls = results
        .iter()
        .filter_map(|result| result.feed_url.clone())
        .filter(|url| seen.insert(url.clone()))
        // Reserve room for web-resolved feeds instead of spending the entire attempt budget
        // on whichever directory branch returns first.
        .take(budget.per_group_limit)
        .collect::<Vec<_>>();
    let candidates = urls.len();
    let started = AtomicUsize::new(0);
    let duplicates = AtomicUsize::new(0);
    let capped = AtomicUsize::new(0);
    let validations = stream::iter(urls)
        .map(|url| {
            let (validate, started, duplicates, capped) =
                (&validate, &started, &duplicates, &capped);
            async move {
                let _permit = budget
                    .permits
                    .acquire()
                    .await
                    .expect("enrichment semaphore stays open");
                {
                    let mut claimed = budget
                        .claimed_urls
                        .lock()
                        .expect("evidence state lock stays healthy");
                    if claimed.contains(&url) {
                        duplicates.fetch_add(1, Ordering::Relaxed);
                        return None;
                    }
                    if claimed.len() >= MAX_FEEDS {
                        capped.fetch_add(1, Ordering::Relaxed);
                        return None;
                    }
                    claimed.insert(url.clone());
                }
                started.fetch_add(1, Ordering::Relaxed);
                Some((url.clone(), validate(url).await))
            }
        })
        .buffer_unordered(MAX_CONCURRENT_FEEDS)
        .take_until(tokio::time::sleep_until(budget.deadline));
    futures_util::pin_mut!(validations);
    let mut finished = 0;
    let mut rejected = 0;
    let mut errors = 0;
    while let Some(validation) = validations.next().await {
        let Some((url, validation)) = validation else {
            continue;
        };
        finished += 1;
        let value = match validation {
            Ok(Some((validated, evidence))) if validated.has_audio_entries => {
                Some((validated, evidence))
            }
            Ok(_) => {
                rejected += 1;
                None
            }
            // Errors are unknown and never cached as invalid.
            Err(_) => {
                errors += 1;
                continue;
            }
        };
        budget
            .completed
            .lock()
            .expect("evidence state lock stays healthy")
            .insert(url, value);
    }
    let validated_feeds = budget.apply_to_results(&mut results);
    let started = started.load(Ordering::Relaxed);
    tracing::info!(
        label,
        candidates,
        started,
        finished,
        rejected,
        errors,
        duplicates = duplicates.load(Ordering::Relaxed),
        capped = capped.load(Ordering::Relaxed),
        pending_at_deadline = started - finished,
        remaining_budget_ms = budget
            .deadline
            .saturating_duration_since(Instant::now())
            .as_millis(),
        "onboarding podcast content evidence collected"
    );
    (results, validated_feeds)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{FeedPublishingActivity, ValidatedFeedFormat};
    use std::sync::Arc;

    fn result(url: &str) -> WebResult {
        WebResult {
            title: url.to_owned(),
            url: url.to_owned(),
            snippet: None,
            published_date: None,
            query: String::new(),
            feed_url: Some(url.to_owned()),
            feed_evidence: None,
        }
    }
    fn feed(url: &str) -> ValidatedFeed {
        let now = chrono::Utc::now();
        let activity = FeedPublishingActivity {
            checked_at: now,
            window_start: now,
            year_window_start: now,
            window_end: now,
            latest_item: None,
            observed_count: None,
            observed_year_count: None,
            undated_count: 1,
        };
        ValidatedFeed {
            effective_url: url.to_owned(),
            format: ValidatedFeedFormat::Rss,
            has_audio_entries: true,
            publishing_activity: activity.clone(),
            podcast_publishing_activity: activity,
        }
    }
    struct Active(Arc<AtomicUsize>);
    impl Drop for Active {
        fn drop(&mut self) {
            self.0.fetch_sub(1, Ordering::SeqCst);
        }
    }

    #[tokio::test]
    async fn deadline_retains_fast_sibling_and_cancels_slow_work() {
        let budget = PodcastEnrichmentBudget {
            deadline: Instant::now() + Duration::from_millis(100),
            ..PodcastEnrichmentBudget::new(1)
        };
        let active = Arc::new(AtomicUsize::new(0));
        let (results, cache) = enrich_podcast_results_with(
            vec![result("slow"), result("fast")],
            &budget,
            "deadline",
            |url| {
                let active = active.clone();
                async move {
                    active.fetch_add(1, Ordering::SeqCst);
                    let _active = Active(active);
                    if url == "slow" {
                        std::future::pending::<()>().await;
                    }
                    Ok(Some((feed(&url), FeedDiscoveryEvidence::default())))
                }
            },
        )
        .await;
        assert_eq!(results.len(), 2); // Timeout remains unknown.
        assert_eq!(cache.len(), 1);
        assert!(cache.contains_key("fast"));
        assert!(results[1].feed_evidence.is_some());
        assert_eq!(active.load(Ordering::SeqCst), 0);
    }

    #[tokio::test]
    async fn separate_lanes_share_request_and_concurrency_limits() {
        let budget = PodcastEnrichmentBudget::new(1);
        let active = Arc::new(AtomicUsize::new(0));
        let peak = Arc::new(AtomicUsize::new(0));
        let calls = Arc::new(AtomicUsize::new(0));
        let validate = |url: String| {
            let (active, peak, calls) = (active.clone(), peak.clone(), calls.clone());
            async move {
                calls.fetch_add(1, Ordering::SeqCst);
                let count = active.fetch_add(1, Ordering::SeqCst) + 1;
                let _active = Active(active);
                peak.fetch_max(count, Ordering::SeqCst);
                tokio::task::yield_now().await;
                Ok(Some((feed(&url), FeedDiscoveryEvidence::default())))
            }
        };
        let first = (0..20).map(|i| result(&format!("first-{i}"))).collect();
        let second = (0..20).map(|i| result(&format!("second-{i}"))).collect();
        let third = (0..20).map(|i| result(&format!("third-{i}"))).collect();
        let (_, _, _) = tokio::join!(
            enrich_podcast_results_with(first, &budget, "first", &validate),
            enrich_podcast_results_with(second, &budget, "second", &validate),
            enrich_podcast_results_with(third, &budget, "third", &validate)
        );
        assert_eq!(calls.load(Ordering::SeqCst), MAX_FEEDS);
        assert!((2..=MAX_CONCURRENT_FEEDS).contains(&peak.load(Ordering::SeqCst)));
    }

    #[tokio::test]
    async fn duplicate_branch_urls_share_fetches_and_completed_rejections() {
        let budget = PodcastEnrichmentBudget::new(2);
        let calls = AtomicUsize::new(0);
        let validate = |url: String| {
            let calls = &calls;
            async move {
                calls.fetch_add(1, Ordering::SeqCst);
                tokio::task::yield_now().await;
                if url == "invalid" {
                    Ok(None)
                } else {
                    Ok(Some((feed(&url), FeedDiscoveryEvidence::default())))
                }
            }
        };
        let (first, second) = tokio::join!(
            enrich_podcast_results_with(
                vec![result("valid"), result("invalid")],
                &budget,
                "first",
                &validate
            ),
            enrich_podcast_results_with(
                vec![result("valid"), result("invalid")],
                &budget,
                "second",
                &validate
            ),
        );
        let mut results = first.0.into_iter().chain(second.0).collect();
        let cache = budget.apply_to_results(&mut results);
        assert_eq!(calls.load(Ordering::SeqCst), 2);
        assert_eq!(cache.len(), 1);
        assert_eq!(results.len(), 2);
        assert!(
            results
                .iter()
                .all(|item| item.url == "valid" && item.feed_evidence.is_some())
        );
        assert_eq!(budget.per_group_limit, 4);
    }

    #[tokio::test]
    async fn invalid_link_in_web_snippet_does_not_erase_the_grounded_page() {
        let budget = PodcastEnrichmentBudget::new(1);
        let direct = result("https://example.com/sitemap.xml");
        let mut page = result("https://example.com/podcasts");
        page.feed_url = direct.feed_url.clone();
        let (results, cache) =
            enrich_podcast_results_with(vec![direct, page], &budget, "web", |_| async { Ok(None) })
                .await;
        assert!(cache.is_empty());
        assert_eq!(results.len(), 1);
        assert_eq!(results[0].url, "https://example.com/podcasts");
        assert!(results[0].feed_url.is_none());
        assert!(results[0].feed_evidence.is_none());
    }

    #[tokio::test]
    async fn rejects_nonfeeds_and_silent_feeds_but_preserves_transport_unknowns() {
        let (results, cache) = enrich_podcast_results_with(
            vec![result("invalid"), result("silent"), result("outage")],
            &PodcastEnrichmentBudget::new(1),
            "test",
            |url| async move {
                match url.as_str() {
                    "invalid" => Ok(None),
                    "silent" => {
                        let mut value = feed(&url);
                        value.has_audio_entries = false;
                        Ok(Some((value, FeedDiscoveryEvidence::default())))
                    }
                    _ => Err(FeedValidationError::Deadline),
                }
            },
        )
        .await;
        assert_eq!(
            results.into_iter().map(|r| r.url).collect::<Vec<_>>(),
            vec!["outage"]
        );
        assert!(cache.is_empty());
    }
}
