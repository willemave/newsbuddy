use chrono::{DateTime, NaiveDate, Utc};
use newsly_db::news_category_reclustering::{NewsCategorySnapshot, NewsCategoryStory};
use newsly_db::{BriefingRefreshSource, BriefingSemanticLens};
use newsly_domain::plan_news_lens_reclustering;
use newsly_providers::{BriefingLensNamingBatch, BriefingLensNamingResult};

use super::planning;

fn date(day: u32) -> NaiveDate {
    NaiveDate::from_ymd_opt(2026, 9, day).expect("valid test date")
}

fn snapshot(story_count: i64) -> NewsCategorySnapshot {
    let cutoff = DateTime::parse_from_rfc3339("2026-09-20T10:00:00Z")
        .expect("valid timestamp")
        .with_timezone(&Utc);
    NewsCategorySnapshot {
        user_id: 7,
        cutoff,
        model: "test-embedding".to_owned(),
        stories: (1..=story_count)
            .map(|id| NewsCategoryStory {
                source: BriefingRefreshSource {
                    source_key: format!("news:{id}"),
                    kind: "news".to_owned(),
                    id,
                    title: format!("Coherent story {id}"),
                    source_name: Some("Example".to_owned()),
                    summary: Some("The same durable topic recurs.".to_owned()),
                    key_points: Vec::new(),
                    url: None,
                    image_url: None,
                    thumbnail_url: None,
                    published_at: Some(cutoff),
                    briefing_context: None,
                },
                event_id: id,
                available_at: cutoff,
                vector: vec![1.0, 0.0],
            })
            .collect(),
        lenses: Vec::new(),
        all_keys: Vec::new(),
        pending: Vec::new(),
        fingerprint: "snapshot".to_owned(),
        corpus_hash: "corpus".to_owned(),
    }
}

fn basis(dimensions: usize, index: usize) -> Vec<f64> {
    let mut vector = vec![0.0; dimensions];
    vector[index] = 1.0;
    vector
}

fn full_cap_snapshot() -> NewsCategorySnapshot {
    let mut snapshot = snapshot(0);
    let mut id = 1;
    for topic in 0..10 {
        for _ in 0..3 {
            snapshot.stories.push(NewsCategoryStory {
                source: BriefingRefreshSource {
                    source_key: format!("news:{id}"),
                    kind: "news".to_owned(),
                    id,
                    title: format!("Topic {topic} story {id}"),
                    source_name: Some("Example".to_owned()),
                    summary: Some("A recurring topic.".to_owned()),
                    key_points: Vec::new(),
                    url: None,
                    image_url: None,
                    thumbnail_url: None,
                    published_at: Some(snapshot.cutoff),
                    briefing_context: None,
                },
                event_id: id,
                available_at: snapshot.cutoff,
                vector: basis(11, if topic == 9 { 10 } else { topic }),
            });
            id += 1;
        }
    }
    snapshot.lenses = (0..10)
        .map(|index| BriefingSemanticLens {
            id: 100 + i64::try_from(index).expect("bounded lens index"),
            key: format!("published-{index}"),
            title: format!("Published {index}"),
            deck: "Existing category".to_owned(),
            position: i32::try_from(index).expect("bounded lens position"),
            centroid: Some(basis(11, index)),
            centroid_weight: 3,
            centroid_model: Some("openrouter:test-embedding".to_owned()),
            routing_rule: Some("Existing topic".to_owned()),
            updated_at: snapshot.cutoff,
        })
        .collect();
    snapshot
}

#[test]
fn unchanged_new_cluster_keeps_its_key_and_reaches_two_nights() {
    let snapshot = snapshot(30);
    let first_plan =
        plan_news_lens_reclustering(&planning::input(&snapshot, None)).expect("first fit succeeds");
    let first = planning::track(&snapshot, None, first_plan, date(20), 101);
    assert_eq!(first.clusters.len(), 1);
    assert_eq!(first.clusters[0].supported_nights, 1);
    assert!(!first.clusters[0].published);

    let second_plan = plan_news_lens_reclustering(&planning::input(&snapshot, Some(&first)))
        .expect("second fit succeeds");
    let second = planning::track(&snapshot, Some(&first), second_plan, date(21), 102);
    assert_eq!(second.clusters.len(), 1);
    assert_eq!(
        second.clusters[0].cluster.plan_id,
        first.clusters[0].cluster.plan_id
    );
    assert_eq!(second.clusters[0].supported_nights, 2);

    let request = planning::naming_request(&snapshot, &second).expect("request is bounded");
    assert_eq!(request.categories.len(), 1);
    assert_eq!(
        request.categories[0].stable_id,
        second.clusters[0].cluster.plan_id
    );
    assert_eq!(request.categories[0].stories.len(), 25);
}

#[test]
fn publication_requires_exact_candidate_ids() {
    let snapshot = snapshot(6);
    let first_plan =
        plan_news_lens_reclustering(&planning::input(&snapshot, None)).expect("first fit succeeds");
    let first = planning::track(&snapshot, None, first_plan, date(20), 201);
    let second_plan = plan_news_lens_reclustering(&planning::input(&snapshot, Some(&first)))
        .expect("second fit succeeds");
    let second = planning::track(&snapshot, Some(&first), second_plan, date(21), 202);

    let result = BriefingLensNamingResult {
        stable_id: second.clusters[0].cluster.plan_id.clone(),
        title: "Durable topic".to_owned(),
        deck: "A recurring subject in recent news.".to_owned(),
        routing_rule: "Stories about the durable recurring subject.".to_owned(),
    };
    let publication = planning::publication(
        &second,
        &BriefingLensNamingBatch {
            categories: vec![result.clone()],
        },
    )
    .expect("known id publishes");
    assert_eq!(publication.len(), 1);
    assert_eq!(publication[0].key, second.clusters[0].cluster.plan_id);

    let mut unknown = result;
    unknown.stable_id = "news-nightly-unknown".to_owned();
    assert!(
        planning::publication(
            &second,
            &BriefingLensNamingBatch {
                categories: vec![unknown],
            },
        )
        .is_err()
    );
}

