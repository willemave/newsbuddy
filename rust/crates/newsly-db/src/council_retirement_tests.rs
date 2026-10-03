mod fixtures;

use chrono::{Duration, Utc};
use serde_json::Value;
use sqlx::PgPool;

use self::fixtures::*;
use crate::chat::{StageChatMessageInput, StageChatTurnOutcome, stage_chat_message};
use crate::chat_tasks::{ChatTurnKind, ChatTurnProcessingContext};
use crate::verify_council_retirement;

#[sqlx::test(migrations = false)]
async fn populated_database_requires_the_explicit_barrier(pool: PgPool) {
    let fixture = legacy_council(&pool, "barrier").await;
    let mut transaction = pool.begin().await.unwrap();
    sqlx::query("RESET newsly.maintenance_barrier_confirmed")
        .execute(&mut *transaction)
        .await
        .unwrap();
    let error = sqlx::raw_sql(RETIREMENT_SQL)
        .execute(&mut *transaction)
        .await
        .expect_err("a populated database must require the barrier");
    assert!(
        error
            .to_string()
            .contains("requires a confirmed maintenance barrier"),
        "unexpected migration error: {error}"
    );
    drop(transaction);
    let council_mode: bool =
        sqlx::query_scalar("SELECT council_mode FROM chat_sessions WHERE id::bigint = $1")
            .bind(fixture.parent_id)
            .fetch_one(&pool)
            .await
            .unwrap();
    assert!(council_mode, "the rejected migration must not mutate rows");
}

#[sqlx::test(migrations = false)]
async fn selected_branch_is_collapsed_without_copying_branch_history(pool: PgPool) {
    let fixture = legacy_council(&pool, "selected").await;
    let before = durable_messages(&pool, &fixture.selected_followup_ids).await;

    apply_retirement(&pool).await.unwrap();
    assert_retired_graph(&pool, &fixture).await;
    let after = durable_messages(&pool, &fixture.selected_followup_ids).await;
    assert_eq!(
        after, before,
        "moving follow-ups must preserve their durable rows"
    );
    for (row, prompt, explicit_null_run) in [
        (&before[0], "Selected follow-up one", false),
        (&before[1], "Selected follow-up two", true),
    ] {
        assert_eq!(
            row.2,
            council_followup_processing_context(
                fixture.user_id,
                fixture.selected_id,
                fixture.parent_id,
                prompt,
                explicit_null_run,
            )
        );
    }

    let detail = crate::get_chat_session_detail(&pool, fixture.user_id, fixture.parent_id)
        .await
        .unwrap();
    let crate::ChatRecordAccess::Found(detail) = detail else {
        panic!("retired parent should remain readable");
    };
    let rendered = detail
        .messages
        .iter()
        .map(|message| message.content.as_str())
        .collect::<Vec<_>>();
    assert!(rendered.contains(&"Selected response"));
    assert!(rendered.contains(&"Selected follow-up one"));
    assert!(rendered.contains(&"Selected answer two"));
}

#[sqlx::test(migrations = false)]
async fn legacy_successful_boundary_does_not_require_newer_run_metadata(pool: PgPool) {
    let fixture = legacy_council(&pool, "legacy-boundary").await;
    sqlx::query("UPDATE chat_messages SET processing_context = NULL WHERE id::bigint = $1")
        .bind(fixture.selected_run_id)
        .execute(&pool)
        .await
        .unwrap();
    assert!(
        crate::verify_council_retirement(&pool)
            .await
            .unwrap()
            .is_ready()
    );
    apply_retirement(&pool).await.unwrap();
    assert_retired_graph(&pool, &fixture).await;
}

#[sqlx::test(migrations = false)]
async fn latest_council_run_is_the_fallback_boundary_for_an_all_failed_branch(pool: PgPool) {
    let fixture = legacy_council(&pool, "failed-boundary").await;
    let base = Utc::now();
    let retry_id = insert_message(
        &pool,
        fixture.selected_id,
        "Ask the council",
        Some("Selected expert could not respond"),
        "failed",
        None,
        Some(council_processing_context(
            fixture.user_id,
            fixture.selected_id,
            fixture.parent_id,
            fixture.council_message_id,
            "retry",
        )),
        base,
    )
    .await;
    let after_retry_id = insert_message(
        &pool,
        fixture.selected_id,
        "After failed candidates",
        Some("Still useful"),
        "completed",
        None,
        None,
        base + Duration::seconds(1),
    )
    .await;
    sqlx::query("UPDATE chat_sessions SET branch_start_message_id = NULL WHERE id::bigint = $1")
        .bind(fixture.selected_id)
        .execute(&pool)
        .await
        .unwrap();

    apply_retirement(&pool).await.unwrap();
    assert_eq!(
        message_session(&pool, after_retry_id).await,
        fixture.parent_id
    );
    assert_eq!(message_session(&pool, retry_id).await, fixture.selected_id);
}

