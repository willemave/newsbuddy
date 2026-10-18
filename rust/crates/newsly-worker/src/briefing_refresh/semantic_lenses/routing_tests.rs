use super::*;

#[test]
fn full_catch_all_preserves_assignments_and_covers_every_omitted_source_once() {
    let (mut seed, _) = routing_cohort(5);
    for mode in [
        newsly_db::BriefingRefreshMode::Full,
        newsly_db::BriefingRefreshMode::Append,
        newsly_db::BriefingRefreshMode::Sweep,
    ] {
        seed.mode = mode;
        for assigned_count in [0, 2, 5] {
            let mut plan = empty_plan(&seed);
            plan.assignments.extend(
                seed.lens_assignment
                    .pending_sources
                    .iter()
                    .take(assigned_count)
                    .map(|source| assignment(source, "news-accepted".into())),
            );
            let original = plan.assignments.clone();
            complete_full_assignments(&mut plan, &seed);
            complete_full_assignments(&mut plan, &seed);
            assert_eq!(&plan.assignments[..assigned_count], &original);
            let expected = if mode == newsly_db::BriefingRefreshMode::Full {
                5
            } else {
                assigned_count
            };
            assert_eq!(plan.assignments.len(), expected);
            assert!(
                plan.assignments[assigned_count..]
                    .iter()
                    .all(|row| row.lens_key == "misc")
            );
            assert_eq!(
                plan.assignments
                    .iter()
                    .map(|row| row.pending_id)
                    .collect::<HashSet<_>>()
                    .len(),
                expected
            );
            assert_eq!(
                plan.new_lenses.len(),
                usize::from(expected > assigned_count)
            );
            assert!(plan.usage.is_empty());
        }
    }
}

#[tokio::test]
async fn full_catch_all_covers_bounded_routes_and_failed_names_without_losing_usage() {
    let gateway = BriefingCompositionGateway::from_env().unwrap();
    let naming_model = gateway.naming_model_spec();
    let (seed, mut vectors) = orthogonal_cohort();
    let mut routed = seed.clone();
    // The original seed has four clusters; only two reached bounded routing.
    routed.lens_assignment.pending_sources.truncate(8);
    vectors.truncate(8);
    let mut plan = plan_semantic_lenses_with_namer(
        &gateway,
        &routed,
        &category_budget_config(),
        &discovery_routes(&routed),
        vectors,
        4,
        |candidates| async move {
            if candidates[0].pending.pending_id <= 4 {
                Ok(generated_for(&candidates))
            } else {
                let name = generated_for(&candidates);
                let usage = naming_usage(&name, naming_model);
                Err(SemanticLensPlanningError::Provider(
                    BriefingCompositionGatewayError::InvalidLensName("rejected".into()),
                )
                .with_usage(vec![usage]))
            }
        },
    )
    .await
    .unwrap();
    let usage = plan.usage.clone();
    let accepted = plan.assignments.clone();
    assert_eq!(usage.len(), 2);
    assert_eq!(accepted.len(), 4);
    complete_full_assignments(&mut plan, &seed);
    assert_eq!(plan.usage, usage);
    assert_eq!(&plan.assignments[..4], &accepted);
    assert_eq!(plan.assignments.len(), 16);
    assert!(
        plan.assignments[4..]
            .iter()
            .all(|row| row.lens_key == "misc")
    );
}

fn routing_cohort(count: usize) -> (PreparedBriefingRefreshSeed, Vec<Vec<f64>>) {
    let (mut seed, _) = orthogonal_cohort();
    seed.lens_assignment.pending_sources.truncate(count);
    (seed, vec![vec![1.0, 0.0, 0.0, 0.0]; count])
}

fn add_routing_category(
    seed: &mut PreparedBriefingRefreshSeed,
    key: &str,
    centroid: Option<Vec<f64>>,
    model: Option<String>,
) {
    let id = 10 + i64::try_from(seed.lens_assignment.active_lenses.len()).unwrap();
    seed.lens_assignment
        .active_lenses
        .push(BriefingSemanticLens {
            id,
            key: key.to_owned(),
            title: "Existing category".into(),
            deck: "The user's existing category definition.".into(),
            position: i32::try_from(id).unwrap(),
            centroid,
            centroid_weight: 3,
            centroid_model: model,
            routing_rule: Some("Only stories matching this definition.".into()),
            updated_at: Utc::now(),
        });
    seed.lens_assignment.active_news_lens_keys.push(key.into());
    seed.lens_assignment.all_lens_keys.push(key.into());
}

