use super::*;
use newsly_db::{
    CreateLearningDeckOutcome, LearningDeckSourceProjection, create_or_rerun_learning_deck,
    delete_learning_deck,
};
use newsly_domain::{ResourceKey, RuntimeOwner};
use newsly_queue::{ClaimRequest, ClaimRuntimeScope, EnqueueRequest, QueueKernel, TaskStatus};
use serde_json::{Map, json};

async fn deck(pool: &PgPool) -> (i64, i64, i64) {
    newsly_db::run_migrations(pool).await.unwrap();
    let user: i64 = sqlx::query_scalar("INSERT INTO users(apple_id,email,is_active,is_admin) VALUES($1,$2,true,false) RETURNING id::bigint")
        .bind(uuid::Uuid::new_v4().to_string())
        .bind(format!("{}@example.com", uuid::Uuid::new_v4()))
        .fetch_one(pool).await.unwrap();
    let (deck_id, task_id) = create_deck(pool, user, "dispatch-test").await;
    (user, deck_id, task_id)
}

async fn create_deck(pool: &PgPool, user: i64, name: &str) -> (i64, i64) {
    let source = LearningDeckSourceProjection {
        source_kind: "github_repo".to_owned(),
        source_identity: format!("github:example/{name}"),
        source_url: Some(format!("https://github.com/example/{name}")),
        source_content_id: None,
        source_title: name.to_owned(),
        source_metadata: Map::new(),
    };
    let mut transaction = pool.begin().await.unwrap();
    let outcome =
        create_or_rerun_learning_deck(&mut transaction, user, &source, None, "/tmp/decks")
            .await
            .unwrap();
    transaction.commit().await.unwrap();
    let CreateLearningDeckOutcome::AttemptCreated { deck_id, task_id } = outcome else {
        panic!("expected a new deck attempt");
    };
    (deck_id, task_id)
}

fn delivery(user: i64, task_id: i64) -> EnqueueRequest {
    EnqueueRequest::run_llm_task(task_id, user, LlmTaskDeliveryKind::LearningDeck)
}

fn claim_request(worker: &str) -> ClaimRequest {
    let mut request = ClaimRequest::for_queue(
        worker,
        TaskType::RunLlmTask.spec().queue,
        ClaimRuntimeScope::namespaces(
            RuntimeOwner::Rust,
            [ResourceKey::new("run_llm_task").unwrap()],
        )
        .unwrap(),
    );
    request.task_type = Some(TaskType::RunLlmTask);
    request
}

#[sqlx::test]
async fn learning_deck_delivery_requires_category_before_provider_dispatch(pool: PgPool) {
    let (user, _, task_id) = deck(&pool).await;
    let mut payload = json!({"user_id":user,"llm_task_id":task_id})
        .as_object()
        .unwrap()
        .clone();
    // An actual persisted deck is required: a forged payload marker alone cannot
    // determine which product executor owns a delivery.
    for marker in [
        None,
        Some(json!("share_action")),
        Some(json!(17)),
        Some(Value::Null),
    ] {
        payload.remove("task_kind");
        if let Some(marker) = marker {
            payload.insert("task_kind".to_owned(), marker);
        }
        let result = classify_delivery(&pool, task_id, user, &payload)
            .await
            .unwrap_err();
        assert!(!result.success);
        assert!(!result.retryable);
        assert_eq!(
            result.error_message.as_deref(),
            Some("LLM task delivery category does not match its workflow")
        );
    }
    payload.insert("task_kind".to_owned(), json!("learning_deck"));
    assert_eq!(
        classify_delivery(&pool, task_id, user, &payload)
            .await
            .unwrap(),
        LlmTaskDispatchKind::LearningDeck
    );
    let status: String = sqlx::query_scalar("SELECT status FROM llm_tasks WHERE id::bigint=$1")
        .bind(task_id)
        .fetch_one(&pool)
        .await
        .unwrap();
    assert_eq!(status, "queued");
    let generation_count: i64 =
        sqlx::query_scalar("SELECT count(*) FROM learning_deck_generation_state")
            .fetch_one(&pool)
            .await
            .unwrap();
    assert_eq!(
        generation_count, 0,
        "classification must not begin provider work"
    );
}

