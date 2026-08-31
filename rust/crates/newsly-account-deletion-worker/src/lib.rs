//! Dedicated `DELETE_USER_ACCOUNT` executor.
//!
//! The handler deliberately remains separate from the content worker. It prepares an immutable
//! cleanup plan in a short transaction, performs idempotent external cleanup without a database
//! connection, and publishes all database deletion in the queue kernel's lease-fenced finalizer.

#![forbid(unsafe_code)]

mod config;
mod external;
mod handler;
mod registry;
mod repository;

pub use config::{
    AccountDeletionProcessConfig, ArtifactStorageConfig, ProcessConfigError, WorkerLogFormat,
};
pub use external::{
    AccountExternalServices, AgentVmDestroyer, ConfiguredArtifactStore, DirectAgentVmDestroyer,
    ObjectArtifactStore, ReqwestXGrantRevoker, UnavailableXGrantRevoker, XGrantRevoker,
};
pub use handler::{AccountDeletionHandler, AccountDeletionServices};
pub use registry::{USER_OWNED_RELATIONS, UserOwnedRelation};
