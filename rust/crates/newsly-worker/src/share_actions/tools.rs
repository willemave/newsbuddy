use std::collections::BTreeMap;
use std::sync::Arc;
use std::time::Duration;

use futures_util::{StreamExt, stream};
use newsly_agent_runtime::{
    AgentEvent, AgentEventSink, AgentRuntimeError, BoxToolFuture, ToolCall, ToolExecutor,
    ToolOutput,
};
use newsly_e2b::{
    BoxByteStream, CommandRequest, DirectE2bProvider, E2bError, ExecutionTag, OutputLimits,
    SandboxHandle, SandboxPath, SandboxProvider, SandboxUser, WorkspacePath,
};
use reqwest::Url;
use schemars::{JsonSchema, schema_for};
use secrecy::{ExposeSecret, SecretString};
use serde::{Deserialize, Serialize};
use serde_json::json;
use tokio::time::Instant;
use tokio_util::sync::CancellationToken;

const DEFAULT_FILE_LIMIT: usize = 100_000;
const MAX_FILE_LIMIT: usize = 1_000_000;
const MAX_WRITE_BYTES: usize = 1_000_000;
const MAX_SEARCH_QUERY_CHARS: usize = 2_000;
const MAX_SEARCH_RESULTS: usize = 8;
const EXCLUDED_SEARCH_DOMAINS: [&str; 8] = [
    "facebook.com",
    "linkedin.com",
    "twitter.com",
    "x.com",
    "instagram.com",
    "tiktok.com",
    "pinterest.com",
    "reddit.com",
];

#[derive(Debug, Clone)]
pub(crate) struct ShareActionToolExecutor {
    provider: Arc<DirectE2bProvider>,
    sandbox: SandboxHandle,
    workspace_root: SandboxPath,
    deadline: Instant,
    cancellation: CancellationToken,
    max_output_chars: usize,
    exa: ExaSearchClient,
    sandbox_user: SandboxUser,
}

impl ShareActionToolExecutor {
    pub(crate) fn new(
        provider: Arc<DirectE2bProvider>,
        sandbox: SandboxHandle,
        workspace_root: &str,
        deadline: Instant,
        cancellation: CancellationToken,
        max_output_chars: usize,
        exa: ExaSearchClient,
    ) -> Result<Self, E2bError> {
        if max_output_chars == 0 {
            return Err(E2bError::InvalidInput(
                "Share Action output limit must be positive".to_owned(),
            ));
        }
        Ok(Self {
            provider,
            sandbox,
            workspace_root: SandboxPath::parse(workspace_root.to_owned())?,
            deadline,
            cancellation,
            max_output_chars,
            exa,
            sandbox_user: SandboxUser::parse("user")?,
        })
    }

    pub(crate) fn definitions() -> Vec<newsly_agent_runtime::ToolDefinition> {
        vec![
            tool_definition::<ExecuteBashInput>(
                "execute_bash",
                "Run one bounded bash command inside the task-specific VM workspace.",
            ),
            tool_definition::<WriteFileInput>(
                "write_file",
                "Write UTF-8 text to a workspace-relative file.",
            ),
            tool_definition::<EditFileInput>(
                "edit_file",
                "Replace exact text in a workspace-relative UTF-8 file, optionally replacing every occurrence.",
            ),
            tool_definition::<ReadFileInput>(
                "read_file",
                "Read bounded UTF-8 text from a workspace-relative file or the read-only /data corpus.",
            ),
            tool_definition::<ListFilesInput>(
                "list_files",
                "List files recursively below a workspace-relative directory or the read-only /data corpus.",
            ),
            tool_definition::<WebSearchInput>(
                "web_search",
                "Search the public web and return bounded title, URL, and snippet records.",
            ),
        ]
    }

    async fn execute_tool(
        &self,
        call: &ToolCall,
        events: &Arc<dyn AgentEventSink>,
    ) -> Result<ToolOutput, AgentRuntimeError> {
        match self.execute_tool_inner(call, events).await {
            Err(AgentRuntimeError::Tool(message)) => Ok(tool_failure(message)),
            result => result,
        }
    }

