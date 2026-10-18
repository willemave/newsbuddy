use newsly_db::{
    CreatedShareActionTask, LlmTaskActionProjection, NewShareActionTask,
    ShareActionFinalizationTask, finish_share_action_task, insert_share_action_task,
    load_share_action_action, mark_share_action_applied, mark_share_action_applying,
    request_share_action,
};
use newsly_queue::QueueKernel;
use serde_json::{Map, Value};
use sqlx::{Postgres, Transaction};

use super::ShareActionFinalizationError;
use super::submission::{ShareSubmissionPolicy, submit_content_action_with_channel};
use super::workflows::ContentActionInput;

/// Saves the source and completes its compatibility ledger in the caller's fenced transaction.
/// Analysis remains asynchronous; this operation makes no provider or sandbox calls.
pub async fn save_immediate_bookmark(
    transaction: &mut Transaction<'_, Postgres>,
    queue: &QueueKernel,
    user_id: i64,
    input: &Map<String, Value>,
    sandbox_root: &str,
) -> Result<(CreatedShareActionTask, LlmTaskActionProjection), ShareActionFinalizationError> {
    let policy = Map::from_iter([("default".to_owned(), Value::from("auto_apply"))]);
    let created = insert_share_action_task(
        transaction,
        &NewShareActionTask {
            user_id,
            mode: "bookmark_only",
            approval_policy: &policy,
            allowed_action: "save_to_knowledge",
            input,
            sandbox_root,
        },
    )
    .await?;
    let task = ShareActionFinalizationTask {
        id: created.id,
        user_id,
        mode: "bookmark_only".to_owned(),
        approval_policy: policy,
        allowed_actions: vec!["save_to_knowledge".to_owned()],
        input: input.clone(),
        status: "queued".to_owned(),
    };
    let action_input = ContentActionInput {
        url: input
            .get("url")
            .and_then(Value::as_str)
            .ok_or_else(|| ShareActionFinalizationError::WrongInput("bookmark URL".to_owned()))?
            .to_owned(),
        title: None,
        platform: None,
        content_type: None,
        instruction: input
            .get("instruction")
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .map(str::to_owned),
        chat_initial_message: None,
    };
    let stored_input = Map::from_iter([
        ("url".to_owned(), Value::from(action_input.url.clone())),
        (
            "instruction".to_owned(),
            action_input
                .instruction
                .clone()
                .map_or(Value::Null, Value::from),
        ),
    ]);
    let action = request_share_action(
        transaction,
        &task,
        "save_to_knowledge",
        &stored_input,
        None,
        "immediate_bookmark",
    )
    .await?;
    mark_share_action_applying(transaction, action.id).await?;
    let submitted = submit_content_action_with_channel(
        transaction,
        queue,
        user_id,
        &action_input,
        ShareSubmissionPolicy::content_saved(),
        "share_sheet",
    )
    .await?;
    let result = Map::from_iter([
        ("content_id".to_owned(), Value::from(submitted.content_id)),
        (
            "task_id".to_owned(),
            submitted.task_id.map_or(Value::Null, Value::from),
        ),
        (
            "already_exists".to_owned(),
            Value::from(submitted.already_exists),
        ),
    ]);
    mark_share_action_applied(transaction, action.id, &result).await?;
    finish_share_action_task(transaction, created.id).await?;
    let projection = load_share_action_action(transaction, created.id, action.id).await?;
    Ok((created, projection))
}

#[cfg(test)]
mod tests {
    use super::*;
    use sqlx::PgPool;

