//! Canonical model and host validation use an immutable copy of the candidate bytes.
use std::collections::{BTreeMap, BTreeSet};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use newsly_agent_runtime::{
    AgentEventSink, AgentRuntimeError, BoxToolFuture, ToolCall, ToolDefinition, ToolExecutor,
    ToolOutput,
};
use newsly_e2b::{
    CommandRequest, DirectE2bProvider, E2bError, ExecutionTag, OutputLimits, SandboxHandle,
    SandboxUser,
};
use schemars::{JsonSchema, schema_for};
use serde::Deserialize;
use serde_json::{Value, json};
use tokio::time::Instant;
use tokio_util::sync::CancellationToken;
use uuid::Uuid;

use super::LearningDeckAgentError;
use super::candidate::capture_candidate;
use crate::learning_deck::artifacts::drafts::DeckCandidate;
use crate::learning_deck::artifacts::{
    LearningDeckArtifactLimits, validate_learning_deck_artifact,
};
use crate::learning_deck::browser::{hosted_viewer_html, validate_in_browser};
use crate::task_tools::TaskToolExecutor;

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct ValidationArguments {}

#[derive(Debug, Clone)]
pub(super) struct DeckValidator {
    provider: Arc<DirectE2bProvider>,
    sandbox: SandboxHandle,
    model_tools: TaskToolExecutor,
    deadline: Instant,
    cancellation: CancellationToken,
    limits: LearningDeckArtifactLimits,
    failure: Arc<Mutex<Option<LearningDeckAgentError>>>,
}

impl DeckValidator {
    pub(super) fn new(
        provider: Arc<DirectE2bProvider>,
        sandbox: SandboxHandle,
        model_tools: TaskToolExecutor,
        deadline: Instant,
        cancellation: CancellationToken,
        limits: LearningDeckArtifactLimits,
    ) -> Self {
        Self {
            provider,
            sandbox,
            model_tools: model_tools.until(deadline),
            deadline,
            cancellation,
            limits,
            failure: Arc::new(Mutex::new(None)),
        }
    }

    pub(super) fn definition() -> ToolDefinition {
        ToolDefinition {
            name: "validate_deck".to_owned(),
            description: "Validate the current output/index.html, source notes, and local assets using Newsly's exact hosted viewer and portrait/landscape Chromium checks. Fix only reported repairable artifact failures before validating again.".to_owned(),
            input_schema: schema_for!(ValidationArguments),
        }
    }

    /// Preserve typed infrastructure failures across the model tool adapter boundary.
    pub(super) fn take_failure(&self) -> Option<LearningDeckAgentError> {
        self.failure
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .take()
    }

    pub(super) async fn validate_candidate(
        &self,
        candidate: &DeckCandidate,
    ) -> Result<Value, LearningDeckAgentError> {
        candidate
            .check_bounds(&self.limits)
            .map_err(LearningDeckAgentError::from_artifact)?;
        let viewer = hosted_viewer_html(&candidate.index_html);
        validate_learning_deck_artifact(&viewer, &candidate.source_notes, &self.limits)
            .map_err(LearningDeckAgentError::from_artifact)?;

        // An exclusive direct child of sticky /tmp cannot follow an agent-created parent symlink.
        let workspace = format!("/tmp/newsly-deck-validation-{}", Uuid::new_v4());
        self.mkdir(vec!["-m755".to_owned(), "--".to_owned(), workspace.clone()])
            .await?;
        let mut directories = BTreeSet::from([
            format!("{workspace}/input"),
            format!("{workspace}/output"),
            format!("{workspace}/output/assets"),
        ]);
        for asset in &candidate.assets {
            if let Some((parent, _)) = asset.relative_path.rsplit_once('/') {
                directories.insert(format!("{workspace}/output/{parent}"));
            }
        }
        let mut arguments = vec!["-p".to_owned(), "--".to_owned()];
        arguments.extend(directories);
        self.mkdir(arguments).await?;
        let tools = self
            .model_tools
            .until(self.deadline)
            .host_workspace(&workspace)?;
        for asset in &candidate.assets {
            let path = format!("output/{}", asset.relative_path);
            tools
                .write_bytes_typed(&path, asset.bytes.clone())
                .await
                .map_err(|error| LearningDeckAgentError::from_file(error, &path))?;
        }
        let report = validate_in_browser(
            &self.provider,
            &self.sandbox,
            &tools,
            &workspace,
            &viewer,
            self.deadline,
            self.cancellation.child_token(),
        )
        .await
        .map_err(LearningDeckAgentError::from_browser)?;
        Ok(Value::Object(report))
    }

