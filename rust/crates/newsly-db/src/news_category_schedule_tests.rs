use chrono::{Datelike, Timelike};

use super::*;

#[test]
fn local_nights_follow_calendar_and_fractional_offsets() {
    let after = Utc.with_ymd_and_hms(2026, 9, 27, 0, 0, 0).unwrap();
    for timezone in [
        "America/Los_Angeles",
        "UTC",
        "Europe/Helsinki",
        "Australia/Lord_Howe",
        "Pacific/Chatham",
    ] {
        let night = next_news_category_night(42, timezone, after, None).unwrap();
        let tz = Tz::from_str(timezone).unwrap();
        let local = night.window_start_at.with_timezone(&tz);
        assert_eq!(local.hour(), 3, "{timezone}");
        assert_eq!(local.minute(), 0, "{timezone}");
        assert_eq!(
            night.due_at - night.window_start_at,
            Duration::minutes(deterministic_jitter_minutes(42))
        );
    }
}

#[test]
fn repeated_local_time_chooses_the_earlier_instant() {
    let tz = Tz::from_str("America/New_York").unwrap();
    let date = NaiveDate::from_ymd_opt(2026, 11, 1).unwrap();
    let local = date.and_hms_opt(1, 30, 0).unwrap();
    let expected = match tz.from_local_datetime(&local) {
        LocalResult::Ambiguous(first, second) => first.min(second).with_timezone(&Utc),
        other => panic!("expected fold, got {other:?}"),
    };
    assert_eq!(
        resolve_earlier(tz.from_local_datetime(&local))
            .unwrap()
            .with_timezone(&Utc),
        expected
    );
}

#[test]
fn missing_local_time_uses_first_valid_minute_in_window() {
    let tz = Tz::from_str("America/New_York").unwrap();
    let date = NaiveDate::from_ymd_opt(2026, 3, 8).unwrap();
    let resolved = first_valid_at_or_after(
        tz,
        date,
        NaiveTime::from_hms_opt(2, 0, 0).unwrap(),
        NaiveTime::from_hms_opt(4, 0, 0).unwrap(),
    )
    .unwrap();
    let local = resolved.with_timezone(&tz);
    assert_eq!((local.hour(), local.minute()), (3, 0));
}

#[test]
fn skipped_calendar_date_advances_without_adding_86400_seconds() {
    let after = Utc.with_ymd_and_hms(2011, 12, 29, 14, 0, 0).unwrap();
    let night = next_news_category_night(7, "Pacific/Apia", after, None).unwrap();
    assert_ne!(
        night.local_date,
        NaiveDate::from_ymd_opt(2011, 12, 30).unwrap()
    );
    assert!(night.local_date.day() >= 31);
}

#[test]
fn successful_publications_are_separated_by_twelve_hours() {
    let after = Utc.with_ymd_and_hms(2026, 9, 27, 0, 0, 0).unwrap();
    let last = after + Duration::hours(10);
    let night = next_news_category_night(9, "Pacific/Kiritimati", after, Some(last)).unwrap();
    assert!(night.due_at >= last + Duration::hours(12));
}

#[test]
fn aliases_have_stable_utc_spelling() {
    for alias in ["UTC", "Etc/UTC", "Etc/GMT", "GMT", "Zulu"] {
        assert_eq!(canonicalize_timezone(alias).unwrap(), "UTC");
    }
}

async fn insert_user(pool: &PgPool, suffix: &str) -> i64 {
    sqlx::query_scalar::<_, i64>(
        "INSERT INTO users(apple_id,email,is_admin,is_active) VALUES($1,$2,false,true) RETURNING id::bigint",
    )
    .bind(format!("nightly-{suffix}"))
    .bind(format!("nightly-{suffix}@example.com"))
    .fetch_one(pool)
    .await
    .unwrap()
}

