use newsly_queue::QueueKernel;
use sqlx::PgPool;

use super::super::model::PreparedSummarizationAttempt;
use super::*;

#[sqlx::test]
async fn repaired_ordinary_summary_preserves_readiness_and_queues_artwork(pool: PgPool) {
    newsly_db::run_migrations(&pool).await.unwrap();
    let user_id: i64 = sqlx::query_scalar(
        "INSERT INTO users (apple_id, email, is_admin, is_active)
         VALUES ('summary-repair', 'summary-repair@example.test', FALSE, TRUE) RETURNING id::bigint",
    )
    .fetch_one(&pool)
    .await
    .unwrap();
    let content_id: i64 = sqlx::query_scalar(
        "INSERT INTO contents (content_type, url, is_aggregate, status, content_metadata)
         VALUES ('article', 'https://example.test/ordinary-repair', FALSE, 'completed', $1)
         RETURNING id::bigint",
    )
    .bind(json!({"content": "Stored article"}))
    .fetch_one(&pool)
    .await
    .unwrap();
    let mut tx = pool.begin().await.unwrap();
    newsly_db::save_content_to_knowledge(&mut tx, user_id, content_id)
        .await
        .unwrap();
    let snapshot = load_summarization_snapshot(&mut tx, content_id)
        .await
        .unwrap()
        .unwrap();
    let plan = SummarizationFinalizationPlan {
        attempt: PreparedSummarizationAttempt {
            content: snapshot,
            input_fingerprint: input_fingerprint("article", "Stored article"),
        },
        mutation: SummarizationMutation::Complete {
            summary: json!({"one_line": "The repaired editorial summary."}),
        },
        finalized_at: chrono::Utc::now(),
    };
    let SummarizationApplyOutcome::Applied(applied) =
        apply_summarization_state(&mut tx, &plan).await.unwrap()
    else {
        panic!("ordinary repair should apply")
    };
    assert_eq!(applied.status, "completed");
    assert_eq!(applied.metadata["domain"]["artwork_status"], "pending");
    crate::summarization::fanout::enqueue_summary_followups(
        &mut tx,
        &QueueKernel::new(pool.clone()),
        &applied,
        0,
        1,
    )
    .await
    .unwrap();
    tx.commit().await.unwrap();

    let (status, task_type): (String, String) = sqlx::query_as(
        "SELECT content.status, task.task_type FROM contents AS content
         JOIN processing_tasks AS task ON task.content_id = content.id WHERE content.id = $1",
    )
    .bind(content_id)
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(
        (status.as_str(), task_type.as_str()),
        ("completed", "generate_image")
    );
}

