use super::prompts::{ChatPromptError, assistant_instruction};
use super::tools::{ChatTool, HostTool};
use newsly_db::AssistantScreenContext;
use std::collections::BTreeSet;
#[derive(Debug, Clone)]
pub(super) struct AssistantRoute {
    pub instruction: Option<&'static str>,
    pub allowed_tools: BTreeSet<String>,
}
pub(super) fn route_assistant_turn(
    prompt: &str,
    context: &AssistantScreenContext,
) -> Result<AssistantRoute, ChatPromptError> {
    let normalized = prompt.to_ascii_lowercase();
    if context.screen_type == "learning_deck" {
        if learning_deck_web_request(&normalized) {
            return route(
                "turn_web_search",
                &[
                    ChatTool::Host(HostTool::SearchWeb),
                    ChatTool::Host(HostTool::ReadWebPage),
                ],
            );
        }
        return route("turn_learning_deck_grounded", &[]);
    }
    if context.screen_type == "weekly_discovery"
        && [" add ", " subscribe ", " follow "]
            .iter()
            .any(|marker| format!(" {normalized} ").contains(marker))
    {
        return route(
            "turn_weekly_discovery_action",
            &ChatTool::all().collect::<Vec<_>>(),
        );
    }
    route(
        "turn_default_tool_preference",
        &ChatTool::all().collect::<Vec<_>>(),
    )
}
pub(super) fn article_tools() -> BTreeSet<String> {
    ChatTool::all().map(|tool| tool.name().to_owned()).collect()
}
fn route(section: &str, tools: &[ChatTool]) -> Result<AssistantRoute, ChatPromptError> {
    Ok(AssistantRoute {
        instruction: Some(assistant_instruction(section)?),
        allowed_tools: tools.iter().map(|tool| tool.name().to_owned()).collect(),
    })
}
fn learning_deck_web_request(value: &str) -> bool {
    if [
        "search the web",
        "search online",
        "search the internet",
        "look up",
        "online sources",
        "external sources",
        "browse the web",
        "up to date",
        "current developments",
        "current events",
        "current research",
        "current best practice",
        "current status",
        "current version",
        "verify online",
        "fact check",
    ]
    .iter()
    .any(|phrase| value.contains(phrase))
    {
        return true;
    }
    let temporal = ["current slide", "current deck", "current card"]
        .iter()
        .fold(value.to_owned(), |value, phrase| value.replace(phrase, ""));
    ["latest", "recent", "today", "currently"]
        .iter()
        .any(|phrase| temporal.contains(phrase))
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn general_assistant_has_same_canonical_tools_for_every_prompt() {
        let context =
            serde_json::from_value(serde_json::json!({"screen_type":"knowledge"})).unwrap();
        for prompt in [
            "hi",
            "Find a podcast",
            "Write a program",
            "Search my saved articles",
        ] {
            let route = route_assistant_turn(prompt, &context).unwrap();
            assert!(route.allowed_tools.contains("execute_bash"));
            assert!(route.allowed_tools.contains("resolve_source"));
            assert!(!route.allowed_tools.contains("find_feed_options"));
        }
    }
    #[test]
    fn deck_stays_grounded_until_external_request() {
        let context =
            serde_json::from_value(serde_json::json!({"screen_type":"learning_deck"})).unwrap();
        assert!(
            route_assistant_turn("Explain the current slide", &context)
                .unwrap()
                .allowed_tools
                .is_empty()
        );
        let route = route_assistant_turn("Verify online", &context).unwrap();
        assert!(route.allowed_tools.contains("read_web_page"));
        assert!(!route.allowed_tools.contains("execute_bash"));
    }
}