    async fn execute_tool_inner(
        &self,
        call: &ToolCall,
        events: &Arc<dyn AgentEventSink>,
    ) -> Result<ToolOutput, AgentRuntimeError> {
        match call.name.as_str() {
            "execute_bash" => {
                let input = parse_arguments::<ExecuteBashInput>(call)?;
                let timeout =
                    Duration::from_secs(input.timeout_seconds.unwrap_or(120).clamp(1, 300));
                let deadline = self.bounded_deadline(timeout)?;
                let request = CommandRequest {
                    command: "/bin/bash".to_owned(),
                    args: vec!["-lc".to_owned(), input.command],
                    env: BTreeMap::new(),
                    cwd: Some(self.workspace_root.as_str().to_owned()),
                    username: Some(self.sandbox_user.clone()),
                    tag: ExecutionTag::new(),
                    stdin_enabled: false,
                    absolute_deadline: deadline,
                    idle_timeout: timeout.min(Duration::from_secs(60)),
                    output_limits: output_limits(self.max_output_chars),
                };
                let stream = self
                    .provider
                    .start_process(&self.sandbox, request, self.cancellation.child_token())
                    .await
                    .map_err(tool_error)?;
                let result = stream.collect_result().await.map_err(tool_error)?;
                let _ = events.publish(AgentEvent::ToolProgress {
                    id: call.id.clone(),
                    text: format!("command exited with {}", result.exit_code),
                });
                Ok(ToolOutput {
                    is_error: result.exit_code != 0,
                    content: json!({
                        "ok": result.exit_code == 0,
                        "stdout": result.output.stdout,
                        "stderr": result.output.stderr,
                        "exit_code": result.exit_code,
                    }),
                })
            }
            "write_file" => {
                let input = parse_arguments::<WriteFileInput>(call)?;
                if input.text.len() > MAX_WRITE_BYTES {
                    return Err(AgentRuntimeError::Tool(format!(
                        "file exceeds the {MAX_WRITE_BYTES}-byte write limit"
                    )));
                }
                self.write_text(&input.path, input.text).await?;
                Ok(ToolOutput {
                    content: json!({"ok": true, "path": input.path, "written": true}),
                    is_error: false,
                })
            }
            "edit_file" => {
                let input = parse_arguments::<EditFileInput>(call)?;
                if input.old_text.is_empty() {
                    return Err(AgentRuntimeError::Tool(
                        "old_text must not be empty".to_owned(),
                    ));
                }
                let original = self.read_text(&input.path, MAX_FILE_LIMIT).await?;
                let occurrences = original.matches(&input.old_text).count();
                if occurrences == 0 {
                    return Err(AgentRuntimeError::Tool("old_text was not found".to_owned()));
                }
                if occurrences > 1 && !input.replace_all {
                    return Err(AgentRuntimeError::Tool(format!(
                        "old_text occurs {occurrences} times; provide more context or set replace_all"
                    )));
                }
                let replacements = if input.replace_all { occurrences } else { 1 };
                let edited = if input.replace_all {
                    original.replace(&input.old_text, &input.new_text)
                } else {
                    original.replacen(&input.old_text, &input.new_text, 1)
                };
                if edited.len() > MAX_WRITE_BYTES {
                    return Err(AgentRuntimeError::Tool(format!(
                        "edited file exceeds the {MAX_WRITE_BYTES}-byte write limit"
                    )));
                }
                self.write_text(&input.path, edited).await?;
                Ok(ToolOutput {
                    content: json!({
                        "ok": true,
                        "path": input.path,
                        "edited": true,
                        "replacements": replacements,
                    }),
                    is_error: false,
                })
            }
            "read_file" => {
                let input = parse_arguments::<ReadFileInput>(call)?;
                let maximum = input
                    .max_bytes
                    .unwrap_or(DEFAULT_FILE_LIMIT)
                    .clamp(1, MAX_FILE_LIMIT);
                let text = self.read_text(&input.path, maximum).await?;
                Ok(ToolOutput {
                    content: json!({"ok": true, "path": input.path, "text": text}),
                    is_error: false,
                })
            }
            "list_files" => {
                let input = parse_arguments::<ListFilesInput>(call)?;
                let relative = input.path.unwrap_or_else(|| ".".to_owned());
                let target = self.readable_file(&relative)?;
                let timeout = Duration::from_secs(30);
                let request = CommandRequest {
                    command: "/usr/bin/find".to_owned(),
                    args: vec![
                        target.as_str().to_owned(),
                        "-type".to_owned(),
                        "f".to_owned(),
                        "-print".to_owned(),
                    ],
                    env: BTreeMap::new(),
                    cwd: Some(self.workspace_root.as_str().to_owned()),
                    username: Some(self.sandbox_user.clone()),
                    tag: ExecutionTag::new(),
                    stdin_enabled: false,
                    absolute_deadline: self.bounded_deadline(timeout)?,
                    idle_timeout: timeout,
                    output_limits: output_limits(self.max_output_chars),
                };
                let stream = self
                    .provider
                    .start_process(&self.sandbox, request, self.cancellation.child_token())
                    .await
                    .map_err(tool_error)?;
                let result = stream.collect_result().await.map_err(tool_error)?;
                if result.exit_code != 0 {
                    return Ok(ToolOutput {
                        content: json!({
                            "ok": false,
                            "error": result.output.stderr,
                            "exit_code": result.exit_code,
                        }),
                        is_error: true,
                    });
                }
                let prefix = format!("{}/", self.workspace_root.as_str());
                let files = result
                    .output
                    .stdout
                    .lines()
                    .map(str::trim)
                    .filter(|line| !line.is_empty())
                    .take(1_000)
                    .map(|line| line.strip_prefix(&prefix).unwrap_or(line).to_owned())
                    .collect::<Vec<_>>();
                Ok(ToolOutput {
                    content: json!({"ok": true, "path": relative, "files": files}),
                    is_error: false,
                })
            }
            "web_search" => {
                let input = parse_arguments::<WebSearchInput>(call)?;
                let query = input.query.trim();
                if query.is_empty() || query.chars().count() > MAX_SEARCH_QUERY_CHARS {
                    return Err(AgentRuntimeError::Tool(
                        "web_search query must contain 1-2000 characters".to_owned(),
                    ));
                }
                let category = input
                    .category
                    .as_deref()
                    .map(str::trim)
                    .filter(|value| !value.is_empty());
                if category.is_some_and(|value| value.chars().count() > 100) {
                    return Err(AgentRuntimeError::Tool(
                        "web_search category must contain at most 100 characters".to_owned(),
                    ));
                }
                let result_limit = input.num_results.unwrap_or(5).clamp(1, MAX_SEARCH_RESULTS);
                let results = self
                    .exa
                    .search(query, result_limit, category)
                    .await
                    .map_err(|error| {
                        AgentRuntimeError::Tool(format!("web_search failed: {error}"))
                    })?;
                let _ = events.publish(AgentEvent::ToolProgress {
                    id: call.id.clone(),
                    text: format!("web_search returned {} results", results.len()),
                });
                Ok(ToolOutput {
                    content: json!({"ok": true, "query": query, "results": results}),
                    is_error: false,
                })
            }
            other => Err(AgentRuntimeError::Tool(format!(
                "unsupported Share Action tool {other}"
            ))),
        }
    }

