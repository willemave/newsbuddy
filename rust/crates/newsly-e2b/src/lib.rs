//! Direct, Newsly-owned E2B control-plane and envd boundary.
//!
//! This crate deliberately has no database dependency. Durable namespace ownership, command
//! leases, and publication fencing are supplied by executable-layer repositories. The transport
//! owns E2B authentication, bounded I/O, `ConnectRPC` process streams, recovery-safe execution tags,
//! and stable error classification.

#![forbid(unsafe_code)]
#![allow(clippy::missing_errors_doc, clippy::missing_panics_doc)]

pub mod bootstrap;
pub mod control_plane;
pub mod envd_process;
pub mod error;
pub mod feed;
mod feed_validation;
pub mod files;
pub mod lifecycle;
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
    CorpusHydrationResult, CorpusTransfer, MAX_CORPUS_ARCHIVE_BYTES, MAX_CORPUS_MANIFEST_BYTES,
    RemoteCorpusState, VM_BOOTSTRAP_EXECUTABLE, VmBootstrapClient, VmBootstrapLimits,
    VmBootstrapProvider, VmCapabilities,
};
pub use control_plane::{ControlPlaneClient, ControlPlaneConfig, SandboxHealth};
pub use envd_process::{
    CapabilityReport, CommandEventStream, EnvdCapability, EnvdProcessClient, ProcessSignal,
};
pub use error::{DeliveryState, E2bError, ErrorDisposition};
pub use feed::{FeedFetchRequest, FeedFetchResult, MAX_FEED_RESPONSE_BYTES, VmFeedProvider};
pub use feed_validation::{FeedValidationError, FeedValidator, ValidatedFeed, ValidatedFeedFormat};
pub use files::{BoxByteStream, EnvdFileClient, FileLimits};
pub use lifecycle::{CommandLeaseState, LifecycleEvent, NamespaceLease, RuntimeOwner};
pub use network::{NetworkPolicy, NetworkRule, NetworkTransform};
pub use session::{
    DirectE2bProvider, RecoveredCommand, ResultManifestLocation, SandboxProvider, SandboxSession,
};
pub use types::{
    CommandEvent, CommandOutput, CommandRequest, CommandResult, ExecutionTag, ExitStatus,
    OutputLimits, ProcessInfo, ProcessSelector, SandboxHandle, SandboxId, SandboxPath,
    SandboxRequest, SandboxUser, SnapshotId, SnapshotInfo, VmNamespace, WorkspacePath,
};
