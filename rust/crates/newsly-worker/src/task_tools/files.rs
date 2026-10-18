//! Host-side file operations preserve transport errors; model tools map them at their boundary.
use super::{BoxByteStream, E2bError, SandboxPath, TaskToolExecutor, WorkspacePath};
use futures_util::{StreamExt as _, stream};
use thiserror::Error;
use tokio::time::Instant;

#[derive(Debug, Error)]
pub(crate) enum TaskFileError {
    #[error(transparent)]
    Transport(#[from] E2bError),
    #[error("file exceeds the {0}-byte read limit")]
    TooLarge(usize),
    #[error("file is not valid UTF-8")]
    Utf8,
}

impl TaskToolExecutor {
    /// A phase executor shares the sandbox but cannot outlive the host executor's deadline.
    pub(crate) fn until(&self, deadline: Instant) -> Self {
        let mut tools = self.clone();
        tools.deadline = deadline.min(self.deadline);
        tools
    }

    /// Host snapshots live outside the model-owned workspace and are read-only to its user.
    pub(crate) fn host_workspace(&self, path: &str) -> Result<Self, E2bError> {
        let mut tools = self.clone();
        tools.workspace_root = SandboxPath::parse(path.to_owned())?;
        tools.sandbox_user = newsly_e2b::SandboxUser::root();
        Ok(tools)
    }

    pub(crate) async fn read_text_typed(
        &self,
        relative: &str,
        maximum: usize,
    ) -> Result<String, TaskFileError> {
        String::from_utf8(self.read_bytes_typed(relative, maximum).await?)
            .map_err(|_| TaskFileError::Utf8)
    }

    pub(crate) async fn read_bytes_typed(
        &self,
        relative: &str,
        maximum: usize,
    ) -> Result<Vec<u8>, TaskFileError> {
        let path = self.typed_path(relative)?;
        let remaining = self.typed_remaining()?;
        let download = self.provider.file_client().download_sandbox_path(
            &self.sandbox,
            &path,
            self.sandbox_user.as_str(),
            remaining,
        );
        let operation = async {
            let mut stream = download.await?;
            let mut bytes = Vec::new();
            while let Some(chunk) = stream.next().await {
                let chunk = chunk?;
                if bytes.len().saturating_add(chunk.len()) > maximum {
                    return Err(TaskFileError::TooLarge(maximum));
                }
                bytes.extend_from_slice(&chunk);
            }
            Ok(bytes)
        };
        tokio::select! {
            () = self.cancellation.cancelled() => Err(E2bError::Cancelled.into()),
            result = tokio::time::timeout_at(self.deadline, operation) => {
                result.unwrap_or_else(|_| Err(E2bError::Deadline.into()))
            }
        }
    }

    pub(crate) async fn write_bytes_typed(
        &self,
        relative: &str,
        bytes: Vec<u8>,
    ) -> Result<(), TaskFileError> {
        let path = self.typed_path(relative)?;
        let remaining = self.typed_remaining()?;
        let length = u64::try_from(bytes.len()).unwrap_or(u64::MAX);
        let source: BoxByteStream = Box::pin(stream::once(async move { Ok(bytes.into()) }));
        let upload = self.provider.file_client().upload_sandbox_path(
            &self.sandbox,
            &path,
            self.sandbox_user.as_str(),
            length,
            source,
            remaining,
        );
        tokio::select! {
            () = self.cancellation.cancelled() => Err(E2bError::Cancelled.into()),
            result = tokio::time::timeout_at(self.deadline, upload) => {
                result.map_err(|_| E2bError::Deadline)?.map_err(TaskFileError::from)
            }
        }
    }

    /// Host housekeeping keeps process/transport errors typed instead of model-facing strings.
    pub(crate) async fn run_host_command(
        &self,
        command: &str,
    ) -> Result<(String, i32), TaskFileError> {
        use newsly_e2b::{CommandRequest, ExecutionTag, OutputLimits};
        use std::{collections::BTreeMap, time::Duration};
        self.typed_remaining()?;
        let deadline = self.deadline.min(Instant::now() + Duration::from_secs(30));
        let operation = async {
            let result = self
                .provider
                .start_process(
                    &self.sandbox,
                    CommandRequest {
                        command: "/bin/bash".to_owned(),
                        args: vec!["-lc".to_owned(), command.to_owned()],
                        env: BTreeMap::new(),
                        cwd: Some(self.workspace_root.as_str().to_owned()),
                        username: Some(self.sandbox_user.clone()),
                        tag: ExecutionTag::new(),
                        stdin_enabled: false,
                        absolute_deadline: deadline,
                        idle_timeout: Duration::from_secs(30),
                        output_limits: OutputLimits {
                            stdout_bytes: 200_000,
                            stderr_bytes: 100_000,
                            combined_bytes: 250_000,
                            event_bytes: 220_000,
                            channel_capacity: 32,
                        },
                    },
                    self.cancellation.child_token(),
                )
                .await?
                .collect_result()
                .await?;
            if !matches!(
                result.status,
                newsly_e2b::ExitStatus::Exited | newsly_e2b::ExitStatus::Failed
            ) {
                return Err(E2bError::Protocol(format!(
                    "host process did not exit normally: {:?}",
                    result.status
                ))
                .into());
            }
            Ok((result.output.stdout, result.exit_code))
        };
        tokio::select! {
            () = self.cancellation.cancelled() => Err(E2bError::Cancelled.into()),
            result = tokio::time::timeout_at(deadline, operation) => {
                result.unwrap_or_else(|_| Err(E2bError::Deadline.into()))
            }
        }
    }

    fn typed_path(&self, relative: &str) -> Result<SandboxPath, TaskFileError> {
        let relative = WorkspacePath::parse(relative.trim().to_owned())?;
        Ok(SandboxPath::parse(format!(
            "{}/{}",
            self.workspace_root.as_str(),
            relative.as_str()
        ))?)
    }

    fn typed_remaining(&self) -> Result<std::time::Duration, TaskFileError> {
        if self.cancellation.is_cancelled() {
            return Err(E2bError::Cancelled.into());
        }
        self.deadline
            .checked_duration_since(Instant::now())
            .filter(|remaining| !remaining.is_zero())
            .ok_or_else(|| E2bError::Deadline.into())
    }
}