    #[sqlx::test(migrations = false)]
    async fn bookmark_is_saved_read_and_queued_before_acceptance(pool: PgPool) {
        newsly_db::run_migrations(&pool).await.expect("migrate");
        let user_id = sqlx::query_scalar::<_, i64>(
            "INSERT INTO users (apple_id, email, is_admin, is_active) VALUES ('immediate', 'immediate@example.test', FALSE, TRUE) RETURNING id::bigint",
        ).fetch_one(&pool).await.expect("user");
        let input = Map::from_iter([(
            "url".to_owned(),
            Value::from("https://example.test/immediate"),
        )]);
        let queue = QueueKernel::new(pool.clone());
        let mut content_id = None;
        for expected_existing in [false, true] {
            let mut transaction = pool.begin().await.expect("transaction");
            let (created, action) = save_immediate_bookmark(
                &mut transaction,
                &queue,
                user_id,
                &input,
                "/tmp/share-test",
            )
            .await
            .expect("save");
            transaction.commit().await.expect("commit");
            assert!(created.id > 0);
            assert_eq!(action.action_status, "applied");
            assert_eq!(action.action_result["already_exists"], expected_existing);
            let id = action.action_result["content_id"]
                .as_i64()
                .expect("content id");
            assert_eq!(*content_id.get_or_insert(id), id);
            let task = newsly_db::find_share_action_for_user(&pool, user_id, created.id)
                .await
                .expect("ledger")
                .expect("task");
            assert_eq!(task.status, "completed");
            assert_eq!(task.actions.len(), 1);
        }
        let (saves, reads, analyzes, llm_jobs) = sqlx::query_as::<_, (i64, i64, i64, i64)>(
            "SELECT (SELECT count(*) FROM content_knowledge_saves), (SELECT count(*) FROM content_read_status), (SELECT count(*) FROM processing_tasks WHERE task_type = 'analyze_url'), (SELECT count(*) FROM processing_tasks WHERE task_type = 'run_llm_task')",
        ).fetch_one(&pool).await.expect("counts");
        assert_eq!((saves, reads, analyzes, llm_jobs), (1, 1, 1, 0));
        let channel: String = sqlx::query_scalar(
            "SELECT content_metadata->>'submitted_via' FROM contents WHERE id::bigint = $1",
        )
        .bind(content_id)
        .fetch_one(&pool)
        .await
        .expect("submission channel");
        assert_eq!(channel, "share_sheet");

        sqlx::query("UPDATE contents SET status = 'completed', content_metadata = jsonb_build_object('summary', 'A readable completed summary') WHERE id::bigint = $1")
            .bind(content_id).execute(&pool).await.expect("complete");
        sqlx::query(
            "UPDATE processing_tasks SET status = 'completed' WHERE task_type = 'analyze_url'",
        )
        .execute(&pool)
        .await
        .expect("finish analysis");
        let mut transaction = pool.begin().await.expect("transaction");
        let mut input = input;
        input.insert("instruction".to_owned(), Value::from("   "));
        let (_, action) =
            save_immediate_bookmark(&mut transaction, &queue, user_id, &input, "/tmp/share-test")
                .await
                .expect("completed save");
        assert_eq!(action.action_result["task_id"], Value::Null);
        transaction.commit().await.expect("commit");
        let count: i64 = sqlx::query_scalar(
            "SELECT count(*) FROM processing_tasks WHERE task_type = 'analyze_url'",
        )
        .fetch_one(&pool)
        .await
        .expect("analyze count");
        assert_eq!(count, 1);
    }

    #[sqlx::test(migrations = false)]
    async fn inactive_bookmark_user_creates_no_rows(pool: PgPool) {
        newsly_db::run_migrations(&pool).await.expect("migrate");
        let user_id = sqlx::query_scalar::<_, i64>(
            "INSERT INTO users (apple_id, email, is_admin, is_active) VALUES ('inactive-immediate', 'inactive@example.test', FALSE, FALSE) RETURNING id::bigint",
        ).fetch_one(&pool).await.expect("user");
        let input = Map::from_iter([(
            "url".to_owned(),
            Value::from("https://example.test/inactive"),
        )]);
        let mut transaction = pool.begin().await.expect("transaction");
        let result = save_immediate_bookmark(
            &mut transaction,
            &QueueKernel::new(pool.clone()),
            user_id,
            &input,
            "/tmp/share-test",
        )
        .await;
        assert!(matches!(
            result,
            Err(ShareActionFinalizationError::Repository(
                newsly_db::ShareActionRepositoryError::UserMissingOrInactive
            ))
        ));
        transaction.rollback().await.expect("rollback");
        let count: i64 = sqlx::query_scalar("SELECT count(*) FROM llm_tasks")
            .fetch_one(&pool)
            .await
            .expect("count");
        assert_eq!(count, 0);
    }
}

