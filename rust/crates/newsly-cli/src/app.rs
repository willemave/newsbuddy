use std::ffi::OsString;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::str::FromStr as _;
use std::time::Duration;

use clap::{CommandFactory as _, Parser as _};
use clap_complete::aot::{Shell, generate};
use newsly_contracts::{
    AgentLibraryFileResponse, AgentLibraryManifestResponse, AgentOnboardingCompleteRequest,
    AgentOnboardingStartRequest, AgentSearchRequest, BulkMarkReadRequest, ContentType,
    OnboardingSelectedAggregator, SubmitContentRequest, SubscribeToFeedRequest,
};
use qrcode::QrCode;
use qrcode::render::unicode::Dense1x2;
use reqwest::Url;
use serde_json::{Value, json};

use crate::args::{
    AuthCommand, Cli, Command, CompletionShell, ConfigCommand, ConfigSetCommand, ContentCommand,
    ContentSubmissionsCommand, JobsCommand, LibraryCommand, NewsCommand, OnboardingCommand,
    OptionalWaitArgs, SourcesCommand, SubmitArgs, WaitArgs,
};
use crate::client::{ApiError, Client, QueryParameters};
use crate::config::{self, FileConfig, RuntimeConfig};
use crate::library::{LibrarySyncError, sync_library};
use crate::output::{Envelope, EnvelopeError, OutputFormat, emit};
use crate::wait::{WaitOptions, job_failed_or_skipped};

#[derive(Debug)]
struct CommandResult {
    command: &'static str,
    data: Option<Value>,
    job: Option<Value>,
}

#[derive(Debug)]
struct RootOptions {
    config: Option<PathBuf>,
    server: Option<String>,
    api_key: Option<String>,
    timeout: Duration,
}

impl CommandResult {
    fn data(command: &'static str, data: Value) -> Self {
        Self {
            command,
            data: Some(data),
            job: None,
        }
    }
}

#[derive(Debug)]
enum CommandError {
    Api(ApiError),
    Local(String),
}

impl CommandError {
    fn local(error: impl std::fmt::Display) -> Self {
        Self::Local(error.to_string())
    }

    fn envelope(self) -> EnvelopeError {
        match self {
            Self::Api(error) => EnvelopeError {
                message: error.message,
                status_code: error.status_code,
                code: error.code,
                details: error.details.map(|details| *details),
                retryable: error.retryable,
                request_id: error.request_id,
            },
            Self::Local(message) => EnvelopeError::message(message),
        }
    }
}

impl From<ApiError> for CommandError {
    fn from(error: ApiError) -> Self {
        Self::Api(error)
    }
}

/// Parse and execute one `newsbuddy` invocation.
pub async fn run<I, T>(
    arguments: I,
    stdout: &mut impl Write,
    stderr: &mut impl Write,
    version: &str,
) -> u8
where
    I: IntoIterator<Item = T>,
    T: Into<OsString> + Clone,
{
    let cli = match Cli::try_parse_from(arguments) {
        Ok(cli) => cli,
        Err(error) => {
            let _ = write!(stderr, "{error}");
            return 1;
        }
    };
    let output_format = if cli.json {
        OutputFormat::Json
    } else {
        cli.output
    };

    if let Command::Completion { shell } = cli.command {
        write_completion(shell, stdout);
        return 0;
    }

    let command_name = command_name(&cli.command);
    let config_path = config::resolve_path(&path_override(cli.config.as_ref()));
    match execute(cli, stderr, version).await {
        Ok(result) => {
            let envelope = Envelope::success(result.command, result.data, result.job);
            if let Err(error) = emit(stdout, &envelope, output_format) {
                let _ = writeln!(stderr, "{error}");
                return 1;
            }
            0
        }
        Err(error) => {
            let envelope = Envelope::failure(
                command_name,
                error.envelope(),
                Some(config_path.to_string_lossy().into_owned()),
            );
            if let Err(error) = emit(stdout, &envelope, output_format) {
                let _ = writeln!(stderr, "{error}");
            }
            1
        }
    }
}

