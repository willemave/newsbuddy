use super::*;

async fn user(pool: &PgPool) -> i64 {
    crate::run_migrations(pool).await.unwrap();
    sqlx::query_scalar("INSERT INTO users(apple_id,email,is_active,is_admin) VALUES($1,$2,true,false) RETURNING id::bigint")
        .bind(uuid::Uuid::new_v4().to_string())
        .bind(format!("{}@example.com", uuid::Uuid::new_v4()))
        .fetch_one(pool).await.unwrap()
}

fn source(name: &str) -> LearningDeckSourceProjection {
    LearningDeckSourceProjection {
        source_kind: "github_repo".to_owned(),
        source_identity: format!("github:{name}"),
        source_url: Some(format!("https://github.com/example/{name}")),
        source_content_id: None,
        source_title: name.to_owned(),
        source_metadata: Map::new(),
    }
}

async fn create(pool: &PgPool, user_id: i64, name: &str) -> CreateLearningDeckOutcome {
    let mut tx = pool.begin().await.unwrap();
    let outcome = create_or_rerun_learning_deck(
        &mut tx,
        user_id,
        &source(name),
        Some("Explain the concurrency model"),
        "/tmp/decks",
    )
    .await
    .unwrap();
    tx.commit().await.unwrap();
    outcome
}

fn created(outcome: CreateLearningDeckOutcome) -> (i64, i64) {
    let CreateLearningDeckOutcome::AttemptCreated { deck_id, task_id } = outcome else {
        panic!("expected a fresh attempt: {outcome:?}");
    };
    (deck_id, task_id)
}

#[sqlx::test]
async fn distinct_sources_queue_while_same_source_reuses_waiting_or_running_attempt(pool: PgPool) {
    let user = user(&pool).await;
    let (first_deck, first_task) = created(create(&pool, user, "first").await);
    sqlx::query("UPDATE llm_tasks SET status='running' WHERE id::bigint=$1")
        .bind(first_task)
        .execute(&pool)
        .await
        .unwrap();
    let (second_deck, second_task) = created(create(&pool, user, "second").await);
    assert_ne!(first_deck, second_deck);
    assert_ne!(first_task, second_task);
    assert_eq!(
        create(&pool, user, "second").await,
        CreateLearningDeckOutcome::ExistingActiveAttempt {
            deck_id: second_deck
        }
    );
    assert_eq!(
        create(&pool, user, "first").await,
        CreateLearningDeckOutcome::ExistingActiveAttempt {
            deck_id: first_deck
        }
    );
    let waiting = get_learning_deck(&pool, user, second_deck)
        .await
        .unwrap()
        .unwrap();
    let attempt = waiting.latest_attempt.unwrap();
    assert_eq!(attempt.status, "queued");
    assert_eq!(
        attempt.interests_prompt.as_deref(),
        Some("Explain the concurrency model")
    );
    assert!(attempt.started_at.is_none());
    let count: i64 = sqlx::query_scalar("SELECT count(*) FROM learning_deck_generation_state")
        .fetch_one(&pool)
        .await
        .unwrap();
    assert_eq!(
        count, 0,
        "accepting a waiting deck never starts its generation deadline"
    );
}

#[sqlx::test]
async fn concurrent_same_source_requests_create_exactly_one_attempt(pool: PgPool) {
    let user = user(&pool).await;
    let (first, second) = tokio::join!(create(&pool, user, "same"), create(&pool, user, "same"));
    let (deck, _) = match first {
        CreateLearningDeckOutcome::AttemptCreated { .. } => created(first),
        CreateLearningDeckOutcome::ExistingActiveAttempt { .. } => created(second),
    };
    let existing = CreateLearningDeckOutcome::ExistingActiveAttempt { deck_id: deck };
    assert!(first == existing || second == existing);
    let count: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM llm_tasks WHERE user_id::bigint=$1 AND task_kind='learning_deck'",
    )
    .bind(user)
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(count, 1);
}

