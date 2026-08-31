use std::collections::BTreeMap;
use std::sync::{Arc, Mutex as StdMutex};
use std::time::Duration;

use futures_util::StreamExt;
use newsly_agent_runtime::{
    AgentEventSink, AgentRuntimeError, BoxToolFuture, ToolCall, ToolDefinition, ToolExecutor,
    ToolOutput,
};
use newsly_contracts::{AssistantFeedOption, FeedFormat, FeedType};
use newsly_db::{
    ChatTaskSnapshot, create_deep_research_handoff, list_unread_chat_news, mark_content_read,
    mark_content_unread, prepare_chat_article_conversion, remove_content_from_knowledge,
    save_content_to_knowledge, search_chat_content, search_chat_knowledge, search_chat_news,
    search_chat_subscription_content,
};
use newsly_e2b::{
    CommandEvent, CommandRequest, DirectE2bProvider, ExecutionTag, FeedValidator, NetworkPolicy,
    OutputLimits, SandboxProvider, SandboxUser,
};
use newsly_providers::OnboardingGateway;
use newsly_queue::QueueKernel;
use schemars::{JsonSchema, schema_for};
use serde::Deserialize;
use serde::Serialize;
use serde_json::{Value, json};
use sha1::{Digest, Sha1};
use sqlx::PgPool;
use tokio::sync::Mutex;
use tokio::time::Instant;
use tokio_util::sync::CancellationToken;

use crate::agent_vm::{AcquiredAgentVmSession, AgentVmLifecycle};
use crate::onboarding_discovery::normalize_seeds;
use crate::share_actions::submission::{
    ShareSubmissionPolicy, apply_validated_feed_action, enqueue_content_sync, submit_content_action,
};
use crate::share_actions::tools::{ExaSearchClient, ShareActionToolExecutor};
use crate::share_actions::workflows::{
    ContentActionInput, FeedActionInput, validated_scraper_type,
};

#[path = "tools/support.rs"]
mod support;
use support::{bounded_query, chat_output_limits, clean, tail_chars, valid_url};

const DEEP_RESEARCH_MODEL: &str = "o4-mini-deep-research-2025-06-26";
const PRIVATE_NETWORK_DENIALS: [&str; 9] = [
    "10.0.0.0/8",
    "100.64.0.0/10",
    "169.254.0.0/16",
    "172.16.0.0/12",
    "192.0.0.0/24",
    "192.168.0.0/16",
    "198.18.0.0/15",
    "fc00::/7",
    "fe80::/10",
];

#[derive(Debug, Clone)]
pub(super) struct ChatToolDependencies {
    pub pool: PgPool,
    pub queue: QueueKernel,
    pub provider: Arc<DirectE2bProvider>,
    pub lifecycle: AgentVmLifecycle,
    pub exa: ExaSearchClient,
    pub onboarding: OnboardingGateway,
    pub feed_validator: FeedValidator,
    pub max_output_chars: usize,
    pub deadline: Instant,
    pub cancellation: CancellationToken,
}

#[derive(Debug)]
pub(super) struct ChatToolExecutor {
    dependencies: ChatToolDependencies,
    snapshot: ChatTaskSnapshot,
    vm: Mutex<Option<ChatVmState>>,
    render_metadata: StdMutex<Option<Value>>,
}

#[derive(Debug)]
struct ChatVmState {
    acquired: AcquiredAgentVmSession,
    executor: ShareActionToolExecutor,
}

struct PendingChatVm {
    provider: Arc<DirectE2bProvider>,
    acquired: Option<AcquiredAgentVmSession>,
    message_id: i64,
}

impl ChatToolExecutor {
    pub(super) fn new(dependencies: ChatToolDependencies, snapshot: ChatTaskSnapshot) -> Self {
        Self {
            dependencies,
            snapshot,
            vm: Mutex::new(None),
            render_metadata: StdMutex::new(None),
        }
    }

