use super::*;
const FP: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const OTHER: &str = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";

async fn fixture(pool: &PgPool) -> LearningDeckGenerationClaimFence {
    crate::run_migrations(pool).await.unwrap();
    let user: i64 = sqlx::query_scalar("INSERT INTO users(apple_id,email,is_active,is_admin) VALUES('deck-generation','deck@example.com',true,false) RETURNING id::bigint").fetch_one(pool).await.unwrap();
    let deck: i64 = sqlx::query_scalar("INSERT INTO learning_decks(user_id,source_kind,source_identity,source_metadata,title,artifact_object_keys,share_enabled) VALUES($1,'url','test-source','{}','Deck','[]',false) RETURNING id::bigint").bind(user).fetch_one(pool).await.unwrap();
    let task: i64 = sqlx::query_scalar("INSERT INTO llm_tasks(user_id,task_kind,mode,workflow_key,workflow_version,workflow_state,status,approval_policy,allowed_actions,tool_policy,input_json,output_json,artifact_manifest,usage_json,status_history,subject_id) VALUES($1,'learning_deck','learning_deck_presentation','learning_deck.presentation.v1',1,'running','running','{}','[]','{}','{}','{}','{}','{}','[]',$2) RETURNING id::bigint").bind(user).bind(deck).fetch_one(pool).await.unwrap();
    sqlx::query("UPDATE learning_decks SET latest_task_id=$1 WHERE id=$2")
        .bind(task)
        .bind(deck)
        .execute(pool)
        .await
        .unwrap();
    let token = Uuid::new_v4();
    let processing: i64 = sqlx::query_scalar("INSERT INTO processing_tasks(task_type,queue_name,status,owner_user_id,payload,locked_by,locked_at,lease_token,lease_expires_at,executor_runtime,executor_version,executor_namespace) VALUES('run_llm_task','llm','processing',$1,$2,'test',timezone('UTC',now()),$3,timezone('UTC',now())+interval '5 minutes','rust',1,'run_llm_task') RETURNING id::bigint").bind(user).bind(json!({"llm_task_id":task,"user_id":user,"task_kind":"learning_deck"})).bind(token).fetch_one(pool).await.unwrap();
    LearningDeckGenerationClaimFence {
        processing_task_id: processing,
        user_id: user,
        llm_task_id: task,
        deck_id: deck,
        locked_by: "test".into(),
        lease_token: token,
        retry_count: 0,
        executor_runtime: "rust".into(),
        executor_version: 1,
        executor_namespace: "run_llm_task".into(),
    }
}
async fn begin(
    pool: &PgPool,
    fence: &LearningDeckGenerationClaimFence,
) -> LearningDeckGenerationState {
    begin_learning_deck_generation(pool, fence, FP, 900)
        .await
        .unwrap()
        .unwrap()
}

