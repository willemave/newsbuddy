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
            layout: Err("missing source content:1".to_owned()),
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
            Some("missing source content:1".to_owned()),
            Some("missing source content:1".to_owned())
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