#[sqlx::test]
async fn deck_summary_success_and_failure_keep_the_source_readable(pool: PgPool) {
    newsly_db::run_migrations(&pool).await.unwrap();
    let user_id: i64 = sqlx::query_scalar(
        "INSERT INTO users (apple_id, email, is_admin, is_active)
         VALUES ('deck-summary', 'deck-summary@example.test', FALSE, TRUE) RETURNING id::bigint",
    )
    .fetch_one(&pool)
    .await
    .unwrap();
    let content_id: i64 = sqlx::query_scalar(
        "INSERT INTO contents (content_type, url, is_aggregate, status, content_metadata, processed_at)
         VALUES ('article', 'https://example.test/summary', FALSE, 'completed', $1, '2026-09-01')
         RETURNING id::bigint",
    )
    .bind(json!({"processing": {"deck_source_only": true}, "content": "Stored article"}))
    .fetch_one(&pool)
    .await
    .unwrap();
    let mut tx = pool.begin().await.unwrap();
    newsly_db::save_content_to_knowledge(&mut tx, user_id, content_id)
        .await
        .unwrap();
    let snapshot = load_summarization_snapshot(&mut tx, content_id)
        .await
        .unwrap()
        .unwrap();
    let mut plan = SummarizationFinalizationPlan {
        attempt: PreparedSummarizationAttempt {
            content: snapshot,
            input_fingerprint: input_fingerprint("article", "Stored article"),
        },
        mutation: SummarizationMutation::Unchanged,
        finalized_at: chrono::Utc::now(),
    };
    for retry_scheduled in [true, false] {
        plan.mutation = SummarizationMutation::Failed {
            reason: "Provider fixture failed".to_owned(),
            retry_scheduled,
            skipped: false,
        };
        let SummarizationApplyOutcome::Applied(applied) =
            apply_summarization_state(&mut tx, &plan).await.unwrap()
        else {
            panic!("failure diagnostics should be applied")
        };
        assert_eq!(applied.status, "completed");
        let (processed_at, error): (NaiveDateTime, Option<String>) =
            sqlx::query_as("SELECT processed_at, error_message FROM contents WHERE id = $1")
                .bind(content_id)
                .fetch_one(&mut *tx)
                .await
                .unwrap();
        assert_eq!(processed_at.to_string(), "2026-09-01 00:00:00");
        assert_eq!(error, None);
    }
    plan.mutation = SummarizationMutation::Complete {
        summary: json!({
            "title": "Editorial summary",
            "one_line": "A guide to a system that repairs its missing editorial summaries.",
            "artifact": {"type": "findings", "payload": {"takeaway": "Repair from durable source text."}},
            "feed_preview": {
                "title": "Editorial summary",
                "one_line": "A guide to a system that repairs its missing editorial summaries.",
                "artifact_type": "findings",
                "preview_bullets": ["Keep sources readable.", "Reuse queue work."],
                "reason_to_read": "Understand the repair path."
            },
        }),
    };
    let SummarizationApplyOutcome::Applied(applied) =
        apply_summarization_state(&mut tx, &plan).await.unwrap()
    else {
        panic!("summary should apply")
    };
    assert_eq!(applied.status, "completed");
    assert_eq!(
        applied.metadata["domain"]["summary_kind"],
        "longform_artifact"
    );
    assert_eq!(
        applied.metadata["domain"]["summary"]["artifact"]["type"],
        "findings"
    );
    assert_eq!(
        applied.metadata["domain"]["feed_preview"]["title"],
        "Editorial summary"
    );
    assert_eq!(applied.metadata["content"], "Stored article");
    assert!(applied.metadata.get("artwork_status").is_none());
    crate::summarization::fanout::enqueue_summary_followups(
        &mut tx,
        &QueueKernel::new(pool.clone()),
        &applied,
        0,
        1,
    )
    .await
    .unwrap();
    tx.commit().await.unwrap();
    let tasks: i64 = sqlx::query_scalar("SELECT count(*) FROM processing_tasks")
        .fetch_one(&pool)
        .await
        .unwrap();
    assert_eq!(tasks, 0);
}

#[sqlx::test]
async fn queued_deck_summary_yields_to_ordinary_resubmission(pool: PgPool) {
    newsly_db::run_migrations(&pool).await.unwrap();
    let content_id: i64 = sqlx::query_scalar(
        "INSERT INTO contents (content_type, url, is_aggregate, status, content_metadata)
         VALUES ('article', 'https://example.test/promote', FALSE, 'completed', $1) RETURNING id::bigint",
    )
    .bind(json!({"processing": {"deck_source_only": true}, "content": "Stored article"}))
    .fetch_one(&pool)
    .await
    .unwrap();
    let mut tx = pool.begin().await.unwrap();
    let snapshot = load_summarization_snapshot(&mut tx, content_id)
        .await
        .unwrap()
        .unwrap();
    let plan = SummarizationFinalizationPlan {
        attempt: PreparedSummarizationAttempt {
            content: snapshot,
            input_fingerprint: input_fingerprint("article", "Stored article"),
        },
        mutation: SummarizationMutation::Complete {
            summary: json!({"one_line": "Editorial summary"}),
        },
        finalized_at: chrono::Utc::now(),
    };
    sqlx::query("UPDATE contents SET status = 'new', content_metadata = $2 WHERE id = $1")
        .bind(content_id)
        .bind(json!({"processing": {"deck_source_only": false}, "content": "Stored article"}))
        .execute(&mut *tx)
        .await
        .unwrap();
    let mut request = newsly_queue::EnqueueRequest::new(newsly_queue::TaskType::AnalyzeUrl);
    request.content_id = Some(content_id);
    let queue = QueueKernel::new(pool.clone());
    queue
        .enqueue_many_in_transaction(&mut tx, vec![request])
        .await
        .unwrap();
    assert!(matches!(
        apply_summarization_state(&mut tx, &plan).await.unwrap(),
        SummarizationApplyOutcome::SourceDeferred,
    ));
    sqlx::query("UPDATE processing_tasks SET status = 'completed' WHERE content_id = $1")
        .bind(content_id)
        .execute(&mut *tx)
        .await
        .unwrap();
    let SummarizationApplyOutcome::Applied(applied) =
        apply_summarization_state(&mut tx, &plan).await.unwrap()
    else {
        panic!("ordinary summary should resume")
    };
    assert_eq!(applied.status, "awaiting_image");
}

