//! Native execution for the chat queue partition.
//!
//! Both `chat_turn` and `dig_deeper` enter through the same durable protocol: a short `SQLx`
//! preparation transaction creates an immutable snapshot, provider/E2B work runs with no
//! connection checked out, advisory stream state is generation fenced, and the queue kernel
//! applies the terminal transcript inside its exact lease fence.

mod agent;
mod events;
mod finalizer;
mod handler;
mod prompts;
mod routing;
mod tools;

pub use agent::{ChatAgentBuildError, ChatAgentConfig, ChatAgentRuntime};
pub use handler::{ChatPartitionHandler, ChatTaskServices};

pub(super) fn usage_source(snapshot: &newsly_db::ChatTaskSnapshot) -> String {
    match snapshot.context.kind {
        newsly_db::ChatTurnKind::Article => "async".to_owned(),
        newsly_db::ChatTurnKind::Assistant => snapshot.context.source.clone(),
        newsly_db::ChatTurnKind::DeepResearch => "deep_research".to_owned(),
    }
}
