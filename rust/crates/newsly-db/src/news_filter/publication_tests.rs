use super::*;
use crate::briefing_refresh::{
    BriefingPendingIdentity, BriefingRefreshLens, BriefingRefreshMode, BriefingRefreshPublication,
    ComposedBriefingAppend, ComposedBriefingSegment, PreparedBriefingRefresh,
};
use std::time::Duration;

#[path = "full_publication_tests.rs"]
mod full_publication_tests;

async fn publication(pool: &PgPool, label: &str) -> (i64, i64, BriefingRefreshPublication) {
    let user_id = user(pool, label).await;
    let news_id = news(pool, 1).await[0];
    let task_id = task(pool, user_id).await;
    let prepared = prepare(pool, task_id, user_id).await;
    assert!(store(pool, task_id, user_id, &prepared, false).await);
    let lens_id:i64=sqlx::query_scalar("INSERT INTO briefing_lenses(user_id,key,tier,title,deck,position,status,centroid_weight) VALUES($1::bigint::integer,'news-test','news','News','Evidence',2,'active',1) RETURNING id::bigint")
        .bind(user_id).fetch_one(pool).await.unwrap();
    sqlx::query("INSERT INTO briefing_states(user_id,version,masthead_title,masthead_deck) VALUES($1::bigint::integer,0,'Test edition','Evidence')")
        .bind(user_id).execute(pool).await.unwrap();
    let pending_id:i64=sqlx::query_scalar("INSERT INTO briefing_pending_sources(user_id,lens_key,source_kind,source_id,enqueued_at,filter_revision,filter_input_hash,filter_outcome) SELECT $1::bigint::integer,'news-test','news',id,timezone('UTC',now()),1,news_filter_input_hash(news_items),'decided' FROM news_items WHERE id::bigint=$2 RETURNING id::bigint")
        .bind(user_id).bind(news_id).fetch_one(pool).await.unwrap();
    let key = format!("news:{news_id}");
    let publication = BriefingRefreshPublication {
        prepared: PreparedBriefingRefresh {
            task_id,
            user_id,
            mode: BriefingRefreshMode::Append,
            starting_version: 0,
            prepared_state_changed: false,
            append_batches: vec![],
            compaction_batches: vec![],
        },
        append_segments: vec![ComposedBriefingAppend {
            pending_rows: vec![BriefingPendingIdentity {
                id: pending_id,
                source_kind: "news".into(),
                source_id: news_id,
                lens_key: "news-test".into(),
            }],
            segment: ComposedBriefingSegment {
                lens: BriefingRefreshLens {
                    id: lens_id,
                    key: "news-test".into(),
                    tier: "news".into(),
                    title: "News".into(),
                    deck: "Evidence".into(),
                    position: 2,
                },
                blocks: json!([{"type":"passage"}]),
                markdown_raw: "Factual evidence".into(),
                narration_text: "Factual evidence".into(),
                source_keys: vec![key.clone()],
                event_groups: vec![vec![key]],
                model: "test".into(),
                prompt_version: crate::BRIEFING_COMPOSITION_PROMPT_VERSION.into(),
            },
        }],
        compactions: vec![],
        embedding_usage: vec![],
        finalized_at: chrono::Utc::now(),
    };
    (user_id, news_id, publication)
}

#[sqlx::test]
async fn settings_apply_rejects_already_composed_pending_news_at_publication(pool: PgPool) {
    let (user_id, _, publication) = publication(&pool, "publication-edit").await;
    let mut save_tx = pool.begin().await.unwrap();
    news_filter_preferences::save(&mut save_tx, user_id, 1, "Only astronomy", true)
        .await
        .unwrap();
    save_tx.commit().await.unwrap();
    let mut publish_tx = pool.begin().await.unwrap();
    let applied = crate::apply_briefing_refresh(&mut publish_tx, &publication, &config())
        .await
        .unwrap();
    publish_tx.commit().await.unwrap();
    assert!(applied.stale);
    assert_eq!(applied.appended_segments, 0);
    let count: i64 =
        sqlx::query_scalar("SELECT count(*) FROM briefing_segments WHERE user_id::bigint=$1")
            .bind(user_id)
            .fetch_one(&pool)
            .await
            .unwrap();
    assert_eq!(
        count, 0,
        "composed prose cannot escape after its pending ownership was invalidated by Apply"
    );
}

