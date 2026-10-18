use super::*;

fn cohort_with_vectors(vectors: Vec<Vec<f64>>) -> (PreparedBriefingRefreshSeed, Vec<Vec<f64>>) {
    let (mut seed, _) = orthogonal_cohort();
    seed.lens_assignment.pending_sources.truncate(vectors.len());
    for pending in &mut seed.lens_assignment.pending_sources {
        pending.enqueued_at = Utc::now();
    }
    (seed, vectors)
}

fn assert_assignment_coverage(
    seed: &PreparedBriefingRefreshSeed,
    plan: &BriefingLensAssignmentPlan,
    deferred: &[i64],
) {
    let mut assigned = plan
        .assignments
        .iter()
        .map(|item| item.pending_id)
        .collect::<HashSet<_>>();
    assert_eq!(
        assigned.len(),
        plan.assignments.len(),
        "no source is assigned twice"
    );
    assert!(
        deferred.iter().all(|id| assigned.insert(*id)),
        "deferred sources are not assigned"
    );
    assert_eq!(
        assigned,
        seed.lens_assignment
            .pending_sources
            .iter()
            .map(|item| item.pending_id)
            .collect()
    );
}

#[tokio::test]
async fn naming_membership_excludes_parenting_from_football_and_its_centroid() {
    let gateway = BriefingCompositionGateway::from_env().unwrap();
    let vectors = vec![
        vec![1.0, 0.0, 0.0, 0.0],
        vec![0.35, 0.94, 0.0, 0.0],
        vec![0.35, 0.25, 0.90, 0.0],
        vec![0.0, 0.0, 0.0, 1.0],
    ];
    let (mut seed, vectors) = cohort_with_vectors(vectors);
    for (pending, title) in seed.lens_assignment.pending_sources.iter_mut().zip([
        "Missouri quarterback returns",
        "Notre Dame football injuries",
        "UCLA football prediction",
        "Parent's college-readiness lessons",
    ]) {
        pending.source.title = title.to_owned();
    }
    let config = category_budget_config();
    let expected = mean_vector(&vectors[..3]);
    let plan = plan_semantic_lenses_with_namer(
        &gateway,
        &seed,
        &config,
        &discovery_routes(&seed),
        vectors,
        1,
        |candidates| async move {
            assert_eq!(candidates.len(), 4);
            let mut generated = generated_for(&candidates);
            generated.name.key = "news-college-football".into();
            generated.name.title = "College Football".into();
            generated.name.member_source_keys =
                vec!["news:1".into(), "news:2".into(), "news:3".into()];
            generated.name.excluded_source_keys = vec!["news:4".into()];
            Ok(generated)
        },
    )
    .await
    .unwrap();
    assert_assignment_coverage(&seed, &plan, &[]);
    let football = plan
        .new_lenses
        .iter()
        .find(|lens| lens.key != "misc")
        .unwrap();
    assert!(
        football
            .centroid
            .as_ref()
            .unwrap()
            .iter()
            .zip(&expected)
            .all(|(actual, expected)| (actual - expected).abs() < 1e-12)
    );
    assert_eq!(football.centroid_weight, 3);
    assert_eq!(
        plan.assignments
            .iter()
            .find(|item| item.source_id == 4)
            .unwrap()
            .lens_key,
        "misc"
    );
    assert_eq!(
        plan.assignments
            .iter()
            .filter(|item| item.lens_key == football.key)
            .count(),
        3
    );
    assert!(plan.new_lenses.iter().any(|lens| lens.key == "misc"));
}

#[tokio::test]
async fn mixed_membership_settles_each_reviewed_proposal_without_repeating_it() {
    let gateway = BriefingCompositionGateway::from_env().unwrap();
    for accepted in [0, 2] {
        let (mut seed, vectors) = orthogonal_cohort();
        seed.lens_assignment.pending_sources.truncate(8);
        let vectors = vectors[..8].to_vec();
        let config = category_budget_config();
        let first = plan_semantic_lenses_with_namer(
            &gateway,
            &seed,
            &config,
            &discovery_routes(&seed),
            vectors.clone(),
            1,
            |candidates| async move {
                let mut generated = generated_for(&candidates);
                generated.usage.request_count = 1;
                generated.name.member_source_keys = candidates[..accepted]
                    .iter()
                    .map(|c| c.pending.source.source_key.clone())
                    .collect();
                generated.name.excluded_source_keys = candidates[accepted..]
                    .iter()
                    .map(|c| c.pending.source.source_key.clone())
                    .collect();
                Ok(generated)
            },
        )
        .await
        .unwrap();
        assert_assignment_coverage(&seed, &first, &[5, 6, 7, 8]);
        assert!(first.assignments.iter().all(|item| item.lens_key == "misc"));
        assert_eq!(first.usage.len(), 1);
        assert_eq!(first.usage[0].operation, "briefing.name_lens");
        assert_eq!(first.usage[0].usage.request_count, 1);
        assert_eq!(first.new_lenses.len(), 1);
        assert_eq!(first.new_lenses[0].key, "misc");
        seed.lens_assignment.pending_sources.drain(..4);
        seed.lens_assignment
            .active_news_lens_keys
            .push("misc".into());
        seed.lens_assignment.all_lens_keys.push("misc".into());
        let second = plan_semantic_lenses_with_namer(
            &gateway,
            &seed,
            &config,
            &discovery_routes(&seed),
            vectors[4..].to_vec(),
            1,
            |candidates| async move {
                let mut generated = generated_for(&candidates);
                generated.usage.request_count = 1;
                generated.name.excluded_source_keys =
                    std::mem::take(&mut generated.name.member_source_keys);
                Ok(generated)
            },
        )
        .await
        .unwrap();
        assert_assignment_coverage(&seed, &second, &[]);
        assert!(second.new_lenses.is_empty());
        assert!(
            second
                .assignments
                .iter()
                .all(|item| item.lens_key == "misc")
        );
    }
}

