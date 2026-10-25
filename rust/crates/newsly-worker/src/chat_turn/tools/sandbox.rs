use super::{
    AcquiredTaskSandbox, AgentEventSink, AgentRuntimeError, Arc, ChatSandboxState,
    ChatToolExecutor, Duration, OutputLimits, SandboxCommand, TaskToolExecutor, ToolCall,
    ToolExecutor, ToolOutput, agent_network_policy, run_sandbox_command,
};
impl ChatToolExecutor {
    pub(super) async fn execute_sandbox(
        &self,
        call: ToolCall,
        events: Arc<dyn AgentEventSink>,
    ) -> Result<ToolOutput, AgentRuntimeError> {
        let mut state = self.sandbox.lock().await;
        if state.is_none() {
            *state = Some(self.acquire_sandbox().await?);
        }
        let state = state.as_ref().expect("chat sandbox state was initialized");
        state.executor.execute(call, events).await
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
        let sandbox = acquired.sandbox.clone();
        if let Err(error) = self.prepare_sandbox(&sandbox).await {
            cleanup_chat_sandbox(acquired, self.snapshot.message_id, "chat sandbox bootstrap")
                .await;
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
                cleanup_chat_sandbox(acquired, self.snapshot.message_id, "chat sandbox bootstrap")
                    .await;
                return Err(AgentRuntimeError::Tool(error.to_string()));
            }
        };
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
                &agent_network_policy(None, false, "chat").await,
            )
            .await
            .map_err(|error| AgentRuntimeError::Tool(error.to_string()))?;
        let result = run_sandbox_command(
            &self.dependencies.provider,
            sandbox,
            SandboxCommand {
                command: "/bin/mkdir".to_owned(),
                args: vec!["-p".to_owned(), self.snapshot.workspace_path.clone()],
                cwd: None,
                deadline: self.dependencies.deadline,
                idle_timeout: Duration::from_secs(60),
                output_limits: OutputLimits::default(),
            },
            self.dependencies.cancellation.child_token(),
        )
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
