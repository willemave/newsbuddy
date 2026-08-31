//! Direct envd Process `ConnectRPC` client and bounded event normalization.

use std::pin::Pin;
use std::sync::Arc;
use std::task::{Context, Poll};
use std::time::Duration;

use base64::Engine as _;
use base64::engine::general_purpose::STANDARD as BASE64_STANDARD;
use buffa::{EnumValue, MessageField};
use bytes::Bytes;
use connectrpc::ErrorCode;
use connectrpc::client::{CallOptions, ClientConfig, HttpClient};
use futures_core::Stream;
use futures_util::StreamExt;
use secrecy::ExposeSecret;
use semver::Version;
use tokio::sync::mpsc;
use tokio_stream::wrappers::ReceiverStream;
use tokio_util::sync::CancellationToken;

use crate::control_plane::ControlPlaneClient;
use crate::error::E2bError;
use crate::generated::process;
use crate::types::{
    CommandEvent, CommandOutput, CommandRequest, CommandResult, ExecutionTag, ExitStatus,
    OutputLimits, ProcessInfo, ProcessSelector, SandboxHandle,
};

const MIN_SUPPORTED_ENVD: &str = "0.1.0";
const ENVD_STDIN_SELECTION: &str = "0.3.0";
const ENVD_CLOSE_STDIN: &str = "0.5.2";
const MAX_STDIN_BYTES: usize = 1024 * 1024;
const KEEPALIVE_INTERVAL_SECONDS: &str = "50";

type RawEventStream = Pin<Box<dyn Stream<Item = Result<RawProcessEvent, E2bError>> + Send>>;
type EventStream = Pin<Box<dyn Stream<Item = Result<CommandEvent, E2bError>> + Send>>;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum EnvdCapability {
    ProcessStreaming,
    ExecutionTags,
    ProcessReconnect,
    ProcessSignals,
    ProcessInput,
    StdinSelection,
    CloseStdin,
}