    pub(crate) async fn write_text(
        &self,
        relative: &str,
        text: String,
    ) -> Result<(), AgentRuntimeError> {
        let path = self.workspace_file(relative)?;
        let length = text.len();
        let source: BoxByteStream = Box::pin(stream::once(async move { Ok(text.into()) }));
        let upload = self.provider.file_client().upload_sandbox_path(
            &self.sandbox,
            &path,
            self.sandbox_user.as_str(),
            u64::try_from(length).unwrap_or(u64::MAX),
            source,
            self.remaining()?,
        );
        tokio::select! {
            () = self.cancellation.cancelled() => Err(AgentRuntimeError::Tool("Share Action VM operation was cancelled".to_owned())),
            result = upload => result.map_err(tool_error),
        }
    }

    pub(crate) async fn read_text(
        &self,
        relative: &str,
        maximum: usize,
    ) -> Result<String, AgentRuntimeError> {
        let bytes = self.read_bytes(relative, maximum).await?;
        String::from_utf8(bytes)
            .map_err(|_| AgentRuntimeError::Tool("file is not valid UTF-8".to_owned()))
    }

    pub(crate) async fn read_bytes(
        &self,
        relative: &str,
        maximum: usize,
    ) -> Result<Vec<u8>, AgentRuntimeError> {
        let path = self.readable_file(relative)?;
        let download = self.provider.file_client().download_sandbox_path(
            &self.sandbox,
            &path,
            self.sandbox_user.as_str(),
            self.remaining()?,
        );
        let mut stream = tokio::select! {
            () = self.cancellation.cancelled() => return Err(AgentRuntimeError::Tool("Share Action VM operation was cancelled".to_owned())),
            result = download => result.map_err(tool_error)?,
        };
        let mut bytes = Vec::new();
        while let Some(chunk) = stream.next().await {
            let chunk = chunk.map_err(tool_error)?;
            if bytes.len().saturating_add(chunk.len()) > maximum {
                return Err(AgentRuntimeError::Tool(format!(
                    "file exceeds the {maximum}-byte read limit"
                )));
            }
            bytes.extend_from_slice(&chunk);
        }
        Ok(bytes)
    }