async fn execute(
    cli: Cli,
    stderr: &mut impl Write,
    version: &str,
) -> Result<CommandResult, CommandError> {
    let root = RootOptions {
        config: cli.config,
        server: cli.server,
        api_key: cli.api_key,
        timeout: cli.timeout,
    };
    match cli.command {
        Command::Config(arguments) => execute_config(&root, arguments.command),
        Command::Auth(arguments) => execute_auth(&root, arguments.command, stderr, version).await,
        Command::Jobs(arguments) => execute_jobs(&root, arguments.command, version).await,
        Command::Content(arguments) => execute_content(&root, arguments.command, version).await,
        Command::Library(arguments) => execute_library(&root, arguments.command, version).await,
        Command::Search(arguments) => {
            let client = authenticated_client(&root, version)?;
            let response = client
                .search_agent(&AgentSearchRequest {
                    query: arguments.query,
                    limit: arguments.limit,
                    include_podcasts: arguments.include_podcasts,
                })
                .await?;
            Ok(CommandResult::data("search", response))
        }
        Command::Sources(arguments) => execute_sources(&root, arguments.command, version).await,
        Command::Onboarding(arguments) => {
            execute_onboarding(&root, arguments.command, version).await
        }
        Command::News(arguments) => execute_news(&root, arguments.command, version).await,
        Command::Version => Ok(CommandResult::data(
            "version",
            json!({ "version": version }),
        )),
        Command::Completion { .. } => unreachable!("completion is handled before dispatch"),
    }
}

fn execute_config(
    cli: &RootOptions,
    command: ConfigCommand,
) -> Result<CommandResult, CommandError> {
    let path = config::resolve_path(&path_override(cli.config.as_ref()));
    match command {
        ConfigCommand::Set(arguments) => match arguments.command {
            ConfigSetCommand::Server { url } => {
                let updated = update_config(&path, |mut current| {
                    current.server_url = url;
                    current
                })?;
                Ok(CommandResult::data(
                    "config.set-server",
                    json!({
                        "config_path": path,
                        "server_url": updated.server_url,
                    }),
                ))
            }
            ConfigSetCommand::ApiKey { key } => {
                let updated = update_config(&path, |mut current| {
                    current.api_key = key;
                    current
                })?;
                Ok(CommandResult::data(
                    "config.set-api-key",
                    json!({
                        "config_path": path,
                        "api_key_set": !updated.api_key.is_empty(),
                    }),
                ))
            }
            ConfigSetCommand::LibraryRoot { path: library_root } => {
                let updated = update_config(&path, |mut current| {
                    current.library_root = library_root.to_string_lossy().into_owned();
                    current
                })?;
                Ok(CommandResult::data(
                    "config.set-library-root",
                    json!({
                        "config_path": path,
                        "library_root": updated.library_root,
                    }),
                ))
            }
        },
        ConfigCommand::Show => {
            let runtime = resolve_runtime(cli)?;
            Ok(CommandResult::data(
                "config.show",
                json!({
                    "config_path": runtime.path,
                    "server_url": runtime.server_url,
                    "api_key_set": !runtime.api_key.is_empty(),
                    "api_key_mask": config::masked_api_key(&runtime.api_key),
                    "library_root": runtime.library_root,
                }),
            ))
        }
    }
}