#[sqlx::test]
async fn retry_and_input_changes_never_reset_budget_or_accept_stale_writes(pool: PgPool) {
    let mut fence = fixture(&pool).await;
    let first = begin(&pool, &fence).await;
    crate::track_artifact(&pool, "private/snapshot.json", fence.llm_task_id)
        .await
        .unwrap();
    assert!((895..=900).contains(&first.remaining_seconds));
    assert!(
        publish_learning_deck_draft(&pool, &fence, FP, "private/snapshot.json")
            .await
            .unwrap()
    );
    let old = fence.clone();
    fence.retry_count = 1;
    fence.lease_token = Uuid::new_v4();
    sqlx::query("UPDATE processing_tasks SET retry_count=$1,lease_token=$2 WHERE id=$3")
        .bind(fence.retry_count)
        .bind(fence.lease_token)
        .bind(fence.processing_task_id)
        .execute(&pool)
        .await
        .unwrap();
    assert!(
        begin_learning_deck_generation(&pool, &old, FP, 900)
            .await
            .unwrap()
            .is_none()
    );
    assert!(
        !publish_learning_deck_draft(&pool, &old, FP, "stale")
            .await
            .unwrap()
    );
    assert!(
        !publish_learning_deck_progress(&pool, &old, "stale")
            .await
            .unwrap()
    );
    let retry = begin(&pool, &fence).await;
    assert_eq!(retry.deadline_at, first.deadline_at);
    assert_eq!(
        retry.draft_object_key.as_deref(),
        Some("private/snapshot.json")
    );
    let mismatch = begin_learning_deck_generation(&pool, &fence, OTHER, 3540)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(mismatch.input_fingerprint, OTHER);
    assert!(mismatch.draft_object_key.is_none());
    assert_eq!(mismatch.deadline_at, first.deadline_at);
    assert!(
        !publish_learning_deck_draft(&pool, &fence, FP, "private/snapshot.json")
            .await
            .unwrap()
    );
    let tracked: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM artifact_cleanup_candidates WHERE object_key='private/snapshot.json'",
    )
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(
        tracked, 1,
        "discarded private bytes remain tracked for terminal cleanup"
    );
    crate::track_artifact(&pool, "private/new-snapshot.json", fence.llm_task_id)
        .await
        .unwrap();
    assert!(
        publish_learning_deck_draft(&pool, &fence, OTHER, "private/new-snapshot.json")
            .await
            .unwrap()
    );
    let unchanged = begin_learning_deck_generation(&pool, &fence, OTHER, 3540)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(unchanged.deadline_at, first.deadline_at);
    assert_eq!(
        unchanged.draft_object_key.as_deref(),
        Some("private/new-snapshot.json")
    );
    assert!(
        !publish_learning_deck_draft(&pool, &fence, OTHER, "wrong-input")
            .await
            .unwrap()
    );
    sqlx::query("UPDATE learning_deck_generation_state SET deadline_at=clock_timestamp()-interval '1 second'").execute(&pool).await.unwrap();
    assert_eq!(begin(&pool, &fence).await.remaining_seconds, 0);
    assert!(
        !publish_learning_deck_progress(&pool, &fence, "expired")
            .await
            .unwrap()
    );
    assert!(
        !publish_learning_deck_draft(&pool, &fence, FP, "expired")
            .await
            .unwrap()
    );
    assert!(
        learning_deck_generation_is_owned(&pool, &fence)
            .await
            .unwrap()
    );
    // A manual new job gets a new budget and never adopts the old job's private draft.
    sqlx::query("UPDATE llm_tasks SET status='failed' WHERE id=$1")
        .bind(fence.llm_task_id)
        .execute(&pool)
        .await
        .unwrap();
    let new_task: i64 = sqlx::query_scalar(
        "INSERT INTO llm_tasks(user_id,task_kind,mode,workflow_key,workflow_version,workflow_state,status,approval_policy,allowed_actions,tool_policy,input_json,output_json,artifact_manifest,usage_json,status_history,subject_id)
         SELECT user_id,task_kind,mode,workflow_key,workflow_version,'running','running',approval_policy,allowed_actions,tool_policy,input_json,'{}','{}','{}','[]',subject_id
         FROM llm_tasks WHERE id=$1 RETURNING id::bigint",
    ).bind(fence.llm_task_id).fetch_one(&pool).await.unwrap();
    fence.llm_task_id = new_task;
    sqlx::query("UPDATE processing_tasks SET payload=$1 WHERE id=$2")
        .bind(json!({"llm_task_id":new_task,"user_id":fence.user_id,"task_kind":"learning_deck"}))
        .bind(fence.processing_task_id)
        .execute(&pool)
        .await
        .unwrap();
    sqlx::query("UPDATE learning_decks SET latest_task_id=$1 WHERE id=$2")
        .bind(new_task)
        .bind(fence.deck_id)
        .execute(&pool)
        .await
        .unwrap();
    let manual = begin(&pool, &fence).await;
    assert!(manual.remaining_seconds > 895);
    assert!(manual.draft_object_key.is_none());
    assert!(manual.deadline_at > first.deadline_at);
}