#[cfg(test)]
mod backlog_tests {
    use super::*;
    use newsly_db::{
        ShareActionPreparation, begin_share_action_preparation, finish_share_action_preparation,
    };
    use sqlx::PgPool;

    #[sqlx::test(migrations = false)]
    async fn queued_bookmark_prepares_a_deterministic_action_without_saving_before_approval(
        pool: PgPool,
    ) {
        newsly_db::run_migrations(&pool).await.expect("migrate");
        let user_id = sqlx::query_scalar::<_, i64>(
            "INSERT INTO users (apple_id, email, is_admin, is_active) VALUES ('queued-bookmark', 'queued@example.test', FALSE, TRUE) RETURNING id::bigint",
        ).fetch_one(&pool).await.expect("user");
        let policy = Map::from_iter([("default".to_owned(), Value::from("approval_required"))]);
        let input =
            Map::from_iter([("url".to_owned(), Value::from("https://example.test/queued"))]);
        let mut transaction = pool.begin().await.expect("transaction");
        let created = insert_share_action_task(
            &mut transaction,
            &NewShareActionTask {
                user_id,
                mode: "bookmark_only",
                approval_policy: &policy,
                allowed_action: "save_to_knowledge",
                input: &input,
                sandbox_root: "/tmp/share-test",
            },
        )
        .await
        .expect("queued task");
        let ShareActionPreparation::Ready(draft) =
            begin_share_action_preparation(&mut transaction, created.id, user_id)
                .await
                .expect("prepare")
        else {
            panic!("ready")
        };
        assert!(!draft.prepare_shared_source);
        let snapshot = finish_share_action_preparation(&mut transaction, draft, None)
            .await
            .expect("snapshot");
        let action =
            crate::share_actions::workflows::build_deterministic_bookmark_action(&snapshot)
                .expect("deterministic action");
        assert_eq!(action.action_name, "save_to_knowledge");
        let count: i64 = sqlx::query_scalar("SELECT count(*) FROM contents")
            .fetch_one(&mut *transaction)
            .await
            .expect("content count");
        assert_eq!(count, 0);
        transaction.commit().await.expect("commit");
    }
}

#[cfg(test)]
mod canonical_tests {
    use super::*;
    use sqlx::PgPool;

    #[sqlx::test(migrations = false)]
    async fn repeated_alias_save_reuses_ready_canonical_winner(pool: PgPool) {
        newsly_db::run_migrations(&pool).await.expect("migrate");
        let user_id = sqlx::query_scalar::<_, i64>(
            "INSERT INTO users (apple_id, email, is_admin, is_active) VALUES ('canonical-share', 'canonical-share@example.test', FALSE, TRUE) RETURNING id::bigint",
        ).fetch_one(&pool).await.expect("user");
        let winner = sqlx::query_scalar::<_, i64>(
            "INSERT INTO contents (url, content_type, status, is_aggregate, content_metadata) VALUES ('https://example.test/winner', 'article', 'completed', FALSE, '{\"summary\":\"Ready summary\"}') RETURNING id::bigint",
        ).fetch_one(&pool).await.expect("winner");
        let alias = sqlx::query_scalar::<_, i64>(
            "INSERT INTO contents (url, content_type, status, is_aggregate, content_metadata) VALUES ('https://example.test/alias', 'article', 'skipped', FALSE, jsonb_build_object('processing', jsonb_build_object('canonical_content_id', $1::text))) RETURNING id::bigint",
        ).bind(winner.to_string()).fetch_one(&pool).await.expect("alias");
        let input = Map::from_iter([("url".to_owned(), Value::from("https://example.test/alias"))]);
        for _ in 0..2 {
            let mut transaction = pool.begin().await.expect("transaction");
            let (_, action) = save_immediate_bookmark(
                &mut transaction,
                &QueueKernel::new(pool.clone()),
                user_id,
                &input,
                "/tmp/share-test",
            )
            .await
            .expect("canonical save");
            assert_eq!(action.action_result["content_id"], winner);
            assert_eq!(action.action_result["task_id"], Value::Null);
            transaction.commit().await.expect("commit");
        }
        let saved: Vec<i64> =
            sqlx::query_scalar("SELECT content_id::bigint FROM content_knowledge_saves")
                .fetch_all(&pool)
                .await
                .expect("saves");
        assert_eq!(saved, vec![winner]);
        let reads: Vec<i64> =
            sqlx::query_scalar("SELECT content_id::bigint FROM content_read_status")
                .fetch_all(&pool)
                .await
                .expect("reads");
        assert_eq!(reads, vec![winner]);
        let tasks: i64 = sqlx::query_scalar(
            "SELECT count(*) FROM processing_tasks WHERE task_type = 'analyze_url'",
        )
        .fetch_one(&pool)
        .await
        .expect("tasks");
        assert_eq!(tasks, 0);
        let status: String =
            sqlx::query_scalar("SELECT status FROM contents WHERE id::bigint = $1")
                .bind(alias)
                .fetch_one(&pool)
                .await
                .expect("alias status");
        assert_eq!(status, "skipped");
    }

