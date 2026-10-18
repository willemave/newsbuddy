use super::*;
use newsly_agent_runtime::ProviderUsage;
use newsly_db::BriefingRefreshClaimFence;
use newsly_providers::{BriefingCompositionGatewayError, GeneratedBriefingLayout};
use std::sync::Mutex;

#[derive(Default)]
struct RejectedComposer(Mutex<Vec<Option<String>>>);

impl BriefingComposer for RejectedComposer {
    fn model_spec(&self) -> &'static str {
        "fake:test"
    }
    async fn compose(
        &self,
        _request: &BriefingCompositionRequest,
        feedback: Option<&str>,
    ) -> Result<GeneratedBriefingLayout, BriefingCompositionGatewayError> {
        self.0.lock().unwrap().push(feedback.map(str::to_owned));
        Ok(GeneratedBriefingLayout {
            layout: Err(
                newsly_providers::BriefingLayoutRejection::NewsSourceLinkMissing(
                    "content:1".to_owned(),
                ),
            ),
            model: "test".to_owned(),
            usage: ProviderUsage {
                request_count: 1,
                input_tokens: 10,
                output_tokens: 5,
                ..Default::default()
            },
            provider_response_id: None,
        })
    }
}

fn unit(title: &str) -> CompositionUnit {
    CompositionUnit {
        ordinal: 0,
        lens: BriefingRefreshLens {
            id: 1,
            key: "longform".to_owned(),
            tier: "longform".to_owned(),
            title: "Reading".to_owned(),
            deck: String::new(),
            position: 0,
        },
        window: PlannedBriefingWindow {
            sources: vec![BriefingRefreshSource {
                source_key: "content:1".to_owned(),
                kind: "content".to_owned(),
                id: 1,
                title: title.to_owned(),
                source_name: None,
                summary: Some("Summary".to_owned()),
                key_points: vec![],
                url: None,
                image_url: None,
                thumbnail_url: None,
                published_at: None,
                briefing_context: None,
            }],
            event_groups: vec![],
        },
        kind: CompositionUnitKind::Append {
            pending_rows: vec![],
        },
    }
}

async fn claim(pool: &PgPool, user: i64) -> (i64, BriefingRefreshClaimFence) {
    let token = uuid::Uuid::new_v4();
    let task: i64 = sqlx::query_scalar("INSERT INTO processing_tasks(task_type,queue_name,status,owner_user_id,locked_by,locked_at,lease_token,lease_expires_at,executor_runtime,executor_version,executor_namespace) VALUES('briefing_refresh','llm','processing',$1::bigint::integer,'test',timezone('UTC',now()),$2,timezone('UTC',now()) + interval '5 minutes','rust',1,'briefing_refresh') RETURNING id::bigint").bind(user).bind(token).fetch_one(pool).await.unwrap();
    (
        task,
        BriefingRefreshClaimFence {
            locked_by: "test".to_owned(),
            lease_token: token,
            retry_count: 0,
            executor_runtime: "rust".to_owned(),
            executor_version: 1,
            executor_namespace: "briefing_refresh".to_owned(),
        },
    )
}

#[sqlx::test]
async fn correction_budget_survives_new_jobs_and_changed_input_recovers(pool: PgPool) {
    newsly_db::run_migrations(&pool).await.unwrap();
    let user: i64 = sqlx::query_scalar("INSERT INTO users(apple_id,email,is_active,is_admin) VALUES('compose-retry','compose-retry@example.com',true,false) RETURNING id::bigint").fetch_one(&pool).await.unwrap();
    let fake = RejectedComposer::default();
    let (task, fence) = claim(&pool, user).await;
    let context = CompositionContext {
        pool: &pool,
        task_id: task,
        user_id: user,
        fence: &fence,
    };
    let error = compose_unit(&fake, unit("Title [Update]"), 3, 0, context)
        .await
        .unwrap_err();
    assert!(!error.retryable());
    assert_eq!(
        *fake.0.lock().unwrap(),
        vec![
            None,
            Some("source content:1 must be linked exactly once".to_owned()),
            Some("source content:1 must be linked exactly once".to_owned())
        ]
    );
    sqlx::query("UPDATE processing_tasks SET status = 'failed', locked_by = NULL, locked_at = NULL, lease_token = NULL, lease_expires_at = NULL WHERE id = $1")
        .bind(task)
        .execute(&pool)
        .await
        .unwrap();
    let (next, next_fence) = claim(&pool, user).await;
    let next_context = CompositionContext {
        pool: &pool,
        task_id: next,
        user_id: user,
        fence: &next_fence,
    };
    assert!(
        compose_unit(&fake, unit("Title [Update]"), 3, 0, next_context)
            .await
            .is_err()
    );
    assert_eq!(
        fake.0.lock().unwrap().len(),
        3,
        "a new task does not reset the correction budget"
    );
    assert!(
        compose_unit(&fake, unit("Changed source input"), 3, 0, next_context)
            .await
            .is_err()
    );
    assert_eq!(
        fake.0.lock().unwrap().len(),
        6,
        "changed input is immediately eligible"
    );
    let calls: i64 =
        sqlx::query_scalar("SELECT sum(request_count)::bigint FROM vendor_usage_records")
            .fetch_one(&pool)
            .await
            .unwrap();
    assert_eq!(calls, 6);
    sqlx::query(
        "UPDATE briefing_composition_cooldowns SET retry_after = now() - interval '1 second'",
    )
    .execute(&pool)
    .await
    .unwrap();
    assert!(
        compose_unit(&fake, unit("Title [Update]"), 3, 0, next_context)
            .await
            .is_err()
    );
    assert_eq!(
        fake.0.lock().unwrap().len(),
        9,
        "unchanged input receives a bounded later recovery attempt"
    );
}