impl EnvdCapability {
    fn name(self) -> &'static str {
        match self {
            Self::ProcessStreaming => "process_streaming",
            Self::ExecutionTags => "execution_tags",
            Self::ProcessReconnect => "process_reconnect",
            Self::ProcessSignals => "process_signals",
            Self::ProcessInput => "process_input",
            Self::StdinSelection => "stdin_selection",
            Self::CloseStdin => "close_stdin",
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
#[allow(clippy::struct_excessive_bools)]
pub struct CapabilityReport {
    pub envd_version: Version,
    pub process_streaming: bool,
    pub execution_tags: bool,
    pub process_reconnect: bool,
    pub process_signals: bool,
    pub process_input: bool,
    pub stdin_selection: bool,
    pub close_stdin: bool,
}

impl CapabilityReport {
    pub fn require(&self, capability: EnvdCapability) -> Result<(), E2bError> {
        let available = match capability {
            EnvdCapability::ProcessStreaming => self.process_streaming,
            EnvdCapability::ExecutionTags => self.execution_tags,
            EnvdCapability::ProcessReconnect => self.process_reconnect,
            EnvdCapability::ProcessSignals => self.process_signals,
            EnvdCapability::ProcessInput => self.process_input,
            EnvdCapability::StdinSelection => self.stdin_selection,
            EnvdCapability::CloseStdin => self.close_stdin,
        };
        if available {
            Ok(())
        } else {
            Err(E2bError::UnsupportedCapability {
                capability: capability.name().to_owned(),
                version: self.envd_version.to_string(),
            })
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ProcessSignal {
    Terminate,
    Kill,
}

#[derive(Clone, Debug)]
pub struct EnvdProcessClient {
    control: ControlPlaneClient,
    minimum_version: Version,
}

impl EnvdProcessClient {
    pub fn new(control: ControlPlaneClient) -> Self {
        Self {
            control,
            minimum_version: Version::parse(MIN_SUPPORTED_ENVD)
                .expect("the built-in minimum envd version is valid"),
        }
    }

    pub fn check_capabilities(
        &self,
        sandbox: &SandboxHandle,
    ) -> Result<CapabilityReport, E2bError> {
        let version_text = sandbox.envd_version.trim().trim_start_matches('v');
        let version =
            Version::parse(version_text).map_err(|_| E2bError::UnsupportedCapability {
                capability: "versioned_envd_protocol".to_owned(),
                version: sandbox.envd_version.clone(),
            })?;
        let supported = version >= self.minimum_version;
        let stdin_selection = version
            >= Version::parse(ENVD_STDIN_SELECTION)
                .expect("the built-in stdin-selection version is valid");
        let close_stdin = version
            >= Version::parse(ENVD_CLOSE_STDIN).expect("the built-in close-stdin version is valid");
        Ok(CapabilityReport {
            envd_version: version,
            process_streaming: supported,
            execution_tags: supported,
            process_reconnect: supported,
            process_signals: supported,
            process_input: supported,
            stdin_selection,
            close_stdin,
        })
    }

    pub async fn start(
        &self,
        sandbox: &SandboxHandle,
        request: CommandRequest,
        cancellation: CancellationToken,
    ) -> Result<CommandEventStream, E2bError> {
        request.validate()?;
        if cancellation.is_cancelled() {
            return Err(E2bError::Cancelled);
        }
        let capabilities = self.check_capabilities(sandbox)?;
        capabilities.require(EnvdCapability::ProcessStreaming)?;
        capabilities.require(EnvdCapability::ExecutionTags)?;
        if !request.stdin_enabled {
            capabilities.require(EnvdCapability::StdinSelection)?;
        }

        let client = self.client(sandbox, request.output_limits.event_bytes)?;
        let process = process::ProcessConfig {
            cmd: request.command.clone(),
            args: request.args.clone(),
            envs: request.env.clone().into_iter().collect(),
            cwd: request.cwd.clone(),
            ..Default::default()
        };
        let rpc_request = process::StartRequest {
            process: MessageField::some(process),
            tag: Some(request.tag.as_str().to_owned()),
            stdin: Some(request.stdin_enabled),
            ..Default::default()
        };
        let remaining = request
            .absolute_deadline
            .checked_duration_since(tokio::time::Instant::now())
            .ok_or(E2bError::Deadline)?;
        let options = process_call_options(remaining, request.username.as_ref())?;
        let stream = client
            .start_with_options(rpc_request, options)
            .await
            .map_err(|error| ambiguous_start_error(&error, &request.tag))?;
        let raw = raw_start_stream(stream, request.tag.clone());
        Ok(self.normalized_stream(
            sandbox.clone(),
            request.tag,
            None,
            raw,
            request.absolute_deadline,
            request.idle_timeout,
            request.output_limits,
            cancellation,
            true,
        ))
    }

    #[allow(clippy::too_many_arguments)]
    pub async fn connect(
        &self,
        sandbox: &SandboxHandle,
        selector: ProcessSelector,
        execution_tag: ExecutionTag,
        absolute_deadline: tokio::time::Instant,
        idle_timeout: Duration,
        output_limits: OutputLimits,
        cancellation: CancellationToken,
    ) -> Result<CommandEventStream, E2bError> {
        output_limits.validate()?;
        if cancellation.is_cancelled() {
            return Err(E2bError::Cancelled);
        }
        if idle_timeout.is_zero() {
            return Err(E2bError::InvalidInput(
                "command idle timeout must be greater than zero".to_owned(),
            ));
        }
        self.check_capabilities(sandbox)?
            .require(EnvdCapability::ProcessReconnect)?;
        let remaining = absolute_deadline
            .checked_duration_since(tokio::time::Instant::now())
            .ok_or(E2bError::Deadline)?;
        let pid_hint = match &selector {
            ProcessSelector::Pid(0) => {
                return Err(E2bError::InvalidInput(
                    "process PID must be greater than zero".to_owned(),
                ));
            }
            ProcessSelector::Pid(pid) => Some(*pid),
            ProcessSelector::Tag(tag) => {
                if tag != &execution_tag {
                    return Err(E2bError::InvalidInput(
                        "process selector tag must match the execution tag".to_owned(),
                    ));
                }
                None
            }
        };
        let client = self.client(sandbox, output_limits.event_bytes)?;
        let stream = client
            .connect_with_options(
                process::ConnectRequest {
                    process: MessageField::some(selector_to_wire(selector)),
                    ..Default::default()
                },
                CallOptions::default().with_timeout(remaining),
            )
            .await
            .map_err(|error| connect_error(&error))?;
        let raw = raw_connect_stream(stream);
        Ok(self.normalized_stream(
            sandbox.clone(),
            execution_tag,
            pid_hint,
            raw,
            absolute_deadline,
            idle_timeout,
            output_limits,
            cancellation,
            false,
        ))
    }

    /// Reattach to an already-created process by unique execution tag. Absence is ambiguous and
    /// never causes a replacement `Start` request.
    #[allow(clippy::too_many_arguments)]
    pub async fn recover_by_tag(
        &self,
        sandbox: &SandboxHandle,
        execution_tag: ExecutionTag,
        absolute_deadline: tokio::time::Instant,
        idle_timeout: Duration,
        output_limits: OutputLimits,
        cancellation: CancellationToken,
    ) -> Result<CommandEventStream, E2bError> {
        let matches = self
            .list(sandbox)
            .await?
            .into_iter()
            .filter(|process| process.tag.as_ref() == Some(&execution_tag))
            .collect::<Vec<_>>();
        let process = match matches.as_slice() {
            [process] => process,
            [] => {
                return Err(E2bError::RecoveryUnavailable {
                    execution_tag: execution_tag.to_string(),
                });
            }
            _ => {
                return Err(E2bError::Protocol(format!(
                    "multiple envd processes have execution tag {execution_tag}"
                )));
            }
        };
        self.connect(
            sandbox,
            ProcessSelector::Pid(process.pid),
            execution_tag,
            absolute_deadline,
            idle_timeout,
            output_limits,
            cancellation,
        )
        .await
    }

    pub async fn list(&self, sandbox: &SandboxHandle) -> Result<Vec<ProcessInfo>, E2bError> {
        self.check_capabilities(sandbox)?
            .require(EnvdCapability::ProcessReconnect)?;
        let client = self.client(sandbox, 1024 * 1024)?;
        let response = client
            .list(process::ListRequest::default())
            .await
            .map_err(|error| connect_error(&error))?
            .into_owned();
        response
            .processes
            .into_iter()
            .map(|mut item| {
                if item.pid == 0 {
                    return Err(E2bError::Protocol(
                        "envd listed a process with PID zero".to_owned(),
                    ));
                }
                let config = item.config.take().unwrap_or_default();
                let tag = item.tag.map(ExecutionTag::parse).transpose()?;
                Ok(ProcessInfo {
                    pid: item.pid,
                    tag,
                    command: config.cmd,
                    args: config.args,
                    cwd: config.cwd,
                })
            })
            .collect()
    }

    pub async fn signal(
        &self,
        sandbox: &SandboxHandle,
        selector: ProcessSelector,
        signal: ProcessSignal,
    ) -> Result<(), E2bError> {
        validate_selector(&selector)?;
        self.check_capabilities(sandbox)?
            .require(EnvdCapability::ProcessSignals)?;
        let signal = match signal {
            ProcessSignal::Terminate => process::Signal::Sigterm,
            ProcessSignal::Kill => process::Signal::Sigkill,
        };
        self.client(sandbox, 64 * 1024)?
            .send_signal(process::SendSignalRequest {
                process: MessageField::some(selector_to_wire(selector)),
                signal: EnumValue::Known(signal),
                ..Default::default()
            })
            .await
            .map_err(|error| connect_error(&error))?;
        Ok(())
    }

    pub async fn send_stdin(
        &self,
        sandbox: &SandboxHandle,
        selector: ProcessSelector,
        input: Bytes,
    ) -> Result<(), E2bError> {
        validate_selector(&selector)?;
        let execution_tag = match &selector {
            ProcessSelector::Pid(_) => None,
            ProcessSelector::Tag(tag) => Some(tag.to_string()),
        };
        self.check_capabilities(sandbox)?
            .require(EnvdCapability::ProcessInput)?;
        if input.len() > MAX_STDIN_BYTES {
            return Err(E2bError::InvalidInput(format!(
                "stdin message exceeds {MAX_STDIN_BYTES} bytes"
            )));
        }
        self.client(sandbox, 64 * 1024)?
            .send_input(process::SendInputRequest {
                process: MessageField::some(selector_to_wire(selector)),
                input: MessageField::some(process::ProcessInput {
                    input: Some(process::process_input::Input::Stdin(input.to_vec())),
                    ..Default::default()
                }),
                ..Default::default()
            })
            .await
            .map_err(|error| {
                ambiguous_mutation_error(&error, "send_process_stdin", execution_tag.as_deref())
            })?;
        Ok(())
    }

    pub async fn close_stdin(
        &self,
        sandbox: &SandboxHandle,
        selector: ProcessSelector,
    ) -> Result<(), E2bError> {
        validate_selector(&selector)?;
        self.check_capabilities(sandbox)?
            .require(EnvdCapability::CloseStdin)?;
        self.client(sandbox, 64 * 1024)?
            .close_stdin(process::CloseStdinRequest {
                process: MessageField::some(selector_to_wire(selector)),
                ..Default::default()
            })
            .await
            .map_err(|error| connect_error(&error))?;
        Ok(())
    }

    fn client(
        &self,
        sandbox: &SandboxHandle,
        max_message_size: usize,
    ) -> Result<process::ProcessClient<HttpClient>, E2bError> {
        let base = self.control.envd_base_url(sandbox)?;
        let uri = base
            .as_str()
            .parse::<http::Uri>()
            .map_err(|error| E2bError::Configuration(error.to_string()))?;
        let roots = webpki_roots::TLS_SERVER_ROOTS
            .iter()
            .cloned()
            .collect::<connectrpc::rustls::RootCertStore>();
        let tls = connectrpc::rustls::ClientConfig::builder_with_provider(Arc::new(
            connectrpc::rustls::crypto::aws_lc_rs::default_provider(),
        ))
        .with_safe_default_protocol_versions()
        .map_err(|error| E2bError::Configuration(error.to_string()))?
        .with_root_certificates(roots)
        .with_no_client_auth();
        let http = HttpClient::with_tls(Arc::new(tls));
        let wire_message_size = max_message_size
            .saturating_mul(4)
            .div_ceil(3)
            .saturating_add(64 * 1024);
        let mut config = ClientConfig::new(uri)
            .json()
            .with_default_timeout(self.control.config().request_timeout)
            .with_default_max_message_size(wire_message_size)
            .with_default_element_memory_limit(wire_message_size.saturating_mul(2))
            .with_default_header("E2b-Sandbox-Id", sandbox.sandbox_id.as_str())
            .with_default_header("E2b-Sandbox-Port", "49983")
            .with_default_header("Keepalive-Ping-Interval", KEEPALIVE_INTERVAL_SECONDS)
            .with_default_header("User-Agent", &self.control.config().user_agent);
        if let Some(token) = &sandbox.envd_access_token {
            let value = http::HeaderValue::from_str(token.expose_secret()).map_err(|_| {
                E2bError::Configuration("envd access token is not a valid header".to_owned())
            })?;
            config = config.with_default_header("X-Access-Token", value);
        }
        Ok(process::ProcessClient::new(http, config))
    }

    #[allow(clippy::too_many_arguments)]
    fn normalized_stream(
        &self,
        sandbox: SandboxHandle,
        execution_tag: ExecutionTag,
        pid_hint: Option<u32>,
        raw: RawEventStream,
        absolute_deadline: tokio::time::Instant,
        idle_timeout: Duration,
        limits: OutputLimits,
        cancellation: CancellationToken,
        ambiguous_on_disconnect: bool,
    ) -> CommandEventStream {
        let client = self.clone();
        let execution_tag_for_pump = execution_tag.clone();
        let cancel_on_drop = cancellation.clone();
        let cancellation_for_pump = cancellation.clone();
        let client_for_stream = client.clone();
        let sandbox_for_stream = sandbox.clone();
        let stream = normalize_events(
            client_for_stream,
            sandbox_for_stream,
            execution_tag,
            pid_hint,
            raw,
            absolute_deadline,
            idle_timeout,
            limits,
            cancellation,
            ambiguous_on_disconnect,
        );
        let (sender, receiver) = mpsc::channel(limits.channel_capacity);
        tokio::spawn(pump_events(
            client,
            sandbox,
            pid_hint,
            execution_tag_for_pump,
            stream,
            sender,
            cancellation_for_pump,
        ));
        CommandEventStream {
            inner: Box::pin(ReceiverStream::new(receiver)),
            cancellation: cancel_on_drop,
        }
    }

    async fn stop_process(
        &self,
        sandbox: &SandboxHandle,
        selector: ProcessSelector,
        immediate: bool,
    ) {
        if !immediate {
            let _ = self
                .signal(sandbox, selector.clone(), ProcessSignal::Terminate)
                .await;
            tokio::time::sleep(Duration::from_millis(250)).await;
        }
        if let Err(error) = self
            .signal(sandbox, selector.clone(), ProcessSignal::Kill)
            .await
        {
            tracing::warn!(
                sandbox_id = %sandbox.sandbox_id,
                ?selector,
                error = %error,
                "unable to confirm E2B process cancellation"
            );
        }
    }
}

fn process_call_options(
    timeout: Duration,
    username: Option<&crate::types::SandboxUser>,
) -> Result<CallOptions, E2bError> {
    let mut options = CallOptions::default().with_timeout(timeout);
    if let Some(username) = username {
        // envd selects a process user with HTTP Basic auth and an empty password. The sandbox's
        // independent X-Access-Token remains on the client as its authentication boundary.
        let encoded = BASE64_STANDARD.encode(format!("{}:", username.as_str()));
        let value = http::HeaderValue::from_str(&format!("Basic {encoded}"))
            .map_err(|_| E2bError::InvalidInput("invalid sandbox username".to_owned()))?;
        options = options.with_header(http::header::AUTHORIZATION, value);
    }
    Ok(options)
}

async fn pump_events<S>(
    client: EnvdProcessClient,
    sandbox: SandboxHandle,
    mut pid: Option<u32>,
    execution_tag: ExecutionTag,
    stream: S,
    sender: mpsc::Sender<Result<CommandEvent, E2bError>>,
    cancellation: CancellationToken,
) where
    S: Stream<Item = Result<CommandEvent, E2bError>> + Send,
{
    futures_util::pin_mut!(stream);
    while let Some(item) = stream.next().await {
        if let Ok(CommandEvent::Started {
            pid: process_id, ..
        }) = &item
        {
            pid = Some(*process_id);
        }
        let process_already_stopped = event_stopped_process(&item);
        let delivery = tokio::select! {
            biased;
            result = sender.send(item) => {
                if result.is_ok() {
                    PumpDelivery::Sent
                } else {
                    PumpDelivery::Closed
                }
            },
            () = cancellation.cancelled() => PumpDelivery::Cancelled,
        };
        match delivery {
            PumpDelivery::Sent => {}
            PumpDelivery::Closed => {
                if !process_already_stopped {
                    client
                        .stop_process(&sandbox, process_selector(pid, &execution_tag), false)
                        .await;
                }
                return;
            }
            PumpDelivery::Cancelled => {
                if !process_already_stopped {
                    client
                        .stop_process(&sandbox, process_selector(pid, &execution_tag), false)
                        .await;
                }
                let _ = sender.send(Err(E2bError::Cancelled)).await;
                return;
            }
        }
    }
}

fn event_stopped_process(item: &Result<CommandEvent, E2bError>) -> bool {
    match item {
        Err(E2bError::Cancelled | E2bError::Deadline | E2bError::OutputLimitExceeded { .. }) => {
            true
        }
        Err(E2bError::Remote {
            code: Some(code), ..
        }) => code == "envd_idle_timeout",
        _ => false,
    }
}

pub struct CommandEventStream {
    inner: EventStream,
    cancellation: CancellationToken,
}

impl std::fmt::Debug for CommandEventStream {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("CommandEventStream")
            .field("cancelled", &self.cancellation.is_cancelled())
            .finish_non_exhaustive()
    }
}

impl CommandEventStream {
    pub fn cancel(&self) {
        self.cancellation.cancel();
    }

    #[must_use]
    pub fn cancellation_token(&self) -> CancellationToken {
        self.cancellation.clone()
    }

    /// Cancel the remote process and drain the bounded delivery channel until its pump exits.
    pub async fn cancel_and_drain(mut self, drain_timeout: Duration) -> Result<(), E2bError> {
        if drain_timeout.is_zero() {
            return Err(E2bError::InvalidInput(
                "stream drain timeout must be greater than zero".to_owned(),
            ));
        }
        self.cancellation.cancel();
        tokio::time::timeout(drain_timeout, async {
            while self.next().await.is_some() {}
        })
        .await
        .map_err(|_| E2bError::Deadline)
    }

    pub async fn collect_result(mut self) -> Result<CommandResult, E2bError> {
        let mut output = CommandOutput::default();
        let mut execution_tag = None;
        let mut pid = None;
        let mut terminal = None;
        while let Some(event) = self.next().await {
            match event? {
                CommandEvent::Started {
                    execution_tag: tag,
                    pid: process_id,
                    ..
                } => {
                    execution_tag = Some(tag);
                    pid = Some(process_id);
                }
                CommandEvent::Stdout { text, .. } | CommandEvent::Pty { text, .. } => {
                    output.stdout.push_str(&text);
                }
                CommandEvent::Stderr { text, .. } => output.stderr.push_str(&text),
                CommandEvent::Exited {
                    status,
                    exit_code,
                    error,
                    ..
                } => terminal = Some((status, exit_code, error)),
                CommandEvent::KeepAlive { .. } | CommandEvent::TransportDisconnected { .. } => {}
            }
        }
        let (status, exit_code, error) = terminal.ok_or(E2bError::MissingTerminalEvent)?;
        Ok(CommandResult {
            execution_tag: execution_tag.ok_or_else(|| {
                E2bError::Protocol("command stream did not include a start event".to_owned())
            })?,
            pid,
            output,
            status,
            exit_code,
            error,
        })
    }
}

impl Stream for CommandEventStream {
    type Item = Result<CommandEvent, E2bError>;

    fn poll_next(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Option<Self::Item>> {
        self.inner.as_mut().poll_next(cx)
    }
}

impl Drop for CommandEventStream {
    fn drop(&mut self) {
        self.cancellation.cancel();
    }
}

#[derive(Debug)]
enum RawProcessEvent {
    Started(u32),
    Stdout(Bytes),
    Stderr(Bytes),
    Pty(Bytes),
    KeepAlive,
    Exited {
        exit_code: i32,
        exited: bool,
        status: String,
        error: Option<String>,
    },
}

enum RawPollResult {
    Cancelled,
    Event(Result<Option<Result<RawProcessEvent, E2bError>>, tokio::time::error::Elapsed>),
}

enum PumpDelivery {
    Sent,
    Closed,
    Cancelled,
}

fn raw_start_stream<B>(
    mut stream: connectrpc::client::ServerStream<
        B,
        process::__buffa::view::StartResponseView<'static>,
    >,
    execution_tag: ExecutionTag,
) -> RawEventStream
where
    B: connectrpc::http_body::Body<Data = Bytes> + Send + Unpin + 'static,
    B::Error: std::fmt::Display + Send + Sync + 'static,
{
    Box::pin(async_stream::try_stream! {
        while let Some(message) = stream.message().await.map_err(|error| {
            E2bError::AmbiguousDelivery {
                operation: "start_process_stream".to_owned(),
                execution_tag: Some(execution_tag.to_string()),
                message: error.to_string(),
            }
        })? {
            yield raw_event_from_wire(message.to_owned_message().event.take())?;
        }
    })
}

fn raw_connect_stream<B>(
    mut stream: connectrpc::client::ServerStream<
        B,
        process::__buffa::view::ConnectResponseView<'static>,
    >,
) -> RawEventStream
where
    B: connectrpc::http_body::Body<Data = Bytes> + Send + Unpin + 'static,
    B::Error: std::fmt::Display + Send + Sync + 'static,
{
    Box::pin(async_stream::try_stream! {
        while let Some(message) = stream.message().await.map_err(|error| connect_stream_error(&error))? {
            yield raw_event_from_wire(message.to_owned_message().event.take())?;
        }
    })
}

fn raw_event_from_wire(event: Option<process::ProcessEvent>) -> Result<RawProcessEvent, E2bError> {
    let event = event
        .and_then(|event| event.event)
        .ok_or_else(|| E2bError::Protocol("envd response omitted process event".to_owned()))?;
    match event {
        process::process_event::Event::Start(event) => Ok(RawProcessEvent::Started(event.pid)),
        process::process_event::Event::Data(event) => match event.output {
            Some(process::process_event::data_event::Output::Stdout(bytes)) => {
                Ok(RawProcessEvent::Stdout(Bytes::from(bytes)))
            }
            Some(process::process_event::data_event::Output::Stderr(bytes)) => {
                Ok(RawProcessEvent::Stderr(Bytes::from(bytes)))
            }
            Some(process::process_event::data_event::Output::Pty(bytes)) => {
                Ok(RawProcessEvent::Pty(Bytes::from(bytes)))
            }
            None => Err(E2bError::Protocol(
                "envd data event omitted output".to_owned(),
            )),
        },
        process::process_event::Event::Keepalive(_) => Ok(RawProcessEvent::KeepAlive),
        process::process_event::Event::End(event) => Ok(RawProcessEvent::Exited {
            exit_code: event.exit_code,
            exited: event.exited,
            status: event.status,
            error: event.error,
        }),
    }
}

#[allow(clippy::too_many_arguments, clippy::too_many_lines)]
fn normalize_events(
    client: EnvdProcessClient,
    sandbox: SandboxHandle,
    execution_tag: ExecutionTag,
    mut pid: Option<u32>,
    mut raw: RawEventStream,
    absolute_deadline: tokio::time::Instant,
    idle_timeout: Duration,
    limits: OutputLimits,
    cancellation: CancellationToken,
    ambiguous_on_disconnect: bool,
) -> impl Stream<Item = Result<CommandEvent, E2bError>> + Send {
    async_stream::try_stream! {
        let mut sequence = 0_u64;
        let mut counters = OutputCounters::default();
        let mut stdout = IncrementalUtf8::default();
        let mut stderr = IncrementalUtf8::default();
        let mut pty = IncrementalUtf8::default();
        let mut started_emitted = false;
        loop {
            let now = tokio::time::Instant::now();
            if now >= absolute_deadline {
                client
                    .stop_process(&sandbox, process_selector(pid, &execution_tag), false)
                    .await;
                Err(E2bError::Deadline)?;
            }
            let idle_deadline = now.checked_add(idle_timeout).unwrap_or(absolute_deadline);
            let deadline = idle_deadline.min(absolute_deadline);
            let next = match tokio::select! {
                () = cancellation.cancelled() => RawPollResult::Cancelled,
                result = tokio::time::timeout_at(deadline, raw.next()) => RawPollResult::Event(result),
            } {
                RawPollResult::Cancelled => {
                    client
                        .stop_process(&sandbox, process_selector(pid, &execution_tag), false)
                        .await;
                    Err(E2bError::Cancelled)?;
                    unreachable!()
                }
                RawPollResult::Event(result) => {
                    match result {
                        Ok(value) => value,
                        Err(_) if deadline == absolute_deadline => {
                            client
                                .stop_process(
                                    &sandbox,
                                    process_selector(pid, &execution_tag),
                                    false,
                                )
                                .await;
                            Err(E2bError::Deadline)?;
                            unreachable!()
                        }
                        Err(_) => {
                            client
                                .stop_process(
                                    &sandbox,
                                    process_selector(pid, &execution_tag),
                                    false,
                                )
                                .await;
                            Err(E2bError::Remote {
                                status: 408,
                                code: Some("envd_idle_timeout".to_owned()),
                                message: "envd process stream became idle".to_owned(),
                            })?;
                            unreachable!()
                        }
                    }
                }
            };
            let Some(raw_event) = next else {
                sequence = sequence.saturating_add(1);
                yield CommandEvent::TransportDisconnected { sequence };
                if ambiguous_on_disconnect {
                    Err(E2bError::AmbiguousDelivery {
                        operation: "start_process_stream".to_owned(),
                        execution_tag: Some(execution_tag.to_string()),
                        message: "envd stream ended without a terminal event".to_owned(),
                    })?;
                }
                Err(E2bError::TransportBeforeDelivery {
                    message: "reconnected envd stream ended without a terminal event".to_owned(),
                })?;
                unreachable!()
            };
            let raw_event = match raw_event {
                Ok(event) => event,
                Err(error) => {
                    sequence = sequence.saturating_add(1);
                    yield CommandEvent::TransportDisconnected { sequence };
                    Err(error)?;
                    unreachable!()
                }
            };
            if let RawProcessEvent::Started(process_id) = raw_event {
                if process_id == 0 {
                    Err(E2bError::Protocol(
                        "envd process stream started with PID zero".to_owned(),
                    ))?;
                }
                if let Some(existing) = pid
                    && existing != process_id
                {
                    Err(E2bError::Protocol(format!(
                        "envd stream changed PID from {existing} to {process_id}"
                    )))?;
                }
                pid = Some(process_id);
                if started_emitted {
                    continue;
                }
                started_emitted = true;
                sequence = sequence.saturating_add(1);
                yield CommandEvent::Started {
                    sequence,
                    sandbox_id: sandbox.sandbox_id.clone(),
                    execution_tag: execution_tag.clone(),
                    pid: process_id,
                };
                continue;
            }
            if !started_emitted {
                Err(E2bError::Protocol(
                    "envd process stream did not begin with a start event".to_owned(),
                ))?;
            }
            match raw_event {
                RawProcessEvent::Started(_) => unreachable!("start events are handled above"),
                RawProcessEvent::Stdout(bytes) => {
                    if let Err(error) = counters.observe_stdout(bytes.len(), limits) {
                        client
                            .stop_process(
                                &sandbox,
                                process_selector(pid, &execution_tag),
                                true,
                            )
                            .await;
                        Err(error)?;
                    }
                    let text = stdout.push(&bytes);
                    sequence = sequence.saturating_add(1);
                    yield CommandEvent::Stdout { sequence, bytes, text };
                }
                RawProcessEvent::Stderr(bytes) => {
                    if let Err(error) = counters.observe_stderr(bytes.len(), limits) {
                        client
                            .stop_process(
                                &sandbox,
                                process_selector(pid, &execution_tag),
                                true,
                            )
                            .await;
                        Err(error)?;
                    }
                    let text = stderr.push(&bytes);
                    sequence = sequence.saturating_add(1);
                    yield CommandEvent::Stderr { sequence, bytes, text };
                }
                RawProcessEvent::Pty(bytes) => {
                    if let Err(error) = counters.observe_stdout(bytes.len(), limits) {
                        client
                            .stop_process(
                                &sandbox,
                                process_selector(pid, &execution_tag),
                                true,
                            )
                            .await;
                        Err(error)?;
                    }
                    let text = pty.push(&bytes);
                    sequence = sequence.saturating_add(1);
                    yield CommandEvent::Pty { sequence, bytes, text };
                }
                RawProcessEvent::KeepAlive => {
                    sequence = sequence.saturating_add(1);
                    yield CommandEvent::KeepAlive { sequence };
                }
                RawProcessEvent::Exited { exit_code, exited, status, error } => {
                    for (channel, text) in [
                        (OutputChannel::Stdout, stdout.finish()),
                        (OutputChannel::Stderr, stderr.finish()),
                        (OutputChannel::Pty, pty.finish()),
                    ] {
                        if text.is_empty() {
                            continue;
                        }
                        sequence = sequence.saturating_add(1);
                        let event = match channel {
                            OutputChannel::Stdout => CommandEvent::Stdout {
                                sequence,
                                bytes: Bytes::new(),
                                text,
                            },
                            OutputChannel::Stderr => CommandEvent::Stderr {
                                sequence,
                                bytes: Bytes::new(),
                                text,
                            },
                            OutputChannel::Pty => CommandEvent::Pty {
                                sequence,
                                bytes: Bytes::new(),
                                text,
                            },
                        };
                        yield event;
                    }
                    sequence = sequence.saturating_add(1);
                    yield CommandEvent::Exited {
                        sequence,
                        status: ExitStatus::from_wire(&status, exited, exit_code),
                        exit_code,
                        error,
                    };
                    break;
                }
            }
        }
    }
}

#[derive(Clone, Copy, Debug)]
enum OutputChannel {
    Stdout,
    Stderr,
    Pty,
}

#[derive(Default)]
struct OutputCounters {
    stdout: usize,
    stderr: usize,
    combined: usize,
}

impl OutputCounters {
    fn observe_stdout(&mut self, count: usize, limits: OutputLimits) -> Result<(), E2bError> {
        self.stdout = self.stdout.saturating_add(count);
        self.combined = self.combined.saturating_add(count);
        check_chunk(count, limits.event_bytes)?;
        check_channel("stdout", self.stdout, limits.stdout_bytes)?;
        check_channel("combined", self.combined, limits.combined_bytes)
    }

    fn observe_stderr(&mut self, count: usize, limits: OutputLimits) -> Result<(), E2bError> {
        self.stderr = self.stderr.saturating_add(count);
        self.combined = self.combined.saturating_add(count);
        check_chunk(count, limits.event_bytes)?;
        check_channel("stderr", self.stderr, limits.stderr_bytes)?;
        check_channel("combined", self.combined, limits.combined_bytes)
    }
}

fn check_chunk(observed: usize, limit: usize) -> Result<(), E2bError> {
    check_channel("event", observed, limit)
}

fn check_channel(channel: &'static str, observed: usize, limit: usize) -> Result<(), E2bError> {
    if observed > limit {
        return Err(E2bError::OutputLimitExceeded {
            channel,
            limit_bytes: limit,
            observed_bytes: observed,
        });
    }
    Ok(())
}

#[derive(Default)]
struct IncrementalUtf8 {
    pending: Vec<u8>,
}

impl IncrementalUtf8 {
    fn push(&mut self, chunk: &[u8]) -> String {
        self.pending.extend_from_slice(chunk);
        let mut output = String::new();
        loop {
            match std::str::from_utf8(&self.pending) {
                Ok(text) => {
                    output.push_str(text);
                    self.pending.clear();
                    break;
                }
                Err(error) => {
                    let valid = error.valid_up_to();
                    if valid > 0 {
                        output.push_str(
                            std::str::from_utf8(&self.pending[..valid])
                                .expect("valid_up_to always identifies valid UTF-8"),
                        );
                    }
                    if let Some(invalid) = error.error_len() {
                        output.push('\u{fffd}');
                        self.pending.drain(..valid.saturating_add(invalid));
                    } else {
                        if valid > 0 {
                            self.pending.drain(..valid);
                        }
                        break;
                    }
                }
            }
        }
        output
    }

    fn finish(&mut self) -> String {
        let output = String::from_utf8_lossy(&self.pending).into_owned();
        self.pending.clear();
        output
    }
}

fn selector_to_wire(selector: ProcessSelector) -> process::ProcessSelector {
    let selector = match selector {
        ProcessSelector::Pid(pid) => process::process_selector::Selector::Pid(pid),
        ProcessSelector::Tag(tag) => {
            process::process_selector::Selector::Tag(tag.as_str().to_owned())
        }
    };
    process::ProcessSelector {
        selector: Some(selector),
        ..Default::default()
    }
}

fn process_selector(pid: Option<u32>, execution_tag: &ExecutionTag) -> ProcessSelector {
    pid.map_or_else(
        || ProcessSelector::Tag(execution_tag.clone()),
        ProcessSelector::Pid,
    )
}

fn validate_selector(selector: &ProcessSelector) -> Result<(), E2bError> {
    if matches!(selector, ProcessSelector::Pid(0)) {
        return Err(E2bError::InvalidInput(
            "process PID must be greater than zero".to_owned(),
        ));
    }
    Ok(())
}

fn ambiguous_start_error(error: &connectrpc::ConnectError, tag: &ExecutionTag) -> E2bError {
    match error.code {
        ErrorCode::DeadlineExceeded
        | ErrorCode::Unavailable
        | ErrorCode::Unknown
        | ErrorCode::Internal
        | ErrorCode::DataLoss => E2bError::AmbiguousDelivery {
            operation: "start_process".to_owned(),
            execution_tag: Some(tag.to_string()),
            message: error.to_string(),
        },
        _ => connect_error(error),
    }
}

fn ambiguous_mutation_error(
    error: &connectrpc::ConnectError,
    operation: &str,
    execution_tag: Option<&str>,
) -> E2bError {
    match error.code {
        ErrorCode::DeadlineExceeded
        | ErrorCode::Unavailable
        | ErrorCode::Unknown
        | ErrorCode::Internal
        | ErrorCode::DataLoss => E2bError::AmbiguousDelivery {
            operation: operation.to_owned(),
            execution_tag: execution_tag.map(str::to_owned),
            message: error.to_string(),
        },
        _ => connect_error(error),
    }
}

fn connect_error(error: &connectrpc::ConnectError) -> E2bError {
    let message = error
        .message
        .clone()
        .unwrap_or_else(|| error.code.as_str().to_owned());
    match error.code {
        ErrorCode::Canceled => E2bError::Cancelled,
        ErrorCode::DeadlineExceeded => E2bError::Deadline,
        ErrorCode::NotFound => E2bError::NotFound { resource: message },
        ErrorCode::PermissionDenied | ErrorCode::Unauthenticated => E2bError::Authentication,
        ErrorCode::ResourceExhausted => E2bError::Quota { message },
        ErrorCode::Unavailable => E2bError::RetryableTransport {
            operation: "envd_rpc".to_owned(),
            message,
        },
        ErrorCode::InvalidArgument
        | ErrorCode::AlreadyExists
        | ErrorCode::FailedPrecondition
        | ErrorCode::Aborted
        | ErrorCode::OutOfRange
        | ErrorCode::Unimplemented => E2bError::Remote {
            status: 400,
            code: Some(error.code.as_str().to_owned()),
            message,
        },
        ErrorCode::Unknown | ErrorCode::Internal | ErrorCode::DataLoss => {
            E2bError::Protocol(message)
        }
        _ => E2bError::Protocol(message),
    }
}

fn connect_stream_error(error: &connectrpc::ConnectError) -> E2bError {
    match error.code {
        ErrorCode::Unavailable | ErrorCode::Unknown | ErrorCode::Internal | ErrorCode::DataLoss => {
            E2bError::StreamInterrupted {
                operation: "connect_process".to_owned(),
                message: error.to_string(),
            }
        }
        _ => connect_error(error),
    }
}

#[cfg(test)]
mod tests {
    use std::time::Duration;

    use bytes::Bytes;
    use futures_util::{StreamExt, stream};
    use secrecy::SecretString;
    use serde::Deserialize;
    use tokio_util::sync::CancellationToken;

    use super::{
        EnvdProcessClient, IncrementalUtf8, OutputCounters, RawEventStream, RawProcessEvent,
        normalize_events, process_call_options,
    };
    use crate::control_plane::{ControlPlaneClient, ControlPlaneConfig};
    use crate::error::E2bError;
    use crate::types::{
        CommandEvent, ExecutionTag, ExitStatus, OutputLimits, SandboxHandle, SandboxId, SandboxUser,
    };

    #[derive(Debug, Deserialize)]
    struct StreamRecording {
        commands: Vec<RecordedCommand>,
    }

    #[derive(Debug, Deserialize)]
    struct RecordedCommand {
        name: String,
        execution_tag: String,
        events: Vec<RecordedEvent>,
        #[serde(default)]
        retry_start_safe: Option<bool>,
    }

    #[derive(Debug, Deserialize)]
    struct RecordedEvent {
        kind: String,
        sequence: u64,
        #[serde(default)]
        text: Option<String>,
        #[serde(default)]
        exit_code: Option<i32>,
    }

    #[test]
    fn stdout_and_stderr_decoders_can_be_advanced_independently() {
        let mut stdout = IncrementalUtf8::default();
        let mut stderr = IncrementalUtf8::default();
        assert_eq!(stdout.push(&[0xe2]), "");
        assert_eq!(stderr.push(b"error"), "error");
        assert_eq!(stdout.push(&[0x82, 0xac]), "€");
        assert_eq!(stdout.finish(), "");
    }

    #[test]
    fn incomplete_utf8_is_replaced_only_at_terminal_flush() {
        let mut decoder = IncrementalUtf8::default();
        assert_eq!(decoder.push(&[0xf0, 0x9f]), "");
        assert_eq!(decoder.finish(), "�");
    }

    #[test]
    fn output_bounds_are_applied_during_observation() {
        let limits = OutputLimits {
            stdout_bytes: 2,
            stderr_bytes: 2,
            combined_bytes: 3,
            event_bytes: 2,
            channel_capacity: 1,
        };
        let mut counters = OutputCounters::default();
        counters.observe_stdout(2, limits).expect("at limit");
        let error = counters
            .observe_stderr(2, limits)
            .expect_err("over combined");
        assert!(matches!(
            error,
            E2bError::OutputLimitExceeded {
                channel: "combined",
                ..
            }
        ));
    }

    #[test]
    fn legacy_and_current_exit_forms_are_accepted() {
        assert_eq!(ExitStatus::from_wire("", true, 0), ExitStatus::Exited);
        assert_eq!(
            ExitStatus::from_wire("terminated", false, 143),
            ExitStatus::Signalled
        );
    }

    #[test]
    fn process_user_uses_envd_basic_auth_selection() {
        let root = SandboxUser::root();
        let options = process_call_options(Duration::from_secs(1), Some(&root))
            .expect("valid root selection");
        assert_eq!(
            options
                .headers()
                .get(http::header::AUTHORIZATION)
                .expect("authorization header"),
            "Basic cm9vdDo="
        );
    }

    #[test]
    fn process_client_selects_a_tls_crypto_provider_explicitly() {
        process_client()
            .client(&sandbox(), 64 * 1024)
            .expect("process client should build without a process-global Rustls provider");
    }

    #[tokio::test]
    async fn success_recording_preserves_stream_order_and_terminal_status() {
        let recording = stream_recording();
        let command = recording
            .commands
            .iter()
            .find(|command| command.name == "success_stream")
            .expect("success recording");
        let tag = ExecutionTag::parse(command.execution_tag.clone()).expect("fixture tag");
        let raw: RawEventStream = Box::pin(stream::iter(vec![
            Ok(RawProcessEvent::Started(71)),
            Ok(RawProcessEvent::Stdout(Bytes::from_static(b"working\n"))),
            Ok(RawProcessEvent::Exited {
                exit_code: 0,
                exited: true,
                status: "exited".to_owned(),
                error: None,
            }),
        ]));
        let events = normalize_events(
            process_client(),
            sandbox(),
            tag,
            None,
            raw,
            tokio::time::Instant::now() + Duration::from_secs(2),
            Duration::from_secs(1),
            OutputLimits::default(),
            CancellationToken::new(),
            true,
        )
        .collect::<Vec<_>>()
        .await
        .into_iter()
        .collect::<Result<Vec<_>, _>>()
        .expect("recorded stream normalizes");

        assert_eq!(
            events
                .iter()
                .map(CommandEvent::sequence)
                .collect::<Vec<_>>(),
            command
                .events
                .iter()
                .map(|event| event.sequence)
                .collect::<Vec<_>>()
        );
        assert_eq!(command.events[0].kind, "started");
        assert_eq!(command.events[1].text.as_deref(), Some("working\n"));
        assert!(matches!(events[0], CommandEvent::Started { pid: 71, .. }));
        assert!(matches!(
            &events[1],
            CommandEvent::Stdout { text, .. } if text == "working\n"
        ));
        assert!(matches!(
            events[2],
            CommandEvent::Exited {
                status: ExitStatus::Exited,
                exit_code: 0,
                ..
            }
        ));
        assert_eq!(command.events[2].kind, "completed");
        assert_eq!(command.events[2].exit_code, Some(0));
    }

    #[tokio::test]
    async fn ambiguous_recording_never_marks_start_retry_safe() {
        let recording = stream_recording();
        let command = recording
            .commands
            .iter()
            .find(|command| command.name == "ambiguous_disconnect")
            .expect("ambiguous recording");
        assert_eq!(command.retry_start_safe, Some(false));
        let tag = ExecutionTag::parse(command.execution_tag.clone()).expect("fixture tag");
        let raw: RawEventStream = Box::pin(stream::iter(vec![Err(E2bError::AmbiguousDelivery {
            operation: "start_process_stream".to_owned(),
            execution_tag: Some(tag.to_string()),
            message: "recorded disconnect".to_owned(),
        })]));
        let events = normalize_events(
            process_client(),
            sandbox(),
            tag,
            None,
            raw,
            tokio::time::Instant::now() + Duration::from_secs(2),
            Duration::from_secs(1),
            OutputLimits::default(),
            CancellationToken::new(),
            true,
        )
        .collect::<Vec<_>>()
        .await;
        assert!(matches!(
            events.first(),
            Some(Ok(CommandEvent::TransportDisconnected { sequence: 1 }))
        ));
        assert!(matches!(
            events.get(1),
            Some(Err(E2bError::AmbiguousDelivery { .. }))
        ));
        assert_eq!(command.events[0].kind, "transport_disconnected");
    }

    fn stream_recording() -> StreamRecording {
        serde_json::from_str(include_str!(
            "../../../../contracts/llm/e2b-command-stream.json"
        ))
        .expect("stream recording must be valid")
    }

    fn process_client() -> EnvdProcessClient {
        let config = ControlPlaneConfig::production(SecretString::from("test-key".to_owned()))
            .expect("test config");
        EnvdProcessClient::new(ControlPlaneClient::new(config).expect("test client"))
    }

    fn sandbox() -> SandboxHandle {
        SandboxHandle {
            sandbox_id: SandboxId::parse("fixture-sandbox").expect("sandbox id"),
            template_id: "fixture-template".to_owned(),
            envd_version: "0.6.7".to_owned(),
            sandbox_domain: "e2b.app".to_owned(),
            envd_access_token: None,
            traffic_access_token: None,
        }
    }
}
