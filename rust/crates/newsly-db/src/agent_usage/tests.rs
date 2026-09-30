use newsly_agent_runtime::{AgentModelUsageObservation, ProviderUsage};
use sqlx::PgPool;
use uuid::Uuid;

use super::{NewAgentModelUsage, record_agent_model_usage};

#[sqlx::test]
async fn response_observation_is_idempotent_and_keeps_exact_routing_metadata(pool: PgPool) {
    let user_id = create_user(&pool).await;
    let observation = AgentModelUsageObservation {
        run_id: Uuid::parse_str("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa").unwrap(),
        sequence: 2,
        feature: "share_action.add_feed".to_owned(),
        provider: "openai".to_owned(),
        model: "gpt-6-luna".to_owned(),
        endpoint: "https://api.openai.com/v1/responses".to_owned(),
        response_id: Some("resp_exact".to_owned()),
        provider_request_id: Some("req_transport".to_owned()),
        requested_service_tier: Some("priority".to_owned()),
        processing_tier: Some("priority".to_owned()),
        usage_is_observed: true,
        provider_reported_cost_usd: None,
        usage: ProviderUsage {
            request_count: 1,
            input_tokens: 100,
            output_tokens: 20,
            cached_input_tokens: 40,
            cache_write_tokens: 5,
            reasoning_tokens: 7,
            input_audio_tokens: 0,
            output_audio_tokens: 0,
        },
    };
    let usage = NewAgentModelUsage {
        observation: &observation,
        operation: "share_action.agent_response",
        source: "queue",
        task_id: Some(77),
        content_id: None,
        session_id: None,
        message_id: None,
        user_id: Some(user_id),
    };

    let mut first = pool.begin().await.unwrap();
    assert!(record_agent_model_usage(&mut first, &usage).await.unwrap());
    first.commit().await.unwrap();
    let mut duplicate = pool.begin().await.unwrap();
    assert!(
        !record_agent_model_usage(&mut duplicate, &usage)
            .await
            .unwrap()
    );
    duplicate.commit().await.unwrap();

    let mut next_observation = observation.clone();
    next_observation.sequence = 3;
    next_observation.response_id = None;
    next_observation.provider_request_id = None;
    let mut next = pool.begin().await.unwrap();
    assert!(
        record_agent_model_usage(
            &mut next,
            &NewAgentModelUsage {
                observation: &next_observation,
                ..usage
            },
        )
        .await
        .unwrap()
    );
    next.commit().await.unwrap();

    let row = sqlx::query_as::<_, (i64, i64, String, String, String)>(
        r#"
        SELECT input_tokens::bigint, cache_read_tokens::bigint,
               metadata::jsonb ->> 'endpoint', metadata::jsonb ->> 'service_tier',
               metadata::jsonb ->> 'provider_request_id'
        FROM vendor_usage_records
        WHERE request_id = 'resp_exact'
        "#,
    )
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(
        row,
        (
            100,
            40,
            "https://api.openai.com/v1/responses".to_owned(),
            "priority".to_owned(),
            "req_transport".to_owned()
        )
    );
    let count: i64 =
        sqlx::query_scalar("SELECT count(*) FROM vendor_usage_records WHERE task_id::bigint = 77")
            .fetch_one(&pool)
            .await
            .unwrap();
    assert_eq!(count, 2);
}

#[sqlx::test]
async fn openrouter_provider_charge_remains_authoritative(pool: PgPool) {
    let observation = AgentModelUsageObservation {
        run_id: Uuid::new_v4(),
        sequence: 1,
        feature: "news_processing.summarize_short_form".to_owned(),
        provider: "openrouter".to_owned(),
        model: "provider/model".to_owned(),
        endpoint: "https://openrouter.ai/api/v1/chat/completions".to_owned(),
        response_id: Some("generation_exact".to_owned()),
        provider_request_id: None,
        requested_service_tier: None,
        processing_tier: None,
        usage_is_observed: true,
        provider_reported_cost_usd: Some(0.0042),
        usage: ProviderUsage {
            request_count: 1,
            input_tokens: 10,
            output_tokens: 2,
            ..ProviderUsage::default()
        },
    };
    let mut transaction = pool.begin().await.unwrap();
    assert!(
        record_agent_model_usage(
            &mut transaction,
            &NewAgentModelUsage {
                observation: &observation,
                operation: "news_processing.summarize_short_form",
                source: "queue",
                task_id: None,
                content_id: None,
                session_id: None,
                message_id: None,
                user_id: None,
            },
        )
        .await
        .unwrap()
    );
    transaction.commit().await.unwrap();

    let row = sqlx::query_as::<_, (f64, String, String, Option<i64>)>(
        "SELECT cost_usd::double precision, cost_basis, pricing_version, task_id::bigint FROM vendor_usage_records WHERE request_id = 'generation_exact'",
    )
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(
        row,
        (
            0.0042,
            "provider_reported".to_owned(),
            "provider-response".to_owned(),
            None,
        )
    );
}

