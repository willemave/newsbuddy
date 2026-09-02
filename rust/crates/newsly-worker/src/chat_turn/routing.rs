use std::collections::BTreeSet;

use newsly_db::AssistantScreenContext;

use super::prompts::{ChatPromptError, assistant_instruction};

const DEFAULT_TOOLS: [&str; 17] = [
    "search_web",
    "find_feed_options",
    "search_knowledge",
    "read_knowledge_item",
    "write_knowledge_items",
    "search_subscription_feeds",
    "search_content",
    "search_news",
    "list_unread_news_items",
    "add_item_to_feed",
    "subscribe_to_feed",
    "save_to_knowledge",
    "remove_from_knowledge",
    "mark_content_read",
    "mark_content_unread",
    "convert_news_to_article_tool",
    "start_deep_research_handoff",
];
const VM_TOOLS: [&str; 5] = [
    "execute_bash",
    "write_file",
    "edit_file",
    "read_file",
    "list_files",
];

#[derive(Debug, Clone)]
pub(super) struct AssistantRoute {
    pub instruction: Option<&'static str>,
    pub allowed_tools: BTreeSet<String>,
}

pub(super) fn route_assistant_turn(
    prompt: &str,
    context: &AssistantScreenContext,
) -> Result<AssistantRoute, ChatPromptError> {
    let normalized = normalize(prompt);
    if context.assistant_action.as_deref() == Some("pick_interesting_unread_news") {
        return route(
            "turn_pick_interesting_unread_news",
            ["list_unread_news_items", "search_web"],
        );
    }
    if weekly_discovery_action(&normalized, context) {
        return route("turn_weekly_discovery_action", ["subscribe_to_feed"]);
    }
    if small_talk(&normalized) {
        return Ok(AssistantRoute {
            instruction: None,
            allowed_tools: BTreeSet::new(),
        });
    }
    if context.screen_type == "learning_deck" {
        if learning_deck_web_request(&normalized) {
            return route("turn_web_search", ["search_web"]);
        }
        return route("turn_learning_deck_grounded", []);
    }
    if feed_finder(&normalized) {
        return default_route("turn_feed_finder", false);
    }
    if markdown_library(&normalized) {
        return default_route("turn_markdown_library", true);
    }
    if content_search(&normalized) {
        return default_route("turn_content_search", false);
    }
    if knowledge_search(&normalized) {
        return default_route("turn_knowledge_search", false);
    }
    if web_request(&normalized) {
        let section = if source_recommendation(&normalized) {
            "turn_source_recommendation"
        } else {
            "turn_web_search"
        };
        return default_route(section, false);
    }
    default_route("turn_default_tool_preference", false)
}

pub(super) fn article_tools() -> BTreeSet<String> {
    VM_TOOLS
        .into_iter()
        .chain([
            "exa_web_search",
            "search_knowledge",
            "read_knowledge_item",
            "write_knowledge_items",
        ])
        .map(str::to_owned)
        .collect()
}

fn route<const N: usize>(
    section: &'static str,
    tools: [&str; N],
) -> Result<AssistantRoute, ChatPromptError> {
    Ok(AssistantRoute {
        instruction: Some(assistant_instruction(section)?),
        allowed_tools: tools.into_iter().map(str::to_owned).collect(),
    })
}

fn default_route(section: &'static str, vm: bool) -> Result<AssistantRoute, ChatPromptError> {
    let mut allowed_tools: BTreeSet<String> =
        DEFAULT_TOOLS.into_iter().map(str::to_owned).collect();
    if vm {
        allowed_tools.extend(VM_TOOLS.into_iter().map(str::to_owned));
    }
    Ok(AssistantRoute {
        instruction: Some(assistant_instruction(section)?),
        allowed_tools,
    })
}

fn normalize(value: &str) -> String {
    value
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .to_ascii_lowercase()
}

fn small_talk(value: &str) -> bool {
    matches!(
        value,
        "" | "hi"
            | "hello"
            | "hey"
            | "yo"
            | "thanks"
            | "thank you"
            | "how are you"
            | "good morning"
            | "good afternoon"
            | "good evening"
            | "hi there"
            | "hello there"
    )
}

fn contains_any(value: &str, needles: &[&str]) -> bool {
    needles.iter().any(|needle| value.contains(needle))
}

fn knowledge_search(value: &str) -> bool {
    if format!(" {value} ").contains(" my ")
        && contains_any(
            value,
            &["favorite", "saved", "bookmarked", "article", "podcast"],
        )
    {
        return true;
    }
    contains_any(
        value,
        &[
            "my favorite",
            "my favourites",
            "my favorites",
            "my saved",
            "my bookmarked",
            "what did i save",
            "what i saved",
            "my article",
            "my podcast",
            "i read",
            "i listened",
            "favorited",
        ],
    )
}

fn content_search(value: &str) -> bool {
    contains_any(
        value,
        &[
            "in my feed",
            "in my inbox",
            "from my feed",
            "from my inbox",
            "my feed",
            "last day's content",
            "recent news items",
            "news items and articles",
            "recent articles",
            "recent posts",
        ],
    )
}

fn markdown_library(value: &str) -> bool {
    if value.is_empty() {
        return false;
    }
    if contains_any(
        value,
        &[
            "markdown",
            "file path",
            "filepath",
            "source md",
            "summary md",
            ".md",
            "saved file",
            "library file",
            "raw markdown",
            "summary markdown",
        ],
    ) {
        return true;
    }
    value.contains("path") && knowledge_search(value)
}

fn feed_finder(value: &str) -> bool {
    if value.contains("http://")
        || value.contains("https://")
        || knowledge_search(value)
        || content_search(value)
    {
        return false;
    }
    contains_any(
        value,
        &[
            "feed",
            "feeds",
            "rss",
            "atom",
            "blog",
            "blogs",
            "newsletter",
            "newsletters",
            "podcast",
            "podcasts",
        ],
    ) && contains_any(
        value,
        &[
            "find",
            "search",
            "look up",
            "discover",
            "recommend",
            "subscribe",
        ],
    )
}

fn source_recommendation(value: &str) -> bool {
    contains_any(
        value,
        &[
            "blogs",
            "blog",
            "publications",
            "publication",
            "newsletters",
            "newsletter",
            "sites",
            "sources",
        ],
    )
}

fn web_request(value: &str) -> bool {
    if knowledge_search(value) || feed_finder(value) || small_talk(value) {
        return false;
    }
    if contains_any(
        value,
        &[
            "latest", "recent", "today", "current", "news", "find", "look up", "search", "who is",
            "what is", "what are", "how to",
        ],
    ) {
        return true;
    }
    value.contains('?')
        && ["what ", "who ", "when ", "where ", "why ", "how "]
            .into_iter()
            .any(|prefix| value.starts_with(prefix))
}

fn learning_deck_web_request(value: &str) -> bool {
    if contains_any(
        value,
        &[
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
            "latest news",
            "recent news",
            "news today",
            "verify online",
            "fact check",
        ],
    ) {
        return true;
    }
    let temporal = ["current slide", "current deck", "current card"]
        .into_iter()
        .fold(value.to_owned(), |value, phrase| value.replace(phrase, ""));
    contains_any(&temporal, &["latest", "recent", "today", "currently"])
}

fn weekly_discovery_action(value: &str, context: &AssistantScreenContext) -> bool {
    context.screen_type == "weekly_discovery"
        && [" add ", " subscribe ", " follow "]
            .into_iter()
            .any(|marker| format!(" {value} ").contains(marker))
}
