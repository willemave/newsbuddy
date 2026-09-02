use std::collections::BTreeMap;
use std::sync::{Arc, Mutex as StdMutex};
use std::time::Duration;

use futures_util::StreamExt;
use newsly_agent_runtime::{
    AgentEventSink, AgentRuntimeError, BoxToolFuture, ToolCall, ToolDefinition, ToolExecutor,
    ToolOutput,
};
use newsly_contracts::{AssistantFeedOption, FeedType};
use newsly_db::{
    ChatTaskSnapshot, create_deep_research_handoff, list_unread_chat_news, mark_content_read,
    mark_content_unread, prepare_chat_article_conversion, remove_content_from_knowledge,
    save_content_to_knowledge, search_agent_knowledge, search_chat_content, search_chat_news,
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
use sqlx::PgPool;
use tokio::sync::Mutex;
use tokio::time::Instant;
use tokio_util::sync::CancellationToken;

use crate::content_body_store::ContentBodyStore;
use crate::knowledge_tools::{
    KnowledgeReferenceInput, ReadKnowledgeInput, read_authorized_knowledge_item,
};
use crate::onboarding_discovery::normalize_seeds;
use crate::share_actions::submission::{
    ShareSubmissionPolicy, apply_validated_feed_action, submit_content_action,
};
use crate::share_actions::workflows::{
    ContentActionInput, FeedActionInput, validated_scraper_type,
};
use crate::task_sandbox::{AcquiredTaskSandbox, TaskSandboxOwner};
use crate::task_tools::{ExaSearchClient, TaskToolExecutor};

#[path = "tools/support.rs"]
mod support;
use support::{
    assistant_feed_option, bounded_query, chat_output_limits, clean, looks_like_podcast_query,
    tail_chars, valid_url,
};

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
    pub lifecycle: TaskSandboxOwner,
    pub exa: ExaSearchClient,
    pub onboarding: OnboardingGateway,
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
    render_metadata: StdMutex<Option<Value>>,
}

#[derive(Debug)]
struct ChatSandboxState {
    acquired: AcquiredTaskSandbox,
    executor: TaskToolExecutor,
}

struct PendingChatSandbox {
    acquired: Option<AcquiredTaskSandbox>,
    message_id: i64,
}

impl ChatToolExecutor {
    pub(super) fn new(dependencies: ChatToolDependencies, snapshot: ChatTaskSnapshot) -> Self {
        Self {
            dependencies,
            snapshot,
            sandbox: Mutex::new(None),
            render_metadata: StdMutex::new(None),
        }
    }