#[tokio::test]
async fn explicit_existing_route_wins_over_centroids_and_only_updates_accepted_category() {
    let gateway = BriefingCompositionGateway::from_env().unwrap();
    let (mut seed, vectors) = routing_cohort(2);
    let model = centroid_model(gateway.embedding_model());
    add_routing_category(
        &mut seed,
        "news-accepted",
        Some(vec![0.0, 1.0, 0.0, 0.0]),
        Some(model.clone()),
    );
    add_routing_category(
        &mut seed,
        "news-nearest",
        Some(vectors[0].clone()),
        Some(model),
    );
    let plan = plan_semantic_lenses_with_namer(
        &gateway,
        &seed,
        &category_budget_config(),
        &[
            CategoryRoute::Existing("news-accepted".into()),
            CategoryRoute::General,
        ],
        vectors,
        1,
        |_| async { panic!("decided routes must not request category naming") },
    )
    .await
    .unwrap();
    assert_eq!(plan.assignments.len(), 2);
    assert_eq!(plan.assignments[0].lens_key, "news-accepted");
    assert_eq!(plan.assignments[1].lens_key, "misc");
    assert_eq!(plan.centroid_mutations.len(), 1);
    assert_eq!(plan.centroid_mutations[0].lens_key, "news-accepted");
    assert_eq!(plan.centroid_mutations[0].centroid_weight, 4);
    assert!(plan.new_lenses.iter().all(|lens| lens.key == "misc"));
    assert!(plan.usage.is_empty());
}

#[tokio::test]
async fn missing_or_obsolete_centroids_still_accept_explicit_routes_without_external_work() {
    let gateway = BriefingCompositionGateway::from_env().unwrap();
    for (centroid, model) in [
        (None, None),
        (
            Some(vec![0.0, 1.0, 0.0, 0.0]),
            Some("openrouter:obsolete-model".into()),
        ),
    ] {
        let (mut seed, vectors) = routing_cohort(1);
        add_routing_category(&mut seed, "news-existing", centroid, model);
        let plan = plan_semantic_lenses_with_namer(
            &gateway,
            &seed,
            &category_budget_config(),
            &[CategoryRoute::Existing("news-existing".into())],
            vectors.clone(),
            1,
            |_| async { panic!("accepted category requires no naming HTTP request") },
        )
        .await
        .unwrap();
        assert_eq!(plan.assignments[0].lens_key, "news-existing");
        assert_eq!(plan.centroid_mutations.len(), 1);
        assert_eq!(plan.centroid_mutations[0].centroid, vectors[0]);
        assert_eq!(plan.centroid_mutations[0].centroid_weight, 1);
        assert_eq!(
            plan.centroid_mutations[0].centroid_model,
            centroid_model(gateway.embedding_model())
        );
        assert!(plan.new_lenses.is_empty());
        assert!(
            plan.usage.is_empty(),
            "planner must not request profile embeddings"
        );
    }
}

#[tokio::test]
async fn routing_rejects_unknown_keys_and_mismatched_decision_counts() {
    let gateway = BriefingCompositionGateway::from_env().unwrap();
    let (seed, vectors) = routing_cohort(1);
    for routes in [
        vec![],
        vec![CategoryRoute::General, CategoryRoute::Discover],
        vec![CategoryRoute::Existing("news-not-in-frozen-choices".into())],
    ] {
        let error = plan_semantic_lenses_with_namer(
            &gateway,
            &seed,
            &category_budget_config(),
            &routes,
            vectors.clone(),
            1,
            |_| async { panic!("invalid routes must fail before naming") },
        )
        .await
        .unwrap_err();
        assert!(matches!(
            error.cause(),
            SemanticLensPlanningError::InvalidRoutes
        ));
    }
}