#[sqlx::test]
async fn identity_payload_and_lifecycle_fence_writes(pool: PgPool) {
    let fence = fixture(&pool).await;
    begin(&pool, &fence).await;
    for invalid in [
        LearningDeckGenerationClaimFence {
            user_id: fence.user_id + 1,
            ..fence.clone()
        },
        LearningDeckGenerationClaimFence {
            deck_id: fence.deck_id + 1,
            ..fence.clone()
        },
        LearningDeckGenerationClaimFence {
            llm_task_id: fence.llm_task_id + 1,
            ..fence.clone()
        },
        LearningDeckGenerationClaimFence {
            locked_by: "other".into(),
            ..fence.clone()
        },
        LearningDeckGenerationClaimFence {
            executor_runtime: "python".into(),
            ..fence.clone()
        },
        LearningDeckGenerationClaimFence {
            executor_version: 2,
            ..fence.clone()
        },
        LearningDeckGenerationClaimFence {
            executor_namespace: "other".into(),
            ..fence.clone()
        },
    ] {
        assert!(
            !learning_deck_generation_is_owned(&pool, &invalid)
                .await
                .unwrap()
        );
        assert!(
            !publish_learning_deck_progress(&pool, &invalid, "blocked")
                .await
                .unwrap()
        );
    }
    let original_payload = json!({"llm_task_id":fence.llm_task_id,"user_id":fence.user_id,"task_kind":"learning_deck"});
    sqlx::query("UPDATE processing_tasks SET payload='{}'")
        .execute(&pool)
        .await
        .unwrap();
    assert!(
        !learning_deck_generation_is_owned(&pool, &fence)
            .await
            .unwrap()
    );
    sqlx::query("UPDATE processing_tasks SET payload=$1")
        .bind(original_payload)
        .execute(&pool)
        .await
        .unwrap();
    for (change, restore) in [
        (
            "UPDATE users SET is_active=false",
            "UPDATE users SET is_active=true",
        ),
        (
            "UPDATE processing_tasks SET lease_expires_at=timezone('UTC',now())-interval '1 second'",
            "UPDATE processing_tasks SET lease_expires_at=timezone('UTC',now())+interval '5 minutes'",
        ),
        (
            "UPDATE llm_tasks SET status='cancelled'",
            "UPDATE llm_tasks SET status='running'",
        ),
        (
            "UPDATE learning_decks SET deleted_at=now()",
            "UPDATE learning_decks SET deleted_at=NULL",
        ),
    ] {
        sqlx::query(change).execute(&pool).await.unwrap();
        assert!(
            !learning_deck_generation_is_owned(&pool, &fence)
                .await
                .unwrap(),
            "{change}"
        );
        assert!(
            !publish_learning_deck_draft(&pool, &fence, FP, "blocked")
                .await
                .unwrap()
        );
        sqlx::query(restore).execute(&pool).await.unwrap();
    }
    sqlx::query("UPDATE learning_decks SET latest_task_id=NULL")
        .execute(&pool)
        .await
        .unwrap();
    assert!(
        !learning_deck_generation_is_owned(&pool, &fence)
            .await
            .unwrap()
    );
}

#[sqlx::test]
async fn progress_is_bounded_and_deletion_collects_private_manifest_and_assets(pool: PgPool) {
    let fence = fixture(&pool).await;
    begin(&pool, &fence).await;
    assert!(
        publish_learning_deck_progress(&pool, &fence, &"界".repeat(400))
            .await
            .unwrap()
    );
    let (status, history): (String, serde_json::Value) =
        sqlx::query_as("SELECT status,status_history FROM llm_tasks WHERE id=$1")
            .bind(fence.llm_task_id)
            .fetch_one(&pool)
            .await
            .unwrap();
    assert_eq!(status, "running");
    assert_eq!(history[0]["note"].as_str().unwrap().chars().count(), 256);
    for key in [
        "private/manifest.json",
        "private/index.html",
        "private/image.png",
    ] {
        crate::track_artifact(&pool, key, fence.llm_task_id)
            .await
            .unwrap();
    }
    assert!(
        publish_learning_deck_draft(&pool, &fence, FP, "private/manifest.json")
            .await
            .unwrap()
    );
    let mut tx = pool.begin().await.unwrap();
    let deleted = crate::delete_learning_deck(&mut tx, fence.user_id, fence.deck_id)
        .await
        .unwrap()
        .unwrap();
    tx.commit().await.unwrap();
    assert_eq!(
        deleted.object_keys,
        vec![
            "private/image.png",
            "private/index.html",
            "private/manifest.json"
        ]
    );
    assert!(
        !publish_learning_deck_draft(&pool, &fence, FP, "late")
            .await
            .unwrap()
    );
    let count: i64 = sqlx::query_scalar("SELECT count(*) FROM learning_deck_generation_state")
        .fetch_one(&pool)
        .await
        .unwrap();
    assert_eq!(count, 0);
}

