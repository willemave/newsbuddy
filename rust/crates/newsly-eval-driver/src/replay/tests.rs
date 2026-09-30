use std::collections::HashMap;

use serde_json::Value;

use super::*;

fn item(id: &str, at: i64, vector: &[f64]) -> ReplayItem {
    ReplayItem {
        id: id.into(),
        available_at: at,
        vector: vector.to_vec(),
        title: String::new(),
        event_id: String::new(),
    }
}

fn variant(algorithm: ReplayAlgorithm) -> ReplayVariant {
    ReplayVariant {
        label: "test".into(),
        algorithm,
        lookback_days: 7,
        k: 2,
        warm_start: true,
        decay_half_life_days: None,
        seed: 7,
        max_iterations: 10,
        min_cluster_size: 2,
        match_threshold: 0.8,
        absorb_threshold: 0.4,
        identity_similarity: 0.7,
        confidence_threshold: None,
    }
}

fn without_timings(response: &ReplayNewsLensesResponse) -> Value {
    let mut value = serde_json::to_value(response).unwrap();
    for run in value["variants"].as_array_mut().unwrap() {
        for checkpoint in run["checkpoints"].as_array_mut().unwrap() {
            checkpoint["metrics"]
                .as_object_mut()
                .unwrap()
                .remove("elapsed_ms");
        }
    }
    value
}

#[test]
fn future_items_cannot_change_prior_checkpoints() {
    let base = ReplayNewsLensesRequest {
        schema_version: 1,
        items: vec![item("a", 0, &[1.0, 0.0]), item("b", 1, &[0.9, 0.1])],
        checkpoints: vec![1],
        variants: vec![variant(ReplayAlgorithm::RollingSphericalKmeans)],
        max_items: 10,
    };
    let mut with_future = base.clone();
    with_future.items.push(item("future", 100, &[0.0, 1.0]));
    let mut changed_future = with_future.clone();
    changed_future.items.last_mut().unwrap().vector = vec![-1.0, 0.0];
    assert_eq!(
        without_timings(&replay_news_lenses(&base).unwrap()),
        without_timings(&replay_news_lenses(&with_future).unwrap())
    );
    assert_eq!(
        without_timings(&replay_news_lenses(&with_future).unwrap()),
        without_timings(&replay_news_lenses(&changed_future).unwrap())
    );
}

#[test]
fn deterministic_repetition_differs_only_in_timings() {
    let request = ReplayNewsLensesRequest {
        schema_version: 1,
        items: vec![item("a", 0, &[1.0, 0.0]), item("b", 1, &[0.0, 1.0])],
        checkpoints: vec![1],
        variants: vec![variant(ReplayAlgorithm::RollingSphericalKmeans)],
        max_items: 10,
    };
    assert_eq!(
        without_timings(&replay_news_lenses(&request).unwrap()),
        without_timings(&replay_news_lenses(&request).unwrap())
    );
}

#[test]
fn frozen_initial_waits_for_first_nonempty_checkpoint() {
    let request = ReplayNewsLensesRequest {
        schema_version: 1,
        items: vec![item("a", 10, &[1.0, 0.0]), item("b", 11, &[0.9, 0.1])],
        checkpoints: vec![1, 11],
        variants: vec![variant(ReplayAlgorithm::FrozenInitial)],
        max_items: 10,
    };
    let response = replay_news_lenses(&request).unwrap();
    assert_eq!(response.variants[0].checkpoints[0].metrics.cluster_count, 0);
    assert!(response.variants[0].checkpoints[1].metrics.cluster_count > 0);
}

#[test]
fn frozen_initial_uses_fitted_centers() {
    let mut config = variant(ReplayAlgorithm::FrozenInitial);
    config.k = 1;
    let request = ReplayNewsLensesRequest {
        schema_version: 1,
        items: vec![item("x", 0, &[1.0, 0.0]), item("y", 0, &[0.0, 1.0])],
        checkpoints: vec![0],
        variants: vec![config],
        max_items: 10,
    };
    let response = replay_news_lenses(&request).unwrap();
    assert!(
        response.variants[0].checkpoints[0]
            .assignments
            .iter()
            .all(|assignment| assignment.cosine_to_center.unwrap() > 0.7)
    );
}

