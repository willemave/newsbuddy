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
fn system_prompt_is_the_rebuild_method_and_stays_time_independent() {
    let prompt = super::system_prompt().unwrap();

    assert!(prompt.contains("deckkit help"));
    assert!(prompt.contains("Method: rebuild the mechanism"));
    assert!(prompt.contains("output/plan.md"));
    assert!(prompt.contains("The task message states your remaining wall-clock budget"));
    assert!(
        !prompt.contains('$'),
        "unsubstituted placeholder in system prompt"
    );
    assert_eq!(super::system_prompt().unwrap(), prompt);
}

#[test]
fn user_prompt_substitutes_every_placeholder_including_remaining_time() {
    let source = serde_json::json!({"source_title": "Neural Networks Are Already Clean", "source_kind": "article"});
    // User text that looks like a placeholder must stay literal.
    let prompt = super::build_user_prompt(
        source.as_object().unwrap(),
        Some("Focus on SAEs, not $remaining_minutes"),
        12,
    );

    assert!(prompt.contains("Primary source: Neural Networks Are Already Clean"));
    assert!(prompt.contains("User instructions: Focus on SAEs, not $remaining_minutes"));
    assert!(prompt.contains("Remaining wall-clock budget: about 12 minutes"));
    assert!(!prompt.contains("deck-design-brief"));
    assert!(!prompt.replace("$remaining_minutes", "").contains('$'));
}

#[test]
fn missing_deck_kit_is_named_infrastructure_not_a_repair() {
    let error = super::missing_deck_kit();

    assert!(!error.repairable());
    assert!(error.to_string().contains("/opt/deckkit/bin/deckkit"));
}

#[test]
fn deck_kit_skeleton_satisfies_the_publication_contract() {
    use super::super::artifacts::{LearningDeckArtifactLimits, validate_learning_deck_artifact};
    let skeleton = include_str!("../../../../../assets/deckkit/skeleton.html")
        .replace("__TITLE__", "Kit contract");
    let limits = LearningDeckArtifactLimits {
        index_html_bytes: 2_000_000,
        source_notes_bytes: 1_000_000,
        asset_count: 40,
        asset_bytes: 5_000_000,
    };

    validate_learning_deck_artifact(&skeleton, "# Sources\n\n- Kit contract", &limits).unwrap();
}
