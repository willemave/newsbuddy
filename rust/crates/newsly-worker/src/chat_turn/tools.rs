use std::collections::BTreeSet;
use std::sync::{Arc, Mutex as StdMutex};
use std::time::Duration;

use newsly_agent_runtime::{
    AgentEventSink, AgentRuntimeError, BoxToolFuture, ToolCall, ToolDefinition, ToolExecutor,
    ToolOutput,
};
use newsly_contracts::AssistantFeedOption;
use newsly_db::{
    ChatContentFilters, ChatTaskSnapshot, canonical_direct_feed_url, load_active_feed_urls,
    mark_content_read, mark_content_unread, remove_content_from_knowledge,
    save_content_to_knowledge, search_accessible_content,
};
use newsly_domain::{AGGREGATOR_KEY_NAMES, AggregatorKey};
use newsly_e2b::{DirectE2bProvider, OutputLimits};
use newsly_providers::{FeedValidator, SourceResolver};
use newsly_queue::QueueKernel;
use schemars::{JsonSchema, schema_for};
use serde::Deserialize;
use serde::Serialize;
use serde_json::{Value, json};
use sqlx::PgPool;
use tokio::sync::Mutex;
use tokio::time::Instant;
use tokio_util::sync::CancellationToken;

use crate::content_body_store::ContentBodyStore;
use crate::share_actions::submission::{
    ShareSubmissionPolicy, apply_validated_feed_action, submit_content_action,
};
use crate::share_actions::workflows::{ContentActionInput, FeedActionInput, validated_feed_type};
use crate::task_sandbox::{
    AcquiredTaskSandbox, SandboxCommand, TaskSandboxOwner, agent_network_policy,
    run_command as run_sandbox_command,
};
use crate::task_tools::{ExaSearchClient, TaskToolExecutor, record_exa_tool_usage};

#[path = "tools/support.rs"]
mod support;
use support::{
    aggregator_feed_option, assistant_feed_option, bounded_query, clean, collected_activity,
    valid_url,
};

#[path = "tools/actions.rs"]
mod actions;
#[path = "tools/canonical.rs"]
mod canonical;
#[path = "tools/catalog.rs"]
mod catalog;
#[path = "tools/mutations.rs"]
mod mutations;
#[path = "tools/sandbox.rs"]
mod sandbox;
use actions::{ConfirmedAction, ResolvedSource};
pub(in crate::chat_turn) use catalog::{ChatTool, HostTool};

#[derive(Debug, Clone)]
pub(super) struct ChatToolDependencies {
    pub pool: PgPool,
    pub queue: QueueKernel,
    pub claim: newsly_queue::ClaimedTask,
    pub provider: Arc<DirectE2bProvider>,
    pub lifecycle: TaskSandboxOwner,
    pub exa: ExaSearchClient,
    pub source_resolver: SourceResolver,
    pub feed_validator: FeedValidator,
    pub body_store: ContentBodyStore,
    pub max_output_chars: usize,
    pub deadline: Instant,
    pub cancellation: CancellationToken,
}

#[derive(Debug)]
pub(super) struct ChatToolExecutor {
    dependencies: ChatToolDependencies,
    snapshot: ChatTaskSnapshot,
    sandbox: Mutex<Option<ChatSandboxState>>,
    /// Subscription cards from every discovery call this turn, first occurrence wins.
    feed_options: StdMutex<Vec<AssistantFeedOption>>,
    completed_actions: StdMutex<Vec<ConfirmedAction>>,
    read_cache: StdMutex<std::collections::BTreeMap<String, ToolOutput>>,
}

#[derive(Debug)]
struct ChatSandboxState {
    acquired: AcquiredTaskSandbox,
    executor: TaskToolExecutor,
}