    pub(super) fn definitions() -> Vec<ToolDefinition> {
        vec![
            definition::<ExecuteBashInput>(
                "execute_bash",
                "Run one bounded bash command inside the persistent user VM.",
            ),
            definition::<WriteFileInput>(
                "write_file",
                "Write UTF-8 text to a chat-workspace relative file.",
            ),
            definition::<EditFileInput>(
                "edit_file",
                "Replace exact text in a chat-workspace relative UTF-8 file.",
            ),
            definition::<ReadFileInput>(
                "read_file",
                "Read bounded UTF-8 text from the chat workspace or read-only /data corpus.",
            ),
            definition::<ListFilesInput>(
                "list_files",
                "List files below the chat workspace or read-only /data corpus.",
            ),
            definition::<WebSearchInput>(
                "exa_web_search",
                "Search the public web through Exa and return title, URL, and snippet records.",
            ),
            definition::<WebSearchInput>(
                "search_web",
                "Search the public web through Exa for current factual context.",
            ),
            definition::<SearchInput>(
                "search_knowledge",
                "Search the user's saved Newsly knowledge library.",
            ),
            definition::<SearchInput>(
                "search_content",
                "Search content visible in the user's Newsly inbox.",
            ),
            definition::<SearchInput>("search_news", "Search user-visible Newsly fast-news items."),
            definition::<SearchInput>(
                "search_subscription_feeds",
                "Search content from sources the user already follows.",
            ),
            definition::<UnreadInput>(
                "list_unread_news_items",
                "List unread user-visible fast-news items.",
            ),
            definition::<WebSearchInput>(
                "find_feed_options",
                "Find candidate blogs, newsletters, podcasts, or RSS feeds for review.",
            ),
            definition::<ContentUrlInput>(
                "add_item_to_feed",
                "Submit one URL into the user's Newsly inbox.",
            ),
            definition::<SubscribeInput>(
                "subscribe_to_feed",
                "Subscribe to a direct feed URL explicitly requested by the user.",
            ),
            definition::<ContentIdInput>(
                "save_to_knowledge",
                "Save one content item to the user's knowledge library.",
            ),
            definition::<ContentIdInput>(
                "remove_from_knowledge",
                "Remove one content item from the user's knowledge library.",
            ),
            definition::<ContentIdInput>("mark_content_read", "Mark one content item read."),
            definition::<ContentIdInput>("mark_content_unread", "Mark one content item unread."),
            definition::<ContentIdInput>(
                "convert_news_to_article_tool",
                "Submit the article URL attached to a Newsly news content row.",
            ),
            definition::<DeepResearchInput>(
                "start_deep_research_handoff",
                "Create a durable deep-research chat handoff for a long-running question.",
            ),
        ]
    }

    pub(super) fn render_metadata(&self) -> Option<Value> {
        self.render_metadata.lock().map_or_else(
            |poisoned| poisoned.into_inner().clone(),
            |value| value.clone(),
        )
    }

    pub(super) async fn close(&self) -> Result<(), AgentRuntimeError> {
        let state = self.vm.lock().await.take();
        let Some(state) = state else { return Ok(()) };
        let mut cleanup_error = None;
        if let Err(error) = self
            .dependencies
            .provider
            .reset_network(&state.acquired.session.sandbox.sandbox_id)
            .await
        {
            tracing::error!(
                message_id = self.snapshot.message_id,
                error = %error,
                "failed to reset chat agent VM network policy"
            );
            cleanup_error = Some(error.to_string());
            if let Err(kill_error) = self
                .dependencies
                .provider
                .kill_sandbox(&state.acquired.session.sandbox.sandbox_id)
                .await
            {
                tracing::error!(
                    message_id = self.snapshot.message_id,
                    error = %kill_error,
                    "failed to destroy chat agent VM after network reset failure"
                );
            }
        }
        if let Err(error) = state.acquired.release().await {
            tracing::error!(
                message_id = self.snapshot.message_id,
                error = %error,
                "failed to release chat agent VM namespace"
            );
            cleanup_error.get_or_insert_with(|| error.to_string());
        }
        cleanup_error.map_or(Ok(()), |error| {
            Err(AgentRuntimeError::Tool(format!(
                "chat VM cleanup failed: {error}"
            )))
        })
    }

