//! Direct, Newsly-owned E2B control-plane and envd boundary.
//!
//! This crate deliberately has no database dependency. The transport owns E2B authentication,
//! bounded I/O, `ConnectRPC` process streams, recovery-safe execution tags, and stable error
//! classification for disposable task sandboxes.

#![forbid(unsafe_code)]
#![allow(clippy::missing_errors_doc, clippy::missing_panics_doc)]

pub mod bootstrap;
pub mod control_plane;
pub mod envd_process;
pub mod error;
pub mod files;
pub mod network;
pub mod session;
pub mod types;

#[doc(hidden)]
#[allow(
    missing_debug_implementations,
    clippy::all,
    clippy::pedantic,
    clippy::nursery
)]
pub mod generated {
    connectrpc::include_generated!();
}

pub use bootstrap::{
    VM_BOOTSTRAP_EXECUTABLE, VmBootstrapClient, VmBootstrapLimits, VmCapabilities,
};
pub use control_plane::{ControlPlaneClient, ControlPlaneConfig, SandboxHealth};
pub use envd_process::{
    CapabilityReport, CommandEventStream, EnvdCapability, EnvdProcessClient, ProcessSignal,
};
pub use error::{DeliveryState, E2bError, ErrorDisposition};
pub use files::{BoxByteStream, EnvdFileClient, FileLimits};
pub use network::NetworkPolicy;
pub use session::DirectE2bProvider;
pub use types::{
    CommandEvent, CommandOutput, CommandRequest, CommandResult, ExecutionTag, ExitStatus,
    OutputLimits, ProcessSelector, SandboxHandle, SandboxId, SandboxInfo, SandboxPath,
    SandboxRequest, SandboxUser, WorkspacePath,
};
