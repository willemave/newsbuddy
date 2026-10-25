use super::*;
fn row(title: &str, artist: &str) -> ItunesPodcast {
    serde_json::from_value(serde_json::json!({"collectionName":title,"artistName":artist})).unwrap()
}
#[test]
fn named_podcast_keeps_host_identity() {
    let ours = row(
        "More or Less",
        "Dave Morin, Jessica Lessin, Brit Morin, and Sam Lessin",
    );
    let bbc = row("More or Less", "BBC Radio 4");
    let hosts = vec!["Sam Lessin".to_owned()];
    assert!(named_podcast_matches("More or Less", &hosts, &ours));
    assert!(!named_podcast_matches("More or Less", &hosts, &bbc));
    assert!(named_podcast_matches(
        "More or Less Sam Lessin podcast",
        &[],
        &ours
    ));
    assert!(!named_podcast_matches(
        "More or Less Sam Lessin podcast",
        &[],
        &bbc
    ));
}
#[test]
fn unrelated_named_sources_do_not_match() {
    assert!(!publication_matches("More or Less", "Podnews Weekly"));
}
fn audio_feed(url: &str) -> crate::ValidatedFeed {
    let now = chrono::Utc::now();
    let activity = crate::FeedPublishingActivity {
        checked_at: now,
        window_start: now,
        year_window_start: now,
        window_end: now,
        latest_item: None,
        observed_count: Some(1),
        observed_year_count: Some(1),
        undated_count: 0,
    };
    crate::ValidatedFeed {
        effective_url: url.into(),
        format: crate::ValidatedFeedFormat::Rss,
        has_audio_entries: true,
        publishing_activity: activity.clone(),
        podcast_publishing_activity: activity,
    }
}
#[test]
fn explicit_publication_with_audio_stays_a_publication_and_redirect_duplicates_collapse() {
    let mut resolution = SourceResolution::default();
    add_validated(
        &mut resolution,
        Candidate {
            title: "Newsletter".into(),
            site_url: "https://example.com".into(),
            feed_url: "http://example.com/feed".into(),
            kind: Some(SourceKind::Publication),
            author: None,
            name_query: None,
        },
        audio_feed("https://example.com/feed"),
    );
    add_validated(
        &mut resolution,
        Candidate {
            title: "Newsletter".into(),
            site_url: "https://example.com".into(),
            feed_url: "https://example.com/feed".into(),
            kind: Some(SourceKind::Publication),
            author: None,
            name_query: None,
        },
        audio_feed("https://example.com/feed"),
    );
    assert_eq!(resolution.feeds.len(), 1);
    assert_eq!(resolution.feeds[0].kind, SourceKind::Publication);
}
#[test]
fn third_party_named_page_cannot_supply_its_own_unrelated_feed() {
    let mut candidate = Candidate {
        title: "Stratechery review".into(),
        site_url: "https://reviews.example/review".into(),
        feed_url: "https://reviews.example/rss".into(),
        kind: Some(SourceKind::Publication),
        author: None,
        name_query: Some("Stratechery".into()),
    };
    assert!(!candidate_matches_identity(
        &candidate,
        Some("Review site updates"),
        &[],
        &[]
    ));
    assert!(candidate_matches_identity(
        &candidate,
        Some("Stratechery by Ben Thompson"),
        &[],
        &[]
    ));
    assert!(!candidate_matches_identity(&candidate, None, &[], &[]));
    candidate.name_query = None;
    assert!(
        candidate_matches_identity(&candidate, Some("Review site updates"), &[], &[]),
        "an explicit feed URL has its own identity"
    );
}
#[derive(Default)]
struct StaleDirectory {
    events: std::sync::Mutex<Vec<String>>,
}
impl SourceLookup for StaleDirectory {
    async fn directory(&self, _: &str, _: bool) -> Result<Vec<ItunesPodcast>, ()> {
        self.events.lock().unwrap().push("directory".into());
        Ok(vec![serde_json::from_value(serde_json::json!({
                "collectionName": "My Show", "artistName": "My Host",
                "feedUrl": "https://old.example/feed", "collectionViewUrl": "https://podcasts.apple.com/id123"
            })).unwrap()])
    }
    async fn search(&self, _: &str) -> (Vec<(String, String)>, ExaSearchUsage, bool) {
        self.events.lock().unwrap().push("search".into());
        (
            vec![("My Show".into(), "https://new.example/feed".into())],
            ExaSearchUsage::default(),
            true,
        )
    }
    async fn page(&self, _: &str) -> Result<PublicWebPage, ()> {
        panic!("feed succeeded")
    }
    async fn validate(&self, url: &str) -> Validation {
        self.events.lock().unwrap().push(format!("validate:{url}"));
        if url.contains("old.example") {
            return Ok(None);
        }
        let mut evidence = FeedDiscoveryEvidence::default();
        evidence.title = Some("My Show".into());
        evidence.authors = vec!["My Host".into()];
        Ok(Some((audio_feed(url), evidence)))
    }
}
#[tokio::test]
async fn stale_directory_feed_does_not_suppress_web_fallback() {
    let lookup = StaleDirectory::default();
    let result = resolve_with(
        &lookup,
        "My Show",
        Some(SourceKind::Podcast),
        &["My Host".into()],
    )
    .await
    .unwrap();
    assert_eq!(result.status, SourceResolutionStatus::Found);
    assert_eq!(
        result.feeds[0].feed.effective_url,
        "https://new.example/feed"
    );
    assert_eq!(
        *lookup.events.lock().unwrap(),
        vec![
            "directory",
            "validate:https://old.example/feed",
            "search",
            "validate:https://new.example/feed"
        ]
    );
}
#[tokio::test]
async fn validation_cache_is_shared_across_candidate_stages() {
    let lookup = StaleDirectory::default();
    let mut cache = HashMap::new();
    let mut result = SourceResolution::default();
    for _ in 0..2 {
        let mut candidates = vec![Candidate {
            title: "Old".into(),
            site_url: "https://old.example".into(),
            feed_url: "https://old.example/feed".into(),
            kind: None,
            author: None,
            name_query: None,
        }];
        validate_candidates(&lookup, &mut candidates, &[], &mut cache, &mut result).await;
    }
    assert!(result.feeds.is_empty());
    assert_eq!(lookup.events.lock().unwrap().len(), 1);
}

