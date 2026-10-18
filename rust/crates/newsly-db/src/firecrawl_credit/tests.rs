use super::*;

async fn expire_cooldown(pool: &PgPool) {
    sqlx::query("UPDATE firecrawl_credit_state SET retry_after = now() - interval '1 second'")
        .execute(pool)
        .await
        .unwrap();
}

async fn probe(pool: &PgPool) -> Uuid {
    let FirecrawlAdmission::Allowed {
        probe_token: Some(token),
    } = acquire_firecrawl_admission(pool).await.unwrap()
    else {
        panic!("expected recovery probe");
    };
    token
}

#[sqlx::test]
async fn concurrent_admissions_reserve_one_probe(pool: PgPool) {
    crate::run_migrations(&pool).await.unwrap();
    assert_eq!(
        acquire_firecrawl_admission(&pool).await.unwrap(),
        FirecrawlAdmission::Allowed { probe_token: None }
    );
    observe_firecrawl_response(&pool, None, 402).await.unwrap();
    assert!(matches!(
        acquire_firecrawl_admission(&pool).await.unwrap(),
        FirecrawlAdmission::Paused { .. }
    ));
    expire_cooldown(&pool).await;
    let mut calls = tokio::task::JoinSet::new();
    for _ in 0..20 {
        let pool = pool.clone();
        calls.spawn(async move { acquire_firecrawl_admission(&pool).await.unwrap() });
    }
    let mut admitted = 0;
    while let Some(call) = calls.join_next().await {
        if matches!(
            call.unwrap(),
            FirecrawlAdmission::Allowed {
                probe_token: Some(_)
            }
        ) {
            admitted += 1;
        }
    }
    assert_eq!(admitted, 1);
}

#[sqlx::test]
async fn exhaustion_deduplicates_alert_and_only_probe_recovers(pool: PgPool) {
    crate::run_migrations(&pool).await.unwrap();
    let pause = observe_firecrawl_response(&pool, None, 402).await.unwrap();
    let alert = crate::pipeline_monitoring::claim_alert(&pool)
        .await
        .unwrap()
        .unwrap();
    crate::pipeline_monitoring::settle_alert(&pool, &alert, None)
        .await
        .unwrap();
    assert_eq!(
        observe_firecrawl_response(&pool, None, 402).await.unwrap(),
        pause
    );
    assert!(
        crate::pipeline_monitoring::claim_alert(&pool)
            .await
            .unwrap()
            .is_none()
    );
    assert_eq!(
        observe_firecrawl_response(&pool, None, 200).await.unwrap(),
        FirecrawlCreditTransition::Unchanged
    );
    assert!(matches!(
        acquire_firecrawl_admission(&pool).await.unwrap(),
        FirecrawlAdmission::Paused { .. }
    ));
    expire_cooldown(&pool).await;
    let token = probe(&pool).await;
    assert_eq!(
        observe_firecrawl_response(&pool, Some(token), 201)
            .await
            .unwrap(),
        FirecrawlCreditTransition::Recovered
    );
    let recovery = crate::pipeline_monitoring::claim_alert(&pool)
        .await
        .unwrap()
        .unwrap();
    assert!(!recovery.active);
    assert_eq!(recovery.revision, alert.revision + 1);
    assert_eq!(
        acquire_firecrawl_admission(&pool).await.unwrap(),
        FirecrawlAdmission::Allowed { probe_token: None }
    );
    observe_firecrawl_response(&pool, None, 402).await.unwrap();
    let again = crate::pipeline_monitoring::claim_alert(&pool)
        .await
        .unwrap()
        .unwrap();
    assert!(again.active);
    assert_eq!(again.revision, recovery.revision + 1);
}

#[sqlx::test]
async fn stale_probe_cannot_clear_newer_pause(pool: PgPool) {
    crate::run_migrations(&pool).await.unwrap();
    observe_firecrawl_response(&pool, None, 402).await.unwrap();
    expire_cooldown(&pool).await;
    let old = probe(&pool).await;
    observe_firecrawl_response(&pool, None, 402).await.unwrap();
    for status in [200, 402, 500] {
        assert_eq!(
            observe_firecrawl_response(&pool, Some(old), status)
                .await
                .unwrap(),
            FirecrawlCreditTransition::Unchanged
        );
    }
    assert!(matches!(
        acquire_firecrawl_admission(&pool).await.unwrap(),
        FirecrawlAdmission::Paused { .. }
    ));
    expire_cooldown(&pool).await;
    let current = probe(&pool).await;
    assert_ne!(current, old);
    assert_eq!(
        observe_firecrawl_response(&pool, Some(old), 200)
            .await
            .unwrap(),
        FirecrawlCreditTransition::Unchanged
    );
    assert_eq!(
        observe_firecrawl_response(&pool, Some(current), 200)
            .await
            .unwrap(),
        FirecrawlCreditTransition::Recovered
    );
}

#[sqlx::test]
async fn abandoned_probe_expires_and_failure_reschedules(pool: PgPool) {
    crate::run_migrations(&pool).await.unwrap();
    observe_firecrawl_response(&pool, None, 402).await.unwrap();
    expire_cooldown(&pool).await;
    let old = probe(&pool).await;
    sqlx::query("UPDATE firecrawl_credit_state SET probe_expires_at = now() - interval '1 second'")
        .execute(&pool)
        .await
        .unwrap();
    assert_eq!(
        observe_firecrawl_response(&pool, Some(old), 200)
            .await
            .unwrap(),
        FirecrawlCreditTransition::Unchanged
    );
    let current = probe(&pool).await;
    assert_ne!(old, current);
    assert!(matches!(
        observe_firecrawl_response(&pool, Some(current), 0)
            .await
            .unwrap(),
        FirecrawlCreditTransition::Paused { .. }
    ));
    let (active, revision): (bool, i64) = sqlx::query_as("SELECT active, revision FROM pipeline_alerts WHERE alert_key = 'firecrawl_credits_exhausted'")
        .fetch_one(&pool).await.unwrap();
    assert!(active);
    assert_eq!(revision, 1);
    assert!(matches!(
        acquire_firecrawl_admission(&pool).await.unwrap(),
        FirecrawlAdmission::Paused { .. }
    ));
    expire_cooldown(&pool).await;
    let current = probe(&pool).await;
    assert!(matches!(
        observe_firecrawl_response(&pool, Some(current), 402)
            .await
            .unwrap(),
        FirecrawlCreditTransition::Paused { .. }
    ));
    assert!(matches!(
        acquire_firecrawl_admission(&pool).await.unwrap(),
        FirecrawlAdmission::Paused { .. }
    ));
}