    async fn execute_inner(
        &self,
        call: ToolCall,
        events: Arc<dyn AgentEventSink>,
    ) -> Result<ToolOutput, AgentRuntimeError> {
        match call.name.as_str() {
            "execute_bash" | "write_file" | "edit_file" | "read_file" | "list_files" => {
                self.execute_vm(call, events).await
            }
            "exa_web_search" | "search_web" => self.search_web(call).await,
            "find_feed_options" => self.find_feed_options(call).await,
            "search_knowledge" => self.search_content_kind(call, SearchKind::Knowledge).await,
            "search_content" => self.search_content_kind(call, SearchKind::Content).await,
            "search_news" => self.search_news(call).await,
            "search_subscription_feeds" => {
                self.search_content_kind(call, SearchKind::Subscription)
                    .await
            }
            "list_unread_news_items" => self.list_unread(call).await,
            "save_to_knowledge" => self.mutate_content(call, Mutation::Save).await,
            "remove_from_knowledge" => self.mutate_content(call, Mutation::Remove).await,
            "mark_content_read" => self.mutate_content(call, Mutation::Read).await,
            "mark_content_unread" => self.mutate_content(call, Mutation::Unread).await,
            "add_item_to_feed" => self.submit_content(call).await,
            "subscribe_to_feed" => self.subscribe(call).await,
            "convert_news_to_article_tool" => self.convert_news(call).await,
            "start_deep_research_handoff" => self.deep_research_handoff(call).await,
            other => Err(AgentRuntimeError::Tool(format!(
                "unsupported chat tool {other}"
            ))),
        }
    }

    async fn execute_vm(
        &self,
        call: ToolCall,
        events: Arc<dyn AgentEventSink>,
    ) -> Result<ToolOutput, AgentRuntimeError> {
        let mut state = self.vm.lock().await;
        if state.is_none() {
            *state = Some(self.acquire_vm().await?);
        }
        let state = state.as_ref().expect("chat VM state was initialized");
        if call.name == "execute_bash" {
            self.execute_bash_streaming(state, &call, &events).await
        } else {
            state.executor.execute(call, events).await
        }
    }

    async fn execute_bash_streaming(
        &self,
        state: &ChatVmState,
        call: &ToolCall,
        events: &Arc<dyn AgentEventSink>,
    ) -> Result<ToolOutput, AgentRuntimeError> {
        let input: ExecuteBashInput = arguments(call)?;
        let timeout = Duration::from_secs(input.timeout_seconds.unwrap_or(120).clamp(1, 300));
        let requested_deadline = Instant::now()
            .checked_add(timeout)
            .ok_or_else(|| AgentRuntimeError::Tool("chat command deadline overflow".to_owned()))?;
        let deadline = requested_deadline.min(self.dependencies.deadline);
        let request = CommandRequest {
            command: "/bin/bash".to_owned(),
            args: vec!["-lc".to_owned(), input.command],
            env: BTreeMap::new(),
            cwd: Some(self.snapshot.workspace_path.clone()),
            username: Some(
                SandboxUser::parse("user")
                    .map_err(|error| AgentRuntimeError::Tool(error.to_string()))?,
            ),
            tag: ExecutionTag::new(),
            stdin_enabled: false,
            absolute_deadline: deadline,
            idle_timeout: timeout.min(Duration::from_secs(60)),
            output_limits: chat_output_limits(self.dependencies.max_output_chars),
        };
        let mut stream = self
            .dependencies
            .provider
            .start_process(
                &state.acquired.session.sandbox,
                request,
                self.dependencies.cancellation.child_token(),
            )
            .await
            .map_err(db_tool_error)?;
        let mut stdout = String::new();
        let mut stderr = String::new();
        let mut terminal = None;
        while let Some(event) = stream.next().await {
            match event.map_err(db_tool_error)? {
                CommandEvent::Stdout { text, .. } | CommandEvent::Pty { text, .. } => {
                    stdout.push_str(&text);
                    events.publish(newsly_agent_runtime::AgentEvent::ToolProgress {
                        id: call.id.clone(),
                        text: tail_chars(&text, 2_000),
                    })?;
                }
                CommandEvent::Stderr { text, .. } => {
                    stderr.push_str(&text);
                    events.publish(newsly_agent_runtime::AgentEvent::ToolProgress {
                        id: call.id.clone(),
                        text: format!("stderr: {}", tail_chars(&text, 1_990)),
                    })?;
                }
                CommandEvent::Exited {
                    status,
                    exit_code,
                    error,
                    ..
                } => terminal = Some((status, exit_code, error)),
                CommandEvent::Started { .. }
                | CommandEvent::KeepAlive { .. }
                | CommandEvent::TransportDisconnected { .. } => {}
            }
        }
        let Some((status, exit_code, error)) = terminal else {
            return Err(AgentRuntimeError::Tool(
                "chat command stream ended without a terminal event".to_owned(),
            ));
        };
        Ok(ToolOutput {
            is_error: exit_code != 0,
            content: json!({
                "ok": exit_code == 0,
                "stdout": stdout,
                "stderr": stderr,
                "status": format!("{status:?}").to_ascii_lowercase(),
                "exit_code": exit_code,
                "error": error,
            }),
        })
    }

