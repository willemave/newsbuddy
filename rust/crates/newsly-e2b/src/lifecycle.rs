//! Database-independent lifecycle and lease boundary types.

use std::time::SystemTime;

use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::error::E2bError;
use crate::types::{SandboxId, VmNamespace};

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RuntimeOwner {
    Python,
    Rust,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CommandLeaseState {
    Idle,
    Active { count: u32 },
}

impl CommandLeaseState {
    pub fn require_idle(self) -> Result<(), E2bError> {
        match self {
            Self::Idle => Ok(()),
            Self::Active { count } => Err(E2bError::ActiveCommandLease {
                active_commands: count,
            }),
        }
    }
}

/// A durable namespace lease supplied by the executable's lifecycle repository.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct NamespaceLease {
    pub namespace: VmNamespace,
    pub runtime_owner: RuntimeOwner,
    pub ownership_version: i64,
    pub token: Uuid,
    pub expires_at: SystemTime,
    pub template_revision: String,
}

impl NamespaceLease {
    pub fn validate(&self) -> Result<(), E2bError> {
        if self.ownership_version < 1 {
            return Err(E2bError::InvalidInput(
                "VM namespace ownership version must be positive".to_owned(),
            ));
        }
        if self.token.is_nil() {
            return Err(E2bError::InvalidInput(
                "VM namespace lease token must be non-nil".to_owned(),
            ));
        }
        if self.template_revision.trim().is_empty()
            || self.template_revision.trim() != self.template_revision
            || self.template_revision.len() > 256
        {
            return Err(E2bError::InvalidInput(
                "VM namespace template revision must be non-empty, unpadded, and at most 256 bytes"
                    .to_owned(),
            ));
        }
        Ok(())
    }

    pub fn require_rust_owner(&self) -> Result<(), E2bError> {
        if self.runtime_owner == RuntimeOwner::Rust {
            Ok(())
        } else {
            Err(E2bError::NamespaceOwnership {
                namespace: self.namespace.to_string(),
                owner: "python".to_owned(),
            })
        }
    }

    pub fn require_active(&self, now: SystemTime) -> Result<(), E2bError> {
        self.validate()?;
        self.require_rust_owner()?;
        if self.expires_at <= now {
            return Err(E2bError::NamespaceLeaseExpired {
                namespace: self.namespace.to_string(),
            });
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(tag = "event", rename_all = "snake_case")]
pub enum LifecycleEvent {
    Acquired {
        provider: String,
        reuse_scope: String,
        reused: bool,
        sandbox_id: SandboxId,
        template_revision: String,
        vm_namespace: VmNamespace,
    },
    CorpusInstalled {
        revision: u64,
        manifest_written_last: bool,
    },
    Released {
        sandbox_retained: bool,
    },
}

#[cfg(test)]
mod tests {
    use super::LifecycleEvent;

    #[test]
    fn lifecycle_recordings_remain_decodable() {
        let fixture: serde_json::Value = serde_json::from_str(include_str!(
            "../../../../contracts/llm/e2b-command-stream.json"
        ))
        .expect("fixture must be valid JSON");
        for event in fixture["lifecycle"].as_array().expect("lifecycle") {
            serde_json::from_value::<LifecycleEvent>(event.clone())
                .expect("lifecycle recording must fit the Rust boundary");
        }
    }
}