impl ChatToolExecutor {
    pub(super) fn new(dependencies: ChatToolDependencies, snapshot: ChatTaskSnapshot) -> Self {
        Self {
            dependencies,
            snapshot,
            sandbox: Mutex::new(None),
            feed_options: StdMutex::new(Vec::new()),
            completed_actions: StdMutex::new(Vec::new()),
            read_cache: StdMutex::new(std::collections::BTreeMap::new()),
        }
    }

    pub(super) fn definitions() -> Vec<ToolDefinition> {
        ChatTool::all().map(ChatTool::definition).collect()
    }

    pub(super) fn render_metadata(&self) -> Option<Value> {
        let feed_options = self.feed_options.lock().map_or_else(
            |poisoned| poisoned.into_inner().clone(),
            |value| value.clone(),
        );
        if feed_options.is_empty() {
            return None;
        }
        serde_json::to_value(ChatRenderMetadata { feed_options }).ok()
    }

    /// Later discovery calls in a turn often narrow an earlier one, and the answer can cite
    /// both, so cards accumulate rather than replace each other.
    fn attach_feed_options(&self, options: &[AssistantFeedOption]) {
        let mut attached = self
            .feed_options
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        for option in options {
            if !attached.iter().any(|existing| existing.id == option.id) {
                attached.push(option.clone());
            }
        }
    }

    pub(super) async fn close(&self) -> Result<(), AgentRuntimeError> {
        let state = self.sandbox.lock().await.take();
        let Some(state) = state else { return Ok(()) };
        state.acquired.release().await.map_err(|error| {
            AgentRuntimeError::Tool(format!("chat sandbox cleanup failed: {error}"))
        })
    }

    async fn execute_inner(
        &self,
        tool: ChatTool,
        call: ToolCall,
        events: Arc<dyn AgentEventSink>,
    ) -> Result<ToolOutput, AgentRuntimeError> {
        match tool {
            ChatTool::Host(tool) => self.execute_canonical(tool, call).await,
            ChatTool::Sandbox(tool) => {
                let mut call = call;
                tool.transport_name().clone_into(&mut call.name);
                self.execute_sandbox(call, events).await
            }
        }
    }

    async fn search_web(&self, call: ToolCall) -> Result<ToolOutput, AgentRuntimeError> {
        let input: WebSearchInput = arguments(&call)?;
        let query = bounded_query(&input.query)?;
        let outcome = self
            .dependencies
            .exa
            .search(query, input.limit.unwrap_or(5).clamp(1, 8), None)
            .await
            .map_err(|error| AgentRuntimeError::Tool(error.to_string()))?;
        record_exa_tool_usage(
            &self.dependencies.pool,
            self.snapshot.user_id,
            &call.id,
            "chat_tool.web_search",
            outcome.usage,
        )
        .await;
        Ok(success(json!({"query": query, "results": outcome.results})))
    }

    async fn submit_content(
        &self,
        url: &str,
        title: Option<String>,
    ) -> Result<ToolOutput, AgentRuntimeError> {
        let url = valid_url(url)?;
        let action = ContentActionInput {
            url,
            title: clean(title),
            platform: None,
            content_type: None,
            instruction: None,
            chat_initial_message: None,
        };
        let mut transaction = self.begin_mutation().await?;
        let submitted = submit_content_action(
            &mut transaction,
            &self.dependencies.queue,
            self.snapshot.user_id,
            &action,
            ShareSubmissionPolicy::content_inbox(),
        )
        .await
        .map_err(db_tool_error)?;
        transaction.commit().await.map_err(db_tool_error)?;
        self.record_action(ConfirmedAction::Inbox {
            url: action.url,
            already_exists: submitted.already_exists,
        });
        Ok(success(json!({
            "content_id": submitted.content_id,
            "task_id": submitted.task_id,
            "already_exists": submitted.already_exists,
            "reference": {"kind":"content","id":submitted.content_id},
            "subscribed": false,
        })))
    }

