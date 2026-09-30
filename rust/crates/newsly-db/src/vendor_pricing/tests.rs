use super::*;
use chrono::TimeZone;
use sqlx::PgPool;

#[sqlx::test]
async fn resource_catalog_requires_complete_units_and_preserves_rate_snapshot(pool: PgPool) {
    let at = Utc.with_ymd_and_hms(2030, 1, 1, 0, 0, 0).unwrap();
    let mut connection = pool.acquire().await.unwrap();
    let meters = [
        VendorResourceMeter {
            unit: "vcpu_second",
            quantity: 3.0,
        },
        VendorResourceMeter {
            unit: "gib_second",
            quantity: 3.0,
        },
    ];
    let priced = calculate_vendor_resource_cost(&mut connection, "e2b", "sandbox", &meters, at)
        .await
        .unwrap()
        .unwrap();
    assert!((priced.cost_usd - 0.000_055_5).abs() < 1e-12);
    assert_eq!(priced.metadata["components"].as_array().unwrap().len(), 2);
    assert_eq!(priced.metadata["basis"], "public_list_estimate");
    let old = Utc.with_ymd_and_hms(2020, 1, 1, 0, 0, 0).unwrap();
    assert!(
        calculate_vendor_resource_cost(&mut connection, "e2b", "sandbox", &meters, old)
            .await
            .unwrap()
            .is_none()
    );
    let incomplete = [
        VendorResourceMeter {
            unit: "vcpu_second",
            quantity: 3.0,
        },
        VendorResourceMeter {
            unit: "unknown",
            quantity: 3.0,
        },
    ];
    assert!(
        calculate_vendor_resource_cost(&mut connection, "e2b", "sandbox", &incomplete, at)
            .await
            .unwrap()
            .is_none()
    );
    let invalid = [VendorResourceMeter {
        unit: "image",
        quantity: f64::NAN,
    }];
    assert!(
        calculate_vendor_resource_cost(
            &mut connection,
            "runware",
            "bytedance:seedream@5.0-lite",
            &invalid,
            at
        )
        .await
        .unwrap()
        .is_none()
    );
    sqlx::query("INSERT INTO vendor_resource_price_rates VALUES ('e2b','sandbox','vcpu_second','2031-01-01',0.00002,'https://e2b.dev/pricing','future')")
        .execute(&mut *connection).await.unwrap();
    let unchanged = calculate_vendor_resource_cost(&mut connection, "e2b", "sandbox", &meters, at)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(unchanged, priced);
}

#[sqlx::test]
async fn measured_transcription_and_standard_tts_use_catalog(pool: PgPool) {
    let mut connection = pool.acquire().await.unwrap();
    let transcribe =
        openai_transcription_cost(&mut connection, "gpt-transcribe", Some(120_000), true, true)
            .await
            .unwrap()
            .unwrap();
    assert!((transcribe.cost_usd - 0.009).abs() < 1e-12);
    assert!(
        openai_transcription_cost(
            &mut connection,
            "gpt-transcribe",
            Some(120_000),
            false,
            true
        )
        .await
        .unwrap()
        .is_none()
    );
    assert!(
        openai_transcription_cost(
            &mut connection,
            "gpt-transcribe",
            Some(120_000),
            true,
            false
        )
        .await
        .unwrap()
        .is_none()
    );
    let flash = elevenlabs_tts_cost(&mut connection, "eleven_flash_v2_5", 2_000, true)
        .await
        .unwrap()
        .unwrap();
    assert!((flash.cost_usd - 0.08).abs() < 1e-12);
    assert!(
        elevenlabs_tts_cost(&mut connection, "custom-model", 2000, true)
            .await
            .unwrap()
            .is_none()
    );
    assert!(
        elevenlabs_tts_cost(&mut connection, "eleven_flash_v2_5", 2000, false)
            .await
            .unwrap()
            .is_none()
    );
}