#[sqlx::test]
async fn deleted_queued_deck_delivery_completes_without_provider_dispatch(pool: PgPool) {
    let (user, deck_id, task_id) = deck(&pool).await;
    let queue = QueueKernel::new(pool.clone());
    let delivery_id = queue.enqueue(delivery(user, task_id)).await.unwrap();
    let mut transaction = pool.begin().await.unwrap();
    delete_learning_deck(&mut transaction, user, deck_id)
        .await
        .unwrap()
        .unwrap();
    transaction.commit().await.unwrap();
    let request = claim_request("deleted-deck-test");
    let claim = queue.claim(&request).await.unwrap().unwrap();
    assert_eq!(claim.id, delivery_id);
    // Terminal classification is the dispatcher's immediate successful branch;
    // no provider executor needs to be configured or invoked to drain this delivery.
    assert_eq!(
        classify_delivery(&pool, task_id, user, &claim.payload)
            .await
            .unwrap(),
        LlmTaskDispatchKind::Terminal
    );
    let transition = queue
        .finalize(&claim, &TaskResult::ok(), 3)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(transition.status, TaskStatus::Completed);
    let status: String = sqlx::query_scalar("SELECT status FROM llm_tasks WHERE id::bigint=$1")
        .bind(task_id)
        .fetch_one(&pool)
        .await
        .unwrap();
    assert_eq!(status, "cancelled");
    let generation_count: i64 =
        sqlx::query_scalar("SELECT count(*) FROM learning_deck_generation_state")
            .fetch_one(&pool)
            .await
            .unwrap();
    assert_eq!(generation_count, 0);
}

#[sqlx::test]
async fn deleting_started_deck_releases_reserved_retry_slot_for_queued_deck(pool: PgPool) {
    let (user, first_deck, first_task) = deck(&pool).await;
    let queue = QueueKernel::new(pool.clone());
    queue.enqueue(delivery(user, first_task)).await.unwrap();
    let request = claim_request("reserved-deck-deletion-test");
    let first_claim = queue.claim(&request).await.unwrap().unwrap();
    // Model the durable boundary after source preparation and before a transient
    // provider failure, without constructing any external provider executor.
    sqlx::query("UPDATE llm_tasks SET status='running',started_at=now() WHERE id::bigint=$1")
        .bind(first_task)
        .execute(&pool)
        .await
        .unwrap();
    sqlx::query("INSERT INTO learning_deck_generation_state(llm_task_id,input_fingerprint,deadline_at) VALUES($1,$2,now()+interval '15 minutes')")
        .bind(first_task).bind("a".repeat(64)).execute(&pool).await.unwrap();
    let retry = queue
        .finalize(
            &first_claim,
            &TaskResult {
                retry_delay_seconds: Some(60),
                ..TaskResult::fail(Some("transient provider failure".to_owned()), true)
            },
            3,
        )
        .await
        .unwrap()
        .unwrap();
    assert_eq!(retry.status, TaskStatus::Pending);
    let (_, next_task) = create_deck(&pool, user, "queued-after-retry").await;
    let next_delivery = queue.enqueue(delivery(user, next_task)).await.unwrap();
    assert!(
        queue.claim(&request).await.unwrap().is_none(),
        "a started deck reserves its owner's slot throughout retry backoff"
    );
    let mut transaction = pool.begin().await.unwrap();
    delete_learning_deck(&mut transaction, user, first_deck)
        .await
        .unwrap()
        .unwrap();
    transaction.commit().await.unwrap();
    let generation_count: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM learning_deck_generation_state WHERE llm_task_id::bigint=$1",
    )
    .bind(first_task)
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(
        generation_count, 0,
        "deletion must release the generation reservation"
    );
    let next_claim = queue.claim(&request).await.unwrap().unwrap();
    assert_eq!(next_claim.id, next_delivery);
    assert_eq!(
        classify_delivery(&pool, next_task, user, &next_claim.payload)
            .await
            .unwrap(),
        LlmTaskDispatchKind::LearningDeck
    );
    let status: String = sqlx::query_scalar("SELECT status FROM llm_tasks WHERE id::bigint=$1")
        .bind(first_task)
        .fetch_one(&pool)
        .await
        .unwrap();
    assert_eq!(status, "cancelled");
}

#[sqlx::test]
async fn share_action_delivery_category_must_match_its_canonical_workflow(pool: PgPool) {
    let (user, _, task) = deck(&pool).await;
    sqlx::query("UPDATE llm_tasks SET task_kind='share_action',mode='add_content',subject_id=NULL WHERE id::bigint=$1")
        .bind(task).execute(&pool).await.unwrap();
    let wrong = delivery(user, task).payload.unwrap();
    let result = classify_delivery(&pool, task, user, &wrong)
        .await
        .unwrap_err();
    assert!(!result.retryable);
    let valid = EnqueueRequest::run_llm_task(task, user, LlmTaskDeliveryKind::ShareAction)
        .payload
        .unwrap();
    assert_eq!(
        classify_delivery(&pool, task, user, &valid).await.unwrap(),
        LlmTaskDispatchKind::ShareAction
    );
}
