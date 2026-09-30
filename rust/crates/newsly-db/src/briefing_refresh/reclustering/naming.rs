//! Naming observations are accounting/cache records, not permission to publish.
use crate::{BriefingLensAssignmentUsage, NewsCategoryRunContext};
use serde_json::Value;
use sqlx::PgPool;
use uuid::Uuid;

pub async fn cached_naming(
    pool: &PgPool,
    user_id: i64,
    input_hash: &str,
) -> Result<Option<Value>, sqlx::Error> {
    sqlx::query_scalar("SELECT attempt.result FROM news_category_naming_attempts attempt JOIN news_category_maintenance_runs run ON run.id=attempt.run_id WHERE run.user_id::bigint=$1 AND attempt.input_hash=$2 AND attempt.outcome='succeeded' AND attempt.result IS NOT NULL ORDER BY attempt.created_at DESC LIMIT 1")
        .bind(user_id).bind(input_hash).fetch_optional(pool).await
}

/// Record the observed result even if a completed external call outlived its lease.
/// This append-only audit cannot alter routing, schedule eligibility or run status.
pub async fn record_naming_attempt(
    pool: &PgPool,
    context: &NewsCategoryRunContext,
    attempt_id: Uuid,
    input_hash: &str,
    result: Option<&Value>,
    usage: Option<&BriefingLensAssignmentUsage>,
) -> Result<(), sqlx::Error> {
    let outcome = if result.is_some() {
        "succeeded"
    } else {
        "failed"
    };
    let mut tx = pool.begin().await?;
    let inserted=sqlx::query("INSERT INTO news_category_naming_attempts(attempt_id,run_id,input_hash,outcome,result) SELECT $1,run.id,$3,$4,$5 FROM news_category_maintenance_runs run WHERE run.id=$2 AND run.user_id::bigint=$6 AND run.task_id::bigint=$7 ON CONFLICT(attempt_id) DO NOTHING")
        .bind(attempt_id).bind(context.run.run_id).bind(input_hash).bind(outcome).bind(result)
        .bind(context.run.user_id).bind(context.task_id).execute(&mut *tx).await?.rows_affected();
    if inserted == 0 {
        tx.rollback().await?;
        return Ok(());
    }
    if let Some(usage) = usage {
        let count = |v: u64| i32::try_from(v).unwrap_or(i32::MAX);
        sqlx::query("INSERT INTO vendor_usage_records(provider,model,feature,operation,source,request_id,task_id,user_id,request_count,input_tokens,cache_read_tokens,cache_write_tokens,output_tokens,total_tokens,currency,pricing_version,metadata,created_at) VALUES($1,$2,$3,$4,'queue',$5,$6,$7,$8,$9,$10,$11,$12,$13,'USD','2026-08-02',$14,timezone('UTC',clock_timestamp()))")
            .bind(&usage.provider).bind(&usage.model).bind(&usage.feature).bind(&usage.operation).bind(&usage.provider_response_id)
            .bind(context.task_id).bind(context.run.user_id).bind(count(usage.usage.request_count))
            .bind(count(usage.usage.input_tokens)).bind(count(usage.usage.cached_input_tokens)).bind(count(usage.usage.cache_write_tokens))
            .bind(count(usage.usage.output_tokens)).bind(count(usage.usage.input_tokens.saturating_add(usage.usage.output_tokens)))
            .bind(serde_json::json!({"attempt_id":attempt_id,"input_hash":input_hash,"outcome":outcome}))
            .execute(&mut *tx).await?;
    }
    tx.commit().await
}