#[sqlx::test]
async fn summary_change_rejects_already_composed_news_at_publication(pool: PgPool) {
    let (user_id, news_id, publication) = publication(&pool, "publication-summary").await;
    sqlx::query(
        "UPDATE news_items SET summary_text='Materially changed evidence' WHERE id::bigint=$1",
    )
    .bind(news_id)
    .execute(&pool)
    .await
    .unwrap();
    let mut publish_tx = pool.begin().await.unwrap();
    let applied = crate::apply_briefing_refresh(&mut publish_tx, &publication, &config())
        .await
        .unwrap();
    publish_tx.commit().await.unwrap();
    assert!(applied.stale);
    assert_eq!(applied.appended_segments, 0);
    let count: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM briefing_pending_sources WHERE user_id::bigint=$1",
    )
    .bind(user_id)
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(
        count, 1,
        "stale summary cannot publish and must remain available for fresh evaluation"
    );
}

#[sqlx::test]
async fn settings_save_waits_for_publication_user_lock_then_clears_unpublished_identity(
    pool: PgPool,
) {
    let (user_id, _, publication) = publication(&pool, "publication-lock").await;
    let mut publication_lock = pool.begin().await.unwrap();
    assert!(
        lock_active_user(&mut publication_lock, user_id)
            .await
            .unwrap()
    );
    let (pid_send, pid_receive) = tokio::sync::oneshot::channel();
    let save_pool = pool.clone();
    let save = tokio::spawn(async move {
        let mut tx = save_pool.begin().await.unwrap();
        let pid: i32 = sqlx::query_scalar("SELECT pg_backend_pid()")
            .fetch_one(&mut *tx)
            .await
            .unwrap();
        pid_send.send(pid).unwrap();
        let saved = news_filter_preferences::save(&mut tx, user_id, 1, "Only astronomy", true)
            .await
            .unwrap();
        tx.commit().await.unwrap();
        saved
    });
    let save_pid = pid_receive.await.unwrap();
    tokio::time::timeout(Duration::from_secs(5),async {
        loop {
            let waiting:bool=sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE pid=$1 AND wait_event_type='Lock')")
                .bind(save_pid).fetch_one(&pool).await.unwrap();
            if waiting {break;}
            tokio::task::yield_now().await;
        }
    }).await.expect("Settings must wait on publication's user SHARE lock");
    assert!(!save.is_finished());
    publication_lock.commit().await.unwrap();
    assert_eq!(save.await.unwrap().revision, 2);
    let mut publish_tx = pool.begin().await.unwrap();
    let applied = crate::apply_briefing_refresh(&mut publish_tx, &publication, &config())
        .await
        .unwrap();
    publish_tx.commit().await.unwrap();
    assert!(applied.stale);
    assert_eq!(applied.appended_segments, 0);
}