#[test]
fn repeated_rows_from_one_canonical_event_do_not_satisfy_support() {
    let mut snapshot = snapshot(6);
    for story in &mut snapshot.stories {
        story.event_id = 1;
    }
    let plan =
        plan_news_lens_reclustering(&planning::input(&snapshot, None)).expect("fit succeeds");
    assert!(plan.clusters.is_empty());
    assert_eq!(plan.mixed_story_ids.len(), 6);
}

#[test]
fn unchanged_fast_path_requires_the_exact_current_publication() {
    let mut snapshot = snapshot(6);
    let first_plan =
        plan_news_lens_reclustering(&planning::input(&snapshot, None)).expect("first fit succeeds");
    let first = planning::track(&snapshot, None, first_plan, date(20), 301);
    let second_plan = plan_news_lens_reclustering(&planning::input(&snapshot, Some(&first)))
        .expect("second fit succeeds");
    let mut second = planning::track(&snapshot, Some(&first), second_plan, date(21), 302);
    second.clusters[0].published = true;
    let names = BriefingLensNamingBatch {
        categories: vec![BriefingLensNamingResult {
            stable_id: second.clusters[0].cluster.plan_id.clone(),
            title: "Durable topic".to_owned(),
            deck: "A recurring subject in recent news.".to_owned(),
            routing_rule: "Stories about the durable recurring subject.".to_owned(),
        }],
    };
    snapshot.lenses = vec![BriefingSemanticLens {
        id: 9,
        key: second.clusters[0].cluster.plan_id.clone(),
        title: names.categories[0].title.clone(),
        deck: names.categories[0].deck.clone(),
        position: 2,
        centroid: Some(second.clusters[0].cluster.center.clone()),
        centroid_weight: 6,
        centroid_model: Some("openrouter:test-embedding".to_owned()),
        routing_rule: Some(names.categories[0].routing_rule.clone()),
        updated_at: snapshot.cutoff,
    }];
    assert!(planning::current_publication_matches(
        &snapshot, &second, &names
    ));

    snapshot.lenses[0].centroid.as_mut().unwrap()[0] = 0.9;
    assert!(!planning::current_publication_matches(
        &snapshot, &second, &names
    ));
    snapshot.lenses[0].centroid = Some(second.clusters[0].cluster.center.clone());
    snapshot.lenses[0].title = "Operator changed title".to_owned();
    assert!(!planning::current_publication_matches(
        &snapshot, &second, &names
    ));
}

#[test]
fn full_cap_candidate_survives_shadow_night_and_publishes_second_night() {
    let snapshot = full_cap_snapshot();
    let first_plan = plan_news_lens_reclustering(&planning::input(&snapshot, None))
        .expect("first full-cap fit succeeds");
    let first = planning::track(&snapshot, None, first_plan, date(20), 401);
    let first_candidate = first
        .clusters
        .iter()
        .find(|cluster| !cluster.published)
        .expect("novel supported topic becomes a candidate");
    assert_eq!(first_candidate.supported_nights, 1);
    let candidate_key = first_candidate.cluster.plan_id.clone();
    assert_eq!(
        planning::naming_request(&snapshot, &first)
            .expect("first request is bounded")
            .categories
            .len(),
        9
    );

    let second_input = planning::input(&snapshot, Some(&first));
    assert_eq!(second_input.existing_clusters.len(), 10);
    assert!(
        second_input
            .existing_clusters
            .iter()
            .any(|cluster| cluster.stable_id == candidate_key)
    );
    assert!(
        second_input
            .existing_clusters
            .iter()
            .all(|cluster| cluster.stable_id != "published-9")
    );
    let second_plan =
        plan_news_lens_reclustering(&second_input).expect("second full-cap fit succeeds");
    let second = planning::track(&snapshot, Some(&first), second_plan, date(21), 402);
    let persisted = second
        .clusters
        .iter()
        .find(|cluster| cluster.cluster.plan_id == candidate_key)
        .expect("candidate identity survives the published bootstrap cap");
    assert_eq!(persisted.supported_nights, 2);

    let request = planning::naming_request(&snapshot, &second).expect("request is bounded");
    assert_eq!(request.categories.len(), 10);
    assert!(
        request
            .categories
            .iter()
            .any(|category| category.stable_id == candidate_key)
    );
    let names = BriefingLensNamingBatch {
        categories: request
            .categories
            .iter()
            .map(|category| BriefingLensNamingResult {
                stable_id: category.stable_id.clone(),
                title: format!("{} title", category.stable_id),
                deck: "Durable category".to_owned(),
                routing_rule: "Matching recurring stories".to_owned(),
            })
            .collect(),
    };
    let publication =
        planning::publication(&second, &names).expect("second-night candidate can publish");
    assert_eq!(publication.len(), 10);
    assert!(publication.iter().any(|lens| lens.key == candidate_key));
}