#[tokio::test]
async fn large_cluster_defers_unseen_members_until_the_category_is_persisted() {
    let gateway = BriefingCompositionGateway::from_env().unwrap();
    let angle = |degrees: f64| vec![degrees.to_radians().cos(), degrees.to_radians().sin()];
    let vectors = vec![
        angle(0.0),
        angle(0.0),
        angle(0.0),
        angle(0.0),
        angle(20.0),
        angle(-20.0),
        angle(30.0),
    ];
    let (mut seed, vectors) = cohort_with_vectors(vectors);
    seed.mode = newsly_db::BriefingRefreshMode::Append;
    let mut config = category_budget_config();
    config.category_cluster_similarity = 0.5;
    config.news_window_max = 5;
    config.pending_max_age_seconds = 1_500;
    assert!(cosine(&vectors[6], &mean_vector(&vectors)) >= 0.9);
    assert!(cosine(&vectors[6], &mean_vector(&vectors[..4])) < 0.9);
    let plan = plan_semantic_lenses_with_namer(
        &gateway,
        &seed,
        &config,
        &discovery_routes(&seed),
        vectors.clone(),
        1,
        |candidates| async move {
            assert_eq!(
                candidates
                    .iter()
                    .map(|c| c.pending.source_id)
                    .collect::<Vec<_>>(),
                vec![1, 2, 3, 4, 5]
            );
            let mut generated = generated_for(&candidates);
            generated
                .name
                .member_source_keys
                .retain(|key| key != "news:5");
            generated.name.excluded_source_keys.push("news:5".into());
            Ok(generated)
        },
    )
    .await
    .unwrap();
    assert_assignment_coverage(&seed, &plan, &[6, 7]);
    let named = plan
        .new_lenses
        .iter()
        .find(|lens| lens.key != "misc")
        .unwrap();
    assert_eq!(named.centroid_weight, 4);
    assert_eq!(
        plan.assignments
            .iter()
            .find(|item| item.source_id == 5)
            .unwrap()
            .lens_key,
        "misc"
    );
    assert!(named.centroid.as_ref().unwrap()[1].abs() < 1e-12);

    let invalid = plan_semantic_lenses_with_namer(
        &gateway,
        &seed,
        &config,
        &discovery_routes(&seed),
        vectors,
        1,
        |candidates| async move {
            let mut generated = generated_for(&candidates);
            generated.name.member_source_keys.push("news:6".into());
            Ok(generated)
        },
    )
    .await
    .unwrap_err();
    assert!(matches!(
        invalid.cause(),
        SemanticLensPlanningError::InvalidMembership(_)
    ));
}

