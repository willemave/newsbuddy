use crate::{BriefingRefreshClaimFence, BriefingSegmentUsage};
use sqlx::PgPool;
use uuid::Uuid;

pub async fn cooling_down(
    pool: &PgPool,
    user_id: i64,
    fingerprint: &str,
) -> Result<bool, sqlx::Error> {
    sqlx::query_scalar("SELECT EXISTS (SELECT 1 FROM briefing_composition_cooldowns WHERE user_id = $1 AND fingerprint = $2 AND retry_after > now())")
        .bind(user_id).bind(fingerprint).fetch_one(pool).await
}

/// Accounting commits independently of edition publication but only under the original live claim.
pub async fn record_attempt(
    pool: &PgPool,
    task_id: i64,
    user_id: i64,
    fence: &BriefingRefreshClaimFence,
    attempt_id: Uuid,
    fingerprint: &str,
    outcome: &str,
    usage: Option<&BriefingSegmentUsage>,
    exhausted: bool,
) -> Result<(), sqlx::Error> {
    let mut tx = pool.begin().await?;
    let owned: Option<i64> = sqlx::query_scalar("SELECT id::bigint FROM processing_tasks WHERE id = $1 AND owner_user_id = $2 AND status = 'processing' AND locked_by = $3 AND lease_token = $4 AND retry_count = $5 AND executor_runtime = $6 AND executor_version = $7 AND executor_namespace = $8 AND lease_expires_at > timezone('UTC', clock_timestamp()) FOR SHARE")
        .bind(task_id).bind(user_id).bind(&fence.locked_by).bind(fence.lease_token).bind(fence.retry_count).bind(&fence.executor_runtime).bind(fence.executor_version).bind(&fence.executor_namespace).fetch_optional(&mut *tx).await?;
    if owned.is_none() {
        return Err(sqlx::Error::RowNotFound);
    }
    let inserted = sqlx::query("INSERT INTO briefing_composition_attempts(attempt_id,task_id,user_id,fingerprint,outcome) VALUES($1,$2,$3,$4,$5) ON CONFLICT DO NOTHING")
        .bind(attempt_id).bind(task_id).bind(user_id).bind(fingerprint).bind(outcome).execute(&mut *tx).await?.rows_affected();
    if inserted == 0 {
        return Ok(());
    }
    if let Some(usage) = usage {
        let count = |value: u64| i32::try_from(value).unwrap_or(i32::MAX);
        sqlx::query("INSERT INTO vendor_usage_records(provider,model,feature,operation,source,request_id,task_id,user_id,request_count,input_tokens,cache_read_tokens,cache_write_tokens,output_tokens,total_tokens,currency,pricing_version,metadata,created_at) VALUES($1,$2,'briefing_compose',$3,'queue',$4,$5,$6,$7,$8,$9,$10,$11,$12,'USD','2026-08-02',$13,timezone('UTC',clock_timestamp()))")
            .bind(&usage.provider).bind(&usage.model).bind(&usage.operation).bind(&usage.provider_response_id).bind(task_id as i32).bind(user_id as i32)
            .bind(count(usage.usage.request_count)).bind(count(usage.usage.input_tokens)).bind(count(usage.usage.cached_input_tokens)).bind(count(usage.usage.cache_write_tokens)).bind(count(usage.usage.output_tokens)).bind(count(usage.usage.input_tokens.saturating_add(usage.usage.output_tokens)))
            .bind(serde_json::json!({"attempt_id":attempt_id,"fingerprint":fingerprint,"outcome":outcome})).execute(&mut *tx).await?;
    }
    if exhausted {
        sqlx::query("INSERT INTO briefing_composition_cooldowns(user_id,fingerprint,retry_after) VALUES($1,$2,now() + interval '24 hours') ON CONFLICT(user_id,fingerprint) DO UPDATE SET retry_after = EXCLUDED.retry_after")
            .bind(user_id).bind(fingerprint).execute(&mut *tx).await?;
    }
    tx.commit().await
}

#[cfg(test)]
mod tests {
    use super::*;

    #[sqlx::test]
    async fn observed_attempts_survive_rejection_deduplicate_and_respect_lease(pool: PgPool) {
        crate::run_migrations(&pool).await.unwrap();
        let user: i64 = sqlx::query_scalar("INSERT INTO users(apple_id,email,is_active,is_admin) VALUES('attempt-test','attempt@example.com',true,false) RETURNING id::bigint").fetch_one(&pool).await.unwrap();
        let token = Uuid::new_v4();
        let task: i64 = sqlx::query_scalar("INSERT INTO processing_tasks(task_type,queue_name,status,owner_user_id,locked_by,locked_at,lease_token,lease_expires_at,executor_runtime,executor_version,executor_namespace) VALUES('briefing_refresh','llm','processing',$1,'test',timezone('UTC',now()),$2,timezone('UTC',now()) + interval '5 minutes','rust',1,'briefing_refresh') RETURNING id::bigint").bind(user as i32).bind(token).fetch_one(&pool).await.unwrap();
        let fence = BriefingRefreshClaimFence {
            locked_by: "test".to_owned(),
            lease_token: token,
            retry_count: 0,
            executor_runtime: "rust".to_owned(),
            executor_version: 1,
            executor_namespace: "briefing_refresh".to_owned(),
        };
        let usage = BriefingSegmentUsage {
            provider: "test".to_owned(),
            model: "fake".to_owned(),
            provider_response_id: None,
            operation: "briefing.compose_window.observed".to_owned(),
            usage: newsly_agent_runtime::ProviderUsage {
                request_count: 1,
                input_tokens: 10,
                output_tokens: 5,
                ..Default::default()
            },
        };
        let attempt = Uuid::new_v4();
        for _ in 0..2 {
            record_attempt(
                &pool,
                task,
                user,
                &fence,
                attempt,
                "input-v1",
                "accepted",
                Some(&usage),
                false,
            )
            .await
            .unwrap();
        }
        record_attempt(
            &pool,
            task,
            user,
            &fence,
            Uuid::new_v4(),
            "input-v1",
            "rejected",
            Some(&usage),
            true,
        )
        .await
        .unwrap();
        let calls: i64 =
            sqlx::query_scalar("SELECT sum(request_count)::bigint FROM vendor_usage_records")
                .fetch_one(&pool)
                .await
                .unwrap();
        assert_eq!(
            calls, 2,
            "accepted sibling and rejected unit both remain accounted without publication"
        );
        assert!(cooling_down(&pool, user, "input-v1").await.unwrap());
        assert!(!cooling_down(&pool, user, "input-v2").await.unwrap());
        sqlx::query("UPDATE processing_tasks SET lease_token = $1 WHERE id = $2")
            .bind(Uuid::new_v4())
            .bind(task)
            .execute(&pool)
            .await
            .unwrap();
        assert!(
            record_attempt(
                &pool,
                task,
                user,
                &fence,
                Uuid::new_v4(),
                "input-v1",
                "accepted",
                Some(&usage),
                false
            )
            .await
            .is_err()
        );
    }
}
