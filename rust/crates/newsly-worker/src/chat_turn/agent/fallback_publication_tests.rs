use super::super::{ChatAgentError, ChatAgentRun, ChatRunCompletion, finish_run};
use super::tests::snapshot;
use crate::chat_turn::events::ChatEvents;
use crate::chat_turn::finalizer::ChatSuccessFinalizer;
use crate::chat_turn::handler::CompletedChatWork;
use crate::chat_turn::tools::{ChatToolDependencies, ChatToolExecutor};
use crate::content_body_store::ContentBodyStore;
use crate::task_sandbox::{TaskSandboxConfig, TaskSandboxOwner};
use crate::task_tools::ExaSearchClient;
use crate::{TaskFinalizer, TaskFinalizerResult};
use newsly_agent_runtime::{
    AgentRuntimeError, NewslyTranscript, ProviderUsage, ToolCall, ToolExecutor,
};
use newsly_domain::{AggregatorKey, ResourceKey, RuntimeOwner};
use newsly_e2b::{ControlPlaneConfig, DirectE2bProvider, FileLimits};
use newsly_providers::{FeedValidator, SourceResolver};
use newsly_queue::{
    ClaimRequest, ClaimRuntimeScope, ClaimedTask, EnqueueRequest, QueueKernel, TaskResult, TaskType,
};
use serde_json::json;
use sqlx::PgPool;
use std::{sync::Arc, time::Duration};
use tokio_util::sync::CancellationToken;

struct Harness {
    snapshot: newsly_db::ChatTaskSnapshot,
    claim: ClaimedTask,
    queue: QueueKernel,
    tools: Arc<ChatToolExecutor>,
    events: Arc<ChatEvents>,
    cancellation: CancellationToken,
    tool_cancellation: CancellationToken,
}

impl Harness {
    async fn new(pool: &PgPool) -> Self {
        let user_id: i64 = sqlx::query_scalar(
            "INSERT INTO users(apple_id,email,is_active,is_admin) VALUES('fallback-reader','fallback@example.test',true,false) RETURNING id::bigint",
        ).fetch_one(pool).await.unwrap();
        let session_id: i64 = sqlx::query_scalar(
            "INSERT INTO chat_sessions(user_id,council_mode,is_hidden_from_history,llm_model,llm_provider) VALUES($1,false,false,'openai:gpt-6.1-sol','openai') RETURNING id::bigint",
        ).bind(user_id).fetch_one(pool).await.unwrap();
        let message_id: i64 = sqlx::query_scalar(
            "INSERT INTO chat_messages(session_id,message_list,status,stream_generation) VALUES($1,'{}','processing',1) RETURNING id::bigint",
        ).bind(session_id).fetch_one(pool).await.unwrap();
        let queue = QueueKernel::new(pool.clone());
        let mut request = EnqueueRequest::new(TaskType::ChatTurn);
        request.owner_user_id = Some(user_id);
        request.payload =
            json!({"user_id":user_id,"session_id":session_id,"message_id":message_id})
                .as_object()
                .cloned();
        let task_id = queue.enqueue(request).await.unwrap();
        let namespace: String = sqlx::query_scalar(
            "SELECT executor_namespace FROM processing_tasks WHERE id::bigint = $1",
        )
        .bind(task_id)
        .fetch_one(pool)
        .await
        .unwrap();
        let mut claim_request = ClaimRequest::for_queue(
            "fallback-publication-test",
            TaskType::ChatTurn.spec().queue,
            ClaimRuntimeScope::namespaces(
                RuntimeOwner::Rust,
                [ResourceKey::new(namespace).unwrap()],
            )
            .unwrap(),
        );
        claim_request.task_type = Some(TaskType::ChatTurn);
        let claim = queue.claim(&claim_request).await.unwrap().unwrap();
        assert_eq!(claim.id, task_id);
        let mut snapshot = snapshot();
        snapshot.user_id = user_id;
        snapshot.session_id = session_id;
        snapshot.message_id = message_id;
        snapshot.queue_task_id = task_id;
        snapshot.llm_task_id = None;
        snapshot.context.user_prompt = "Follow arXiv machine learning papers".into();
        snapshot.context.session.user_id = user_id;
        snapshot.context.session.effective_session_id = session_id;
        snapshot.context.session.visible_session_id = session_id;

        // This path uses no sandbox, search, source-resolution, or model HTTP calls.
        let mut control = ControlPlaneConfig::production("unused-test-key".into()).unwrap();
        control.api_base = "http://127.0.0.1:9/".parse().unwrap();
        let provider = Arc::new(DirectE2bProvider::new(control, FileLimits::default()).unwrap());
        let lifecycle = TaskSandboxOwner::new(
            pool.clone(),
            provider.clone(),
            TaskSandboxConfig {
                template_id: "test-template".into(),
                template_revision: "test-revision".into(),
                sandbox_timeout: Duration::from_secs(60),
            },
        )
        .unwrap();
        let cancellation = CancellationToken::new();
        let tool_cancellation = cancellation.child_token();
        let tools = Arc::new(ChatToolExecutor::new(
            ChatToolDependencies {
                pool: pool.clone(),
                queue: queue.clone(),
                claim: claim.clone(),
                provider,
                lifecycle,
                exa: ExaSearchClient::new(
                    None,
                    "http://127.0.0.1:9/".parse().unwrap(),
                    Duration::from_secs(1),
                )
                .unwrap(),
                source_resolver: SourceResolver::from_env().unwrap(),
                feed_validator: FeedValidator::new(),
                body_store: ContentBodyStore::from_env().unwrap(),
                max_output_chars: 10_000,
                deadline: tokio::time::Instant::now() + Duration::from_secs(60),
                cancellation: tool_cancellation.clone(),
            },
            snapshot.clone(),
        ));
        let events = Arc::new(ChatEvents::new(pool.clone(), &snapshot));
        Self {
            snapshot,
            claim,
            queue,
            tools,
            events,
            cancellation,
            tool_cancellation,
        }
    }