#[tokio::test]
async fn cap_without_general_desk_never_absorbs_unmatched_sources() {
    let gateway = BriefingCompositionGateway::from_env().unwrap();
    let (mut seed, vectors) =
        cohort_with_vectors(vec![vec![0.7, 0.7], vec![0.0, 1.0], vec![0.0, 1.0]]);
    let mut config = category_budget_config();
    config.max_news_lenses = 1;
    seed.lens_assignment
        .active_news_lens_keys
        .push("news-existing".into());
    seed.lens_assignment
        .active_lenses
        .push(BriefingSemanticLens {
            id: 10,
            key: "news-existing".into(),
            title: "Existing".into(),
            deck: "Existing semantic category".into(),
            position: 2,
            centroid: Some(vec![1.0, 0.0]),
            centroid_weight: 3,
            centroid_model: Some(centroid_model(gateway.embedding_model())),
            routing_rule: None,
            updated_at: Utc::now(),
        });
    seed.lens_assignment.next_news_position = 3;
    let plan = plan_semantic_lenses_with_namer(
        &gateway,
        &seed,
        &config,
        &discovery_routes(&seed),
        vectors,
        1,
        |_| async { panic!("cap must not name more categories") },
    )
    .await
    .unwrap();
    assert_assignment_coverage(&seed, &plan, &[]);
    assert_eq!(plan.assignments[0].lens_key, "misc");
    assert!(
        plan.assignments[1..]
            .iter()
            .all(|item| item.lens_key == "misc")
    );
    assert_eq!(plan.new_lenses.len(), 1);
    assert_eq!(plan.new_lenses[0].key, "misc");
    let mut failed = empty_plan(&seed);
    assign_failed_sources(&mut failed, &seed, &seed.lens_assignment.pending_sources);
    assert_assignment_coverage(&seed, &failed, &[]);
    assert_eq!(failed.new_lenses[0].key, "misc");
    assert!(
        failed
            .assignments
            .iter()
            .all(|item| item.lens_key == "misc")
    );
}

#[tokio::test]
async fn first_cohort_zero_age_assigns_sparse_general_residual_without_a_named_category() {
    let gateway = BriefingCompositionGateway::from_env().unwrap();
    for count in [1, 2] {
        let (seed, vectors) =
            cohort_with_vectors(vec![vec![1.0, 0.0], vec![0.0, 1.0]][..count].to_vec());
        let mut config = category_budget_config();
        config.pending_max_age_seconds = 1_500;
        let waiting = plan_semantic_lenses_with_namer(
            &gateway,
            &seed,
            &config,
            &discovery_routes(&seed),
            vectors.clone(),
            1,
            |_| async { panic!("a named category still requires three sources") },
        )
        .await
        .unwrap();
        assert_assignment_coverage(
            &seed,
            &waiting,
            &(1..=i64::try_from(count).unwrap()).collect::<Vec<_>>(),
        );
        config.pending_max_age_seconds = 0; // The existing active first-cohort policy.
        let plan = plan_semantic_lenses_with_namer(
            &gateway,
            &seed,
            &config,
            &discovery_routes(&seed),
            vectors,
            1,
            |_| async { panic!("sparse residual uses the truthful general desk") },
        )
        .await
        .unwrap();
        assert_assignment_coverage(&seed, &plan, &[]);
        assert_eq!(plan.new_lenses.len(), 1);
        assert_eq!(plan.new_lenses[0].key, "misc");
    }
}

#[tokio::test]
async fn a_mixed_result_does_not_spend_unused_named_slots_on_unreviewed_proposals() {
    let gateway = BriefingCompositionGateway::from_env().unwrap();
    let (mut seed, vectors) = orthogonal_cohort();
    seed.lens_assignment.pending_sources.truncate(8);
    let mut config = category_budget_config();
    config.max_news_lenses = 1;
    let plan = plan_semantic_lenses_with_namer(
        &gateway,
        &seed,
        &config,
        &discovery_routes(&seed),
        vectors[..8].to_vec(),
        usize::MAX,
        |candidates| async move {
            let mut generated = generated_for(&candidates);
            generated.name.excluded_source_keys =
                std::mem::take(&mut generated.name.member_source_keys);
            Ok(generated)
        },
    )
    .await
    .unwrap();
    assert_assignment_coverage(&seed, &plan, &[5, 6, 7, 8]);
    assert!(plan.assignments.iter().all(|item| item.lens_key == "misc"));
    assert_eq!(plan.new_lenses.len(), 1);
    assert_eq!(plan.new_lenses[0].key, "misc");
}

#[tokio::test]
async fn a_capped_proposal_is_not_forced_into_a_category_created_in_the_same_sweep() {
    let gateway = BriefingCompositionGateway::from_env().unwrap();
    let (seed, vectors) =
        cohort_with_vectors([vec![vec![1.0, 0.0]; 4], vec![vec![0.7, 0.7]; 4]].concat());
    let mut config = category_budget_config();
    config.max_news_lenses = 1;
    let plan = plan_semantic_lenses_with_namer(
        &gateway,
        &seed,
        &config,
        &discovery_routes(&seed),
        vectors,
        usize::MAX,
        |candidates| async move { Ok(generated_for(&candidates)) },
    )
    .await
    .unwrap();
    assert_assignment_coverage(&seed, &plan, &[]);
    assert_eq!(plan.new_lenses.len(), 2);
    assert_eq!(plan.new_lenses[0].centroid_weight, 4);
    assert_eq!(plan.usage.len(), 1);
    assert_eq!(
        plan.assignments
            .iter()
            .filter(|item| item.lens_key == "misc")
            .count(),
        4
    );
}