#[sqlx::test(migrations = false)]
async fn successful_retry_boundary_leaves_older_followups_in_the_archive(pool: PgPool) {
    let fixture = legacy_council(&pool, "retry-boundary").await;
    let base = Utc::now();
    let retry_id = insert_message(
        &pool,
        fixture.selected_id,
        "Ask the council",
        Some("Retried selected response"),
        "completed",
        None,
        Some(council_processing_context(
            fixture.user_id,
            fixture.selected_id,
            fixture.parent_id,
            fixture.council_message_id,
            "retry",
        )),
        base,
    )
    .await;
    let current_followup_id = insert_message(
        &pool,
        fixture.selected_id,
        "After retry",
        Some("Current branch answer"),
        "completed",
        None,
        None,
        base + Duration::seconds(1),
    )
    .await;
    sqlx::query(
        "UPDATE chat_sessions SET branch_start_message_id = $2::bigint::integer WHERE id::bigint = $1",
    )
    .bind(fixture.selected_id)
    .bind(retry_id)
    .execute(&pool)
    .await
    .unwrap();

    apply_retirement(&pool).await.unwrap();
    let old_locations = sqlx::query_scalar::<_, i64>(
        "SELECT DISTINCT session_id::bigint FROM chat_messages WHERE id::bigint = ANY($1)",
    )
    .bind(&fixture.selected_followup_ids)
    .fetch_all(&pool)
    .await
    .unwrap();
    assert_eq!(old_locations, vec![fixture.selected_id]);
    assert_eq!(
        message_session(&pool, current_followup_id).await,
        fixture.parent_id
    );
}

#[sqlx::test(migrations = false)]
async fn active_council_work_aborts_atomically(pool: PgPool) {
    let fixture = legacy_council(&pool, "processing").await;
    set_message_status(&pool, fixture.selected_run_id, "processing").await;
    let before = council_snapshot(&pool, fixture.parent_id, fixture.selected_id).await;
    let error = apply_retirement(&pool)
        .await
        .expect_err("active branch work must block retirement");
    assert!(database_error_message(&error).contains("processing"));
    let after = council_snapshot(&pool, fixture.parent_id, fixture.selected_id).await;
    assert_eq!(after, before, "the failed migration must roll back fully");

    set_message_status(&pool, fixture.selected_run_id, "completed").await;
    set_message_status(&pool, fixture.council_message_id, "processing").await;
    let error = apply_retirement(&pool)
        .await
        .expect_err("a processing parent aggregate must block retirement");
    assert!(database_error_message(&error).contains("processing"));

    set_message_status(&pool, fixture.council_message_id, "completed").await;
    let hidden_processing_id = insert_message(
        &pool,
        fixture.alternative_id,
        "Unlinked hidden work",
        None,
        "processing",
        None,
        Some(ordinary_processing_context(
            fixture.user_id,
            fixture.alternative_id,
            "Unlinked hidden work",
        )),
        Utc::now(),
    )
    .await;
    let error = apply_retirement(&pool)
        .await
        .expect_err("any processing row on a hidden branch must block retirement");
    assert!(database_error_message(&error).contains("processing"));
    assert_eq!(
        message_session(&pool, hidden_processing_id).await,
        fixture.alternative_id
    );
}

#[sqlx::test(migrations = false)]
async fn invalid_selected_child_links_fail_closed(pool: PgPool) {
    let fixture = legacy_council(&pool, "wrong-owner").await;
    let other_user = insert_user(&pool, "wrong-owner-other").await;
    sqlx::query("UPDATE chat_sessions SET user_id = $2::bigint::integer WHERE id::bigint = $1")
        .bind(fixture.selected_id)
        .bind(other_user)
        .execute(&pool)
        .await
        .unwrap();
    let error = apply_retirement(&pool)
        .await
        .expect_err("a selected child owned by another user must fail closed");
    assert!(database_error_message(&error).contains("branch"));
    let still_active: bool =
        sqlx::query_scalar("SELECT council_mode FROM chat_sessions WHERE id::bigint = $1")
            .bind(fixture.parent_id)
            .fetch_one(&pool)
            .await
            .unwrap();
    assert!(still_active);
}

#[sqlx::test(migrations = false)]
async fn orphaned_selected_child_links_fail_closed(pool: PgPool) {
    let fixture = legacy_council(&pool, "orphan").await;
    sqlx::query(
        "UPDATE chat_sessions SET active_child_session_id = 2147483647 WHERE id::bigint = $1",
    )
    .bind(fixture.parent_id)
    .execute(&pool)
    .await
    .unwrap();
    let error = apply_retirement(&pool)
        .await
        .expect_err("an orphaned selected child must fail closed");
    assert!(database_error_message(&error).contains("branch"));
}

