use super::{
    ApplyBriefingLensAssignmentOutcome, BriefingLensAssignmentPlan, PgPool,
    PreparedBriefingRefreshSeed, apply_briefing_lens_assignment, insert_eligible_article,
    insert_live_briefing_task, insert_news_backlog, prepare_seed, test_config,
};
use crate::briefing_refresh::BriefingPendingLensAssignment;

async fn insert_category(pool: &PgPool, user_id: i64, title: &str, deck: &str, rule: &str) {
    sqlx::query(
        r#"
        INSERT INTO briefing_lenses (
            user_id, key, tier, title, deck, routing_rule, position, status, centroid_weight
        ) VALUES ($1::bigint::integer, 'news-science', 'news', $2, $3, $4, 2, 'active', 0)
        "#,
    )
    .bind(user_id)
    .bind(title)
    .bind(deck)
    .bind(rule)
    .execute(pool)
    .await
    .expect("user category should insert");
}

fn assignment_plan(seed: &PreparedBriefingRefreshSeed) -> BriefingLensAssignmentPlan {
    assert_eq!(seed.lens_assignment.pending_sources.len(), 1);
    let source = &seed.lens_assignment.pending_sources[0];
    BriefingLensAssignmentPlan {
        task_id: seed.task_id,
        user_id: seed.user_id,
        starting_version: seed.starting_version,
        assignments: vec![BriefingPendingLensAssignment {
            pending_id: source.pending_id,
            source_kind: source.source_kind.clone(),
            source_id: source.source_id,
            lens_key: "news-science".to_owned(),
        }],
        centroid_mutations: Vec::new(),
        new_lenses: Vec::new(),
        usage: Vec::new(),
    }
}

#[sqlx::test(migrations = false)]
async fn same_category_key_preserves_each_users_profile_and_assignments(pool: PgPool) {
    crate::run_migrations(&pool).await.unwrap();
    let first_user = insert_eligible_article(&pool).await;
    let second_user: i64 = sqlx::query_scalar(
        "INSERT INTO users (apple_id, email, is_admin, is_active) VALUES ('category-second', 'category-second@example.com', false, true) RETURNING id::bigint",
    )
    .fetch_one(&pool)
    .await
    .unwrap();
    insert_news_backlog(&pool, first_user, 1).await;
    sqlx::query(
        "INSERT INTO user_scraper_configs(user_id,scraper_type,feed_url,config,is_active) VALUES($1::bigint::integer,'aggregator','aggregator://techmeme','{\"key\":\"techmeme\"}',true)",
    )
    .bind(second_user)
    .execute(&pool)
    .await
    .unwrap();
    insert_category(
        &pool,
        first_user,
        "Climate science",
        "Research into a changing climate.",
        "Climate research and atmospheric science.",
    )
    .await;
    insert_category(
        &pool,
        second_user,
        "Medical science",
        "Clinical findings and medical research.",
        "Medicine, clinical trials and public health.",
    )
    .await;
    let first_task = insert_live_briefing_task(&pool, first_user).await;
    let second_task = insert_live_briefing_task(&pool, second_user).await;
    let first_seed = prepare_seed(&pool, first_task, first_user).await;
    let second_seed = prepare_seed(&pool, second_task, second_user).await;
    let first_profile = first_seed.lens_assignment.active_lenses[0].clone();
    let second_profile = second_seed.lens_assignment.active_lenses[0].clone();
    assert_ne!(first_profile.id, second_profile.id);
    assert_eq!(first_profile.key, second_profile.key);
    assert_eq!(first_profile.title, "Climate science");
    assert_eq!(first_profile.deck, "Research into a changing climate.");
    assert_eq!(
        first_profile.routing_rule.as_deref(),
        Some("Climate research and atmospheric science.")
    );
    assert_eq!(second_profile.title, "Medical science");
    assert_eq!(
        second_profile.deck,
        "Clinical findings and medical research."
    );
    assert_eq!(
        second_profile.routing_rule.as_deref(),
        Some("Medicine, clinical trials and public health.")
    );
    let plan = assignment_plan(&first_seed);
    let mut transaction = pool.begin().await.unwrap();
    assert!(matches!(
        apply_briefing_lens_assignment(&mut transaction, first_seed, &plan, &test_config())
            .await
            .unwrap(),
        ApplyBriefingLensAssignmentOutcome::Ready(_)
    ));
    transaction.commit().await.unwrap();

    let first_after = prepare_seed(&pool, first_task, first_user).await;
    let second_after = prepare_seed(&pool, second_task, second_user).await;
    let persisted = &first_after.lens_assignment.active_lenses[0];
    assert_eq!(persisted.title, first_profile.title);
    assert_eq!(persisted.deck, first_profile.deck);
    assert_eq!(persisted.routing_rule, first_profile.routing_rule);
    assert_eq!(
        second_after.lens_assignment.active_lenses,
        vec![second_profile]
    );
    assert!(first_after.lens_assignment.pending_sources.is_empty());
    assert_eq!(
        second_after.lens_assignment.pending_sources,
        second_seed.lens_assignment.pending_sources
    );
    let assignments: Vec<(i64, Option<String>)> = sqlx::query_as(
        "SELECT user_id::bigint, lens_key FROM briefing_pending_sources WHERE source_kind='news' ORDER BY user_id",
    )
    .fetch_all(&pool)
    .await
    .unwrap();
    assert_eq!(
        assignments,
        vec![
            (first_user, Some("news-science".to_owned())),
            (second_user, None)
        ]
    );
}

