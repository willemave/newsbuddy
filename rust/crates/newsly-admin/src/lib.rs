//! Operator commands and server-rendered administrative presentation.

#![forbid(unsafe_code)]

pub mod database;
pub mod e2e;
pub mod evals;
pub mod operator;
mod ownership_manifest;

pub use ownership_manifest::{
    ManifestValidationError, OwnershipPolicyManifest, load_ownership_policy_manifest,
};