    async fn acquire_vm(&self) -> Result<ChatVmState, AgentRuntimeError> {
        let task_id = self
            .snapshot
            .llm_task_id
            .unwrap_or(self.snapshot.queue_task_id);
        let acquired = self
            .dependencies
            .lifecycle
            .acquire_for_task(
                self.snapshot.user_id,
                &self.snapshot.vm_namespace,
                task_id,
                "chat",
                self.dependencies.deadline,
                self.dependencies.cancellation.child_token(),
            )
            .await
            .map_err(|error| AgentRuntimeError::Tool(error.to_string()))?;
        let mut pending = PendingChatVm::new(
            Arc::clone(&self.dependencies.provider),
            acquired,
            self.snapshot.message_id,
        );
        let sandbox = pending.sandbox().clone();
        if let Err(error) = self.prepare_vm(&sandbox).await {
            pending.cleanup().await;
            return Err(error);
        }
        let executor = match ShareActionToolExecutor::new(
            Arc::clone(&self.dependencies.provider),
            sandbox.clone(),
            &self.snapshot.workspace_path,
            self.dependencies.deadline,
            self.dependencies.cancellation.child_token(),
            self.dependencies.max_output_chars,
            self.dependencies.exa.clone(),
        ) {
            Ok(executor) => executor,
            Err(error) => {
                pending.cleanup().await;
                return Err(AgentRuntimeError::Tool(error.to_string()));
            }
        };
        let acquired = pending.disarm();
        Ok(ChatVmState { acquired, executor })
    }

    async fn prepare_vm(
        &self,
        sandbox: &newsly_e2b::SandboxHandle,
    ) -> Result<(), AgentRuntimeError> {
        self.dependencies
            .provider
            .update_network(
                &sandbox.sandbox_id,
                &NetworkPolicy {
                    deny_out: PRIVATE_NETWORK_DENIALS
                        .into_iter()
                        .map(str::to_owned)
                        .collect(),
                    allow_internet_access: Some(true),
                    ..NetworkPolicy::default()
                },
            )
            .await
            .map_err(|error| AgentRuntimeError::Tool(error.to_string()))?;
        let stream = self
            .dependencies
            .provider
            .start_process(
                sandbox,
                CommandRequest {
                    command: "/bin/mkdir".to_owned(),
                    args: vec![
                        "-p".to_owned(),
                        self.snapshot.workspace_path.clone(),
                        self.snapshot.shared_workspace_path.clone(),
                    ],
                    env: BTreeMap::new(),
                    cwd: None,
                    username: Some(
                        SandboxUser::parse("user")
                            .map_err(|error| AgentRuntimeError::Tool(error.to_string()))?,
                    ),
                    tag: ExecutionTag::new(),
                    stdin_enabled: false,
                    absolute_deadline: self.dependencies.deadline,
                    idle_timeout: Duration::from_secs(60),
                    output_limits: OutputLimits::default(),
                },
                self.dependencies.cancellation.child_token(),
            )
            .await
            .map_err(|error| AgentRuntimeError::Tool(error.to_string()))?;
        let result = stream
            .collect_result()
            .await
            .map_err(|error| AgentRuntimeError::Tool(error.to_string()))?;
        if result.exit_code != 0 {
            return Err(AgentRuntimeError::Tool(format!(
                "chat VM workspace bootstrap exited with {}: {}",
                result.exit_code, result.output.stderr
            )));
        }
        Ok(())
    }

    async fn search_web(&self, call: ToolCall) -> Result<ToolOutput, AgentRuntimeError> {
        let input: WebSearchInput = arguments(&call)?;
        let query = bounded_query(&input.query)?;
        let results = self
            .dependencies
            .exa
            .search(
                query,
                input.limit.or(input.num_results).unwrap_or(5).clamp(1, 8),
                input.category.as_deref(),
            )
            .await
            .map_err(|error| AgentRuntimeError::Tool(error.to_string()))?;
        Ok(success(json!({"query": query, "results": results})))
    }