#[sqlx::test(migrations = false)]
async fn category_profile_edits_fence_inflight_assignments_even_without_timestamp_change(
    pool: PgPool,
) {
    crate::run_migrations(&pool).await.unwrap();
    let user_id = insert_eligible_article(&pool).await;
    insert_news_backlog(&pool, user_id, 1).await;
    insert_category(
        &pool,
        user_id,
        "Science",
        "Research findings and discoveries.",
        "Scientific research.",
    )
    .await;
    let task_id = insert_live_briefing_task(&pool, user_id).await;
    for (field, update) in [
        (
            "title",
            "UPDATE briefing_lenses SET title='Changed science desk' WHERE user_id::bigint=$1 AND key='news-science'",
        ),
        (
            "deck",
            "UPDATE briefing_lenses SET deck='Changed research coverage.' WHERE user_id::bigint=$1 AND key='news-science'",
        ),
        (
            "routing_rule",
            "UPDATE briefing_lenses SET routing_rule='Only peer-reviewed research.' WHERE user_id::bigint=$1 AND key='news-science'",
        ),
    ] {
        let seed = prepare_seed(&pool, task_id, user_id).await;
        let plan = assignment_plan(&seed);
        sqlx::query(update)
            .bind(user_id)
            .execute(&pool)
            .await
            .unwrap();
        let updated_at: chrono::NaiveDateTime = sqlx::query_scalar(
            "SELECT updated_at FROM briefing_lenses WHERE user_id::bigint=$1 AND key='news-science'",
        )
        .bind(user_id)
        .fetch_one(&pool)
        .await
        .unwrap();
        assert_eq!(
            updated_at.and_utc(),
            seed.lens_assignment.active_lenses[0].updated_at
        );
        let mut transaction = pool.begin().await.unwrap();
        let outcome = apply_briefing_lens_assignment(&mut transaction, seed, &plan, &test_config())
            .await
            .unwrap();
        assert_eq!(
            outcome,
            ApplyBriefingLensAssignmentOutcome::Stale,
            "{field} edit must fence the old assignment"
        );
        transaction.commit().await.unwrap();
        let pending: Option<String> = sqlx::query_scalar(
            "SELECT lens_key FROM briefing_pending_sources WHERE user_id::bigint=$1 AND source_kind='news'",
        ).bind(user_id).fetch_one(&pool).await.unwrap();
        assert_eq!(
            pending, None,
            "{field} edit must leave the story unassigned"
        );
    }
}

#[sqlx::test(migrations = false)]
async fn matching_metadata_topic_cannot_assign_news_before_category_inference(pool: PgPool) {
    crate::run_migrations(&pool).await.unwrap();
    let user_id = insert_eligible_article(&pool).await;
    insert_news_backlog(&pool, user_id, 1).await;
    insert_category(
        &pool,
        user_id,
        "Science",
        "Scientific research and discoveries.",
        "Scientific research.",
    )
    .await;
    sqlx::query(
        r#"UPDATE news_items SET raw_metadata='{"aggregator":{"topic":"Science","topic_title":"Science"},"summary":{"title":"Science story"}}'::json"#,
    )
    .execute(&pool)
    .await
    .unwrap();
    let task_id = insert_live_briefing_task(&pool, user_id).await;
    let seed = prepare_seed(&pool, task_id, user_id).await;
    assert_eq!(seed.lens_assignment.active_lenses[0].key, "news-science");
    assert_eq!(seed.lens_assignment.pending_sources.len(), 1);
    assert_eq!(
        seed.lens_assignment.pending_sources[0].source.title,
        "Science story"
    );
    let pending: Vec<(String, Option<String>)> = sqlx::query_as(
        "SELECT source_kind,lens_key FROM briefing_pending_sources WHERE user_id::bigint=$1 ORDER BY source_kind",
    )
    .bind(user_id)
    .fetch_all(&pool)
    .await
    .unwrap();
    assert_eq!(
        pending,
        vec![
            ("content".into(), Some("articles".into())),
            ("news".into(), None)
        ]
    );
}