async fn execute_auth(
    cli: &RootOptions,
    command: AuthCommand,
    stderr: &mut impl Write,
    version: &str,
) -> Result<CommandResult, CommandError> {
    match command {
        AuthCommand::Login {
            device_name,
            poll_interval,
            poll_timeout,
        } => {
            if poll_interval.is_zero() {
                return Err(CommandError::Local(
                    "poll-interval must be greater than zero".to_owned(),
                ));
            }
            let runtime = resolve_runtime(cli)?;
            runtime
                .validate_server_only()
                .map_err(CommandError::local)?;
            let client = Client::new(&runtime, cli.timeout, version)?;
            let device_name = device_name.unwrap_or_else(default_device_name);
            let started = client.start_cli_link(Some(&device_name)).await?;
            let session_id = required_string(&started, "session_id", "CLI link start")?;
            let poll_token = required_string(&started, "poll_token", "CLI link start")?;
            let approve_url = required_string(&started, "approve_url", "CLI link start")?;
            render_cli_link(stderr, approve_url);

            let linked = client
                .wait_for_cli_link(
                    session_id,
                    poll_token,
                    WaitOptions {
                        interval: poll_interval,
                        timeout: poll_timeout,
                    },
                )
                .await?;
            let api_key = required_string(&linked, "api_key", "CLI link poll")?.to_owned();
            if api_key.is_empty() {
                return Err(CommandError::Local(
                    "CLI link completed without an API key".to_owned(),
                ));
            }
            let key_prefix = linked.get("key_prefix").cloned().unwrap_or(Value::Null);
            let saved = update_config(&runtime.path, |mut current| {
                current.server_url.clone_from(&runtime.server_url);
                current.api_key = api_key;
                if current.library_root.is_empty() {
                    current.library_root = runtime.library_root.to_string_lossy().into_owned();
                }
                current
            })?;
            Ok(CommandResult::data(
                "auth.login",
                json!({
                    "config_path": runtime.path,
                    "server_url": saved.server_url,
                    "api_key_set": !saved.api_key.is_empty(),
                    "key_prefix": key_prefix,
                    "library_root": saved.library_root,
                }),
            ))
        }
    }
}

async fn execute_jobs(
    cli: &RootOptions,
    command: JobsCommand,
    version: &str,
) -> Result<CommandResult, CommandError> {
    let client = authenticated_client(cli, version)?;
    match command {
        JobsCommand::Get { job_id } => Ok(CommandResult::data(
            "jobs.get",
            client.get_job(job_id).await?,
        )),
        JobsCommand::Wait { job_id, wait } => {
            let response = client.wait_for_job(job_id, wait_options(wait)).await?;
            Ok(CommandResult::data("jobs.wait", response))
        }
    }
}

async fn execute_content(
    cli: &RootOptions,
    command: ContentCommand,
    version: &str,
) -> Result<CommandResult, CommandError> {
    let client = authenticated_client(cli, version)?;
    match command {
        ContentCommand::List {
            limit,
            cursor,
            content_type,
            date,
            read_filter,
        } => {
            let mut query = Vec::with_capacity(content_type.len() + 4);
            query.extend(
                content_type
                    .into_iter()
                    .map(|value| ("content_type".to_owned(), value)),
            );
            push_query(&mut query, "date", date);
            push_query(&mut query, "read_filter", Some(read_filter));
            push_query(&mut query, "cursor", cursor);
            query.push(("limit".to_owned(), limit.to_string()));
            Ok(CommandResult::data(
                "content.list",
                client.list_content(&query).await?,
            ))
        }
        ContentCommand::Get { content_id } => Ok(CommandResult::data(
            "content.get",
            client.get_content(content_id).await?,
        )),
        ContentCommand::Submit(arguments) => {
            execute_submit(&client, arguments, false, "content.submit").await
        }
        ContentCommand::Summarize(arguments) => {
            execute_submit(&client, arguments, true, "content.summarize").await
        }
        ContentCommand::Submissions(arguments) => match arguments.command {
            ContentSubmissionsCommand::List { limit, cursor } => {
                let mut query = vec![("limit".to_owned(), limit.to_string())];
                push_query(&mut query, "cursor", cursor);
                Ok(CommandResult::data(
                    "content.submissions.list",
                    client.list_content_submission_statuses(&query).await?,
                ))
            }
        },
    }
}

async fn execute_submit(
    client: &Client,
    arguments: SubmitArgs,
    summarize: bool,
    command: &'static str,
) -> Result<CommandResult, CommandError> {
    let url = parse_http_url(&arguments.url)?;
    let wait = optional_wait_options(arguments.wait)?;
    let content_type = arguments
        .content_type
        .as_deref()
        .map(ContentType::from_str)
        .transpose()
        .map_err(CommandError::local)?;
    let request = SubmitContentRequest {
        url: url.to_string(),
        content_type,
        title: arguments.title,
        platform: arguments.platform,
        instruction: arguments.note,
        crawl_links: arguments.crawl_links,
        subscribe_to_feed: false,
        share_and_chat: false,
        chat_initial_message: None,
        save_to_knowledge_and_mark_read: summarize,
    };
    let response = client.submit_content(&request).await?;
    let mut result = CommandResult::data(command, response.clone());
    if let Some(wait) = wait {
        if let Some(task_id) = optional_i64(&response, "task_id", "content submission")? {
            let job = client.wait_for_job(task_id, wait).await?;
            if job_failed_or_skipped(&job) {
                return Err(CommandError::Api(ApiError::local_with_details(
                    "submission job did not complete successfully",
                    job,
                )));
            }
            result.job = Some(job);
        }
        let content_id = required_i64(&response, "content_id", "content submission")?;
        let _ = client.wait_for_submitted_content(content_id, wait).await?;
    }
    Ok(result)
}