    pub(super) fn definitions() -> Vec<ToolDefinition> {
        vec![
            definition::<ExecuteBashInput>(
                "execute_bash",
                "Run one bounded bash command inside this turn's task sandbox.",
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
                "Read bounded UTF-8 text from the task-scoped chat workspace.",
            ),
            definition::<ListFilesInput>(
                "list_files",
                "List files below the task-scoped chat workspace.",
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
            definition::<ReadKnowledgeInput>(
                "read_knowledge_item",
                "Read one typed Knowledge reference through the host without starting a sandbox.",
            ),
            definition::<WriteKnowledgeInput>(
                "write_knowledge_items",
                "Copy selected Knowledge references into input/knowledge in this turn's task sandbox.",
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
        let state = self.sandbox.lock().await.take();
        let Some(state) = state else { return Ok(()) };
        state.acquired.release().await.map_err(|error| {
            AgentRuntimeError::Tool(format!("chat sandbox cleanup failed: {error}"))
        })
    }

    async fn execute_inner(
        &self,
        call: ToolCall,
        events: Arc<dyn AgentEventSink>,
    ) -> Result<ToolOutput, AgentRuntimeError> {
        match call.name.as_str() {
            "execute_bash"
            | "write_file"
            | "edit_file"
            | "read_file"
            | "list_files"
            | "write_knowledge_items" => self.execute_sandbox(call, events).await,
            "exa_web_search" | "search_web" => self.search_web(call).await,
            "find_feed_options" => self.find_feed_options(call).await,
            "search_knowledge" => self.search_content_kind(call, SearchKind::Knowledge).await,
            "read_knowledge_item" => self.read_knowledge_item(call).await,
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

    async fn execute_sandbox(
        &self,
        call: ToolCall,
        events: Arc<dyn AgentEventSink>,
    ) -> Result<ToolOutput, AgentRuntimeError> {
        let mut state = self.sandbox.lock().await;
        if state.is_none() {
            *state = Some(self.acquire_sandbox().await?);
        }
        let state = state.as_ref().expect("chat sandbox state was initialized");
        if call.name == "execute_bash" {
            self.execute_bash_streaming(state, &call, &events).await
        } else {
            state.executor.execute(call, events).await
        }
    }

    async fn execute_bash_streaming(
        &self,
        state: &ChatSandboxState,
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
                &state.acquired.sandbox,
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

    async fn acquire_sandbox(&self) -> Result<ChatSandboxState, AgentRuntimeError> {
        let task_id = self.snapshot.llm_task_id.ok_or_else(|| {
            AgentRuntimeError::Tool(
                "chat task has no LLM attempt available for a task sandbox".to_owned(),
            )
        })?;
        let acquired = self
            .dependencies
            .lifecycle
            .acquire_for_task(
                self.snapshot.user_id,
                task_id,
                "chat",
                self.dependencies.deadline,
                self.dependencies.cancellation.child_token(),
            )
            .await
            .map_err(|error| AgentRuntimeError::Tool(error.to_string()))?;
        let mut pending = PendingChatSandbox::new(acquired, self.snapshot.message_id);
        let sandbox = pending.sandbox().clone();
        if let Err(error) = self.prepare_sandbox(&sandbox).await {
            pending.cleanup().await;
            return Err(error);
        }
        let executor = match TaskToolExecutor::new(
            Arc::clone(&self.dependencies.provider),
            sandbox.clone(),
            &self.snapshot.workspace_path,
            self.dependencies.deadline,
            self.dependencies.cancellation.child_token(),
            self.dependencies.max_output_chars,
            self.dependencies.exa.clone(),
            self.dependencies.pool.clone(),
            self.snapshot.user_id,
            self.dependencies.body_store.clone(),
        ) {
            Ok(executor) => executor,
            Err(error) => {
                pending.cleanup().await;
                return Err(AgentRuntimeError::Tool(error.to_string()));
            }
        };
        let acquired = pending.disarm();
        Ok(ChatSandboxState { acquired, executor })
    }

    async fn prepare_sandbox(
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
                    args: vec!["-p".to_owned(), self.snapshot.workspace_path.clone()],
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
                "chat sandbox workspace bootstrap exited with {}: {}",
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
                search_agent_knowledge(&self.dependencies.pool, self.snapshot.user_id, query, limit)
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

    async fn read_knowledge_item(&self, call: ToolCall) -> Result<ToolOutput, AgentRuntimeError> {
        let input: ReadKnowledgeInput = arguments(&call)?;
        let output = read_authorized_knowledge_item(
            &self.dependencies.pool,
            self.snapshot.user_id,
            &self.dependencies.body_store,
            input,
        )
        .await
        .map_err(db_tool_error)?;
        Ok(success(
            serde_json::to_value(output).map_err(db_tool_error)?,
        ))
    }
}

impl PendingChatSandbox {
    fn new(acquired: AcquiredTaskSandbox, message_id: i64) -> Self {
        Self {
            acquired: Some(acquired),
            message_id,
        }
    }

    fn sandbox(&self) -> &newsly_e2b::SandboxHandle {
        &self
            .acquired
            .as_ref()
            .expect("pending chat sandbox must own its acquired session")
            .sandbox
    }

    fn disarm(mut self) -> AcquiredTaskSandbox {
        self.acquired
            .take()
            .expect("pending chat sandbox must own its acquired session")
    }

    async fn cleanup(&mut self) {
        let Some(acquired) = self.acquired.take() else {
            return;
        };
        cleanup_chat_sandbox(acquired, self.message_id, "chat sandbox bootstrap").await;
    }
}

impl Drop for PendingChatSandbox {
    fn drop(&mut self) {
        let Some(acquired) = self.acquired.take() else {
            return;
        };
        let message_id = self.message_id;
        let Ok(runtime) = tokio::runtime::Handle::try_current() else {
            tracing::error!(
                message_id,
                "cannot schedule cancellation cleanup for a pending chat sandbox"
            );
            drop(acquired);
            return;
        };
        runtime.spawn(async move {
            cleanup_chat_sandbox(acquired, message_id, "cancelled chat sandbox bootstrap").await;
        });
    }
}

async fn cleanup_chat_sandbox(
    acquired: AcquiredTaskSandbox,
    message_id: i64,
    operation: &'static str,
) {
    let sandbox_id = acquired.sandbox.sandbox_id.clone();
    if let Err(release_error) = acquired.release().await {
        tracing::error!(
            message_id,
            sandbox_id = ?sandbox_id,
            error = %release_error,
            operation,
            "failed to destroy chat sandbox after cleanup"
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
    reason = "fields define the E2B tool schema; execution is delegated to the shared sandbox executor"
)]
struct WriteFileInput {
    path: String,
    text: String,
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
#[expect(
    dead_code,
    reason = "fields define the E2B tool schema; execution is delegated to the shared sandbox executor"
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
    reason = "fields define the E2B tool schema; execution is delegated to the shared sandbox executor"
)]
struct ReadFileInput {
    path: String,
    max_bytes: Option<usize>,
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
#[expect(
    dead_code,
    reason = "fields define the E2B tool schema; execution is delegated to the shared sandbox executor"
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
#[expect(
    dead_code,
    reason = "fields define the E2B tool schema; execution is delegated to the shared task executor"
)]
struct WriteKnowledgeInput {
    references: Vec<KnowledgeReferenceInput>,
    directory: Option<String>,
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