#[sqlx::test]
async fn progressive_readiness_skips_only_the_exact_cooling_input(pool: PgPool) {
    newsly_db::run_migrations(&pool).await.unwrap();
    let user: i64 = sqlx::query_scalar("INSERT INTO users(apple_id,email,is_active,is_admin) VALUES('progressive-ready','progressive-ready@example.com',true,false) RETURNING id::bigint").fetch_one(&pool).await.unwrap();
    let fake = RejectedComposer::default();
    let (task, fence) = claim(&pool, user).await;
    let context = CompositionContext {
        pool: &pool,
        task_id: task,
        user_id: user,
        fence: &fence,
    };
    assert!(
        composition_is_ready(fake.model_spec(), 0, &unit("Cooling category"), context)
            .await
            .unwrap()
    );
    assert!(
        compose_unit(&fake, unit("Cooling category"), 1, 0, context)
            .await
            .is_err()
    );
    assert!(
        !composition_is_ready(fake.model_spec(), 0, &unit("Cooling category"), context)
            .await
            .unwrap()
    );
    assert!(
        composition_is_ready(fake.model_spec(), 0, &unit("Next category"), context)
            .await
            .unwrap()
    );
    assert!(
        composition_is_ready("different:model", 0, &unit("Cooling category"), context)
            .await
            .unwrap()
    );
    assert_eq!(
        fake.0.lock().unwrap().len(),
        1,
        "readiness checks must not call the provider"
    );
}

fn append_batch(id: i64, key: &str, tier: &str) -> newsly_db::BriefingAppendBatch {
    let mut template = unit(key);
    template.lens.id = id;
    template.lens.key = key.to_owned();
    template.lens.tier = tier.to_owned();
    template.lens.position = i32::try_from(id).unwrap();
    let mut source = template.window.sources.remove(0);
    source.id = id;
    source.kind = if tier == "news" { "news" } else { "content" }.to_owned();
    source.source_key = format!("{}:{id}", source.kind);
    newsly_db::BriefingAppendBatch {
        lens: template.lens,
        pending_rows: vec![BriefingPendingIdentity {
            id,
            source_kind: source.kind.clone(),
            source_id: id,
            lens_key: key.to_owned(),
        }],
        sources: vec![source],
    }
}

