use super::*;

async fn published_full(pool: &PgPool, label: &str) -> (i64, i64, BriefingRefreshPublication) {
    let (user_id, news_id, mut publication) = publication(pool, label).await;
    let mut tx = pool.begin().await.unwrap();
    let applied = crate::apply_briefing_refresh(&mut tx, &publication, &config())
        .await
        .unwrap();
    tx.commit().await.unwrap();
    publication.prepared.mode = BriefingRefreshMode::Full;
    publication.prepared.starting_version = applied.version;
    (user_id, news_id, publication)
}

#[sqlx::test]
async fn empty_full_cannot_remove_eligible_published_coverage(pool: PgPool) {
    let (user_id, _, mut publication) = published_full(&pool, "full-empty").await;
    publication.append_segments.clear();
    let mut tx = pool.begin().await.unwrap();
    let applied = crate::apply_briefing_refresh(&mut tx, &publication, &config())
        .await
        .unwrap();
    tx.commit().await.unwrap();
    assert!(applied.stale);
    assert_eq!(
        (applied.appended_segments, applied.compacted_segments),
        (0, 0)
    );
    let active: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM briefing_segments WHERE user_id::bigint=$1 AND status='active'",
    )
    .bind(user_id)
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(active, 1);
}

#[sqlx::test]
async fn empty_full_preserves_eligible_content_but_allows_removal_from_inbox(pool: PgPool) {
    let (user_id, _, mut publication) = published_full(&pool, "full-content").await;
    let content_id: i64 = sqlx::query_scalar(
        "INSERT INTO contents(content_type,url,title,status,content_metadata,is_aggregate)
         VALUES('article','https://example.test/full-content','Saved article','completed',
         '{\"image_generated_at\":\"2026-10-01\",\"image_url\":\"/static/test.png\"}',false) RETURNING id::bigint",
    ).fetch_one(&pool).await.unwrap();
    sqlx::query("INSERT INTO content_status(user_id,content_id,status) VALUES($1,$2,'inbox')")
        .bind(user_id)
        .bind(content_id)
        .execute(&pool)
        .await
        .unwrap();
    sqlx::query("UPDATE briefing_segments SET source_keys=$2 WHERE user_id::bigint=$1")
        .bind(user_id)
        .bind(json!([format!("content:{content_id}")]))
        .execute(&pool)
        .await
        .unwrap();
    publication.append_segments.clear();
    let mut tx = pool.begin().await.unwrap();
    let applied = crate::apply_briefing_refresh(&mut tx, &publication, &config())
        .await
        .unwrap();
    tx.commit().await.unwrap();
    assert!(applied.stale);
    assert_eq!(applied.compacted_segments, 0);
    sqlx::query("UPDATE content_status SET status='archived' WHERE user_id::bigint=$1 AND content_id::bigint=$2")
        .bind(user_id).bind(content_id).execute(&pool).await.unwrap();
    let mut tx = pool.begin().await.unwrap();
    let applied = crate::apply_briefing_refresh(&mut tx, &publication, &config())
        .await
        .unwrap();
    tx.commit().await.unwrap();
    assert!(!applied.stale);
    assert_eq!(applied.compacted_segments, 1);
}

#[sqlx::test]
async fn empty_full_can_remove_sources_that_are_no_longer_eligible(pool: PgPool) {
    let (user_id, news_id, mut publication) = published_full(&pool, "full-read").await;
    sqlx::query("INSERT INTO news_item_read_status(user_id,news_item_id,read_at,created_at) VALUES($1,$2,now(),now())")
        .bind(user_id).bind(news_id).execute(&pool).await.unwrap();
    publication.append_segments.clear();
    let mut tx = pool.begin().await.unwrap();
    let applied = crate::apply_briefing_refresh(&mut tx, &publication, &config())
        .await
        .unwrap();
    tx.commit().await.unwrap();
    assert!(!applied.stale);
    assert_eq!(
        (applied.appended_segments, applied.compacted_segments),
        (0, 1)
    );
}

#[sqlx::test]
async fn complete_full_atomically_replaces_old_coverage(pool: PgPool) {
    let (user_id, news_id, mut publication) = published_full(&pool, "full-complete").await;
    let pending: i64 = sqlx::query_scalar(
        "INSERT INTO briefing_pending_sources(user_id,lens_key,source_kind,source_id,filter_revision,filter_input_hash,filter_outcome)
         SELECT $1,'news-test','news',id,1,news_filter_input_hash(news_items),'decided' FROM news_items WHERE id::bigint=$2 RETURNING id::bigint")
        .bind(user_id).bind(news_id).fetch_one(&pool).await.unwrap();
    publication.append_segments[0].pending_rows[0].id = pending;
    sqlx::query("UPDATE briefing_segments SET status='degraded' WHERE user_id::bigint=$1")
        .bind(user_id)
        .execute(&pool)
        .await
        .unwrap();
    let mut tx = pool.begin().await.unwrap();
    let applied = crate::apply_briefing_refresh(&mut tx, &publication, &config())
        .await
        .unwrap();
    tx.commit().await.unwrap();
    assert!(!applied.stale);
    assert_eq!(
        (applied.appended_segments, applied.compacted_segments),
        (1, 1)
    );
    let pending: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM briefing_pending_sources WHERE user_id::bigint=$1",
    )
    .bind(user_id)
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(pending, 0);
}

#[sqlx::test]
async fn aborted_full_pending_is_pruned_before_incremental_composition(pool: PgPool) {
    let (user_id, _, publication) = published_full(&pool, "full-aborted").await;
    for mode in [BriefingRefreshMode::Full, BriefingRefreshMode::Sweep] {
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
            mode,
            &config(),
            &pass,
            prepared.lease_token,
        )
        .await
        .unwrap() else {
            panic!("active user");
        };
        assert_eq!(
            seed.lens_assignment.pending_sources.len(),
            usize::from(mode == BriefingRefreshMode::Full)
        );
        if mode == BriefingRefreshMode::Full {
            sqlx::query(
                "UPDATE briefing_pending_sources SET lens_key='news-test' WHERE user_id::bigint=$1",
            )
            .bind(user_id)
            .execute(&mut *tx)
            .await
            .unwrap();
        }
        tx.commit().await.unwrap();
        // Assignment commits, then the Full worker dies before publication.
    }
    let pending: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM briefing_pending_sources WHERE user_id::bigint=$1",
    )
    .bind(user_id)
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(pending, 0);
}
