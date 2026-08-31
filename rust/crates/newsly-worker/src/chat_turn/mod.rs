//! Native execution for the chat queue partition.
//!
//! Both `chat_turn` and `dig_deeper` enter through the same durable protocol: a short `SQLx`
//! preparation transaction creates an immutable snapshot, provider/E2B work runs with no
//! connection checked out, advisory stream state is generation fenced, and the queue kernel
//! applies the terminal transcript inside its exact lease fence.

mod agent;
mod deep_research;
mod events;
mod finalizer;
mod handler;
mod prompts;
mod routing;
mod storage;
mod tools;

pub use agent::{ChatAgentBuildError, ChatAgentConfig, ChatAgentRuntime};
pub use handler::{ChatPartitionHandler, ChatTaskServices};