#[derive(Default)]
struct ExhaustedValidationBudget {
    checked: std::sync::Mutex<Vec<String>>,
}

impl SourceLookup for ExhaustedValidationBudget {
    async fn directory(&self, _: &str, _: bool) -> Result<Vec<ItunesPodcast>, ()> {
        panic!("publication resolution does not use the podcast directory")
    }

    async fn search(&self, _: &str) -> (Vec<(String, String)>, ExaSearchUsage, bool) {
        (
            (1..=3)
                .map(|index| {
                    (
                        "My Publication".into(),
                        format!("https://publisher{index}.example"),
                    )
                })
                .collect(),
            ExaSearchUsage::default(),
            true,
        )
    }

    async fn page(&self, url: &str) -> Result<PublicWebPage, ()> {
        Ok(PublicWebPage {
            url: url.into(),
            title: Some("My Publication".into()),
            text: String::new(),
            links: Vec::new(),
            truncated: false,
            feed_urls: (1..=7).map(|index| format!("{url}/feed{index}")).collect(),
        })
    }

    async fn validate(&self, url: &str) -> Validation {
        self.checked.lock().unwrap().push(url.into());
        if url == "https://publisher3.example" {
            let mut evidence = FeedDiscoveryEvidence::default();
            evidence.title = Some("My Publication".into());
            return Ok(Some((audio_feed(url), evidence)));
        }
        Ok(None)
    }
}

#[tokio::test]
async fn exhausted_validation_budget_reports_unavailable_for_unchecked_web_candidate() {
    let lookup = ExhaustedValidationBudget::default();
    let result = resolve_with(
        &lookup,
        "My Publication",
        Some(SourceKind::Publication),
        &[],
    )
    .await
    .unwrap();
    assert_eq!(result.status, SourceResolutionStatus::Unavailable);
    assert!(result.feeds.is_empty());
    assert!(
        result
            .limitations
            .iter()
            .any(|limitation| limitation == "Feed validation budget exhausted")
    );
    let checked = lookup.checked.lock().unwrap();
    assert_eq!(checked.len(), 16);
    assert!(
        !checked
            .iter()
            .any(|url| url == "https://publisher3.example")
    );
}

#[tokio::test]
async fn candidate_batch_limit_reports_unchecked_candidates() {
    let lookup = ExhaustedValidationBudget::default();
    let mut candidates = (1..=9)
        .map(|index| Candidate {
            title: "My Publication".into(),
            site_url: "https://publisher.example".into(),
            feed_url: format!("https://publisher.example/feed{index}"),
            kind: Some(SourceKind::Publication),
            author: None,
            name_query: None,
        })
        .collect();
    let mut result = SourceResolution::default();
    validate_candidates(
        &lookup,
        &mut candidates,
        &[],
        &mut HashMap::new(),
        &mut result,
    )
    .await;
    assert!(
        result
            .limitations
            .iter()
            .any(|limitation| limitation == "Candidate feed check limit reached")
    );
    assert_eq!(lookup.checked.lock().unwrap().len(), 8);
}