#[sqlx::test]
async fn timezone_update_is_revision_fenced_and_due_runs_are_date_deduplicated(pool: PgPool) {
    let user_id = insert_user(&pool, "schedule").await;
    let mut transaction = pool.begin().await.unwrap();
    let initial =
        set_user_news_category_timezone(&mut transaction, user_id, "America/Los_Angeles", None)
            .await
            .unwrap();
    transaction.commit().await.unwrap();
    assert_eq!(initial.timezone_revision, 1);

    let mut stale = pool.begin().await.unwrap();
    let error = set_user_news_category_timezone(&mut stale, user_id, "UTC", None)
        .await
        .unwrap_err();
    assert!(matches!(
        error,
        NewsCategoryScheduleError::StaleRevision { current: 1 }
    ));
    stale.rollback().await.unwrap();

    let due_at = Utc.with_ymd_and_hms(2026, 9, 28, 10, 10, 0).unwrap();
    sqlx::query(
        "UPDATE user_news_category_schedule SET next_due_at=$2,next_local_date='2026-09-28',next_window_start_at=$3,next_window_end_at=$4 WHERE user_id::bigint=$1",
    )
    .bind(user_id)
    .bind(due_at.naive_utc())
    .bind(Utc.with_ymd_and_hms(2026, 9, 28, 10, 0, 0).unwrap().naive_utc())
    .bind(Utc.with_ymd_and_hms(2026, 9, 28, 12, 0, 0).unwrap().naive_utc())
    .execute(&pool)
    .await
    .unwrap();
    let mut transaction = pool.begin().await.unwrap();
    let runs = prepare_due_news_category_runs(
        &mut transaction,
        due_at,
        8,
        NewsCategoryMaintenanceMode::Shadow,
    )
    .await
    .unwrap();
    transaction.commit().await.unwrap();
    assert_eq!(runs.len(), 1);
    assert_eq!(
        runs[0].local_date,
        NaiveDate::from_ymd_opt(2026, 9, 28).unwrap()
    );

    let count = sqlx::query_scalar::<_, i64>(
        "SELECT count(*)::bigint FROM news_category_maintenance_runs WHERE user_id::bigint=$1 AND local_date='2026-09-28'",
    )
    .bind(user_id)
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(count, 1);

    let lease_token = Uuid::parse_str("00000000-0000-4000-8000-000000000717").unwrap();
    let task_id = sqlx::query_scalar::<_, i64>(
        r"
        INSERT INTO processing_tasks (
            task_type, queue_name, status, owner_user_id, locked_by, locked_at,
            lease_token, lease_expires_at, executor_runtime, executor_version,
            executor_namespace
        ) VALUES (
            'recluster_news_lenses','llm','processing',$1::bigint::integer,
            'nightly-test',$2,$3,$4,'rust',1,'recluster_news_lenses'
        ) RETURNING id::bigint
        ",
    )
    .bind(user_id)
    .bind(due_at.naive_utc())
    .bind(lease_token)
    .bind((due_at + Duration::hours(1)).naive_utc())
    .fetch_one(&pool)
    .await
    .unwrap();
    let mut transaction = pool.begin().await.unwrap();
    assert!(
        attach_news_category_run_task(&mut transaction, runs[0].run_id, task_id)
            .await
            .unwrap()
    );
    transaction.commit().await.unwrap();
    let claim_fence = NewsCategoryRunClaimFence {
        locked_by: "nightly-test".to_owned(),
        lease_token,
        retry_count: 0,
        executor_runtime: "rust".to_owned(),
        executor_version: 1,
        executor_namespace: "recluster_news_lenses".to_owned(),
    };
    let successor_token = Uuid::parse_str("00000000-0000-4000-8000-000000000718").unwrap();
    sqlx::query(
        "UPDATE processing_tasks SET locked_by='successor', lease_token=$2, retry_count=1 WHERE id::bigint=$1",
    )
    .bind(task_id)
    .bind(successor_token)
    .execute(&pool)
    .await
    .unwrap();
    assert_eq!(
        begin_news_category_run(
            &pool,
            task_id,
            user_id,
            runs[0].run_id,
            1,
            &claim_fence,
            due_at,
        )
        .await
        .unwrap(),
        BeginNewsCategoryRunOutcome::Ineligible
    );
    let status = sqlx::query_scalar::<_, String>(
        "SELECT status FROM news_category_maintenance_runs WHERE id=$1",
    )
    .bind(runs[0].run_id)
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(status, "scheduled");
    sqlx::query(
        "UPDATE processing_tasks SET locked_by=$2, lease_token=$3, retry_count=0 WHERE id::bigint=$1",
    )
    .bind(task_id)
    .bind(&claim_fence.locked_by)
    .bind(claim_fence.lease_token)
    .execute(&pool)
    .await
    .unwrap();
    let context = begin_news_category_run(
        &pool,
        task_id,
        user_id,
        runs[0].run_id,
        1,
        &claim_fence,
        due_at,
    )
    .await
    .unwrap();
    assert!(matches!(context, BeginNewsCategoryRunOutcome::Ready(_)));

    sqlx::query(
        "UPDATE user_news_category_schedule SET timezone_revision=2 WHERE user_id::bigint=$1",
    )
    .bind(user_id)
    .execute(&pool)
    .await
    .unwrap();
    let mut transaction = pool.begin().await.unwrap();
    assert!(
        !finish_news_category_run(
            &mut transaction,
            runs[0].run_id,
            1,
            NewsCategoryRunStatus::Shadowed,
            &serde_json::json!({"candidate": true}),
            due_at + Duration::minutes(1),
        )
        .await
        .unwrap()
    );
    transaction.commit().await.unwrap();
    let status = sqlx::query_scalar::<_, String>(
        "SELECT status FROM news_category_maintenance_runs WHERE id=$1",
    )
    .bind(runs[0].run_id)
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(status, "failed");

    sqlx::query(
        r"
        INSERT INTO news_category_maintenance_runs (
            user_id,local_date,timezone,timezone_revision,scheduled_at,
            window_start_at,window_end_at,mode,status
        ) VALUES (
            $1::bigint::integer,'2026-09-29','America/Los_Angeles',2,$2,$2,$3,
            'shadow','running'
        )
        ",
    )
    .bind(user_id)
    .bind(due_at.naive_utc())
    .bind((due_at + Duration::hours(1)).naive_utc())
    .execute(&pool)
    .await
    .unwrap();
    let mut transaction = pool.begin().await.unwrap();
    assert_eq!(
        settle_stale_news_category_runs(&mut transaction, due_at + Duration::hours(2), 8)
            .await
            .unwrap(),
        1
    );
    transaction.commit().await.unwrap();
    let status = sqlx::query_scalar::<_, String>(
        "SELECT status FROM news_category_maintenance_runs WHERE user_id::bigint=$1 AND local_date='2026-09-29'",
    )
    .bind(user_id)
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(status, "skipped_window");
}