async fn execute_library(
    cli: &RootOptions,
    command: LibraryCommand,
    version: &str,
) -> Result<CommandResult, CommandError> {
    match command {
        LibraryCommand::Sync {
            dir,
            include_source,
            allow_prune_all,
        } => {
            let runtime = resolve_runtime(cli)?;
            runtime.validate_remote().map_err(CommandError::local)?;
            let root = dir.unwrap_or_else(|| runtime.library_root.clone());
            let client = Client::new(&runtime, cli.timeout, version)?;
            let manifest_value = client.get_library_manifest(include_source).await?;
            let manifest: AgentLibraryManifestResponse = serde_json::from_value(manifest_value)
                .map_err(|error| {
                    CommandError::Local(format!("invalid library manifest response: {error}"))
                })?;
            let receipt = sync_library(&root, &manifest, allow_prune_all, |relative_path| {
                let client = &client;
                async move {
                    let value = client.get_library_file(&relative_path).await?;
                    serde_json::from_value::<AgentLibraryFileResponse>(value).map_err(|error| {
                        ApiError::local(format!("invalid library file response: {error}"))
                    })
                }
            })
            .await
            .map_err(library_error)?;
            Ok(CommandResult::data(
                "library.sync",
                serde_json::to_value(receipt).map_err(CommandError::local)?,
            ))
        }
    }
}

async fn execute_sources(
    cli: &RootOptions,
    command: SourcesCommand,
    version: &str,
) -> Result<CommandResult, CommandError> {
    let client = authenticated_client(cli, version)?;
    match command {
        SourcesCommand::List { source_type } => {
            let mut query = Vec::new();
            push_query(&mut query, "type", source_type);
            Ok(CommandResult::data(
                "sources.list",
                client.list_sources(&query).await?,
            ))
        }
        SourcesCommand::Add {
            feed_url,
            feed_type,
            display_name,
        } => {
            const SUPPORTED: [&str; 3] = ["atom", "substack", "podcast_rss"];
            if !SUPPORTED.contains(&feed_type.as_str()) {
                return Err(CommandError::Local(format!(
                    "unsupported feed type {feed_type:?}; expected one of: {}",
                    SUPPORTED.join(", ")
                )));
            }
            let request = SubscribeToFeedRequest {
                feed_url: parse_http_url(&feed_url)?.to_string(),
                feed_type,
                display_name,
            };
            Ok(CommandResult::data(
                "sources.add",
                client.subscribe_source(&request).await?,
            ))
        }
    }
}

async fn execute_onboarding(
    cli: &RootOptions,
    command: OnboardingCommand,
    version: &str,
) -> Result<CommandResult, CommandError> {
    let client = authenticated_client(cli, version)?;
    match command {
        OnboardingCommand::Start {
            brief,
            seed_url,
            seed_feed,
            wait,
        } => {
            let wait = optional_wait_options(wait)?;
            let response = client
                .start_onboarding(&AgentOnboardingStartRequest {
                    brief,
                    preferences: None,
                    seed_urls: seed_url,
                    seed_feeds: seed_feed,
                })
                .await?;
            let mut result = CommandResult::data("onboarding.start", response.clone());
            if let Some(wait) = wait {
                let run_id = required_i64(&response, "run_id", "onboarding start")?;
                result.job = Some(client.wait_for_onboarding(run_id, wait).await?);
            }
            Ok(result)
        }
        OnboardingCommand::Status { run_id } => Ok(CommandResult::data(
            "onboarding.status",
            client.get_onboarding(run_id).await?,
        )),
        OnboardingCommand::Complete {
            run_id,
            accept_all,
            suggestion_id,
            aggregator,
        } => {
            let selected_aggregators = aggregator
                .into_iter()
                .map(|key| OnboardingSelectedAggregator {
                    key,
                    title: None,
                    topics: Vec::new(),
                })
                .collect();
            Ok(CommandResult::data(
                "onboarding.complete",
                client
                    .complete_onboarding(
                        run_id,
                        &AgentOnboardingCompleteRequest {
                            accept_all,
                            selected_suggestion_ids: suggestion_id,
                            selected_aggregators,
                        },
                    )
                    .await?,
            ))
        }
    }
}

