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

fn singleton_cluster(id: i64) -> Cluster {
    Cluster::new(Candidate {
        pending: BriefingUnassignedSource {
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
        },
        vector: vec![1.0, 0.0],
    })
}

#[test]
fn packing_small_clusters_merges_a_remainder_into_the_only_group() {
    let packed = pack_small_clusters((1..=5).map(singleton_cluster).collect(), 3, 4);
    assert_eq!(packed.len(), 1);
    let ids: Vec<_> = packed[0]
        .candidates
        .iter()
        .map(|candidate| candidate.pending.source_id)
        .collect();
    assert_eq!(ids, vec![1, 2, 3, 4, 5]);
}

#[test]
fn packing_small_clusters_keeps_other_groups_when_merging_a_remainder() {
    let packed = pack_small_clusters((1..=9).map(singleton_cluster).collect(), 3, 4);
    let ids: Vec<Vec<_>> = packed
        .iter()
        .map(|cluster| {
            cluster
                .candidates
                .iter()
                .map(|candidate| candidate.pending.source_id)
                .collect()
        })
        .collect();
    assert_eq!(ids, vec![vec![1, 2, 3, 4, 9], vec![5, 6, 7, 8]]);
}

fn generated_name(id: i64) -> newsly_providers::GeneratedBriefingLensName {
    newsly_providers::GeneratedBriefingLensName {
        name: newsly_providers::BriefingLensName {
            key: format!("news-{id}"),
            title: format!("Category {id}"),
            deck: "A category for the test sources.".to_owned(),
        },
        model: "test".to_owned(),
        usage: newsly_agent_runtime::ProviderUsage::default(),
        provider_response_id: None,
    }
}

#[tokio::test]
async fn naming_overlaps_four_requests_but_preserves_order_and_available_slots() {
    use std::sync::atomic::{AtomicUsize, Ordering};

    let clusters: Vec<_> = (1..=9).map(singleton_cluster).collect();
    let barrier = tokio::sync::Barrier::new(4);
    let calls = AtomicUsize::new(0);
    let names = tokio::time::timeout(
        std::time::Duration::from_secs(2),
        name_clusters(&clusters, 5, 1, |sources| {
            let barrier = &barrier;
            let calls = &calls;
            async move {
                let call = calls.fetch_add(1, Ordering::SeqCst);
                assert_eq!(sources.len(), 1);
                if call < 4 {
                    barrier.wait().await;
                }
                // Different completion order must not change category order.
                for _ in 0..(5 - sources[0].pending.source_id).max(0) {
                    tokio::task::yield_now().await;
                }
                Ok(generated_name(sources[0].pending.source_id))
            }
        }),
    )
    .await
    .expect("the first four naming requests must run together")
    .unwrap();
    assert_eq!(calls.load(Ordering::SeqCst), 5);
    assert_eq!(
        names
            .iter()
            .map(|name| name.name.key.as_str())
            .collect::<Vec<_>>(),
        vec!["news-1", "news-2", "news-3", "news-4", "news-5"]
    );
}

#[tokio::test]
async fn failed_naming_batch_stops_before_requesting_later_categories() {
    use std::sync::atomic::{AtomicUsize, Ordering};

    let clusters: Vec<_> = (1..=9).map(singleton_cluster).collect();
    let calls = AtomicUsize::new(0);
    let result = name_clusters(&clusters, 9, 4, |sources| {
        calls.fetch_add(1, Ordering::SeqCst);
        async move {
            if sources[0].pending.source_id == 2 {
                Err(SemanticLensPlanningError::InconsistentEmbeddingWidth)
            } else {
                Ok(generated_name(sources[0].pending.source_id))
            }
        }
    })
    .await;
    assert!(result.is_err());
    assert_eq!(calls.load(Ordering::SeqCst), 4);
}

