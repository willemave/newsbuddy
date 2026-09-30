use std::collections::HashSet;

use super::*;

fn vector(x: f64, y: f64) -> Vec<f64> {
    let mut value = vec![0.0; 8];
    value[0] = x;
    value[1] = y;
    value
}

fn basis(dimensions: usize, index: usize) -> Vec<f64> {
    let mut value = vec![0.0; dimensions];
    value[index] = 1.0;
    value
}

fn story(id: &str, event: &str, at: i64, x: f64, y: f64) -> NewsLensReclusterStory {
    NewsLensReclusterStory {
        story_id: id.into(),
        event_id: event.into(),
        available_at: at,
        vector: vector(x, y),
    }
}

#[test]
fn weak_and_under_supported_stories_stay_mixed_without_changing_centers() {
    let input = NewsLensReclusteringInput {
        now: 10,
        stories: vec![
            story("a", "e1", 1, 1.0, 0.0),
            story("b", "e2", 2, 1.0, 0.0),
            story("c", "e3", 3, 1.0, 0.0),
            story("weak", "e4", 4, -1.0, 0.0),
        ],
        existing_clusters: vec![NewsLensReclusterExistingCluster {
            stable_id: "tech".into(),
            center: vector(1.0, 0.0),
            member_story_ids: vec![],
        }],
        config: NewsLensReclusteringConfig {
            max_semantic_clusters: 1,
            ..Default::default()
        },
    };
    let plan = plan_news_lens_reclustering(&input).unwrap();
    assert_eq!(plan.clusters.len(), 1);
    assert!((plan.clusters[0].center[0] - 1.0).abs() < 1e-12);
    assert_eq!(plan.mixed_story_ids, vec!["weak"]);
}

#[test]
fn optimal_identity_prefers_total_overlap_and_preserves_stable_ids() {
    let input = NewsLensReclusteringInput {
        now: 10,
        stories: vec![
            story("a", "e1", 1, 1.0, 0.0),
            story("b", "e2", 2, 1.0, 0.0),
            story("c", "e3", 3, 1.0, 0.0),
            story("d", "e4", 4, 0.0, 1.0),
            story("e", "e5", 5, 0.0, 1.0),
            story("f", "e6", 6, 0.0, 1.0),
        ],
        existing_clusters: vec![
            NewsLensReclusterExistingCluster {
                stable_id: "x".into(),
                center: vector(1.0, 0.0),
                member_story_ids: vec!["a".into(), "b".into()],
            },
            NewsLensReclusterExistingCluster {
                stable_id: "y".into(),
                center: vector(0.0, 1.0),
                member_story_ids: vec!["d".into(), "e".into()],
            },
        ],
        config: NewsLensReclusteringConfig {
            max_semantic_clusters: 2,
            ..Default::default()
        },
    };
    let plan = plan_news_lens_reclustering(&input).unwrap();
    assert_eq!(
        plan.clusters
            .iter()
            .map(|cluster| cluster.plan_id.as_str())
            .collect::<HashSet<_>>(),
        HashSet::from(["x", "y"])
    );
}

#[test]
fn future_and_duplicate_events_do_not_create_supported_topics() {
    let input = NewsLensReclusteringInput {
        now: 10,
        stories: vec![
            story("a", "same", 1, 1.0, 0.0),
            story("b", "same", 2, 1.0, 0.0),
            story("future", "e3", 11, 1.0, 0.0),
        ],
        existing_clusters: Vec::new(),
        config: NewsLensReclusteringConfig {
            max_semantic_clusters: 1,
            ..Default::default()
        },
    };
    let plan = plan_news_lens_reclustering(&input).unwrap();
    assert!(plan.clusters.is_empty());
    assert_eq!(plan.mixed_story_ids.len(), 2);
}

#[test]
fn full_warm_cap_reseeds_for_an_entirely_novel_corpus() {
    let mut stories = Vec::new();
    for topic in 0..10 {
        for event in 0..3 {
            stories.push(NewsLensReclusterStory {
                story_id: format!("story-{topic}-{event}"),
                event_id: format!("event-{topic}-{event}"),
                available_at: i64::from(topic * 3 + event),
                vector: basis(
                    20,
                    10 + usize::try_from(topic).expect("bounded topic index"),
                ),
            });
        }
    }
    let input = NewsLensReclusteringInput {
        now: 100,
        stories,
        existing_clusters: (0..10)
            .map(|index| NewsLensReclusterExistingCluster {
                stable_id: format!("old-{index}"),
                center: basis(20, index),
                member_story_ids: Vec::new(),
            })
            .collect(),
        config: NewsLensReclusteringConfig::default(),
    };

    let plan = plan_news_lens_reclustering(&input).expect("novel partition fits");

    assert_eq!(plan.clusters.len(), 10);
    assert_eq!(plan.diagnostics.births, 10);
    assert!(plan.mixed_story_ids.is_empty());
    assert!(
        plan.clusters
            .iter()
            .all(|cluster| cluster.distinct_event_count == 3)
    );
}

#[test]
fn duplicate_event_noise_does_not_reseed_and_terminates() {
    let mut stories = Vec::new();
    for topic in 0..9 {
        for event in 0..3 {
            stories.push(NewsLensReclusterStory {
                story_id: format!("stable-{topic}-{event}"),
                event_id: format!("stable-event-{topic}-{event}"),
                available_at: i64::from(topic * 3 + event),
                vector: basis(13, usize::try_from(topic).expect("bounded topic index")),
            });
        }
    }
    for noise in 0..3 {
        for duplicate in 0..4 {
            stories.push(NewsLensReclusterStory {
                story_id: format!("noise-{noise}-{duplicate}"),
                event_id: format!("noise-event-{noise}"),
                available_at: 30 + i64::from(noise * 4 + duplicate),
                vector: basis(
                    13,
                    10 + usize::try_from(noise).expect("bounded noise index"),
                ),
            });
        }
    }
    let input = NewsLensReclusteringInput {
        now: 100,
        stories,
        existing_clusters: (0..10)
            .map(|index| NewsLensReclusterExistingCluster {
                stable_id: format!("old-{index}"),
                center: basis(13, index),
                member_story_ids: Vec::new(),
            })
            .collect(),
        config: NewsLensReclusteringConfig::default(),
    };

    let plan = plan_news_lens_reclustering(&input).expect("bounded fit terminates");

    assert_eq!(plan.clusters.len(), 9);
    assert_eq!(plan.mixed_story_ids.len(), 12);
    assert!(
        plan.clusters
            .iter()
            .all(|cluster| !matches!(cluster.identity, NewsLensClusterIdentity::New))
    );
}
