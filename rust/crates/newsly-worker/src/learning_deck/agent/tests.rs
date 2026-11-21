use std::time::Duration;

use super::learning_deck_agent_limits;

#[test]
fn learning_deck_generation_has_no_app_level_model_caps() {
    // A live Luna run exhausted the former 4,000-token ceiling while constructing the final
    // write_file call. Both the initial and repair responses then contained neither text nor a
    // complete tool call, so no artifact could be created. Anthropic requires a ceiling, so the
    // limit is the model's own 128K output maximum.
    let limits = learning_deck_agent_limits(37, Duration::from_secs(901));

    assert_eq!(limits.request_limit, None);
    assert_eq!(limits.output_token_limit, Some(128_000));
    assert_eq!(limits.tool_call_limit, 37);
    assert_eq!(limits.deadline, Duration::from_secs(901));
}

#[test]
fn infrastructure_failures_never_become_artifact_repairs() {
    use super::{BrowserValidationError, LearningDeckAgentError, TaskFileError};
    use newsly_e2b::E2bError;
    let expired = LearningDeckAgentError::from_browser(BrowserValidationError::Deadline);
    assert!(matches!(
        expired,
        LearningDeckAgentError::E2b(E2bError::Deadline)
    ));
    assert!(!expired.repairable());
    let missing_vm = LearningDeckAgentError::from_file(
        TaskFileError::Transport(E2bError::NotFound {
            resource: "sandbox".to_owned(),
        }),
        "output/index.html",
    );
    assert!(!missing_vm.repairable());
    assert_eq!(missing_vm.error_type(), "sandbox_execution_failed");
    let invalid = LearningDeckAgentError::from_file(TaskFileError::Utf8, "output/index.html");
    assert!(invalid.repairable());
    let internal = LearningDeckAgentError::from_browser(BrowserValidationError::InvalidOutcome(
        "invalid protocol".to_owned(),
    ));
    assert!(!internal.repairable());
    assert_eq!(internal.error_type(), "validation_infrastructure_failed");
}

#[test]
fn prompts_resolve_every_placeholder_and_state_the_working_time() {
    let source = serde_json::Map::from_iter([
        ("source_title".to_owned(), serde_json::json!("A paper")),
        ("source_kind".to_owned(), serde_json::json!("content")),
    ]);
    let user = super::build_user_prompt(&source, None, Duration::from_secs(719));
    assert!(user.contains("about 11 minutes"));
    assert!(user.contains("Primary source: A paper"));
    let system = super::system_prompt().unwrap();
    let brief = super::design_brief().unwrap();
    assert!(system.contains("scratch/teaching-plan.md"));
    assert!(brief.contains("720 x 1280") && brief.contains("reveal.js@6.0.1"));
    for prompt in [&user, &system, &brief] {
        assert!(!prompt.contains('$'), "unresolved placeholder in prompt");
    }
}