    async fn find_feed_options(&self, call: ToolCall) -> Result<ToolOutput, AgentRuntimeError> {
        let input: WebSearchInput = arguments(&call)?;
        let query = bounded_query(&input.query)?;
        let limit = input.limit.or(input.num_results).unwrap_or(5).clamp(1, 5);
        let topics = vec![query.to_owned()];
        let seeds = self
            .dependencies
            .onboarding
            .fast_discover(query, &topics)
            .await
            .map_err(|error| AgentRuntimeError::Tool(error.to_string()))?;
        let suggestions = normalize_seeds(&self.dependencies.feed_validator, seeds, query, &topics)
            .await
            .map_err(|error| AgentRuntimeError::Tool(error.to_string()))?;
        let mut options = suggestions
            .into_iter()
            .filter_map(assistant_feed_option)
            .collect::<Vec<_>>();
        if looks_like_podcast_query(query) {
            options.sort_by_key(|option| u8::from(option.feed_type != FeedType::PodcastRss));
        }
        options.truncate(limit);
        let payload = AssistantFeedOptionsResult {
            query: query.to_owned(),
            options: options.clone(),
        };
        let metadata = serde_json::to_value(ChatRenderMetadata {
            feed_options: options,
        })
        .map_err(|error| AgentRuntimeError::Tool(error.to_string()))?;
        match self.render_metadata.lock() {
            Ok(mut value) => *value = Some(metadata.clone()),
            Err(poisoned) => *poisoned.into_inner() = Some(metadata.clone()),
        }
        Ok(success(serde_json::to_value(payload).map_err(|error| {
            AgentRuntimeError::Tool(error.to_string())
        })?))
    }

    async fn search_content_kind(
        &self,
        call: ToolCall,
        kind: SearchKind,
    ) -> Result<ToolOutput, AgentRuntimeError> {
        let input: SearchInput = arguments(&call)?;
        let query = bounded_query(&input.query)?;
        let limit = i64::try_from(input.limit.unwrap_or(5).clamp(1, 10)).unwrap_or(10);
        let results = match kind {
            SearchKind::Knowledge => {
                search_chat_knowledge(&self.dependencies.pool, self.snapshot.user_id, query, limit)
                    .await
            }
            SearchKind::Content => {
                search_chat_content(&self.dependencies.pool, self.snapshot.user_id, query, limit)
                    .await
            }
            SearchKind::Subscription => {
                search_chat_subscription_content(
                    &self.dependencies.pool,
                    self.snapshot.user_id,
                    query,
                    limit,
                )
                .await
            }
        }
        .map_err(|error| AgentRuntimeError::Tool(error.to_string()))?;
        Ok(success(json!({"query": query, "items": results})))
    }

    async fn search_news(&self, call: ToolCall) -> Result<ToolOutput, AgentRuntimeError> {
        let input: SearchInput = arguments(&call)?;
        let query = bounded_query(&input.query)?;
        let limit = i64::try_from(input.limit.unwrap_or(5).clamp(1, 10)).unwrap_or(10);
        let results =
            search_chat_news(&self.dependencies.pool, self.snapshot.user_id, query, limit)
                .await
                .map_err(|error| AgentRuntimeError::Tool(error.to_string()))?;
        Ok(success(json!({"query": query, "items": results})))
    }

    async fn list_unread(&self, call: ToolCall) -> Result<ToolOutput, AgentRuntimeError> {
        let input: UnreadInput = arguments(&call)?;
        let limit = i64::try_from(input.limit.unwrap_or(100).clamp(1, 200)).unwrap_or(100);
        let page = list_unread_chat_news(&self.dependencies.pool, self.snapshot.user_id, limit)
            .await
            .map_err(|error| AgentRuntimeError::Tool(error.to_string()))?;
        let returned_count = page.items.len();
        Ok(success(json!({
            "items": page.items,
            "total_count": page.total_count,
            "returned_count": returned_count,
            "truncated": page.total_count > i64::try_from(returned_count).unwrap_or(i64::MAX),
            "limit": limit,
        })))
    }