    #[sqlx::test(migrations = false)]
    async fn invalid_alias_references_roll_back_without_saving(pool: PgPool) {
        newsly_db::run_migrations(&pool).await.expect("migrate");
        let user_id = sqlx::query_scalar::<_, i64>(
            "INSERT INTO users (apple_id, email, is_admin, is_active) VALUES ('invalid-canonical-share', 'invalid-canonical@example.test', FALSE, TRUE) RETURNING id::bigint",
        ).fetch_one(&pool).await.expect("user");
        let alias = sqlx::query_scalar::<_, i64>(
            "INSERT INTO contents (url, content_type, status, is_aggregate) VALUES ('https://example.test/broken-alias', 'article', 'skipped', FALSE) RETURNING id::bigint",
        ).fetch_one(&pool).await.expect("alias");
        let input = Map::from_iter([(
            "url".to_owned(),
            Value::from("https://example.test/broken-alias"),
        )]);
        let other_alias = sqlx::query_scalar::<_, i64>(
            "INSERT INTO contents (url, content_type, status, is_aggregate, content_metadata) VALUES ('https://example.test/other-broken-alias', 'article', 'skipped', FALSE, jsonb_build_object('canonical_content_id', $1::bigint)) RETURNING id::bigint",
        ).bind(alias).fetch_one(&pool).await.expect("other alias");
        for target in [alias, other_alias, i64::from(i32::MAX)] {
            sqlx::query("UPDATE contents SET content_metadata = jsonb_build_object('canonical_content_id', $2::bigint) WHERE id::bigint = $1")
                .bind(alias).bind(target).execute(&pool).await.expect("pointer");
            let mut transaction = pool.begin().await.expect("transaction");
            let result = save_immediate_bookmark(
                &mut transaction,
                &QueueKernel::new(pool.clone()),
                user_id,
                &input,
                "/tmp/share-test",
            )
            .await;
            assert!(matches!(
                result,
                Err(ShareActionFinalizationError::Submission(
                    crate::share_actions::submission::ShareSubmissionError::Repository(
                        newsly_db::ContentSubmissionRepositoryError::InvalidCanonicalReference
                    )
                ))
            ));
            transaction.rollback().await.expect("rollback");
        }
        let (saves, ledgers, tasks): (i64, i64, i64) = sqlx::query_as(
            "SELECT (SELECT count(*) FROM content_knowledge_saves), (SELECT count(*) FROM llm_tasks), (SELECT count(*) FROM processing_tasks)",
        ).fetch_one(&pool).await.expect("counts");
        assert_eq!((saves, ledgers, tasks), (0, 0, 0));
    }
}
