use sqlx::PgPool;

use super::{
    OnboardingCompletionInput, OnboardingFlowRepositoryError,
    load_onboarding_completion_suggestions, validate_completion_selection,
};

async fn create_selection_test_schema(pool: &PgPool) {
    sqlx::query(
        r#"
        CREATE TABLE onboarding_discovery_runs (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            user_id bigint NOT NULL,
            status text NOT NULL
        )
        "#,
    )
    .execute(pool)
    .await
    .expect("test discovery-run table should be created");
    sqlx::query(
        r#"
        CREATE TABLE onboarding_discovery_suggestions (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            run_id bigint NOT NULL,
            user_id bigint NOT NULL,
            suggestion_type text NOT NULL,
            title text,
            feed_url text,
            subreddit text,
            status text NOT NULL
        )
        "#,
    )
    .execute(pool)
    .await
    .expect("test discovery-suggestion table should be created");
}

async fn insert_run(pool: &PgPool, user_id: i64, status: &str) -> i64 {
    sqlx::query_scalar::<_, i64>(
        r#"
        INSERT INTO onboarding_discovery_runs (user_id, status)
        VALUES ($1, $2)
        RETURNING id::bigint
        "#,
    )
    .bind(user_id)
    .bind(status)
    .fetch_one(pool)
    .await
    .expect("test discovery run should be inserted")
}

async fn insert_suggestion(pool: &PgPool, user_id: i64, run_id: i64) -> i64 {
    sqlx::query_scalar::<_, i64>(
        r#"
        INSERT INTO onboarding_discovery_suggestions (
            run_id,
            user_id,
            suggestion_type,
            feed_url,
            title,
            status
        )
        VALUES (
            $1,
            $2,
            'substack',
            'https://example.com/feed',
            'Example',
            'new'
        )
        RETURNING id::bigint
        "#,
    )
    .bind(run_id)
    .bind(user_id)
    .fetch_one(pool)
    .await
    .expect("test suggestion should be inserted")
}

fn completion_input(
    user_id: i64,
    run_id: i64,
    suggestion_ids: Vec<i64>,
) -> OnboardingCompletionInput {
    OnboardingCompletionInput {
        user_id,
        discovery_run_id: Some(run_id),
        selected_suggestion_ids: suggestion_ids,
        sources: Vec::new(),
        subreddits: Vec::new(),
        aggregators: Vec::new(),
        update_twitter_username: false,
        twitter_username: None,
    }
}

#[sqlx::test(migrations = false)]
async fn completion_selection_is_scoped_to_owned_completed_run(pool: PgPool) {
    create_selection_test_schema(&pool).await;
    let owner_id = 7;
    let other_user_id = 8;
    let owned_run_id = insert_run(&pool, owner_id, "completed").await;
    let foreign_run_id = insert_run(&pool, other_user_id, "completed").await;
    let pending_run_id = insert_run(&pool, owner_id, "pending").await;
    let owned_suggestion_id = insert_suggestion(&pool, owner_id, owned_run_id).await;
    let foreign_suggestion_id = insert_suggestion(&pool, other_user_id, foreign_run_id).await;

    let selected = load_onboarding_completion_suggestions(
        &pool,
        owner_id,
        owned_run_id,
        &[owned_suggestion_id],
    )
    .await
    .expect("owned completed selection should resolve");
    assert_eq!(
        selected
            .iter()
            .map(|suggestion| suggestion.id)
            .collect::<Vec<_>>(),
        vec![owned_suggestion_id]
    );

    let foreign_run = load_onboarding_completion_suggestions(
        &pool,
        owner_id,
        foreign_run_id,
        &[foreign_suggestion_id],
    )
    .await;
    assert!(matches!(
        foreign_run,
        Err(OnboardingFlowRepositoryError::DiscoveryRunNotFound)
    ));

    let pending =
        load_onboarding_completion_suggestions(&pool, owner_id, pending_run_id, &[]).await;
    assert!(matches!(
        pending,
        Err(OnboardingFlowRepositoryError::DiscoveryRunNotCompleted)
    ));

    let cross_run = load_onboarding_completion_suggestions(
        &pool,
        owner_id,
        owned_run_id,
        &[foreign_suggestion_id],
    )
    .await;
    assert!(matches!(
        cross_run,
        Err(OnboardingFlowRepositoryError::InvalidSuggestionSelection)
    ));

    let mut transaction = pool.begin().await.expect("test transaction should start");
    let final_validation = validate_completion_selection(
        &mut transaction,
        &completion_input(owner_id, owned_run_id, vec![foreign_suggestion_id]),
    )
    .await;
    assert!(matches!(
        final_validation,
        Err(OnboardingFlowRepositoryError::InvalidSuggestionSelection)
    ));
}