    async fn mutate_content(
        &self,
        call: ToolCall,
        mutation: Mutation,
    ) -> Result<ToolOutput, AgentRuntimeError> {
        let input: ContentIdInput = arguments(&call)?;
        if input.content_id <= 0 {
            return Err(AgentRuntimeError::Tool(
                "content_id must be positive".to_owned(),
            ));
        }
        let mut transaction = self
            .dependencies
            .pool
            .begin()
            .await
            .map_err(db_tool_error)?;
        let changed = match mutation {
            Mutation::Save => {
                save_content_to_knowledge(
                    &mut transaction,
                    self.snapshot.user_id,
                    input.content_id,
                )
                .await
                .map_err(db_tool_error)?;
                true
            }
            Mutation::Remove => remove_content_from_knowledge(
                &mut transaction,
                self.snapshot.user_id,
                input.content_id,
            )
            .await
            .map_err(db_tool_error)?,
            Mutation::Read => {
                mark_content_read(&mut transaction, self.snapshot.user_id, input.content_id)
                    .await
                    .map_err(db_tool_error)?;
                true
            }
            Mutation::Unread => {
                mark_content_unread(&mut transaction, self.snapshot.user_id, input.content_id)
                    .await
                    .map_err(db_tool_error)?
                    > 0
            }
        };
        if matches!(mutation, Mutation::Save | Mutation::Remove) {
            enqueue_content_sync(
                &mut transaction,
                &self.dependencies.queue,
                self.snapshot.user_id,
                input.content_id,
            )
            .await
            .map_err(db_tool_error)?;
        }
        transaction.commit().await.map_err(db_tool_error)?;
        Ok(success(json!({
            "content_id": input.content_id,
            "changed": changed,
            "action": mutation.as_str(),
        })))
    }

    async fn submit_content(&self, call: ToolCall) -> Result<ToolOutput, AgentRuntimeError> {
        let input: ContentUrlInput = arguments(&call)?;
        let url = valid_url(&input.url)?;
        let action = ContentActionInput {
            url,
            title: clean(input.title),
            platform: None,
            content_type: None,
            instruction: None,
            chat_initial_message: None,
        };
        let mut transaction = self
            .dependencies
            .pool
            .begin()
            .await
            .map_err(db_tool_error)?;
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
        Ok(success(json!({
            "content_id": submitted.content_id,
            "task_id": submitted.task_id,
            "already_exists": submitted.already_exists,
            "subscribed": false,
        })))
    }

    async fn subscribe(&self, call: ToolCall) -> Result<ToolOutput, AgentRuntimeError> {
        let input: SubscribeInput = arguments(&call)?;
        let url = valid_url(&input.url)?;
        let feed_type_hint = clean(input.feed_type);
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
        let action = FeedActionInput {
            feed_type: Some(validated_scraper_type(&validated)),
            feed_format: Some(validated.format.as_str().to_owned()),
            url: validated.effective_url,
            title: clean(input.title),
            platform: feed_type_hint,
            instruction: None,
        };
        let mut transaction = self
            .dependencies
            .pool
            .begin()
            .await
            .map_err(db_tool_error)?;
        let subscription = apply_validated_feed_action(
            &mut transaction,
            &self.dependencies.queue,
            self.snapshot.user_id,
            &action,
        )
        .await
        .map_err(db_tool_error)?;
        transaction.commit().await.map_err(db_tool_error)?;
        Ok(success(json!({
            "subscribed": true,
            "subscription_outcome": subscription.outcome,
            "config_id": subscription.config_id,
            "feed_url": subscription.feed_url,
            "feed_type": subscription.feed_type,
            "feed_format": subscription.feed_format,
            "backfill_task_id": subscription.backfill_task_id,
        })))
    }

    async fn convert_news(&self, call: ToolCall) -> Result<ToolOutput, AgentRuntimeError> {
        let input: ContentIdInput = arguments(&call)?;
        let mut transaction = self
            .dependencies
            .pool
            .begin()
            .await
            .map_err(db_tool_error)?;
        let source = prepare_chat_article_conversion(
            &mut transaction,
            self.snapshot.user_id,
            input.content_id,
        )
        .await
        .map_err(db_tool_error)?;
        let Some(source) = source else {
            transaction.rollback().await.map_err(db_tool_error)?;
            return Ok(failure(format!(
                "Content {} is not a convertible news item",
                input.content_id
            )));
        };
        let action = ContentActionInput {
            url: source.url,
            title: source.title,
            platform: None,
            content_type: None,
            instruction: None,
            chat_initial_message: None,
        };
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
        Ok(success(json!({
            "source_content_id": input.content_id,
            "content_id": submitted.content_id,
            "task_id": submitted.task_id,
            "already_exists": submitted.already_exists,
        })))
    }