    fn workspace_file(&self, relative: &str) -> Result<SandboxPath, AgentRuntimeError> {
        if relative.trim().is_empty() || relative.trim() == "." {
            return Ok(self.workspace_root.clone());
        }
        let relative = WorkspacePath::parse(relative.to_owned()).map_err(tool_error)?;
        SandboxPath::parse(format!(
            "{}/{}",
            self.workspace_root.as_str(),
            relative.as_str()
        ))
        .map_err(tool_error)
    }

    fn readable_file(&self, path: &str) -> Result<SandboxPath, AgentRuntimeError> {
        let path = path.trim();
        if path == "/data" || path.starts_with("/data/") {
            return SandboxPath::parse(path.to_owned()).map_err(tool_error);
        }
        self.workspace_file(path)
    }

    fn bounded_deadline(&self, requested: Duration) -> Result<Instant, AgentRuntimeError> {
        let requested = Instant::now()
            .checked_add(requested)
            .ok_or(AgentRuntimeError::DeadlineExceeded)?;
        Ok(requested.min(self.deadline))
    }

    fn remaining(&self) -> Result<Duration, AgentRuntimeError> {
        self.deadline
            .checked_duration_since(Instant::now())
            .filter(|value| !value.is_zero())
            .ok_or(AgentRuntimeError::DeadlineExceeded)
    }
}

impl ToolExecutor for ShareActionToolExecutor {
    fn execute(&self, call: ToolCall, events: Arc<dyn AgentEventSink>) -> BoxToolFuture<'_> {
        Box::pin(async move { self.execute_tool(&call, &events).await })
    }
}

#[derive(Debug, Clone)]
pub(crate) struct ExaSearchClient {
    client: reqwest::Client,
    api_key: Option<SecretString>,
    endpoint: Url,
}

impl ExaSearchClient {
    pub(crate) fn new(
        api_key: Option<SecretString>,
        endpoint: Url,
        timeout: Duration,
    ) -> Result<Self, reqwest::Error> {
        Ok(Self {
            client: reqwest::Client::builder().timeout(timeout).build()?,
            api_key,
            endpoint,
        })
    }

