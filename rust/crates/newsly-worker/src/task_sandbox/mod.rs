mod lifecycle;
mod setup;

pub use lifecycle::{AcquiredTaskSandbox, TaskSandboxConfig, TaskSandboxError, TaskSandboxOwner};
pub(crate) use setup::{
    SandboxCommand, agent_network_policy, allowed_tools, direct_provider, exa_endpoint,
    run_command, secret_env, secret_env_alias,
};