    async fn subscribe(&self) -> Result<newsly_agent_runtime::ToolOutput, AgentRuntimeError> {
        self.tools.execute(ToolCall {
            id: "subscribe-call".into(),
            name: "subscribe_to_source".into(),
            arguments: json!({"source":{"kind":"aggregator","key":"arxiv"},"topics":["cs.LG"]}),
        }, self.events.clone()).await
    }

    async fn finish_model_error(&self) -> Result<ChatAgentRun, ChatAgentError> {
        finish_run(
            ChatRunCompletion {
                snapshot: &self.snapshot,
                history_message_count: 0,
                tools: self.tools.clone(),
                events: self.events.clone(),
                cancellation: self.cancellation.clone(),
                tool_cancellation: self.tool_cancellation.clone(),
            },
            Err(ChatAgentError::Agent(AgentRuntimeError::Provider(
                "scripted model failure after tool".into(),
            ))),
        )
        .await
    }
}

#[sqlx::test(migrations = "../newsly-db/migrations")]
async fn executor_subscription_survives_model_error_and_fenced_publication(pool: PgPool) {
    subscription_fallback(&pool, false).await;
}

#[sqlx::test(migrations = "../newsly-db/migrations")]
async fn executor_topic_update_survives_model_error_and_fenced_publication(pool: PgPool) {
    subscription_fallback(&pool, true).await;
}