#[sqlx::test]
async fn retry_queues_behind_another_deck_and_keeps_original_instructions(pool: PgPool) {
    let user = user(&pool).await;
    let (failed_deck, failed_task) = created(create(&pool, user, "failed").await);
    sqlx::query("UPDATE llm_tasks SET status='failed' WHERE id::bigint=$1")
        .bind(failed_task)
        .execute(&pool)
        .await
        .unwrap();
    created(create(&pool, user, "other").await);
    let mut tx = pool.begin().await.unwrap();
    let retry = retry_learning_deck(&mut tx, user, failed_deck, "/tmp/decks")
        .await
        .unwrap();
    let RetryLearningDeckOutcome::AttemptCreated { deck_id, task_id } = retry else {
        panic!("expected queued retry: {retry:?}");
    };
    assert_eq!(deck_id, failed_deck);
    assert_eq!(
        retry_learning_deck(&mut tx, user, failed_deck, "/tmp/decks")
            .await
            .unwrap(),
        RetryLearningDeckOutcome::ExistingActiveRetry { deck_id }
    );
    tx.commit().await.unwrap();
    let input: Value = sqlx::query_scalar("SELECT input_json FROM llm_tasks WHERE id::bigint=$1")
        .bind(task_id)
        .fetch_one(&pool)
        .await
        .unwrap();
    assert_eq!(input["interests_prompt"], "Explain the concurrency model");
    assert_eq!(input["retry_of_attempt_id"], failed_task);
}

#[sqlx::test]
async fn deleting_waiting_deck_cancels_only_its_attempt(pool: PgPool) {
    let user = user(&pool).await;
    let (_, first_task) = created(create(&pool, user, "first").await);
    let (waiting_deck, waiting_task) = created(create(&pool, user, "waiting").await);
    let mut tx = pool.begin().await.unwrap();
    assert!(
        delete_learning_deck(&mut tx, user, waiting_deck)
            .await
            .unwrap()
            .is_some()
    );
    tx.commit().await.unwrap();
    let states: Vec<(i64, String)> =
        sqlx::query_as("SELECT id::bigint,status FROM llm_tasks ORDER BY id")
            .fetch_all(&pool)
            .await
            .unwrap();
    assert_eq!(
        states,
        vec![
            (first_task, "queued".to_owned()),
            (waiting_task, "cancelled".to_owned())
        ]
    );
    assert!(
        get_learning_deck(&pool, user, waiting_deck)
            .await
            .unwrap()
            .is_none()
    );
}

#[sqlx::test]
async fn source_wait_backoff_starts_at_preparation_after_a_long_queue_wait(pool: PgPool) {
    let user = user(&pool).await;
    let content: i64 = sqlx::query_scalar("INSERT INTO contents(content_type,url,title,status,is_aggregate) VALUES('article','https://example.com/waiting','Waiting source','processing',false) RETURNING id::bigint")
        .fetch_one(&pool).await.unwrap();
    let source = LearningDeckSourceProjection {
        source_kind: "content".to_owned(),
        source_identity: format!("content:{content}"),
        source_content_id: Some(content),
        source_url: Some("https://example.com/waiting".to_owned()),
        source_title: "Waiting source".to_owned(),
        source_metadata: Map::new(),
    };
    let mut tx = pool.begin().await.unwrap();
    let (_, task) = created(
        create_or_rerun_learning_deck(&mut tx, user, &source, None, "/tmp/decks")
            .await
            .unwrap(),
    );
    tx.commit().await.unwrap();
    sqlx::query("UPDATE llm_tasks SET created_at=timezone('UTC',now())-interval '2 hours' WHERE id::bigint=$1")
        .bind(task).execute(&pool).await.unwrap();
    sqlx::query("INSERT INTO processing_tasks(task_type,content_id,status,executor_runtime,executor_version,executor_namespace) VALUES('process_content',$1,'pending','rust',1,'process_content')")
        .bind(content).execute(&pool).await.unwrap();
    let mut tx = pool.begin().await.unwrap();
    let prepared = crate::begin_learning_deck_preparation(&mut tx, task, user)
        .await
        .unwrap();
    let crate::LearningDeckPreparationOutcome::Ready(snapshot) = prepared else {
        panic!("expected source preparation: {prepared:?}");
    };
    tx.commit().await.unwrap();
    let mut tx = pool.begin().await.unwrap();
    let result =
        crate::settle_learning_deck_source_missing(&mut tx, &snapshot, "Waiting for source")
            .await
            .unwrap();
    assert!(
        matches!(
            result,
            crate::LearningDeckSourceSettlement::Deferred {
                retry_delay_seconds: 30,
                ..
            }
        ),
        "{result:?}"
    );
    tx.commit().await.unwrap();
}