    async fn mkdir(&self, args: Vec<String>) -> Result<(), LearningDeckAgentError> {
        let absolute_deadline = self.deadline.min(Instant::now() + Duration::from_secs(30));
        if absolute_deadline <= Instant::now() {
            return Err(LearningDeckAgentError::E2b(E2bError::Deadline));
        }
        let result = self
            .provider
            .start_process(
                &self.sandbox,
                CommandRequest {
                    command: "/bin/mkdir".to_owned(),
                    args,
                    env: BTreeMap::new(),
                    cwd: None,
                    username: Some(SandboxUser::root()),
                    tag: ExecutionTag::new(),
                    stdin_enabled: false,
                    absolute_deadline,
                    idle_timeout: Duration::from_secs(30),
                    output_limits: OutputLimits {
                        stdout_bytes: 10_000,
                        stderr_bytes: 10_000,
                        combined_bytes: 20_000,
                        event_bytes: 22_000,
                        channel_capacity: 8,
                    },
                },
                self.cancellation.child_token(),
            )
            .await?
            .collect_result()
            .await?;
        if result.exit_code != 0 {
            return Err(LearningDeckAgentError::E2b(E2bError::Protocol(
                "could not create the host validation snapshot".to_owned(),
            )));
        }
        Ok(())
    }
}

/// A model phase cutoff leaves the host's reserved validation time available.
/// Earlier browser process deadlines remain infrastructure failures.
fn deadline_ends_model_phase(
    error: &LearningDeckAgentError,
    deadline: Instant,
    now: Instant,
) -> bool {
    matches!(error, LearningDeckAgentError::E2b(E2bError::Deadline))
        && deadline.saturating_duration_since(now) < Duration::from_secs(30)
}

impl ToolExecutor for DeckValidator {
    fn execute(&self, call: ToolCall, events: Arc<dyn AgentEventSink>) -> BoxToolFuture<'_> {
        Box::pin(async move {
            if call.name != "validate_deck" {
                return self.model_tools.execute(call, events).await;
            }
            if let Err(error) = serde_json::from_value::<ValidationArguments>(call.arguments) {
                return Ok(ToolOutput {
                    content: json!({"status":"failed", "error":format!("validate_deck takes an empty object: {error}"), "repairable":false}),
                    is_error: true,
                });
            }
            let result = async {
                let candidate = capture_candidate(&self.model_tools, &self.limits).await?;
                self.validate_candidate(&candidate).await
            }
            .await;
            match result {
                Ok(report) => Ok(ToolOutput {
                    content: report,
                    is_error: false,
                }),
                Err(error) if error.repairable() => Ok(ToolOutput {
                    content: error.log_payload(),
                    is_error: true,
                }),
                Err(error) if deadline_ends_model_phase(&error, self.deadline, Instant::now()) => {
                    Err(AgentRuntimeError::DeadlineExceeded)
                }
                Err(error) => {
                    let message = error.to_string();
                    let mut failure = self
                        .failure
                        .lock()
                        .unwrap_or_else(std::sync::PoisonError::into_inner);
                    if failure.is_none() {
                        *failure = Some(error);
                    }
                    Err(AgentRuntimeError::Provider(message))
                }
            }
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn canonical_validation_has_no_model_selected_paths_or_flags() {
        assert!(serde_json::from_value::<ValidationArguments>(json!({})).is_ok());
        assert!(
            serde_json::from_value::<ValidationArguments>(json!({"skip_overflow":true})).is_err()
        );
        assert!(serde_json::from_value::<ValidationArguments>(json!(null)).is_err());
        assert_eq!(DeckValidator::definition().name, "validate_deck");
    }

    #[test]
    fn model_cutoff_deadlines_preserve_host_validation_reserve() {
        let now = Instant::now();
        let deadline_error = LearningDeckAgentError::E2b(E2bError::Deadline);
        assert!(!deadline_ends_model_phase(
            &deadline_error,
            now + Duration::from_secs(180),
            now
        ));
        assert!(!deadline_ends_model_phase(
            &deadline_error,
            now + Duration::from_secs(30),
            now
        ));
        assert!(deadline_ends_model_phase(
            &deadline_error,
            now + Duration::from_secs(30) - Duration::from_nanos(1),
            now
        ));
        assert!(deadline_ends_model_phase(&deadline_error, now, now));
        assert!(deadline_ends_model_phase(
            &deadline_error,
            now - Duration::from_secs(1),
            now
        ));
        for error in [
            LearningDeckAgentError::E2b(E2bError::Cancelled),
            LearningDeckAgentError::E2b(E2bError::Protocol("browser failed".to_owned())),
            LearningDeckAgentError::Agent(AgentRuntimeError::DeadlineExceeded),
        ] {
            assert!(!deadline_ends_model_phase(&error, now, now));
        }
    }
}