    async fn subscribe(
        &self,
        source: ResolvedSource,
        title: Option<String>,
    ) -> Result<ToolOutput, AgentRuntimeError> {
        let ResolvedSource::Feed {
            url,
            kind,
            reference,
            title: stored_title,
        } = source
        else {
            return Err(db_tool_error(
                "Use an aggregator subscription for this source",
            ));
        };
        let url = valid_url(&url)?;
        let title = title.or(stored_title);
        let validation = self.dependencies.feed_validator.validate_feed(&url);
        let validated = tokio::select! {
            () = self.dependencies.cancellation.cancelled() => {
                return Err(AgentRuntimeError::Tool("feed validation was cancelled".to_owned()));
            }
            () = tokio::time::sleep_until(self.dependencies.deadline) => {
                return Err(AgentRuntimeError::Tool("feed validation exceeded the chat deadline".to_owned()));
            }
            result = validation => result
                .map_err(|error| AgentRuntimeError::Tool(error.to_string()))?,
        }
        .ok_or_else(|| AgentRuntimeError::Tool("the URL is not a valid feed".to_owned()))?;
        if kind == Some(newsly_providers::SourceKind::Podcast) && !validated.has_audio_entries {
            return Err(db_tool_error("The source feed contains no audio entries"));
        }
        let action = FeedActionInput {
            feed_type: Some(validated_feed_type(&validated, kind).as_str().to_owned()),
            feed_format: Some(validated.format.as_str().to_owned()),
            url: validated.effective_url,
            title: clean(title),
            platform: None,
            instruction: None,
        };
        let mut transaction = self.begin_mutation().await?;
        let subscription = apply_validated_feed_action(
            &mut transaction,
            &self.dependencies.queue,
            self.snapshot.user_id,
            &action,
            reference.as_ref(),
        )
        .await
        .map_err(db_tool_error)?;
        transaction.commit().await.map_err(db_tool_error)?;
        self.record_action(ConfirmedAction::Subscribed {
            source: ResolvedSource::Feed {
                url: subscription.feed_url.clone(),
                title: action.title,
                kind,
                reference: None,
            },
            mutation: subscription.mutation,
        });
        Ok(success(json!({
            "subscribed": true,
            "subscription_outcome": subscription.mutation.as_str(),
            "config_id": subscription.config_id,
            "feed_url": subscription.feed_url,
            "feed_type": subscription.feed_type,
            "feed_format": subscription.feed_format,
            "backfill_task_id": subscription.backfill_task_id,
        })))
    }

    async fn subscribe_aggregator(
        &self,
        key: &str,
        topics: Option<Vec<String>>,
    ) -> Result<ToolOutput, AgentRuntimeError> {
        let key = AggregatorKey::from_key(&key.trim().to_ascii_lowercase()).ok_or_else(|| {
            AgentRuntimeError::Tool(format!(
                "unknown aggregator key {:?}; valid keys: {}",
                key,
                AGGREGATOR_KEY_NAMES.join(", ")
            ))
        })?;
        let topics = topics
            .map(|topics| canonical_aggregator_topics(key, &topics))
            .transpose()?;
        let mut transaction = self.begin_mutation().await?;
        let before = crate::aggregator_demand::snapshot(&mut transaction)
            .await
            .map_err(db_tool_error)?;
        let applied = newsly_db::apply_aggregator_subscription(
            &mut transaction,
            self.snapshot.user_id,
            key,
            topics.as_deref(),
        )
        .await
        .map_err(db_tool_error)?;
        crate::aggregator_demand::enqueue_new_demand(
            &mut transaction,
            &self.dependencies.queue,
            &before,
        )
        .await
        .map_err(db_tool_error)?;
        transaction.commit().await.map_err(db_tool_error)?;
        self.record_action(ConfirmedAction::Subscribed {
            source: ResolvedSource::Aggregator(key),
            mutation: applied.mutation,
        });
        Ok(success(json!({
            "subscribed": true,
            "aggregator": key.as_str(),
            "title": key.display_name(),
            "subscription_outcome": applied.mutation.as_str(),
            "config_id": applied.config.id,
            "feed_url": applied.config.feed_url,
            "topics": applied.config.config.get("topics").cloned().unwrap_or(Value::Null),
            "offered_topics": key.topics(),
        })))
    }
}

