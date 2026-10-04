use std::env;
use std::sync::Arc;
use std::time::Duration;

use anyhow::{Context, Result, anyhow};
use newsly_db::Database;
use newsly_providers::OpenAiBackgroundGateway;
use newsly_queue::{QueueKernel, TaskType};
use newsly_worker::HandlerRegistry;
use newsly_worker::chat_turn::{ChatAgentRuntime, ChatPartitionHandler, ChatTaskServices};
use newsly_worker::process::{
    QueueWorkerRunConfig, initialize_observability, notification_database_url, run_queue_worker,
};
use newsly_worker::queue_process_config::QueueWorkerProcessConfig;
use secrecy::SecretString;

#[tokio::main]
pub(crate) async fn main() -> Result<()> {
    let process = QueueWorkerProcessConfig::from_env("newsly-chat-worker", "rust-chat")
        .context("invalid Newsly Rust chat-worker process configuration")?;
    initialize_observability(&process.log_filter, process.log_format)
        .context("chat-worker observability initialization failed")?;

    let database = Database::connect_lazy(&process.database)
        .context("chat-worker database configuration failed")?;
    database
        .check()
        .await
        .context("chat-worker PostgreSQL readiness check failed")?;
    let queue = QueueKernel::new(database.pool().clone());
    let agent = Arc::new(
        ChatAgentRuntime::from_env(database.pool().clone(), queue.clone())
            .context("chat agent initialization failed")?,
    );
    let openai_api_key = required_secret("OPENAI_API_KEY")?;
    let deep_research = Arc::new(
        OpenAiBackgroundGateway::new(
            &openai_api_key,
            optional_env("OPENAI_API_BASE_URL").as_deref(),
            Duration::from_secs(parse_positive_u64(
                "OPENAI_BACKGROUND_TIMEOUT_SECONDS",
                600,
            )?),
        )
        .context("deep-research provider initialization failed")?,
    );
    let services = Arc::new(ChatTaskServices::new(
        database.pool().clone(),
        agent,
        deep_research,
        process.max_retries,
    ));

    let mut handlers = HandlerRegistry::new();
    handlers.register(ChatPartitionHandler::chat_turn(Arc::clone(&services)))?;
    handlers.register(ChatPartitionHandler::dig_deeper(services))?;
    run_queue_worker(
        database,
        queue,
        handlers,
        notification_database_url(process.database_url()),
        QueueWorkerRunConfig {
            process_name: "Newsly Rust chat worker",
            worker_id: &process.worker_id,
            lease_duration: process.lease_duration,
            max_retries: process.max_retries,
            task_types: &[TaskType::ChatTurn, TaskType::DigDeeper],
            task_type_filter: None,
        },
    )
    .await
}

fn required_secret(name: &'static str) -> Result<SecretString> {
    optional_env(name)
        .map(SecretString::from)
        .ok_or_else(|| anyhow!("{name} is required"))
}

fn optional_env(name: &'static str) -> Option<String> {
    env::var(name)
        .ok()
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty())
}

fn parse_positive_u64(name: &'static str, default: u64) -> Result<u64> {
    let value = env::var(name).map_or(Ok(default), |value| {
        value
            .parse::<u64>()
            .with_context(|| format!("{name} must be an integer"))
    })?;
    if value == 0 {
        return Err(anyhow!("{name} must be positive"));
    }
    Ok(value)
}