#[sqlx::test]
async fn progressive_selection_keeps_articles_and_podcasts_with_one_ready_news(pool: PgPool) {
    newsly_db::run_migrations(&pool).await.unwrap();
    let user: i64 = sqlx::query_scalar("INSERT INTO users(apple_id,email,is_active,is_admin) VALUES('progressive-sections','progressive-sections@example.com',true,false) RETURNING id::bigint")
        .fetch_one(&pool).await.unwrap();
    let (task, fence) = claim(&pool, user).await;
    let services = BriefingRefreshWorkerServices::new(
        pool.clone(),
        QueueKernel::new(pool.clone()),
        BriefingCompositionGateway::from_env().unwrap(),
        NewsFilterGateway::new(None).unwrap(),
        NewsCategoryGateway::new(None).unwrap(),
        BriefingRefreshWorkerConfig::from_env().unwrap(),
    );
    let context = CompositionContext {
        pool: &pool,
        task_id: task,
        user_id: user,
        fence: &fence,
    };
    let mut prepared = PreparedBriefingRefresh {
        task_id: task,
        user_id: user,
        mode: BriefingRefreshMode::Append,
        starting_version: 0,
        prepared_state_changed: false,
        // Deep sections precede News in the input to exercise progressive sorting.
        append_batches: vec![
            append_batch(4, "longform", "longform"),
            append_batch(5, "audio", "audio"),
            append_batch(1, "news-cooling", "news"),
            append_batch(2, "news-ready", "news"),
            append_batch(3, "news-later", "news"),
        ],
        compaction_batches: vec![],
    };
    let cooling = &prepared.append_batches[2];
    let cooling_unit = CompositionUnit {
        ordinal: 0,
        lens: cooling.lens.clone(),
        window: PlannedBriefingWindow {
            sources: cooling.sources.clone(),
            event_groups: vec![vec![cooling.sources[0].source_key.clone()]],
        },
        kind: CompositionUnitKind::Append {
            pending_rows: cooling.pending_rows.clone(),
        },
    };
    let fingerprint = composition_fingerprint(
        services.gateway.model_spec(),
        services.config.max_figures_deep,
        &composition_request(&cooling_unit),
    );
    sqlx::query("INSERT INTO briefing_composition_cooldowns(user_id,fingerprint,retry_after) VALUES($1,$2,now() + interval '24 hours')")
        .bind(user).bind(fingerprint).execute(&pool).await.unwrap();

    // A later News batch must be skipped before its planned sources are resolved.
    // If selected, this deliberately incomplete pending map would fail selection.
    let later_pending = std::mem::take(&mut prepared.append_batches[4].pending_rows);
    let (selected, usage) = select_append_units(&services, &prepared, true, &[], context)
        .await
        .unwrap();
    assert_eq!(
        selected
            .iter()
            .map(|unit| unit.lens.key.as_str())
            .collect::<Vec<_>>(),
        ["news-ready", "longform", "audio"]
    );
    assert_eq!(
        selected.iter().map(|unit| unit.ordinal).collect::<Vec<_>>(),
        [0, 1, 2]
    );
    assert!(
        usage.is_empty(),
        "single-source windows require no provider calls"
    );
    for selected in &selected {
        let CompositionUnitKind::Append { pending_rows } = &selected.kind else {
            panic!("append selection produced a compaction");
        };
        assert_eq!(pending_rows.len(), 1);
        assert_eq!(pending_rows[0].source_id, selected.window.sources[0].id);
    }

    prepared.append_batches[4].pending_rows = later_pending;
    for mode in [BriefingRefreshMode::Append, BriefingRefreshMode::Full] {
        prepared.mode = mode;
        let (selected, usage) = select_append_units(&services, &prepared, false, &[], context)
            .await
            .unwrap();
        assert_eq!(
            selected
                .iter()
                .map(|unit| unit.lens.key.as_str())
                .collect::<Vec<_>>(),
            [
                "longform",
                "audio",
                "news-cooling",
                "news-ready",
                "news-later"
            ]
        );
        assert_eq!(
            selected.iter().map(|unit| unit.ordinal).collect::<Vec<_>>(),
            [0, 1, 2, 3, 4]
        );
        assert!(usage.is_empty());
    }
}

#[sqlx::test]
async fn prior_response_contract_cooldown_does_not_block_revised_news(pool: PgPool) {
    newsly_db::run_migrations(&pool).await.unwrap();
    let user: i64 = sqlx::query_scalar("INSERT INTO users(apple_id,email,is_active,is_admin) VALUES('compose-contract','compose-contract@example.com',true,false) RETURNING id::bigint").fetch_one(&pool).await.unwrap();
    let fake = RejectedComposer::default();
    let mut news = unit("Captured source");
    news.lens.tier = "news".to_owned();
    let request = composition_request(&news);
    // Exact prior production fingerprint, before the response contract was included.
    let prior = newsly_domain::sha256_hex(
        format!("briefing-v9-headlines|fake:test|0|{request:?}").as_bytes(),
    );
    sqlx::query("INSERT INTO briefing_composition_cooldowns(user_id,fingerprint,retry_after) VALUES($1,$2,now()+interval '24 hours')")
        .bind(user).bind(prior).execute(&pool).await.unwrap();
    let (task, fence) = claim(&pool, user).await;
    let context = CompositionContext {
        pool: &pool,
        task_id: task,
        user_id: user,
        fence: &fence,
    };
    assert!(
        composition_is_ready(fake.model_spec(), 0, &news, context)
            .await
            .unwrap()
    );
    assert!(compose_unit(&fake, news, 1, 0, context).await.is_err());
    assert_eq!(
        fake.0.lock().unwrap().len(),
        1,
        "revised contract must get a fresh validated attempt"
    );
}