    async fn deep_research_handoff(&self, call: ToolCall) -> Result<ToolOutput, AgentRuntimeError> {
        let input: DeepResearchInput = arguments(&call)?;
        let question = bounded_query(&input.question)?;
        let mut transaction = self
            .dependencies
            .pool
            .begin()
            .await
            .map_err(db_tool_error)?;
        let session_id = create_deep_research_handoff(
            &mut transaction,
            self.snapshot.user_id,
            self.snapshot.context.session.content_id,
            question,
            DEEP_RESEARCH_MODEL,
        )
        .await
        .map_err(db_tool_error)?;
        transaction.commit().await.map_err(db_tool_error)?;
        let Some(session_id) = session_id else {
            return Ok(failure("The user account is no longer active".to_owned()));
        };
        Ok(success(json!({
            "session_id": session_id,
            "message": format!("Started a deep research handoff in session {session_id}."),
        })))
    }
}

impl PendingChatVm {
    fn new(
        provider: Arc<DirectE2bProvider>,
        acquired: AcquiredAgentVmSession,
        message_id: i64,
    ) -> Self {
        Self {
            provider,
            acquired: Some(acquired),
            message_id,
        }
    }

    fn sandbox(&self) -> &newsly_e2b::SandboxHandle {
        &self
            .acquired
            .as_ref()
            .expect("pending chat VM must own its acquired session")
            .session
            .sandbox
    }

    fn disarm(mut self) -> AcquiredAgentVmSession {
        self.acquired
            .take()
            .expect("pending chat VM must own its acquired session")
    }

    async fn cleanup(&mut self) {
        let Some(acquired) = self.acquired.take() else {
            return;
        };
        cleanup_chat_vm(
            Arc::clone(&self.provider),
            acquired,
            self.message_id,
            "chat VM bootstrap",
        )
        .await;
    }
}

impl Drop for PendingChatVm {
    fn drop(&mut self) {
        let Some(acquired) = self.acquired.take() else {
            return;
        };
        let provider = Arc::clone(&self.provider);
        let message_id = self.message_id;
        let Ok(runtime) = tokio::runtime::Handle::try_current() else {
            tracing::error!(
                message_id,
                "cannot schedule cancellation cleanup for a pending chat VM"
            );
            drop(acquired);
            return;
        };
        runtime.spawn(async move {
            cleanup_chat_vm(
                provider,
                acquired,
                message_id,
                "cancelled chat VM bootstrap",
            )
            .await;
        });
    }
}

async fn cleanup_chat_vm(
    provider: Arc<DirectE2bProvider>,
    acquired: AcquiredAgentVmSession,
    message_id: i64,
    operation: &'static str,
) {
    let sandbox_id = acquired.session.sandbox.sandbox_id.clone();
    if let Err(reset_error) = provider.reset_network(&sandbox_id).await {
        tracing::warn!(
            message_id,
            sandbox_id = ?sandbox_id,
            error = %reset_error,
            operation,
            "failed to restore deny-by-default policy for chat VM"
        );
        if let Err(kill_error) = provider.kill_sandbox(&sandbox_id).await {
            tracing::error!(
                message_id,
                sandbox_id = ?sandbox_id,
                error = %kill_error,
                operation,
                "failed to destroy chat VM after network cleanup failure"
            );
        }
    }
    if let Err(release_error) = acquired.release().await {
        tracing::error!(
            message_id,
            sandbox_id = ?sandbox_id,
            error = %release_error,
            operation,
            "failed to release chat VM namespace after cleanup"
        );
    }
}

impl ToolExecutor for ChatToolExecutor {
    fn execute(&self, call: ToolCall, events: Arc<dyn AgentEventSink>) -> BoxToolFuture<'_> {
        Box::pin(async move {
            match self.execute_inner(call, events).await {
                Err(AgentRuntimeError::Tool(message)) => Ok(failure(message)),
                result => result,
            }
        })
    }
}

#[derive(Debug, Clone, Copy)]
enum SearchKind {
    Knowledge,
    Content,
    Subscription,
}

#[derive(Debug, Clone, Copy)]
enum Mutation {
    Save,
    Remove,
    Read,
    Unread,
}