#[sqlx::test]
async fn settings_dedupe_does_not_wait_on_finalizer_processing_task_lock(pool: PgPool) {
    let user_id = user(&pool, "lock-order").await;
    let task_id = task(&pool, user_id).await;
    let key = format!("briefing_refresh:{user_id}:append");
    sqlx::query("UPDATE processing_tasks SET dedupe_key=$2 WHERE id::bigint=$1")
        .bind(task_id)
        .bind(&key)
        .execute(&pool)
        .await
        .unwrap();
    let mut finalizer = pool.begin().await.unwrap();
    sqlx::query("SELECT id FROM processing_tasks WHERE id::bigint=$1 FOR UPDATE")
        .bind(task_id)
        .execute(&mut *finalizer)
        .await
        .unwrap();
    let mut settings = pool.begin().await.unwrap();
    news_filter_preferences::save(&mut settings, user_id, 1, "Changed preference", true)
        .await
        .unwrap();
    let pending_finalizer = tokio::spawn(async move {
        assert!(lock_active_user(&mut finalizer, user_id).await.unwrap());
        finalizer.commit().await.unwrap();
    });
    // Mirror QueueKernel's conflict-only insertion against the already processing append task.
    // That crate is intentionally not a dependency of newsly-db; the finalizer lock is real.
    let enqueued=tokio::time::timeout(Duration::from_secs(3),sqlx::query("INSERT INTO processing_tasks(task_type,payload,status,queue_name,owner_user_id,dedupe_key,executor_runtime,executor_version,executor_namespace) VALUES('briefing_refresh','{}','pending','llm',$1::bigint::integer,$2,'rust',1,'briefing_refresh') ON CONFLICT(dedupe_key) WHERE dedupe_key IS NOT NULL AND status IN ('pending','processing') DO NOTHING")
        .bind(user_id).bind(&key).execute(&mut *settings)).await;
    if enqueued.is_err() {
        settings.rollback().await.unwrap();
        pending_finalizer.await.unwrap();
        panic!(
            "Settings enqueue waits on task locked by finalizer while holding user UPDATE: lock inversion"
        );
    }
    assert_eq!(enqueued.unwrap().unwrap().rows_affected(), 0);
    assert!(
        !crate::expedite_pending_briefing_refresh(&mut settings, task_id, chrono::Utc::now())
            .await
            .unwrap()
    );
    settings.commit().await.unwrap();
    tokio::time::timeout(Duration::from_secs(3), pending_finalizer)
        .await
        .unwrap()
        .unwrap();
}

#[sqlx::test]
async fn unchanged_current_decision_and_pending_identity_publish_news(pool: PgPool) {
    let (_, _, publication) = publication(&pool, "publication-current").await;
    let mut tx = pool.begin().await.unwrap();
    let applied = crate::apply_briefing_refresh(&mut tx, &publication, &config())
        .await
        .unwrap();
    tx.commit().await.unwrap();
    assert!(
        !applied.stale,
        "control fixture must be publishable before exercising invalidation races"
    );
    assert_eq!(applied.appended_segments, 1);
}

async fn wait_for_lock(pool: &PgPool, pid: i32) {
    tokio::time::timeout(Duration::from_secs(5),async {
        loop {
            let waiting:bool=sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE pid=$1 AND wait_event_type='Lock')")
                .bind(pid).fetch_one(pool).await.unwrap();
            if waiting {return;}
            tokio::task::yield_now().await;
        }
    }).await.expect("controlled participant must reach its expected database lock wait");
}

