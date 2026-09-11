use super::*;

#[test]
fn unique_keys_are_normalized_and_suffixed() {
    let mut used = HashSet::from(["news-public-infrastructure".to_owned()]);
    assert_eq!(
        unique_lens_key("News-Public Infrastructure", &mut used),
        "news-public-infrastructure-2"
    );
}

#[test]
fn centroid_model_change_resets_instead_of_blending() {
    let lens = BriefingSemanticLens {
        id: 1,
        key: "news-ai".to_owned(),
        title: "AI".to_owned(),
        deck: "Artificial intelligence systems.".to_owned(),
        position: 2,
        centroid: Some(vec![1.0, 0.0]),
        centroid_weight: 20,
        centroid_model: Some("openrouter:old".to_owned()),
        routing_rule: None,
        updated_at: Utc::now(),
    };
    let mut working = WorkingLens::existing(&lens, &[0.5, 0.5], "openrouter:new", 2);
    working.update_centroid(&[0.0, 1.0], 32, "openrouter:new");
    assert_eq!(working.centroid, Some(vec![0.0, 1.0]));
    assert_eq!(working.centroid_weight, 1);
    assert_eq!(working.similarity_vector, vec![0.5, 0.5]);
}

#[test]
fn routing_keeps_the_first_lens_on_equal_similarity() {
    let lenses = vec![
        WorkingLens::new(
            "news-first".to_owned(),
            "First".to_owned(),
            "First semantic category.".to_owned(),
            2,
            vec![1.0, 0.0],
            1,
            "openrouter:model",
        ),
        WorkingLens::new(
            "news-second".to_owned(),
            "Second".to_owned(),
            "Second semantic category.".to_owned(),
            3,
            vec![1.0, 0.0],
            1,
            "openrouter:model",
        ),
    ];
    assert_eq!(best_lens(&[1.0, 0.0], &lenses), Some((0, 1.0)));
}

#[test]
fn greedy_clustering_matches_similarity_boundary() {
    let pending = |id| BriefingUnassignedSource {
        pending_id: id,
        source_kind: "news".to_owned(),
        source_id: id,
        enqueued_at: Utc::now(),
        source: newsly_db::BriefingRefreshSource {
            source_key: format!("news:{id}"),
            kind: "news".to_owned(),
            id,
            title: format!("Source {id}"),
            source_name: None,
            summary: None,
            key_points: Vec::new(),
            url: None,
            image_url: None,
            thumbnail_url: None,
            published_at: None,
            briefing_context: None,
        },
    };
    let clusters = cluster_candidates(
        vec![
            Candidate {
                pending: pending(1),
                vector: vec![1.0, 0.0],
            },
            Candidate {
                pending: pending(2),
                vector: vec![0.99, 0.01],
            },
            Candidate {
                pending: pending(3),
                vector: vec![0.0, 1.0],
            },
        ],
        0.9,
    );
    assert_eq!(clusters.len(), 2);
    assert_eq!(clusters[0].candidates.len(), 2);
}