impl Mutation {
    const fn as_str(self) -> &'static str {
        match self {
            Self::Save => "save_to_knowledge",
            Self::Remove => "remove_from_knowledge",
            Self::Read => "mark_content_read",
            Self::Unread => "mark_content_unread",
        }
    }
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct ExecuteBashInput {
    command: String,
    timeout_seconds: Option<u64>,
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
#[expect(
    dead_code,
    reason = "fields define the E2B tool schema; execution is delegated to the shared VM executor"
)]
struct WriteFileInput {
    path: String,
    text: String,
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
#[expect(
    dead_code,
    reason = "fields define the E2B tool schema; execution is delegated to the shared VM executor"
)]
struct EditFileInput {
    path: String,
    old_text: String,
    new_text: String,
    #[serde(default)]
    replace_all: bool,
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
#[expect(
    dead_code,
    reason = "fields define the E2B tool schema; execution is delegated to the shared VM executor"
)]
struct ReadFileInput {
    path: String,
    max_bytes: Option<usize>,
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
#[expect(
    dead_code,
    reason = "fields define the E2B tool schema; execution is delegated to the shared VM executor"
)]
struct ListFilesInput {
    path: Option<String>,
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct WebSearchInput {
    query: String,
    limit: Option<usize>,
    num_results: Option<usize>,
    category: Option<String>,
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct SearchInput {
    query: String,
    limit: Option<usize>,
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct UnreadInput {
    limit: Option<usize>,
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct ContentUrlInput {
    url: String,
    title: Option<String>,
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct SubscribeInput {
    url: String,
    title: Option<String>,
    feed_type: Option<String>,
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct ContentIdInput {
    content_id: i64,
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct DeepResearchInput {
    question: String,
}

#[derive(Debug, Serialize)]
struct AssistantFeedOptionsResult {
    query: String,
    options: Vec<AssistantFeedOption>,
}

#[derive(Debug, Serialize)]
struct ChatRenderMetadata {
    feed_options: Vec<AssistantFeedOption>,
}

fn assistant_feed_option(
    suggestion: newsly_db::NewOnboardingSuggestion,
) -> Option<AssistantFeedOption> {
    let feed_url = suggestion
        .feed_url
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty() && value.chars().count() <= 2_048)?;
    let site_url = suggestion
        .site_url
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty() && value.chars().count() <= 2_048)
        .unwrap_or_else(|| feed_url.clone());
    let feed_type = match suggestion.suggestion_type.as_str() {
        "atom" => FeedType::Atom,
        "substack" => FeedType::Substack,
        "podcast_rss" => FeedType::PodcastRss,
        _ => return None,
    };
    let title = suggestion
        .title
        .as_deref()
        .and_then(|value| bounded_text(value, 300))
        .or_else(|| feed_host_label(&site_url))
        .unwrap_or_else(|| feed_url.clone());
    let rationale = suggestion
        .rationale
        .as_deref()
        .and_then(|value| bounded_text(value, 600));
    let digest = Sha1::digest(feed_url.as_bytes());
    let id = format!("{digest:x}").chars().take(16).collect();
    let feed_format =
        if feed_type == FeedType::Atom || feed_url.to_ascii_lowercase().contains("atom") {
            FeedFormat::Atom
        } else {
            FeedFormat::Rss
        };
    Some(AssistantFeedOption {
        id,
        title,
        site_url: site_url.clone(),
        feed_url,
        feed_type,
        feed_format,
        description: None,
        rationale,
        evidence_url: Some(site_url),
        is_subscribed: false,
    })
}

fn bounded_text(value: &str, maximum: usize) -> Option<String> {
    let value = value.trim();
    if value.is_empty() {
        return None;
    }
    let count = value.chars().count();
    if count <= maximum {
        Some(value.to_owned())
    } else if maximum > 3 {
        Some(format!(
            "{}...",
            value
                .chars()
                .take(maximum - 3)
                .collect::<String>()
                .trim_end()
        ))
    } else {
        Some(value.chars().take(maximum).collect())
    }
}

fn feed_host_label(value: &str) -> Option<String> {
    reqwest::Url::parse(value)
        .ok()?
        .host_str()
        .map(|host| host.trim_start_matches("www.").to_owned())
        .filter(|host| !host.is_empty())
}

fn looks_like_podcast_query(value: &str) -> bool {
    let value = value.to_ascii_lowercase();
    [
        "podcast", "podcasts", "episode", "episodes", "show", "shows",
    ]
    .into_iter()
    .any(|hint| value.contains(hint))
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
