use newsly_db::BriefingLensAssignmentUsage;
use sqlx::PgPool;

/// Retains billable observations independently of later task ownership or product writes.
pub(super) async fn persist_observed_usage(
    pool: &PgPool,
    task_id: i64,
    user_id: i64,
    usages: &[BriefingLensAssignmentUsage],
) -> Result<(), sqlx::Error> {
    if usages.is_empty() {
        return Ok(());
    }
    let mut transaction = pool.begin().await?;
    for usage in usages {
        newsly_db::news_lens_embeddings::record_usage(
            &mut *transaction,
            task_id,
            Some(user_id),
            usage,
        )
        .await?;
    }
    transaction.commit().await
}

/// Consume completed-plan usage only after its independent ledger transaction commits.
pub(super) async fn persist_completed_usage(
    pool: &PgPool,
    task_id: i64,
    user_id: i64,
    usages: &mut Vec<BriefingLensAssignmentUsage>,
) -> Result<(), sqlx::Error> {
    persist_observed_usage(pool, task_id, user_id, usages).await?;
    usages.clear();
    Ok(())
}

#[cfg(test)]
mod tests {
    use newsly_agent_runtime::ProviderUsage;
    use newsly_queue::{EnqueueRequest, QueueKernel, TaskType};
    use serde_json::{Value, json};

    use super::*;

    #[derive(Debug, sqlx::FromRow)]
    struct UsageRow {
        task_id: i64,
        user_id: i64,
        provider: String,
        model: String,
        feature: String,
        operation: String,
        source: String,
        request_id: Option<String>,
        request_count: i32,
        input_tokens: i32,
        cache_read_tokens: i32,
        cache_write_tokens: i32,
        output_tokens: i32,
        total_tokens: i32,
    }