#[tokio::test]
async fn cancelling_naming_drops_in_flight_calls_without_starting_another_batch() {
    use std::sync::atomic::{AtomicUsize, Ordering};

    struct InFlight<'a>(&'a AtomicUsize);
    impl Drop for InFlight<'_> {
        fn drop(&mut self) {
            self.0.fetch_sub(1, Ordering::SeqCst);
        }
    }

    let clusters: Vec<_> = (1..=9).map(singleton_cluster).collect();
    let active = AtomicUsize::new(0);
    let calls = AtomicUsize::new(0);
    {
        let work = name_clusters(&clusters, 9, 4, |_| async {
            calls.fetch_add(1, Ordering::SeqCst);
            active.fetch_add(1, Ordering::SeqCst);
            let _guard = InFlight(&active);
            std::future::pending().await
        });
        tokio::pin!(work);
        assert!(futures_util::poll!(&mut work).is_pending());
        assert_eq!(active.load(Ordering::SeqCst), 4);
    }
    assert_eq!(active.load(Ordering::SeqCst), 0);
    assert_eq!(calls.load(Ordering::SeqCst), 4);
}

fn orthogonal_cohort() -> (PreparedBriefingRefreshSeed, Vec<Vec<f64>>) {
    let mut pending_sources = Vec::new();
    let mut vectors = Vec::new();
    let start = Utc::now() - chrono::Duration::minutes(10);
    for index in 0_usize..16 {
        let id = i64::try_from(index + 1).unwrap();
        let mut source = singleton_cluster(id).candidates.remove(0).pending;
        source.enqueued_at = start + chrono::Duration::seconds(id);
        pending_sources.push(source);
        let mut vector = vec![0.0; 4];
        vector[index / 4] = 1.0;
        vectors.push(vector);
    }
    (
        PreparedBriefingRefreshSeed {
            task_id: 1,
            user_id: 1,
            mode: newsly_db::BriefingRefreshMode::Full,
            starting_version: 1,
            pending_added: 16,
            prepared_state_changed: false,
            lens_assignment: newsly_db::BriefingLensAssignmentSnapshot {
                pending_sources,
                active_lenses: vec![],
                active_news_lens_keys: vec![],
                all_lens_keys: vec![],
                next_news_position: 2,
            },
            claim_fence: newsly_db::BriefingRefreshClaimFence {
                locked_by: "test".into(),
                lease_token: uuid::Uuid::nil(),
                retry_count: 0,
                executor_runtime: "rust".into(),
                executor_version: 1,
                executor_namespace: "briefing_refresh".into(),
            },
        },
        vectors,
    )
}

fn category_budget_config() -> BriefingRefreshConfig {
    BriefingRefreshConfig {
        masthead_title: "Test Briefing".into(),
        window_min: 3,
        news_window_max: 4,
        new_lens_min_items: 3,
        pending_max_age_seconds: 0,
        max_news_lenses: 3,
        category_similarity: 0.9,
        category_cluster_similarity: 0.9,
        category_absorb_similarity: 0.9,
        centroid_max_weight: 32,
        sweep_seconds: 60,
        lens_idle_days: 7,
    }
}

#[tokio::test]
async fn unrestricted_rebuild_keeps_clusters_beyond_the_category_cap() {
    use std::sync::atomic::{AtomicUsize, Ordering};

    let gateway = BriefingCompositionGateway::from_env().unwrap();
    let (seed, vectors) = orthogonal_cohort();
    let config = category_budget_config();
    config.validate().unwrap();
    let calls = AtomicUsize::new(0);
    let plan = plan_semantic_lenses_with_namer(
        &gateway,
        &seed,
        &config,
        128,
        vectors,
        usize::MAX,
        |candidates| {
            calls.fetch_add(1, Ordering::SeqCst);
            async move { Ok(generated_name(candidates[0].pending.source_id)) }
        },
    )
    .await
    .unwrap();
    assert_eq!(
        calls.load(Ordering::SeqCst),
        3,
        "the category cap bounds naming, not source coverage"
    );
    assert_eq!(plan.new_lenses.len(), 3);
    assert_eq!(plan.assignments.len(), 16);
    let actual: HashSet<_> = plan
        .assignments
        .iter()
        .map(|item| item.pending_id)
        .collect();
    let expected: HashSet<_> = seed
        .lens_assignment
        .pending_sources
        .iter()
        .map(|item| item.pending_id)
        .collect();
    assert_eq!(
        actual, expected,
        "the fourth orthogonal cluster must fall back into an existing category"
    );
    assert_eq!(
        plan.new_lenses
            .iter()
            .map(|lens| lens.position)
            .collect::<Vec<_>>(),
        vec![2, 3, 4]
    );
}

