//! Typed facts recorded only after the product transaction commits.
use super::canonical::Reference;
use super::{
    AggregatorKey, AssistantFeedOption, ChatToolExecutor, canonical_direct_feed_url, valid_url,
};
use newsly_db::FeedSubscriptionMutation;

#[derive(Debug, Clone)]
pub(super) enum ResolvedSource {
    Feed {
        url: String,
        title: Option<String>,
        kind: Option<newsly_providers::SourceKind>,
        reference: Option<newsly_db::FeedSubscriptionReference>,
    },
    Aggregator(AggregatorKey),
}
impl ResolvedSource {
    fn label(&self) -> String {
        match self {
            Self::Aggregator(key) => key.display_name().to_owned(),
            Self::Feed { url, title, .. } => link(title.as_deref().unwrap_or("this source"), url),
        }
    }
    fn url(&self) -> String {
        match self {
            Self::Feed { url, .. } => url.clone(),
            Self::Aggregator(key) => format!("aggregator://{}", key.as_str()),
        }
    }
}
#[derive(Debug, Clone, Copy)]
pub(super) enum StateChange {
    Read(bool),
    Saved(bool),
}
#[derive(Debug, Clone)]
pub(super) enum ConfirmedAction {
    Inbox {
        url: String,
        already_exists: bool,
    },
    Subscribed {
        source: ResolvedSource,
        mutation: FeedSubscriptionMutation,
    },
    StateChanged {
        reference: Reference,
        change: StateChange,
    },
}
impl ChatToolExecutor {
    pub(in crate::chat_turn) fn fallback_summary(&self) -> Option<String> {
        fallback_summary(
            &self
                .completed_actions
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner),
        )
    }
    pub(super) fn record_action(&self, action: ConfirmedAction) {
        if let ConfirmedAction::Subscribed { source, .. } = &action {
            mark_subscribed_options(
                &mut self
                    .feed_options
                    .lock()
                    .unwrap_or_else(std::sync::PoisonError::into_inner),
                source,
            );
        }
        self.read_cache
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .clear();
        self.completed_actions
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .push(action);
    }
}
pub(super) fn mark_subscribed_options(
    options: &mut [AssistantFeedOption],
    source: &ResolvedSource,
) {
    let canonical = canonical_direct_feed_url(&source.url());
    for option in options {
        if canonical_direct_feed_url(&option.feed_url) == canonical {
            option.is_subscribed = true;
        }
    }
}
pub(super) fn fallback_summary(actions: &[ConfirmedAction]) -> Option<String> {
    if actions.is_empty() {
        return None;
    }
    Some(format!(
        "Completed actions before the response stopped:\n{}",
        actions
            .iter()
            .map(|action| format!("- {}", human_action_summary(action)))
            .collect::<Vec<_>>()
            .join("\n")
    ))
}
fn link(label: &str, url: &str) -> String {
    let label = label
        .replace('\\', "\\\\")
        .replace('[', "\\[")
        .replace(']', "\\]");
    valid_url(url).map_or_else(|_| label.clone(), |url| format!("[{label}]({url})"))
}
fn human_action_summary(action: &ConfirmedAction) -> String {
    match action {
        ConfirmedAction::Inbox {
            url,
            already_exists: true,
        } => format!("{} was already in your inbox.", link("this item", url)),
        ConfirmedAction::Inbox {
            url,
            already_exists: false,
        } => format!("Added {} to your inbox.", link("this item", url)),
        ConfirmedAction::Subscribed { source, mutation } => {
            let label = source.label();
            match mutation {
                FeedSubscriptionMutation::AlreadySubscribed => {
                    format!("You already follow {label}.")
                }
                FeedSubscriptionMutation::TopicsUpdated => {
                    format!("Updated the topic filter for {label}.")
                }
                FeedSubscriptionMutation::Created | FeedSubscriptionMutation::Reactivated => {
                    format!("You now follow {label}.")
                }
            }
        }
        ConfirmedAction::StateChanged { reference, change } => {
            let label = match reference {
                Reference::Content { .. } => "the item",
                Reference::News { .. } => "the News item",
            };
            match change {
                StateChange::Read(true) => format!("Marked {label} as read."),
                StateChange::Read(false) => format!("Marked {label} as unread."),
                StateChange::Saved(true) => format!("Saved {label} to Knowledge."),
                StateChange::Saved(false) => format!("Removed {label} from Knowledge."),
            }
        }
    }
}
