use std::time::Duration;

use newsly_queue::TaskType;
use serde_json::{Map, Value, json};
use sqlx::PgPool;

use super::{
    generated_image_request, instruction_child_analysis_request, lock_content_after_usage_user,
    next_content_task, x_bookmark_fallback_user_id,
};

#[sqlx::test(migrations = false)]
async fn content_finalization_locks_user_before_content(pool: PgPool) {
    newsly_db::run_migrations(&pool)
        .await
        .expect("test database should migrate");
    let user_id = sqlx::query_scalar::<_, i64>(
        r"
        INSERT INTO users (apple_id, email, is_admin, is_active)
        VALUES ('content-lock-order', 'content-lock-order@example.com', FALSE, TRUE)
        RETURNING id::bigint
        ",
    )
    .fetch_one(&pool)
    .await
    .expect("test user should insert");
    let content_id = sqlx::query_scalar::<_, i64>(
        r"
        INSERT INTO contents (
            content_type, url, title, source, status, content_metadata, is_aggregate
        )
        VALUES ('article', 'https://example.com/lock-order', 'Lock order',
                'self submission', 'processing', $1, FALSE)
        RETURNING id::bigint
        ",
    )
    .bind(json!({"processing": {"submitted_by_user_id": user_id}}))
    .fetch_one(&pool)
    .await
    .expect("test content should insert");

    let mut submission = pool.begin().await.expect("submission transaction");
    sqlx::query("SELECT id FROM users WHERE id::bigint = $1 FOR UPDATE")
        .bind(user_id)
        .execute(&mut *submission)
        .await
        .expect("submission should hold the user lock");

    let finalizer_pool = pool.clone();
    let finalizer = tokio::spawn(async move {
        let mut transaction = finalizer_pool.begin().await.expect("finalizer transaction");
        sqlx::query("SET LOCAL application_name = 'newsly-content-lock-order-finalizer'")
            .execute(&mut *transaction)
            .await
            .expect("finalizer application name should set");
        let (content, active_user_id) = lock_content_after_usage_user(&mut transaction, content_id)
            .await
            .expect("finalizer locks should succeed");
        transaction.commit().await.expect("finalizer should commit");
        (content.map(|content| content.id), active_user_id)
    });

    tokio::time::timeout(Duration::from_secs(2), async {
        loop {
            let waiting: bool = sqlx::query_scalar(
                r"
                SELECT EXISTS (
                    SELECT 1
                    FROM pg_stat_activity
                    WHERE datname = current_database()
                      AND application_name = 'newsly-content-lock-order-finalizer'
                      AND wait_event_type = 'Lock'
                )
                ",
            )
            .fetch_one(&pool)
            .await
            .expect("finalizer wait state should be observable");
            if waiting {
                break;
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    })
    .await
    .expect("finalizer should wait on the user before touching content");

    sqlx::query("SET LOCAL lock_timeout = '1s'")
        .execute(&mut *submission)
        .await
        .expect("submission lock timeout should set");
    sqlx::query("SELECT id FROM contents WHERE id::bigint = $1 FOR UPDATE")
        .bind(content_id)
        .execute(&mut *submission)
        .await
        .expect("submission should acquire content without a lock cycle");
    submission.commit().await.expect("submission should commit");

    let (locked_content_id, active_user_id) =
        tokio::time::timeout(Duration::from_secs(2), finalizer)
            .await
            .expect("finalizer should resume after submission commits")
            .expect("finalizer task should not panic");
    assert_eq!(locked_content_id, Some(content_id));
    assert_eq!(active_user_id, Some(user_id));
}

#[test]
fn tweet_video_routes_through_media_before_summarization() {
    let metadata = object(&json!({
        "domain": {
            "platform": "twitter",
            "has_video": true
        }
    }));
    assert_eq!(
        next_content_task(&metadata, None),
        TaskType::DownloadTweetVideoAudio
    );
}

#[test]
fn tweet_video_with_transcript_and_non_tweet_video_route_to_summary() {
    let transcribed = object(&json!({
        "platform": "twitter",
        "has_video": true,
        "video_transcript": "Spoken words"
    }));
    assert_eq!(next_content_task(&transcribed, None), TaskType::Summarize);

    let unrelated = object(&json!({"has_video": true}));
    assert_eq!(next_content_task(&unrelated, None), TaskType::Summarize);
}

#[test]
fn instruction_child_requests_keep_analysis_and_image_ownership_distinct() {
    let analysis = instruction_child_analysis_request(42, 7);
    assert_eq!(analysis.task_type, TaskType::AnalyzeUrl);
    assert_eq!(analysis.content_id, Some(42));
    assert_eq!(analysis.access_user_id, Some(7));
    assert_eq!(
        analysis
            .payload
            .as_ref()
            .and_then(|payload| payload.get("content_id"))
            .and_then(Value::as_i64),
        Some(42)
    );

    let image = generated_image_request(42);
    assert_eq!(image.task_type, TaskType::GenerateImage);
    assert_eq!(image.content_id, Some(42));
    assert_eq!(image.dedupe, Some(true));
    assert_eq!(image.access_user_id, None);
}

#[test]
fn x_bookmark_fallback_only_applies_to_bookmark_submissions() {
    let bookmark = object(&json!({
        "processing": {"submitted_via": "X_Bookmarks"}
    }));
    assert_eq!(x_bookmark_fallback_user_id(&bookmark, Some(7)), Some(7));
    assert_eq!(x_bookmark_fallback_user_id(&bookmark, None), None);

    let share_sheet = object(&json!({"submitted_via": "share_sheet"}));
    assert_eq!(x_bookmark_fallback_user_id(&share_sheet, Some(7)), None);
}

fn object(value: &Value) -> Map<String, Value> {
    value.as_object().cloned().unwrap()
}