#[sqlx::test]
async fn checkpoint_and_deletion_do_not_deadlock_or_resurrect_state(pool: PgPool) {
    let fence = fixture(&pool).await;
    begin(&pool, &fence).await;
    let mut publication = pool.begin().await.unwrap();
    assert!(
        lock_owned_generation(&mut publication, &fence)
            .await
            .unwrap()
    );
    let mut delete_tx = pool.begin().await.unwrap();
    sqlx::query("SELECT id FROM learning_decks WHERE id = $1 FOR UPDATE")
        .bind(fence.deck_id)
        .execute(&mut *delete_tx)
        .await
        .unwrap();
    let delete_fence = fence.clone();
    let deletion = tokio::spawn(async move {
        crate::delete_learning_deck(&mut delete_tx, delete_fence.user_id, delete_fence.deck_id)
            .await
            .unwrap();
        delete_tx.commit().await.unwrap();
    });
    sqlx::query("UPDATE learning_deck_generation_state SET draft_object_key='private/raced.json'")
        .execute(&mut *publication)
        .await
        .unwrap();
    publication.commit().await.unwrap();
    tokio::time::timeout(std::time::Duration::from_secs(5), deletion)
        .await
        .unwrap()
        .unwrap();
    assert!(
        !learning_deck_generation_is_owned(&pool, &fence)
            .await
            .unwrap()
    );
    let count: i64 = sqlx::query_scalar("SELECT count(*) FROM learning_deck_generation_state")
        .fetch_one(&pool)
        .await
        .unwrap();
    assert_eq!(count, 0);
}

#[sqlx::test]
async fn publication_rechecks_budget_after_waiting_for_task_lock(pool: PgPool) {
    let fence = fixture(&pool).await;
    begin(&pool, &fence).await;
    let mut blocker = pool.begin().await.unwrap();
    sqlx::query("SELECT id FROM llm_tasks WHERE id=$1 FOR UPDATE")
        .bind(fence.llm_task_id)
        .execute(&mut *blocker)
        .await
        .unwrap();
    let publish_pool = pool.clone();
    let publish_fence = fence.clone();
    let publication = tokio::spawn(async move {
        let artifact = crate::StoredLearningDeckArtifact {
            storage_prefix: "published".into(),
            deck_object_key: "published/index.html".into(),
            source_notes_object_key: "published/notes.md".into(),
            source_notes_html_object_key: "published/notes.html".into(),
            thumbnail_object_key: None,
            artifact_object_keys: vec!["published/index.html".into()],
        };
        let empty = serde_json::Map::new();
        let mut tx = publish_pool.begin().await.unwrap();
        let result = crate::publish_learning_deck(
            &mut tx,
            &crate::PublishLearningDeck {
                task_id: publish_fence.llm_task_id,
                user_id: publish_fence.user_id,
                deck_id: publish_fence.deck_id,
                artifact: &artifact,
                browser_validation: &empty,
                source_metadata_updates: &empty,
                model_provider: "test",
                model_name: "test",
                sandbox_provider: "test",
                sandbox_id: None,
                agent_log_object_key: None,
                usage_json: &empty,
                vendor_usage: None,
            },
        )
        .await
        .unwrap();
        tx.commit().await.unwrap();
        result
    });
    sqlx::query("UPDATE learning_deck_generation_state SET deadline_at=clock_timestamp()-interval '1 second'")
        .execute(&mut *blocker).await.unwrap();
    blocker.commit().await.unwrap();
    let result = tokio::time::timeout(std::time::Duration::from_secs(5), publication)
        .await
        .unwrap()
        .unwrap();
    assert!(
        matches!(result,crate::PublishLearningDeckOutcome::Failed {error_type,..} if error_type=="generation_deadline_exceeded")
    );
    let (status,key):(String,Option<String>)=sqlx::query_as("SELECT task.status,deck.deck_object_key FROM llm_tasks task JOIN learning_decks deck ON deck.id=task.subject_id WHERE task.id=$1")
        .bind(fence.llm_task_id).fetch_one(&pool).await.unwrap();
    assert_eq!(status, "failed");
    assert!(key.is_none());
}