#[sqlx::test]
async fn extractor_is_non_billable_without_hiding_unknown_external_costs(pool: PgPool) {
    for model in [
        "crawl4ai",
        "static_readability",
        "policy-v1",
        "external_future_adapter",
    ] {
        sqlx::query("INSERT INTO vendor_usage_records (provider,model,feature,operation,created_at) VALUES ('document_extractor',$1,'document_extraction','extract_article',timezone('UTC',now()))")
            .bind(model).execute(&pool).await.unwrap();
    }
    let known: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM vendor_usage_records WHERE cost_usd=0 AND cost_basis='non_billable'",
    )
    .fetch_one(&pool)
    .await
    .unwrap();
    let unknown: i64 =
        sqlx::query_scalar("SELECT count(*) FROM vendor_usage_records WHERE cost_usd IS NULL")
            .fetch_one(&pool)
            .await
            .unwrap();
    assert_eq!(known, 3);
    assert_eq!(unknown, 1);
}

#[sqlx::test]
async fn token_prices_apply_cache_and_context_rates_only_to_verified_observations(pool: PgPool) {
    async fn insert(
        pool: &PgPool,
        tier: &str,
        input: i32,
        requests: i32,
    ) -> (Option<f64>, Option<String>, Value) {
        sqlx::query_as("INSERT INTO vendor_usage_records (provider,model,feature,operation,input_tokens,output_tokens,cache_read_tokens,cache_write_tokens,request_count,metadata,created_at) VALUES ('openai','gpt-6-luna','chat','chat.turn',$1,1000,100,50,$2,$3,timezone('UTC',clock_timestamp())) RETURNING cost_usd,cost_basis,metadata::jsonb")
            .bind(input).bind(requests).bind(json!({"model_request_sequence":1,"endpoint":"https://api.openai.com/v1/responses","service_tier":tier}))
            .fetch_one(pool).await.unwrap()
    }
    let short = insert(&pool, "default", 1000, 1).await;
    // 850 uncached + 100 cached + 50 cache writes + 1000 output.
    assert!((short.0.unwrap() - 0.000_592_25).abs() < 1e-12);
    assert_eq!(short.1.as_deref(), Some("public_list_estimate"));
    let long = insert(&pool, "default", 300_000, 1).await;
    assert!((long.0.unwrap() - 0.060_734_5).abs() < 1e-12);
    assert_eq!(long.2["pricing"]["input_rate_usd"], json!(0.20));
    let unknown = insert(&pool, "unknown", 1000, 1).await;
    assert!(unknown.0.is_none());
    assert_eq!(
        unknown.2["cost_reason"],
        "unverified_endpoint_or_service_tier"
    );
    let fast = insert(&pool, "priority", 1000, 1).await;
    assert!((fast.0.unwrap() - 0.001_184_5).abs() < 1e-12);
    assert_eq!(fast.2["pricing"]["tier_multiplier"], json!(2.0));
    let flex = insert(&pool, "flex", 1000, 1).await;
    assert!((flex.0.unwrap() - 0.000_296_125).abs() < 1e-12);
    let unknown_tier = insert(&pool, "unpriced_tier", 1000, 1).await;
    assert!(unknown_tier.0.is_none());
    let aggregate = insert(&pool, "default", 1000, 2).await;
    assert!(aggregate.0.is_none());
    assert_eq!(
        aggregate.2["cost_reason"],
        "aggregate_or_unobserved_requests"
    );
}

#[sqlx::test]
async fn missing_usage_does_not_infer_cost_from_default_counters(pool: PgPool) {
    let row: (Option<f64>, Value) = sqlx::query_as(
        "INSERT INTO vendor_usage_records (provider,model,feature,operation,input_tokens,output_tokens,request_count,metadata,created_at) VALUES ('openai','gpt-6-luna','chat','chat.turn',100,0,1,$1,timezone('UTC',clock_timestamp())) RETURNING cost_usd,metadata::jsonb",
    )
    .bind(json!({"model_request_sequence":1,"endpoint":"https://api.openai.com/v1/responses","service_tier":"default","usage_is_observed":false}))
    .fetch_one(&pool).await.unwrap();
    assert!(row.0.is_none());
    assert_eq!(row.1["cost_reason"], "missing_provider_usage");
}
