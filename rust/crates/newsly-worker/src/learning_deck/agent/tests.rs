use std::time::Duration;

use super::learning_deck_agent_limits;

#[test]
fn learning_deck_generation_has_no_app_level_model_caps() {
    // A live Luna run exhausted the former 4,000-token ceiling while constructing the final
    // write_file call. Both the initial and repair responses then contained neither text nor a
    // complete tool call, so no artifact could be created.
    let limits = learning_deck_agent_limits(37, Duration::from_secs(901));

    assert_eq!(limits.request_limit, None);
    assert_eq!(limits.output_token_limit, None);
    assert_eq!(limits.tool_call_limit, 37);
    assert_eq!(limits.deadline, Duration::from_secs(901));
}