async fn execute_news(
    cli: &RootOptions,
    command: NewsCommand,
    version: &str,
) -> Result<CommandResult, CommandError> {
    let client = authenticated_client(cli, version)?;
    match command {
        NewsCommand::List {
            limit,
            cursor,
            read_filter,
        } => {
            let mut query = vec![
                ("limit".to_owned(), limit.to_string()),
                ("read_filter".to_owned(), read_filter),
            ];
            push_query(&mut query, "cursor", cursor);
            Ok(CommandResult::data(
                "news.list",
                client.list_news_items(&query).await?,
            ))
        }
        NewsCommand::Get { news_item_id } => Ok(CommandResult::data(
            "news.get",
            client.get_news_item(news_item_id).await?,
        )),
        NewsCommand::Convert { news_item_id } => Ok(CommandResult::data(
            "news.convert",
            client.convert_news_item_to_article(news_item_id).await?,
        )),
        NewsCommand::MarkRead { news_item_id } => Ok(CommandResult::data(
            "news.mark-read",
            client
                .mark_news_items_read(&BulkMarkReadRequest {
                    content_ids: news_item_id,
                })
                .await?,
        )),
    }
}

fn update_config(
    path: &Path,
    update: impl FnOnce(FileConfig) -> FileConfig,
) -> Result<FileConfig, CommandError> {
    config::update(path, update).map_err(CommandError::local)
}

fn resolve_runtime(cli: &RootOptions) -> Result<RuntimeConfig, CommandError> {
    config::resolve_runtime(
        &path_override(cli.config.as_ref()),
        cli.server.as_deref().unwrap_or_default(),
        cli.api_key.as_deref().unwrap_or_default(),
    )
    .map_err(CommandError::local)
}

fn authenticated_client(cli: &RootOptions, version: &str) -> Result<Client, CommandError> {
    let runtime = resolve_runtime(cli)?;
    runtime.validate_remote().map_err(CommandError::local)?;
    Client::new(&runtime, cli.timeout, version).map_err(Into::into)
}

fn path_override(path: Option<&PathBuf>) -> String {
    path.map(|path| path.to_string_lossy().into_owned())
        .unwrap_or_default()
}

fn wait_options(arguments: WaitArgs) -> WaitOptions {
    let _ = arguments.wait;
    WaitOptions {
        interval: arguments.wait_interval,
        timeout: arguments.wait_timeout,
    }
}

fn optional_wait_options(arguments: OptionalWaitArgs) -> Result<Option<WaitOptions>, CommandError> {
    if !arguments.wait {
        return Ok(None);
    }
    if arguments.wait_interval.is_zero() {
        return Err(CommandError::Local(
            "wait-interval must be greater than zero".to_owned(),
        ));
    }
    Ok(Some(WaitOptions {
        interval: arguments.wait_interval,
        timeout: arguments.wait_timeout,
    }))
}

fn parse_http_url(raw: &str) -> Result<Url, CommandError> {
    let url = Url::parse(raw).map_err(CommandError::local)?;
    if !matches!(url.scheme(), "http" | "https") {
        return Err(CommandError::Local("url must use http or https".to_owned()));
    }
    if url.host_str().is_none() {
        return Err(CommandError::Local("url must include a host".to_owned()));
    }
    Ok(url)
}

fn push_query(query: &mut QueryParameters, name: &str, value: Option<String>) {
    if let Some(value) = value {
        query.push((name.to_owned(), value));
    }
}

fn required_string<'a>(
    value: &'a Value,
    field: &str,
    response_name: &str,
) -> Result<&'a str, CommandError> {
    value.get(field).and_then(Value::as_str).ok_or_else(|| {
        CommandError::Local(format!(
            "invalid {response_name} response: missing string field {field:?}"
        ))
    })
}