async fn subscription_fallback(pool: &PgPool, existing_subscription: bool) {
    let harness = Harness::new(pool).await;
    if existing_subscription {
        let mut transaction = pool.begin().await.unwrap();
        newsly_db::apply_aggregator_subscription(
            &mut transaction,
            harness.snapshot.user_id,
            AggregatorKey::Arxiv,
            Some(&["cs.CL"]),
        )
        .await
        .unwrap();
        transaction.commit().await.unwrap();
    }
    assert!(harness.tools.fallback_summary().is_none());
    let output = harness.subscribe().await.unwrap();
    assert!(!output.is_error);
    assert_eq!(
        output.content["subscription_outcome"],
        if existing_subscription {
            "topics_updated"
        } else {
            "created"
        }
    );
    let summary = harness
        .tools
        .fallback_summary()
        .expect("committed typed action");
    let expected = if existing_subscription {
        "Updated the topic filter for arXiv."
    } else {
        "You now follow arXiv."
    };
    assert!(summary.contains(expected), "{summary}");
    let run = harness.finish_model_error().await.unwrap();
    assert_eq!(run.outcome.output_text, summary);
    assert_eq!(run.outcome.usage, ProviderUsage::default());
    assert!(run.outcome.provider_response_id.is_none());
    let completed = CompletedChatWork {
        output_text: run.outcome.output_text,
        transcript: run.turn_transcript,
        render_metadata: run.render_metadata,
        tool_names: run.tool_names,
        model_provider: run.model_provider,
        model_name: run.outcome.model_name,
        usage: run.outcome.usage,
    };
    let finalizer = ChatSuccessFinalizer::new(harness.snapshot.clone(), completed);
    let mut fenced = harness
        .queue
        .begin_fenced_finalization(&harness.claim, &TaskResult::ok(), 0)
        .await
        .unwrap()
        .unwrap();
    assert!(matches!(
        finalizer.apply(fenced.transaction_mut()).await.unwrap(),
        TaskFinalizerResult::Keep
    ));
    assert!(fenced.finish().await.unwrap().is_some());
    let (status, error, message_list): (String, Option<String>, String) =
        sqlx::query_as("SELECT status,error,message_list FROM chat_messages WHERE id::bigint = $1")
            .bind(harness.snapshot.message_id)
            .fetch_one(pool)
            .await
            .unwrap();
    assert_eq!(status, "completed");
    assert!(error.is_none());
    let transcript: NewslyTranscript = serde_json::from_str(&message_list).unwrap();
    transcript.validate().unwrap();
    assert!(message_list.contains(expected));
    let task_status: String =
        sqlx::query_scalar("SELECT status FROM processing_tasks WHERE id::bigint = $1")
            .bind(harness.claim.id)
            .fetch_one(pool)
            .await
            .unwrap();
    assert_eq!(task_status, "completed");
    let configs: Vec<serde_json::Value> =
        sqlx::query_scalar("SELECT config FROM user_scraper_configs WHERE user_id::bigint = $1")
            .bind(harness.snapshot.user_id)
            .fetch_all(pool)
            .await
            .unwrap();
    assert_eq!(configs.len(), 1);
    assert_eq!(configs[0]["topics"], json!(["cs.LG"]));
    let usage: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM vendor_usage_records WHERE message_id::bigint = $1",
    )
    .bind(harness.snapshot.message_id)
    .fetch_one(pool)
    .await
    .unwrap();
    assert_eq!(usage, 0);
}

#[sqlx::test(migrations = "../newsly-db/migrations")]
async fn cancellation_after_committed_action_never_publishes_success(pool: PgPool) {
    let harness = Harness::new(&pool).await;
    assert!(!harness.subscribe().await.unwrap().is_error);
    assert!(harness.tools.fallback_summary().is_some());
    harness.cancellation.cancel();
    assert!(matches!(
        harness.finish_model_error().await,
        Err(ChatAgentError::Cancelled)
    ));
    let status: String =
        sqlx::query_scalar("SELECT status FROM chat_messages WHERE id::bigint = $1")
            .bind(harness.snapshot.message_id)
            .fetch_one(&pool)
            .await
            .unwrap();
    assert_eq!(status, "processing");
}

#[sqlx::test(migrations = "../newsly-db/migrations")]
async fn failed_product_transaction_records_no_action_or_success_fallback(pool: PgPool) {
    let harness = Harness::new(&pool).await;
    sqlx::query("CREATE FUNCTION reject_test_subscription() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'scripted subscription failure'; END; $$")
        .execute(&pool).await.unwrap();
    sqlx::query("CREATE TRIGGER reject_test_subscription BEFORE INSERT ON user_scraper_configs FOR EACH ROW EXECUTE FUNCTION reject_test_subscription()")
        .execute(&pool).await.unwrap();
    assert!(harness.subscribe().await.unwrap().is_error);
    assert!(harness.tools.fallback_summary().is_none());
    assert!(matches!(
        harness.finish_model_error().await,
        Err(ChatAgentError::Agent(AgentRuntimeError::Provider(_)))
    ));
    let configs: i64 =
        sqlx::query_scalar("SELECT count(*) FROM user_scraper_configs WHERE user_id::bigint = $1")
            .bind(harness.snapshot.user_id)
            .fetch_one(&pool)
            .await
            .unwrap();
    assert_eq!(configs, 0);
}