#[tokio::test]
async fn confident_other_discovers_a_category_below_cap_despite_matching_existing_centroid() {
    let gateway = BriefingCompositionGateway::from_env().unwrap();
    let (mut seed, vectors) = routing_cohort(3);
    add_routing_category(
        &mut seed,
        "news-existing",
        Some(vectors[0].clone()),
        Some(centroid_model(gateway.embedding_model())),
    );
    let plan = plan_semantic_lenses_with_namer(
        &gateway,
        &seed,
        &category_budget_config(),
        &discovery_routes(&seed),
        vectors,
        1,
        |candidates| async move { Ok(generated_for(&candidates)) },
    )
    .await
    .unwrap();
    assert_eq!(plan.new_lenses.len(), 1);
    assert_ne!(plan.new_lenses[0].key, "news-existing");
    assert_ne!(plan.new_lenses[0].key, "misc");
    assert_eq!(plan.assignments.len(), 3);
    assert!(
        plan.assignments
            .iter()
            .all(|item| item.lens_key == plan.new_lenses[0].key)
    );
    assert!(plan.centroid_mutations.is_empty());
    assert_eq!(plan.usage.len(), 1);
}

#[tokio::test]
async fn unshown_tail_waits_at_zero_age_then_routes_to_the_persisted_category() {
    let gateway = BriefingCompositionGateway::from_env().unwrap();
    let (mut seed, vectors) = routing_cohort(5);
    seed.mode = newsly_db::BriefingRefreshMode::Append;
    let config = category_budget_config();
    assert_eq!(config.pending_max_age_seconds, 0);
    assert_eq!(config.news_window_max, 4);
    let first = plan_semantic_lenses_with_namer(
        &gateway,
        &seed,
        &config,
        &discovery_routes(&seed),
        vectors.clone(),
        1,
        |candidates| async move {
            assert_eq!(candidates.len(), 4);
            Ok(generated_for(&candidates))
        },
    )
    .await
    .unwrap();
    assert_eq!(first.assignments.len(), 4);
    assert!(first.assignments.iter().all(|item| item.pending_id != 5));
    assert_eq!(first.new_lenses.len(), 1);
    assert_ne!(first.new_lenses[0].key, "misc");
    assert_eq!(first.new_lenses[0].centroid_weight, 4);
    let named = &first.new_lenses[0];
    seed.lens_assignment.pending_sources.drain(..4);
    add_routing_category(
        &mut seed,
        &named.key,
        named.centroid.clone(),
        named.centroid_model.clone(),
    );
    seed.lens_assignment.active_lenses[0].centroid_weight = named.centroid_weight;
    let second = plan_semantic_lenses_with_namer(
        &gateway,
        &seed,
        &config,
        &[CategoryRoute::Existing(named.key.clone())],
        vectors[4..].to_vec(),
        1,
        |_| async { panic!("next task uses the persisted category decision") },
    )
    .await
    .unwrap();
    assert_eq!(second.assignments.len(), 1);
    assert_eq!(second.assignments[0].pending_id, 5);
    assert_eq!(second.assignments[0].lens_key, named.key);
    assert_eq!(second.centroid_mutations[0].centroid_weight, 5);
    assert!(second.new_lenses.is_empty());
}

#[tokio::test]
async fn full_rebuild_preserves_unreviewed_tail_in_general_news() {
    let gateway = BriefingCompositionGateway::from_env().unwrap();
    let (seed, vectors) = routing_cohort(5);
    assert_eq!(seed.mode, newsly_db::BriefingRefreshMode::Full);
    let plan = plan_semantic_lenses_with_namer(
        &gateway,
        &seed,
        &category_budget_config(),
        &discovery_routes(&seed),
        vectors,
        1,
        |candidates| async move { Ok(generated_for(&candidates)) },
    )
    .await
    .unwrap();
    assert_eq!(plan.assignments.len(), 5);
    assert_eq!(
        plan.assignments
            .iter()
            .find(|item| item.pending_id == 5)
            .unwrap()
            .lens_key,
        "misc"
    );
    assert_eq!(
        plan.new_lenses
            .iter()
            .find(|lens| lens.key != "misc")
            .unwrap()
            .centroid_weight,
        4
    );
}
