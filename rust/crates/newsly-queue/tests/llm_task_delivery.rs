use newsly_queue::{
    EnqueueRequest, LlmTaskDeliveryKind, PayloadError, QueueError, QueueKernel, TaskType,
};
use serde_json::{Map, Value, json};
use sqlx::PgPool;

#[test]
fn typed_deliveries_match_the_required_category_contract() {
    let schema: Value = serde_json::from_str(include_str!(
        "../../../../contracts/tasks/run_llm_task.schema.json"
    ))
    .unwrap();
    let categories: Vec<_> = LlmTaskDeliveryKind::ALL
        .iter()
        .map(|kind| kind.as_str())
        .collect();
    assert_eq!(schema["properties"]["task_kind"]["enum"], json!(categories));
    assert!(
        schema["required"]
            .as_array()
            .unwrap()
            .contains(&json!("task_kind"))
    );
    for &kind in LlmTaskDeliveryKind::ALL {
        let request = EnqueueRequest::run_llm_task(42, 7, kind);
        assert_eq!(request.task_type, TaskType::RunLlmTask);
        assert_eq!(request.owner_user_id, Some(7));
        let payload = request.payload.unwrap();
        assert_eq!(payload["llm_task_id"], 42);
        assert_eq!(payload["user_id"], 7);
        assert_eq!(payload["task_kind"], serde_json::to_value(kind).unwrap());
        assert_eq!(
            TaskType::RunLlmTask
                .normalize_payload(Some(payload.clone()))
                .unwrap(),
            payload
        );
        assert_eq!(
            serde_json::from_value::<LlmTaskDeliveryKind>(payload["task_kind"].clone()).unwrap(),
            kind
        );
    }
}

fn invalid_categories() -> Vec<(Option<Value>, PayloadError)> {
    let task_type = TaskType::RunLlmTask;
    let field = "task_kind";
    vec![
        (None, PayloadError::MissingField { task_type, field }),
        (
            Some(Value::Null),
            PayloadError::WrongType { task_type, field },
        ),
        (
            Some(json!(17)),
            PayloadError::WrongType { task_type, field },
        ),
        (
            Some(json!({})),
            PayloadError::WrongType { task_type, field },
        ),
        (
            Some(json!("learning-deck")),
            PayloadError::OutOfRange { task_type, field },
        ),
        (
            Some(json!("")),
            PayloadError::OutOfRange { task_type, field },
        ),
    ]
}

fn payload_with_category(category: Option<Value>, user: i64) -> Map<String, Value> {
    let mut payload = json!({"llm_task_id":42,"user_id":user})
        .as_object()
        .unwrap()
        .clone();
    if let Some(category) = category {
        payload.insert("task_kind".to_owned(), category);
    }
    payload
}

#[test]
fn normalization_rejects_missing_unknown_and_malformed_categories() {
    for (category, expected) in invalid_categories() {
        assert_eq!(
            TaskType::RunLlmTask.normalize_payload(Some(payload_with_category(category, 7))),
            Err(expected)
        );
    }
}

#[sqlx::test(migrations = "../newsly-db/migrations")]
async fn invalid_categories_are_rejected_before_any_delivery_is_persisted(pool: PgPool) {
    let user: i64 = sqlx::query_scalar("INSERT INTO users(apple_id,email,is_active,is_admin) VALUES('delivery-contract','delivery-contract@example.com',true,false) RETURNING id::bigint")
        .fetch_one(&pool).await.unwrap();
    let queue = QueueKernel::new(pool.clone());
    for (category, expected) in invalid_categories() {
        let mut request = EnqueueRequest::run_llm_task(42, user, LlmTaskDeliveryKind::LearningDeck);
        request.payload = Some(payload_with_category(category, user));
        let error = queue.enqueue(request).await.unwrap_err();
        assert!(matches!(error, QueueError::Payload(actual) if actual == expected));
    }
    let count: i64 = sqlx::query_scalar("SELECT count(*) FROM processing_tasks")
        .fetch_one(&pool)
        .await
        .unwrap();
    assert_eq!(count, 0);
    queue
        .enqueue(EnqueueRequest::run_llm_task(
            42,
            user,
            LlmTaskDeliveryKind::LearningDeck,
        ))
        .await
        .unwrap();
    let count: i64 = sqlx::query_scalar("SELECT count(*) FROM processing_tasks")
        .fetch_one(&pool)
        .await
        .unwrap();
    assert_eq!(count, 1);
}