#[sqlx::test]
async fn queued_settings_edit_cannot_deadlock_prepare_against_finalizer(pool: PgPool) {
    let user_id = user(&pool, "three-way-lock").await;
    let task_id = task(&pool, user_id).await;
    let expected_lease: Uuid =
        sqlx::query_scalar("SELECT lease_token FROM processing_tasks WHERE id::bigint=$1")
            .bind(task_id)
            .fetch_one(&pool)
            .await
            .unwrap();
    let mut finalizer = pool.begin().await.unwrap();
    sqlx::query("SELECT id FROM processing_tasks WHERE id::bigint=$1 FOR UPDATE")
        .bind(task_id)
        .execute(&mut *finalizer)
        .await
        .unwrap();
    let (prepare_pid_send, prepare_pid_receive) = tokio::sync::oneshot::channel();
    let prepare_pool = pool.clone();
    let preparing = tokio::spawn(async move {
        let mut tx = prepare_pool.begin().await.unwrap();
        let pid: i32 = sqlx::query_scalar("SELECT pg_backend_pid()")
            .fetch_one(&mut *tx)
            .await
            .unwrap();
        prepare_pid_send.send(pid).unwrap();
        let result = prepare_refresh(&mut tx, task_id, user_id, expected_lease)
            .await
            .map_err(|error| error.to_string());
        if result.is_ok() {
            tx.commit().await.unwrap();
        } else {
            tx.rollback().await.unwrap();
        }
        result
    });
    wait_for_lock(&pool, prepare_pid_receive.await.unwrap()).await;
    let (save_pid_send, save_pid_receive) = tokio::sync::oneshot::channel();
    let save_pool = pool.clone();
    let settings = tokio::spawn(async move {
        let mut tx = save_pool.begin().await.unwrap();
        let pid: i32 = sqlx::query_scalar("SELECT pg_backend_pid()")
            .fetch_one(&mut *tx)
            .await
            .unwrap();
        save_pid_send.send(pid).unwrap();
        let result = news_filter_preferences::save(&mut tx, user_id, 1, "Changed preference", true)
            .await
            .map_err(|error| error.to_string());
        if result.is_ok() {
            tx.commit().await.unwrap();
        } else {
            tx.rollback().await.unwrap();
        }
        result
    });
    let save_pid = save_pid_receive.await.unwrap();
    // A queued Settings update must not block the finalizer's compatible user share lock.
    tokio::time::timeout(Duration::from_secs(5),async {
        loop {
            if settings.is_finished() {break;}
            let waiting:bool=sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE pid=$1 AND wait_event_type='Lock')")
                .bind(save_pid).fetch_one(&pool).await.unwrap();
            if waiting {break;}
            tokio::task::yield_now().await;
        }
    }).await.unwrap();
    let finalizing = tokio::spawn(async move {
        let result = lock_active_user(&mut finalizer, user_id)
            .await
            .map_err(|error| error.to_string());
        if result.is_ok() {
            finalizer.commit().await.unwrap();
        } else {
            finalizer.rollback().await.unwrap();
        }
        result
    });
    let prepare_result = tokio::time::timeout(Duration::from_secs(5), preparing)
        .await
        .unwrap()
        .unwrap();
    let save_result = tokio::time::timeout(Duration::from_secs(5), settings)
        .await
        .unwrap()
        .unwrap();
    let final_result = tokio::time::timeout(Duration::from_secs(5), finalizing)
        .await
        .unwrap()
        .unwrap();
    assert!(
        prepare_result.is_ok() && save_result.is_ok() && final_result.is_ok(),
        "task/user lock ordering deadlocked: prepare={prepare_result:?}, Settings={save_result:?}, finalizer={final_result:?}"
    );
}

#[sqlx::test]
async fn matching_replacement_summary_cannot_authorize_already_composed_old_evidence(pool: PgPool) {
    let (user_id, news_id, publication) = publication(&pool, "publication-new-cache").await;
    sqlx::query("UPDATE news_items SET summary_text='Replacement evidence' WHERE id::bigint=$1")
        .bind(news_id)
        .execute(&pool)
        .await
        .unwrap();
    let task_id = publication.prepared.task_id;
    let replacement = prepare(&pool, task_id, user_id).await;
    assert_eq!(replacement.candidates.len(), 1);
    assert!(store(&pool, task_id, user_id, &replacement, false).await);
    assert!(
        admits(&pool, user_id, news_id).await,
        "new cache correctly authorizes the replacement evidence"
    );
    let mut publish_tx = pool.begin().await.unwrap();
    let applied = crate::apply_briefing_refresh(&mut publish_tx, &publication, &config())
        .await
        .unwrap();
    publish_tx.commit().await.unwrap();
    assert!(
        applied.stale,
        "a new matching cache identity cannot validate prose composed from an older pending evidence hash"
    );
    assert_eq!(applied.appended_segments, 0);
}