    #[sqlx::test(migrations = false)]
    async fn failed_semantic_planning_retains_observed_calls_without_mutating_task(pool: PgPool) {
        newsly_db::run_migrations(&pool).await.unwrap();
        let user_id: i64 = sqlx::query_scalar(
            "INSERT INTO users (apple_id,email,is_active,is_admin) VALUES ('semantic-failure-usage','semantic-failure-usage@example.com',true,false) RETURNING id::bigint",
        ).fetch_one(&pool).await.unwrap();
        let mut request = EnqueueRequest::new(TaskType::BriefingRefresh);
        request.owner_user_id = Some(user_id);
        request.payload = json!({"user_id":user_id}).as_object().cloned();
        let task_id = QueueKernel::new(pool.clone())
            .enqueue(request)
            .await
            .unwrap();
        let task_before: Value = sqlx::query_scalar(
            "SELECT to_jsonb(task) FROM processing_tasks task WHERE id::bigint=$1",
        )
        .bind(task_id)
        .fetch_one(&pool)
        .await
        .unwrap();
        let usages = [None, None, Some("successful-sibling")]
            .into_iter()
            .enumerate()
            .map(|(index, response)| {
                let multiplier = u64::try_from(index + 1).unwrap();
                BriefingLensAssignmentUsage {
                    provider: "openai".to_owned(),
                    model: "gpt-6-luna".to_owned(),
                    provider_response_id: response.map(str::to_owned),
                    usage: ProviderUsage {
                        request_count: 1,
                        input_tokens: 101 * multiplier,
                        cached_input_tokens: 7 * multiplier,
                        cache_write_tokens: 3 * multiplier,
                        output_tokens: 11 * multiplier,
                        ..ProviderUsage::default()
                    },
                    feature: "briefing_lens_naming".to_owned(),
                    operation: "name_lens".to_owned(),
                }
            })
            .collect::<Vec<_>>();
        persist_observed_usage(&pool, task_id, user_id, &[])
            .await
            .unwrap();
        persist_observed_usage(&pool, task_id, user_id, &usages)
            .await
            .unwrap();
        let rows: Vec<UsageRow> = sqlx::query_as(
            "SELECT task_id::bigint,user_id::bigint,provider,model,feature,operation,source,request_id,request_count,input_tokens,cache_read_tokens,cache_write_tokens,output_tokens,total_tokens FROM vendor_usage_records WHERE task_id::bigint=$1 ORDER BY input_tokens",
        ).bind(task_id).fetch_all(&pool).await.unwrap();
        assert_eq!(rows.len(), 3);
        for (index, row) in rows.into_iter().enumerate() {
            let multiplier = i32::try_from(index + 1).unwrap();
            assert_eq!((row.task_id, row.user_id), (task_id, user_id));
            assert_eq!(row.provider, "openai");
            assert_eq!(row.model, "gpt-6-luna");
            assert_eq!(row.feature, "briefing_lens_naming");
            assert_eq!(row.operation, "name_lens");
            assert_eq!(row.source, "queue");
            assert_eq!(
                row.request_id.as_deref(),
                if index == 2 {
                    Some("successful-sibling")
                } else {
                    None
                }
            );
            assert_eq!(row.request_count, 1);
            assert_eq!(row.input_tokens, 101 * multiplier);
            assert_eq!(row.cache_read_tokens, 7 * multiplier);
            assert_eq!(row.cache_write_tokens, 3 * multiplier);
            assert_eq!(row.output_tokens, 11 * multiplier);
            assert_eq!(row.total_tokens, 112 * multiplier);
        }
        let task_after: Value = sqlx::query_scalar(
            "SELECT to_jsonb(task) FROM processing_tasks task WHERE id::bigint=$1",
        )
        .bind(task_id)
        .fetch_one(&pool)
        .await
        .unwrap();
        assert_eq!(task_before, task_after);
    }
    #[sqlx::test(migrations = false)]
    async fn completed_naming_usage_survives_lease_loss_and_later_database_rollback(pool: PgPool) {
        use newsly_domain::{ResourceKey, RuntimeOwner};
        use newsly_queue::{ClaimRequest, ClaimRuntimeScope};
        newsly_db::run_migrations(&pool).await.unwrap();
        let user: i64 = sqlx::query_scalar("INSERT INTO users(apple_id,email,is_active,is_admin) VALUES('completed-usage','completed-usage@example.com',true,false) RETURNING id::bigint")
            .fetch_one(&pool).await.unwrap();
        let queue = QueueKernel::new(pool.clone());
        let mut request = EnqueueRequest::new(TaskType::BriefingRefresh);
        request.owner_user_id = Some(user);
        request.payload = json!({"user_id":user,"mode":"full"}).as_object().cloned();
        let task = queue.enqueue(request).await.unwrap();
        let claim = queue
            .claim(&ClaimRequest::for_queue(
                "completed-usage",
                TaskType::BriefingRefresh.spec().queue,
                ClaimRuntimeScope::namespaces(
                    RuntimeOwner::Rust,
                    [ResourceKey::new("briefing_refresh").unwrap()],
                )
                .unwrap(),
            ))
            .await
            .unwrap()
            .unwrap();
        let mut usages = vec![BriefingLensAssignmentUsage {
            provider: "openai".into(),
            model: "test".into(),
            provider_response_id: None,
            usage: ProviderUsage {
                request_count: 1,
                input_tokens: 10,
                output_tokens: 2,
                ..ProviderUsage::default()
            },
            feature: "briefing_lens_naming".into(),
            operation: "name_lens".into(),
        }];
        persist_completed_usage(&pool, task, user, &mut usages)
            .await
            .unwrap();
        assert!(
            usages.is_empty(),
            "product assignment must not record these calls again"
        );
        persist_completed_usage(&pool, task, user, &mut usages)
            .await
            .unwrap();
        let mut product = pool.begin().await.unwrap();
        sqlx::query("UPDATE processing_tasks SET payload='{}' WHERE id::bigint=$1")
            .bind(task)
            .execute(&mut *product)
            .await
            .unwrap();
        assert!(
            sqlx::query("SELECT 1/0")
                .execute(&mut *product)
                .await
                .is_err()
        );
        product.rollback().await.unwrap();
        sqlx::query("UPDATE processing_tasks SET lease_expires_at=now()-interval '1 second' WHERE id::bigint=$1")
            .bind(task).execute(&pool).await.unwrap();
        assert!(
            queue
                .begin_fenced_finalization(&claim, &newsly_queue::TaskResult::ok(), 3)
                .await
                .unwrap()
                .is_none()
        );
        let (count, requests): (i64, i64) = sqlx::query_as("SELECT count(*),sum(request_count)::bigint FROM vendor_usage_records WHERE task_id::bigint=$1")
            .bind(task).fetch_one(&pool).await.unwrap();
        assert_eq!((count, requests), (1, 1));
        let payload: Value =
            sqlx::query_scalar("SELECT payload FROM processing_tasks WHERE id::bigint=$1")
                .bind(task)
                .fetch_one(&pool)
                .await
                .unwrap();
        assert_eq!(payload["mode"], "full");
    }
}