#[test]
fn centroid_identity_continues_across_disjoint_windows() {
    let mut config = variant(ReplayAlgorithm::FrozenInitial);
    config.lookback_days = 1;
    config.k = 1;
    let request = ReplayNewsLensesRequest {
        schema_version: 1,
        items: vec![
            item("old", 0, &[1.0, 0.0]),
            item("new", 2 * DAY_SECONDS, &[1.0, 0.0]),
        ],
        checkpoints: vec![0, 2 * DAY_SECONDS],
        variants: vec![config],
        max_items: 10,
    };
    let response = replay_news_lenses(&request).unwrap();
    let checkpoints = &response.variants[0].checkpoints;
    assert_eq!(
        checkpoints[0].clusters[0].cluster_key,
        checkpoints[1].clusters[0].cluster_key
    );
    assert_eq!(checkpoints[1].metrics.births, 0);
    assert_eq!(checkpoints[1].metrics.deaths, 0);
}

#[test]
fn prequential_fit_uses_prior_fitted_centers_only() {
    let mut config = variant(ReplayAlgorithm::RollingSphericalKmeans);
    config.k = 1;
    let request = ReplayNewsLensesRequest {
        schema_version: 1,
        items: vec![
            item("a", 0, &[1.0, 0.0]),
            item("b", 1, &[1.0, 0.0]),
            item("new", 2, &[0.0, 1.0]),
        ],
        checkpoints: vec![1, 2],
        variants: vec![config],
        max_items: 10,
    };
    let response = replay_news_lenses(&request).unwrap();
    let metrics = &response.variants[0].checkpoints[1].metrics;
    assert_eq!(metrics.new_arrival_count, 1);
    assert!(metrics.pre_recluster_mean_cosine.unwrap().abs() < 1e-12);
}

#[test]
fn noise_and_window_cap_retain_every_seen_source() {
    let mut config = variant(ReplayAlgorithm::RollingSphericalKmeans);
    config.k = 1;
    config.confidence_threshold = Some(0.99);
    let request = ReplayNewsLensesRequest {
        schema_version: 1,
        items: vec![
            item("a", 0, &[1.0, 0.0]),
            item("b", 1, &[0.0, 1.0]),
            item("c", 2, &[-1.0, 0.0]),
            item("d", 2, &[0.0, -1.0]),
        ],
        checkpoints: vec![0, 2],
        variants: vec![config],
        max_items: 2,
    };
    let response = replay_news_lenses(&request).unwrap();
    let second = &response.variants[0].checkpoints[1];
    assert_eq!(second.metrics.n_seen, 4);
    assert_eq!(second.metrics.n_window, 2);
    assert_eq!(second.assignments.len(), 4);
    assert_eq!(second.metrics.new_arrival_count, 3);
    let ids = second
        .assignments
        .iter()
        .map(|assignment| assignment.item_id.as_str())
        .collect::<std::collections::HashSet<_>>();
    assert_eq!(ids, std::collections::HashSet::from(["a", "b", "c", "d"]));
}

#[test]
fn incremental_training_respects_window_cap() {
    let mut config = variant(ReplayAlgorithm::IncrementalSurrogate);
    config.k = 1;
    config.min_cluster_size = 1;
    let base = ReplayNewsLensesRequest {
        schema_version: 1,
        items: vec![item("a", 0, &[1.0, 0.0]), item("z", 1, &[0.9, 0.1])],
        checkpoints: vec![0, 1],
        variants: vec![config.clone()],
        max_items: 1,
    };
    let with_excluded = ReplayNewsLensesRequest {
        schema_version: 1,
        items: vec![
            item("a", 0, &[1.0, 0.0]),
            item("b", 1, &[0.0, 1.0]),
            item("z", 1, &[0.9, 0.1]),
        ],
        checkpoints: vec![0, 1],
        variants: vec![config],
        max_items: 1,
    };
    let base_response = replay_news_lenses(&base).unwrap();
    let excluded_response = replay_news_lenses(&with_excluded).unwrap();
    let active_similarity = |response: &ReplayNewsLensesResponse| {
        response.variants[0].checkpoints[1]
            .assignments
            .iter()
            .find(|assignment| assignment.item_id == "z")
            .unwrap()
            .cosine_to_center
            .unwrap()
    };
    assert!(
        (active_similarity(&base_response) - active_similarity(&excluded_response)).abs() < 1e-12
    );
    assert_eq!(
        excluded_response.variants[0].checkpoints[1]
            .assignments
            .len(),
        3
    );
}