#[sqlx::test]
async fn full_rebuild_preserves_exact_unavailable_pending_but_not_changed_evidence(pool: PgPool) {
    let (user_id, news_id, publication) = publication(&pool, "full-unavailable").await;
    sqlx::query("DELETE FROM news_filter_decisions WHERE user_id::bigint=$1")
        .bind(user_id)
        .execute(&pool)
        .await
        .unwrap();
    sqlx::query(
        "UPDATE briefing_pending_sources SET filter_outcome='unavailable' WHERE user_id::bigint=$1",
    )
    .bind(user_id)
    .execute(&pool)
    .await
    .unwrap();
    let task_id = publication.prepared.task_id;
    let prepared = prepare(&pool, task_id, user_id).await;
    assert!(
        prepared.candidates.is_empty(),
        "a previous unavailable admission must not repeat inference"
    );
    let pass = NewsFilterPass {
        revision: 1,
        examined: prepared.examined,
        more_work: prepared.more_work,
        unavailable: vec![],
    };
    let mut tx = pool.begin().await.unwrap();
    crate::prepare_briefing_refresh(
        &mut tx,
        task_id,
        user_id,
        BriefingRefreshMode::Full,
        &config(),
        &pass,
        prepared.lease_token,
    )
    .await
    .unwrap();
    tx.commit().await.unwrap();
    let retained:Vec<(i64,String,i64,bool)>=sqlx::query_as("SELECT p.source_id::bigint,p.filter_outcome,p.filter_revision,p.filter_input_hash=news_filter_input_hash(n) FROM briefing_pending_sources p JOIN news_items n ON n.id=p.source_id WHERE p.user_id::bigint=$1 AND p.source_kind='news'")
        .bind(user_id).fetch_all(&pool).await.unwrap();
    assert_eq!(
        retained,
        vec![(news_id, "unavailable".into(), 1, true)],
        "Full must preserve current unavailable provenance through delete/reseed"
    );
    sqlx::query("UPDATE news_items SET summary_text='Different evidence' WHERE id::bigint=$1")
        .bind(news_id)
        .execute(&pool)
        .await
        .unwrap();
    let mut tx = pool.begin().await.unwrap();
    crate::prepare_briefing_refresh(
        &mut tx,
        task_id,
        user_id,
        BriefingRefreshMode::Full,
        &config(),
        &pass,
        prepared.lease_token,
    )
    .await
    .unwrap();
    tx.commit().await.unwrap();
    let pending:i64=sqlx::query_scalar("SELECT count(*) FROM briefing_pending_sources WHERE user_id::bigint=$1 AND source_kind='news'").bind(user_id).fetch_one(&pool).await.unwrap();
    assert_eq!(
        pending, 0,
        "old unavailable evidence cannot authorize changed News during Full"
    );
}