#[tokio::test]
async fn progressive_budget_names_one_cluster_then_advances_to_a_second_category() {
    use std::sync::atomic::{AtomicUsize, Ordering};

    let gateway = BriefingCompositionGateway::from_env().unwrap();
    let (mut seed, vectors) = orthogonal_cohort();
    seed.mode = newsly_db::BriefingRefreshMode::Append;
    let config = category_budget_config();
    let calls = AtomicUsize::new(0);
    let first = plan_semantic_lenses_with_namer(
        &gateway,
        &seed,
        &config,
        128,
        vectors.clone(),
        1,
        |candidates| {
            calls.fetch_add(1, Ordering::SeqCst);
            async move { Ok(generated_name(candidates[0].pending.source_id)) }
        },
    )
    .await
    .unwrap();
    assert_eq!(calls.load(Ordering::SeqCst), 1);
    assert_eq!(first.new_lenses.len(), 1);
    assert_eq!(
        first
            .assignments
            .iter()
            .map(|item| item.pending_id)
            .collect::<Vec<_>>(),
        vec![1, 2, 3, 4]
    );
    assert_eq!(
        seed.lens_assignment.pending_sources.len(),
        16,
        "planning does not discard frozen cohort identities"
    );
    let lens = &first.new_lenses[0];
    let first_ids: HashSet<_> = first
        .assignments
        .iter()
        .map(|item| item.pending_id)
        .collect();
    let remaining_vectors: Vec<_> = seed
        .lens_assignment
        .pending_sources
        .iter()
        .zip(vectors)
        .filter_map(|(pending, vector)| {
            (!first_ids.contains(&pending.pending_id)).then_some(vector)
        })
        .collect();
    seed.lens_assignment
        .pending_sources
        .retain(|pending| !first_ids.contains(&pending.pending_id));
    seed.lens_assignment
        .active_lenses
        .push(BriefingSemanticLens {
            id: 10,
            key: lens.key.clone(),
            title: lens.title.clone(),
            deck: lens.deck.clone(),
            position: lens.position,
            centroid: lens.centroid.clone(),
            centroid_weight: lens.centroid_weight,
            centroid_model: lens.centroid_model.clone(),
            routing_rule: None,
            updated_at: Utc::now(),
        });
    seed.lens_assignment
        .active_news_lens_keys
        .push(lens.key.clone());
    seed.lens_assignment.all_lens_keys.push(lens.key.clone());
    seed.lens_assignment.next_news_position = 3;
    let second = plan_semantic_lenses_with_namer(
        &gateway,
        &seed,
        &config,
        128,
        remaining_vectors,
        1,
        |candidates| {
            calls.fetch_add(1, Ordering::SeqCst);
            async move { Ok(generated_name(candidates[0].pending.source_id)) }
        },
    )
    .await
    .unwrap();
    assert_eq!(
        calls.load(Ordering::SeqCst),
        2,
        "each pass makes one naming call"
    );
    assert_eq!(second.new_lenses.len(), 1);
    assert_eq!(second.new_lenses[0].position, 3);
    assert_ne!(second.new_lenses[0].key, lens.key);
    assert_eq!(
        second
            .assignments
            .iter()
            .map(|item| item.pending_id)
            .collect::<Vec<_>>(),
        vec![5, 6, 7, 8]
    );
    assert_eq!(
        seed.lens_assignment.pending_sources.len() - second.assignments.len(),
        8
    );
}