fn required_i64(value: &Value, field: &str, response_name: &str) -> Result<i64, CommandError> {
    value.get(field).and_then(Value::as_i64).ok_or_else(|| {
        CommandError::Local(format!(
            "invalid {response_name} response: missing integer field {field:?}"
        ))
    })
}

fn optional_i64(
    value: &Value,
    field: &str,
    response_name: &str,
) -> Result<Option<i64>, CommandError> {
    match value.get(field) {
        None | Some(Value::Null) => Ok(None),
        Some(value) => value.as_i64().map(Some).ok_or_else(|| {
            CommandError::Local(format!(
                "invalid {response_name} response: field {field:?} is not an integer"
            ))
        }),
    }
}

fn default_device_name() -> String {
    hostname::get()
        .ok()
        .and_then(|name| name.into_string().ok())
        .map(|name| name.trim().to_owned())
        .filter(|name| !name.is_empty())
        .unwrap_or_else(|| "Newsbuddy CLI".to_owned())
}

fn render_cli_link(writer: &mut impl Write, approve_url: &str) {
    let _ = writeln!(
        writer,
        "Scan this QR code in the Newsbuddy app to approve CLI access:"
    );
    if let Ok(code) = QrCode::new(approve_url.as_bytes()) {
        let rendered = code.render::<Dense1x2>().build();
        let _ = writeln!(writer, "{rendered}");
    }
    let _ = writeln!(writer, "\nApproval link:\n{approve_url}\n");
}

fn library_error(error: LibrarySyncError<ApiError>) -> CommandError {
    match error {
        LibrarySyncError::Remote(error) => CommandError::Api(error),
        error => CommandError::Local(error.to_string()),
    }
}

fn write_completion(shell: CompletionShell, writer: &mut impl Write) {
    let shell = match shell {
        CompletionShell::Bash => Shell::Bash,
        CompletionShell::Zsh => Shell::Zsh,
        CompletionShell::Fish => Shell::Fish,
        CompletionShell::Powershell => Shell::PowerShell,
    };
    let mut command = Cli::command();
    generate(shell, &mut command, "newsbuddy", writer);
}

fn command_name(command: &Command) -> &'static str {
    match command {
        Command::Config(arguments) => match &arguments.command {
            ConfigCommand::Set(arguments) => match arguments.command {
                ConfigSetCommand::Server { .. } => "config.set-server",
                ConfigSetCommand::ApiKey { .. } => "config.set-api-key",
                ConfigSetCommand::LibraryRoot { .. } => "config.set-library-root",
            },
            ConfigCommand::Show => "config.show",
        },
        Command::Auth(_) => "auth.login",
        Command::Jobs(arguments) => match arguments.command {
            JobsCommand::Get { .. } => "jobs.get",
            JobsCommand::Wait { .. } => "jobs.wait",
        },
        Command::Content(arguments) => match &arguments.command {
            ContentCommand::List { .. } => "content.list",
            ContentCommand::Get { .. } => "content.get",
            ContentCommand::Submit(_) => "content.submit",
            ContentCommand::Summarize(_) => "content.summarize",
            ContentCommand::Submissions(_) => "content.submissions.list",
        },
        Command::Library(_) => "library.sync",
        Command::Search(_) => "search",
        Command::Sources(arguments) => match arguments.command {
            SourcesCommand::List { .. } => "sources.list",
            SourcesCommand::Add { .. } => "sources.add",
        },
        Command::Onboarding(arguments) => match arguments.command {
            OnboardingCommand::Start { .. } => "onboarding.start",
            OnboardingCommand::Status { .. } => "onboarding.status",
            OnboardingCommand::Complete { .. } => "onboarding.complete",
        },
        Command::News(arguments) => match arguments.command {
            NewsCommand::List { .. } => "news.list",
            NewsCommand::Get { .. } => "news.get",
            NewsCommand::Convert { .. } => "news.convert",
            NewsCommand::MarkRead { .. } => "news.mark-read",
        },
        Command::Completion { .. } => "completion",
        Command::Version => "version",
    }
}

#[cfg(test)]
mod tests;