impl ToolExecutor for ChatToolExecutor {
    fn execute(&self, call: ToolCall, events: Arc<dyn AgentEventSink>) -> BoxToolFuture<'_> {
        Box::pin(async move {
            let tool = ChatTool::parse(&call.name)
                .ok_or_else(|| db_tool_error(format!("Unsupported chat tool {}", call.name)))?;
            let key = tool
                .cacheable()
                .then(|| format!("{}:{}", tool.name(), call.arguments));
            if let Some(key) = &key
                && let Some(output) = self
                    .read_cache
                    .lock()
                    .unwrap_or_else(std::sync::PoisonError::into_inner)
                    .get(key)
                    .cloned()
            {
                return Ok(output);
            }
            let result = if tool.read_only() {
                tokio::select! {
                    () = self.dependencies.cancellation.cancelled() => Err(AgentRuntimeError::EventSink("chat turn cancelled".to_owned())),
                    result = tokio::time::timeout_at(self.dependencies.deadline,self.execute_inner(tool,call,events)) => result.unwrap_or_else(|_|Err(db_tool_error("Chat tool deadline exceeded"))),
                }
            } else {
                self.execute_inner(tool, call, events).await
            };
            match result {
                Ok(output) => {
                    if !output.is_error
                        && let Some(key) = key
                    {
                        self.read_cache
                            .lock()
                            .unwrap_or_else(std::sync::PoisonError::into_inner)
                            .insert(key, output.clone());
                    }
                    Ok(output)
                }
                Err(AgentRuntimeError::Tool(message)) => Ok(failure(message)),
                result => result,
            }
        })
    }
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct WebSearchInput {
    query: String,
    limit: Option<usize>,
}

#[derive(Debug, Serialize)]
struct ChatRenderMetadata {
    feed_options: Vec<AssistantFeedOption>,
}

/// Catalog-spells and de-duplicates requested topics, rejecting any the aggregator doesn't offer.
fn canonical_aggregator_topics(
    key: AggregatorKey,
    topics: &[String],
) -> Result<Vec<&'static str>, AgentRuntimeError> {
    let mut canonical = BTreeSet::new();
    for topic in topics
        .iter()
        .map(|topic| topic.trim())
        .filter(|topic| !topic.is_empty())
    {
        let spelled = key.canonical_topic(topic).ok_or_else(|| {
            AgentRuntimeError::Tool(match key.topics() {
                [] => format!("{} has no topic filter", key.display_name()),
                offered => format!(
                    "{} does not offer topic {topic:?}; offered topics: {}",
                    key.display_name(),
                    offered.join(", ")
                ),
            })
        })?;
        canonical.insert(spelled);
    }
    Ok(canonical.into_iter().collect())
}

fn definition<T: JsonSchema>(name: &str, description: &str) -> ToolDefinition {
    ToolDefinition {
        name: name.to_owned(),
        description: description.to_owned(),
        input_schema: schema_for!(T),
    }
}

fn arguments<T: for<'de> Deserialize<'de>>(call: &ToolCall) -> Result<T, AgentRuntimeError> {
    serde_json::from_value(call.arguments.clone()).map_err(|error| {
        AgentRuntimeError::Tool(format!("invalid {} arguments: {error}", call.name))
    })
}

fn db_tool_error(error: impl std::fmt::Display) -> AgentRuntimeError {
    AgentRuntimeError::Tool(error.to_string())
}

fn success(content: Value) -> ToolOutput {
    ToolOutput {
        content,
        is_error: false,
    }
}

fn failure(message: String) -> ToolOutput {
    ToolOutput {
        content: json!({"ok": false, "error": message}),
        is_error: true,
    }
}

#[cfg(test)]
#[path = "tools/aggregator_tool_tests.rs"]
mod aggregator_tool_tests;