    pub(crate) async fn search(
        &self,
        query: &str,
        num_results: usize,
        category: Option<&str>,
    ) -> Result<Vec<ExaSearchResult>, ExaSearchError> {
        let key = self.api_key.as_ref().ok_or(ExaSearchError::NotConfigured)?;
        let mut request = json!({
            "query": query,
            "numResults": num_results,
            "excludeDomains": EXCLUDED_SEARCH_DOMAINS,
            "contents": {
                "livecrawl": "fallback",
                "summary": {"query": "Key points and main takeaways"},
                "text": {"maxCharacters": 1500}
            }
        });
        if let Some(category) = category {
            request["category"] = json!(category);
        }
        let response = self
            .client
            .post(self.endpoint.clone())
            .header("x-api-key", key.expose_secret())
            .json(&request)
            .send()
            .await?
            .error_for_status()?;
        let body: ExaSearchResponse = response.json().await?;
        Ok(body
            .results
            .into_iter()
            .filter(|result| !result.url.trim().is_empty())
            .take(num_results)
            .map(|result| ExaSearchResult {
                title: nonempty(Some(result.title)).unwrap_or_else(|| result.url.clone()),
                url: result.url,
                snippet: nonempty(result.summary).or_else(|| nonempty(result.text)),
                published_date: nonempty(result.published_date),
            })
            .collect())
    }
}

#[derive(Debug, Serialize)]
pub(crate) struct ExaSearchResult {
    pub(crate) title: String,
    pub(crate) url: String,
    pub(crate) snippet: Option<String>,
    pub(crate) published_date: Option<String>,
}

#[derive(Debug, Deserialize)]
struct ExaSearchResponse {
    #[serde(default)]
    results: Vec<ExaSearchRow>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ExaSearchRow {
    #[serde(default)]
    title: String,
    #[serde(default)]
    url: String,
    summary: Option<String>,
    text: Option<String>,
    published_date: Option<String>,
}

#[derive(Debug, thiserror::Error)]
pub(crate) enum ExaSearchError {
    #[error("Exa is not configured")]
    NotConfigured,
    #[error("Exa request failed")]
    Request(#[from] reqwest::Error),
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct ExecuteBashInput {
    command: String,
    timeout_seconds: Option<u64>,
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct WriteFileInput {
    path: String,
    text: String,
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct EditFileInput {
    path: String,
    old_text: String,
    new_text: String,
    #[serde(default)]
    replace_all: bool,
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct ReadFileInput {
    path: String,
    max_bytes: Option<usize>,
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct ListFilesInput {
    path: Option<String>,
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct WebSearchInput {
    query: String,
    num_results: Option<usize>,
    category: Option<String>,
}

fn tool_definition<T: JsonSchema>(
    name: &str,
    description: &str,
) -> newsly_agent_runtime::ToolDefinition {
    newsly_agent_runtime::ToolDefinition {
        name: name.to_owned(),
        description: description.to_owned(),
        input_schema: schema_for!(T),
    }
}

fn parse_arguments<T: for<'de> Deserialize<'de>>(call: &ToolCall) -> Result<T, AgentRuntimeError> {
    serde_json::from_value(call.arguments.clone()).map_err(|error| {
        AgentRuntimeError::Tool(format!("invalid {} arguments: {error}", call.name))
    })
}

fn output_limits(maximum_chars: usize) -> OutputLimits {
    let channel = maximum_chars.saturating_mul(4).clamp(4_000, 800_000);
    OutputLimits {
        stdout_bytes: channel,
        stderr_bytes: channel,
        combined_bytes: channel.saturating_mul(2),
        event_bytes: channel,
        channel_capacity: 32,
    }
}

fn tool_error(error: impl std::fmt::Display) -> AgentRuntimeError {
    AgentRuntimeError::Tool(error.to_string())
}

fn tool_failure(message: String) -> ToolOutput {
    ToolOutput {
        content: json!({"ok": false, "error": message}),
        is_error: true,
    }
}

fn nonempty(value: Option<String>) -> Option<String> {
    value.and_then(|value| {
        let trimmed = value.trim();
        (!trimmed.is_empty()).then(|| trimmed.chars().take(1_500).collect())
    })
}