#[sqlx::test]
async fn promoted_deck_summary_defers_stale_input_at_the_retry_limit(pool: PgPool) {
    use newsly_queue::{
        ClaimRequest, ClaimRuntimeScope, EnqueueRequest, TaskQueue, TaskResult, TaskType,
    };

    use super::super::finalizer::SummarizationFinalizer;
    use crate::{TaskFinalizer, TaskFinalizerResult};

    newsly_db::run_migrations(&pool).await.unwrap();
    let content_id: i64 = sqlx::query_scalar(
        "INSERT INTO contents (content_type, url, is_aggregate, status, content_metadata)
         VALUES ('article', 'https://example.test/promoted-stale', FALSE, 'completed', $1)
         RETURNING id::bigint",
    )
    .bind(json!({"processing": {"deck_source_only": true}, "content": "Previous source"}))
    .fetch_one(&pool)
    .await
    .unwrap();
    let queue = QueueKernel::new(pool.clone());
    let mut request = EnqueueRequest::new(TaskType::Summarize);
    request.content_id = Some(content_id);
    let task_id = queue.enqueue(request.clone()).await.unwrap();
    let retry_limit = 3;
    sqlx::query("UPDATE processing_tasks SET retry_count = $2 WHERE id = $1")
        .bind(task_id)
        .bind(retry_limit)
        .execute(&pool)
        .await
        .unwrap();
    let scope = ClaimRuntimeScope::namespaces(
        newsly_domain::RuntimeOwner::Rust,
        [newsly_domain::ResourceKey::new("summarize").unwrap()],
    )
    .unwrap();
    let claim = queue
        .claim(&ClaimRequest::for_queue(
            "promoted-stale",
            TaskQueue::Content,
            scope,
        ))
        .await
        .unwrap()
        .unwrap();
    assert_eq!(claim.retry_count, retry_limit);

    let mut tx = pool.begin().await.unwrap();
    let snapshot = load_summarization_snapshot(&mut tx, content_id)
        .await
        .unwrap()
        .unwrap();
    tx.commit().await.unwrap();
    let plan = SummarizationFinalizationPlan {
        attempt: PreparedSummarizationAttempt {
            content: snapshot,
            input_fingerprint: input_fingerprint("article", "Previous source"),
        },
        mutation: SummarizationMutation::Complete {
            summary: json!({"one_line": "A stale summary"}),
        },
        finalized_at: chrono::Utc::now(),
    };
    let mut tx = pool.begin().await.unwrap();
    sqlx::query("UPDATE contents SET status = 'processing', content_metadata = $2 WHERE id = $1")
        .bind(content_id)
        .bind(json!({"processing": {"deck_source_only": false}, "content": "Updated source"}))
        .execute(&mut *tx)
        .await
        .unwrap();
    let delivery = queue
        .enqueue_many_in_transaction(&mut tx, vec![request])
        .await
        .unwrap();
    assert_eq!(delivery.task_ids, [task_id]);
    assert!(delivery.inserted_task_ids.is_empty());
    tx.commit().await.unwrap();

    let mut fenced = queue
        .begin_fenced_finalization(&claim, &TaskResult::ok(), retry_limit)
        .await
        .unwrap()
        .unwrap();
    let finalizer = SummarizationFinalizer::new(queue.clone(), plan, 0, 1);
    let TaskFinalizerResult::Override(result) =
        finalizer.apply(fenced.transaction_mut()).await.unwrap()
    else {
        panic!("the stale result should be deferred");
    };
    assert!(result.deferred);
    fenced.replace_result(&result, retry_limit).unwrap();
    fenced.finish().await.unwrap();

    let (status, retries, summary): (String, i32, Option<Value>) = sqlx::query_as(
        "SELECT task.status, task.retry_count, content.content_metadata->'summary'
         FROM processing_tasks AS task JOIN contents AS content ON content.id = task.content_id
         WHERE task.id = $1",
    )
    .bind(task_id)
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(
        (status.as_str(), retries, summary),
        ("pending", retry_limit, None)
    );
}