#[sqlx::test]
async fn partial_full_rebuild_retains_published_news_outside_filter_window(pool: PgPool) {
    let (user_id, routed_id, mut publication) = publication(&pool, "full-partial-coverage").await;
    let mut tx = pool.begin().await.unwrap();
    let initial = crate::apply_briefing_refresh(&mut tx, &publication, &config())
        .await
        .unwrap();
    tx.commit().await.unwrap();
    assert_eq!(initial.appended_segments, 1);

    // This unread source belongs to the existing edition. Its publication keeps it
    // eligible even after it ages beyond the filter's inference/admission window.
    let deferred_id: i64 = sqlx::query_scalar(
        "INSERT INTO news_items(ingest_key,visibility_scope,platform,status,summary_text,
         summary_key_points,raw_metadata,ingested_at,created_at)
         VALUES('full-deferred-old','global','hackernews','ready','Older factual evidence',
         '[\"An older concrete fact\"]','{\"summary\":{\"title\":\"Older evidence\"}}',
         timezone('UTC',now())-interval '9 days',timezone('UTC',now())-interval '9 days')
         RETURNING id::bigint",
    )
    .fetch_one(&pool)
    .await
    .unwrap();
    let old_segment_id: i64 = sqlx::query_scalar(
        "UPDATE briefing_segments SET source_keys=$2,event_groups=$3
         WHERE user_id::bigint=$1 RETURNING id::bigint",
    )
    .bind(user_id)
    .bind(json!([
        format!("news:{routed_id}"),
        format!("news:{deferred_id}")
    ]))
    .bind(json!([
        [format!("news:{routed_id}")],
        [format!("news:{deferred_id}")]
    ]))
    .fetch_one(&pool)
    .await
    .unwrap();
    assert!(admits(&pool, user_id, deferred_id).await);

    let prepared = prepare(&pool, publication.prepared.task_id, user_id).await;
    let pass = NewsFilterPass {
        revision: prepared.preference.revision,
        examined: prepared.examined,
        more_work: prepared.more_work,
        unavailable: vec![],
    };
    let mut tx = pool.begin().await.unwrap();
    let crate::PrepareBriefingRefreshOutcome::Ready(seed) = crate::prepare_briefing_refresh(
        &mut tx,
        publication.prepared.task_id,
        user_id,
        BriefingRefreshMode::Full,
        &config(),
        &pass,
        prepared.lease_token,
    )
    .await
    .unwrap() else {
        panic!("active owner must prepare Full");
    };
    assert_eq!(seed.lens_assignment.pending_sources.len(), 2);
    // Model a bounded routing pass: A is assigned/composed, while B remains pending.
    let routed_pending: i64 = sqlx::query_scalar(
        "UPDATE briefing_pending_sources SET lens_key='news-test'
         WHERE user_id::bigint=$1 AND source_kind='news' AND source_id::bigint=$2
         RETURNING id::bigint",
    )
    .bind(user_id)
    .bind(routed_id)
    .fetch_one(&mut *tx)
    .await
    .unwrap();
    tx.commit().await.unwrap();
    publication.prepared.mode = BriefingRefreshMode::Full;
    publication.prepared.starting_version = seed.starting_version;
    publication.append_segments[0].pending_rows[0].id = routed_pending;

    let mut tx = pool.begin().await.unwrap();
    let applied = crate::apply_briefing_refresh(&mut tx, &publication, &config())
        .await
        .unwrap();
    tx.commit().await.unwrap();
    let old_status: String =
        sqlx::query_scalar("SELECT status FROM briefing_segments WHERE id::bigint=$1")
            .bind(old_segment_id)
            .fetch_one(&pool)
            .await
            .unwrap();
    let still_admitted = admits(&pool, user_id, deferred_id).await;
    assert_eq!(
        (old_status.as_str(), still_admitted),
        ("active", true),
        "partial Full must retain the old edition and its filter exemption; appended={}, compacted={}",
        applied.appended_segments,
        applied.compacted_segments,
    );
    assert!(applied.stale);
    assert_eq!(applied.appended_segments, 0);
    let prepared = prepare(&pool, publication.prepared.task_id, user_id).await;
    let pass = NewsFilterPass {
        revision: prepared.preference.revision,
        examined: prepared.examined,
        more_work: prepared.more_work,
        unavailable: vec![],
    };
    let mut tx = pool.begin().await.unwrap();
    crate::prepare_briefing_refresh(
        &mut tx,
        publication.prepared.task_id,
        user_id,
        BriefingRefreshMode::Sweep,
        &config(),
        &pass,
        prepared.lease_token,
    )
    .await
    .unwrap();
    tx.commit().await.unwrap();
    let pending: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM briefing_pending_sources WHERE user_id::bigint=$1",
    )
    .bind(user_id)
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(
        pending, 0,
        "a later sweep must not duplicate either old published source"
    );
    assert!(admits(&pool, user_id, deferred_id).await);
}