#[test]
fn final_kmeans_labels_match_final_centers() {
    let mut config = variant(ReplayAlgorithm::RollingSphericalKmeans);
    config.max_iterations = 1;
    let items = vec![
        WorkItem {
            id: "a".into(),
            event_id: "a".into(),
            available_at: 0,
            vector: vec![1.0, 0.0],
        },
        WorkItem {
            id: "b".into(),
            event_id: "b".into(),
            available_at: 0,
            vector: vec![0.8, 0.2],
        },
        WorkItem {
            id: "c".into(),
            event_id: "c".into(),
            available_at: 0,
            vector: vec![0.0, 1.0],
        },
    ];
    let raw = spherical_kmeans(&items, &[0, 1, 2], 0, &config, None);
    for (position, item_index) in [0, 1, 2].into_iter().enumerate() {
        assert_eq!(
            raw.labels[position],
            Some(best_center(&items[item_index].vector, &raw.centers).0)
        );
    }
}

#[test]
fn validates_vectors_ids_and_checkpoint_order() {
    let base = |items, checkpoints| ReplayNewsLensesRequest {
        schema_version: 1,
        items,
        checkpoints,
        variants: vec![variant(ReplayAlgorithm::FrozenInitial)],
        max_items: 10,
    };
    assert!(matches!(
        replay_news_lenses(&base(vec![item("a", 0, &[0.0, 0.0])], vec![1])),
        Err(ReplayError::InvalidVector { .. })
    ));
    assert!(matches!(
        replay_news_lenses(&base(
            vec![item("a", 0, &[1.0]), item("a", 1, &[1.0])],
            vec![1]
        )),
        Err(ReplayError::DuplicateItem(_))
    ));
    assert!(matches!(
        replay_news_lenses(&base(vec![item("a", 0, &[1.0])], vec![2, 1])),
        Err(ReplayError::CheckpointsNotIncreasing)
    ));
}

#[test]
fn adjusted_rand_is_permutation_invariant() {
    let indices = vec![0, 1, 2, 3];
    let left = HashMap::from([
        (0, Some("a".into())),
        (1, Some("a".into())),
        (2, Some("b".into())),
        (3, Some("b".into())),
    ]);
    let right = HashMap::from([
        (0, Some("x".into())),
        (1, Some("x".into())),
        (2, Some("y".into())),
        (3, Some("y".into())),
    ]);
    assert!((adjusted_rand(&indices, &left, &right) - 1.0).abs() < 1e-12);
}

#[test]
fn every_algorithm_produces_bounded_assignments() {
    let algorithms = [
        ReplayAlgorithm::FrozenInitial,
        ReplayAlgorithm::IncrementalSurrogate,
        ReplayAlgorithm::RollingSphericalKmeans,
        ReplayAlgorithm::NoveltyAware,
    ];
    let request = ReplayNewsLensesRequest {
        schema_version: 1,
        items: vec![
            item("a", 0, &[1.0, 0.0]),
            item("b", 1, &[0.99, 0.01]),
            item("c", 2, &[0.0, 1.0]),
            item("d", 3, &[0.01, 0.99]),
        ],
        checkpoints: vec![1, 3],
        variants: algorithms.into_iter().map(variant).collect(),
        max_items: 4,
    };
    let response = replay_news_lenses(&request).unwrap();
    assert!(
        response
            .variants
            .iter()
            .all(|run| run.checkpoints.len() == 2
                && run.checkpoints[1].assignments.len() == 4
                && run.checkpoints[1].metrics.cluster_count <= 2)
    );
}