#[sqlx::test]
async fn inactive_owner_drops_attribution_without_cross_user_record(pool: PgPool) {
    let user_id = create_user(&pool).await;
    sqlx::query("UPDATE users SET is_active = FALSE WHERE id::bigint = $1")
        .bind(user_id)
        .execute(&pool)
        .await
        .unwrap();
    let observation = AgentModelUsageObservation {
        run_id: Uuid::new_v4(),
        sequence: 1,
        feature: "article_chat".to_owned(),
        provider: "anthropic".to_owned(),
        model: "claude-test".to_owned(),
        endpoint: "https://api.anthropic.com/v1/messages".to_owned(),
        response_id: None,
        provider_request_id: None,
        requested_service_tier: None,
        processing_tier: None,
        usage_is_observed: true,
        provider_reported_cost_usd: None,
        usage: ProviderUsage {
            request_count: 1,
            input_tokens: 10,
            output_tokens: 2,
            ..ProviderUsage::default()
        },
    };
    let mut transaction = pool.begin().await.unwrap();
    let inserted = record_agent_model_usage(
        &mut transaction,
        &NewAgentModelUsage {
            observation: &observation,
            operation: "chat.async",
            source: "async",
            task_id: Some(88),
            content_id: None,
            session_id: None,
            message_id: None,
            user_id: Some(user_id),
        },
    )
    .await
    .unwrap();
    transaction.commit().await.unwrap();
    assert!(!inserted);
    let count: i64 = sqlx::query_scalar("SELECT count(*) FROM vendor_usage_records")
        .fetch_one(&pool)
        .await
        .unwrap();
    assert_eq!(count, 0);
}

#[sqlx::test]
async fn absent_provider_usage_is_explicitly_marked_unknown(pool: PgPool) {
    let observation = AgentModelUsageObservation {
        run_id: Uuid::new_v4(),
        sequence: 1,
        feature: "article_chat".to_owned(),
        provider: "openai".to_owned(),
        model: "gpt-test".to_owned(),
        endpoint: "https://api.openai.com/v1/responses".to_owned(),
        response_id: Some("resp_without_usage".to_owned()),
        provider_request_id: None,
        requested_service_tier: None,
        processing_tier: None,
        usage_is_observed: false,
        provider_reported_cost_usd: None,
        usage: ProviderUsage::default(),
    };
    let mut transaction = pool.begin().await.unwrap();
    assert!(
        record_agent_model_usage(
            &mut transaction,
            &NewAgentModelUsage {
                observation: &observation,
                operation: "chat.async",
                source: "async",
                task_id: Some(101),
                content_id: None,
                session_id: None,
                message_id: None,
                user_id: None,
            },
        )
        .await
        .unwrap()
    );
    transaction.commit().await.unwrap();

    let row = sqlx::query_as::<_, (Option<f64>, String, bool)>(
        "SELECT cost_usd::double precision, metadata::jsonb ->> 'cost_reason', (metadata::jsonb ->> 'usage_is_observed')::boolean FROM vendor_usage_records WHERE request_id = 'resp_without_usage'",
    )
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(row, (None, "missing_provider_usage".to_owned(), false));
}

async fn create_user(pool: &PgPool) -> i64 {
    sqlx::query_scalar::<_, i32>(
        r#"
        INSERT INTO users (apple_id, email, is_admin, is_active)
        VALUES ($1, $2, FALSE, TRUE)
        RETURNING id
        "#,
    )
    .bind(format!("agent-usage-{}", Uuid::new_v4()))
    .bind(format!("agent-usage-{}@example.com", Uuid::new_v4()))
    .fetch_one(pool)
    .await
    .unwrap()
    .into()
}