#[sqlx::test(migrations = false)]
async fn preflight_is_read_only_and_classifies_migration_blockers(pool: PgPool) {
    let fixture = legacy_council(&pool, "preflight").await;
    let ready = verify_council_retirement(&pool).await.unwrap();
    assert!(ready.is_ready());
    assert_eq!(ready.processing_message_count, 0);
    assert_eq!(ready.malformed_session_count, 0);

    let before = sqlx::query_as::<_, (bool, i64, i64)>(
        r#"
        SELECT council_mode,
               (SELECT count(*)::bigint FROM chat_sessions),
               (SELECT count(*)::bigint FROM chat_messages)
        FROM chat_sessions WHERE id::bigint = $1
        "#,
    )
    .bind(fixture.parent_id)
    .fetch_one(&pool)
    .await
    .unwrap();
    verify_council_retirement(&pool).await.unwrap();
    let after = sqlx::query_as::<_, (bool, i64, i64)>(
        r#"
        SELECT council_mode,
               (SELECT count(*)::bigint FROM chat_sessions),
               (SELECT count(*)::bigint FROM chat_messages)
        FROM chat_sessions WHERE id::bigint = $1
        "#,
    )
    .bind(fixture.parent_id)
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(after, before, "preflight must not mutate durable state");

    set_message_status(&pool, fixture.selected_run_id, "processing").await;
    let blocked = verify_council_retirement(&pool).await.unwrap();
    assert!(!blocked.is_ready());
    assert_eq!(blocked.processing_message_count, 1);
    assert_eq!(
        blocked.processing_message_ids,
        vec![fixture.selected_run_id]
    );
    assert_eq!(blocked.malformed_session_count, 0);
    let error = apply_retirement(&pool)
        .await
        .expect_err("the blocker reported by preflight must also block migration");
    assert!(database_error_message(&error).contains("processing"));
}

#[sqlx::test(migrations = false)]
async fn retirement_is_idempotent_and_ordinary_chat_remains_usable(pool: PgPool) {
    let fixture = legacy_council(&pool, "idempotent").await;
    let ordinary_id = insert_session(&pool, fixture.user_id, None, false, None).await;
    let old_context = ordinary_processing_context(fixture.user_id, ordinary_id, "Already queued");
    let queued_id = insert_message(
        &pool,
        ordinary_id,
        "Already queued",
        None,
        "processing",
        None,
        Some(old_context.clone()),
        Utc::now(),
    )
    .await;

    apply_retirement(&pool).await.unwrap();
    assert_retired_graph(&pool, &fixture).await;
    apply_retirement(&pool).await.unwrap();
    assert_retired_graph(&pool, &fixture).await;

    let stored = sqlx::query_as::<_, (String, Value)>(
        "SELECT status, processing_context FROM chat_messages WHERE id::bigint = $1",
    )
    .bind(queued_id)
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(stored.0, "processing");
    assert_eq!(stored.1, old_context);
    let decoded: ChatTurnProcessingContext = serde_json::from_value(stored.1).unwrap();
    assert!(decoded.council_run.is_none());
    assert_eq!(decoded.session.parent_session_id, None);

    let mut transaction = pool.begin().await.unwrap();
    let staged = stage_chat_message(
        &mut transaction,
        &StageChatMessageInput {
            user_id: fixture.user_id,
            session_id: fixture.parent_id,
            user_prompt: "Continue as an ordinary chat",
        },
    )
    .await
    .unwrap();
    let StageChatTurnOutcome::Staged(staged) = staged else {
        panic!("ordinary parent should accept a new turn after retirement");
    };
    let context: ChatTurnProcessingContext =
        serde_json::from_value(staged.processing_context.clone()).unwrap();
    assert_eq!(context.kind, ChatTurnKind::Article);
    assert_eq!(context.user_prompt, "Continue as an ordinary chat");
    assert_eq!(context.session.effective_session_id, fixture.parent_id);
    assert_eq!(context.session.visible_session_id, fixture.parent_id);
    let object = staged.processing_context.as_object().unwrap();
    assert!(!object.contains_key("council_run"));
    let session = object["session"].as_object().unwrap();
    for retired_key in [
        "parent_session_id",
        "is_hidden_from_history",
        "council_persona_id",
        "council_persona_name",
        "council_persona_prompt",
    ] {
        assert!(!session.contains_key(retired_key));
    }
    transaction.commit().await.unwrap();
}

#[sqlx::test(migrations = false)]
async fn database_constraints_prevent_old_binaries_from_restarting_council(pool: PgPool) {
    migrate_to_before_retirement(&pool).await;
    apply_retirement(&pool).await.unwrap();
    let user_id = insert_user(&pool, "constraints").await;

    let ordinary_id: i64 = sqlx::query_scalar(
        r#"
        INSERT INTO chat_sessions (
            user_id, title, session_type, llm_model, llm_provider, is_archived
        )
        VALUES ($1::bigint::integer, 'Ordinary', 'article', 'openai:gpt-5.6-terra', 'openai', FALSE)
        RETURNING id::bigint
        "#,
    )
    .bind(user_id)
    .fetch_one(&pool)
    .await
    .expect("ordinary inserts should receive retired-column defaults");

    for statement in [
        "UPDATE chat_sessions SET council_mode = TRUE WHERE id::bigint = $1",
        "UPDATE chat_sessions SET active_child_session_id = id WHERE id::bigint = $1",
        "UPDATE chat_sessions SET council_message_id = id WHERE id::bigint = $1",
        "UPDATE chat_sessions SET is_hidden_from_history = TRUE WHERE id::bigint = $1",
    ] {
        sqlx::query(statement)
            .bind(ordinary_id)
            .execute(&pool)
            .await
            .expect_err("retired council state must violate a database constraint");
    }
}
