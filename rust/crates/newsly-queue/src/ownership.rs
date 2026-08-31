use std::collections::BTreeSet;

use newsly_domain::{OwnershipVersion, ResourceKey, RuntimeOwner};
use serde::{Deserialize, Serialize};
use thiserror::Error;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TaskExecutorStamp {
    pub runtime: RuntimeOwner,
    pub ownership_version: OwnershipVersion,
    pub namespace: ResourceKey,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ClaimRuntimeScope {
    runtime: RuntimeOwner,
    namespaces: Option<BTreeSet<String>>,
}

impl ClaimRuntimeScope {
    pub const fn all_namespaces(runtime: RuntimeOwner) -> Self {
        Self {
            runtime,
            namespaces: None,
        }
    }

    /// Restricts a worker to explicit task ownership namespaces.
    ///
    /// # Errors
    ///
    /// Returns an empty-scope error for an empty set.
    pub fn namespaces(
        runtime: RuntimeOwner,
        namespaces: impl IntoIterator<Item = ResourceKey>,
    ) -> Result<Self, ExecutorFenceError> {
        let namespaces = namespaces
            .into_iter()
            .map(|namespace| namespace.to_string())
            .collect::<BTreeSet<_>>();
        if namespaces.is_empty() {
            return Err(ExecutorFenceError::EmptyNamespaceScope);
        }
        Ok(Self {
            runtime,
            namespaces: Some(namespaces),
        })
    }

    pub fn allows(&self, stamp: &TaskExecutorStamp) -> bool {
        if self.runtime != stamp.runtime {
            return false;
        }
        self.namespaces
            .as_ref()
            .is_none_or(|namespaces| namespaces.contains(stamp.namespace.as_str()))
    }

    pub const fn runtime(&self) -> RuntimeOwner {
        self.runtime
    }

    pub fn namespace_values(&self) -> Option<Vec<&str>> {
        self.namespaces
            .as_ref()
            .map(|namespaces| namespaces.iter().map(String::as_str).collect())
    }
}

/// Verifies the immutable executor stamp copied into a claim before renewal or finalization.
///
/// # Errors
///
/// Returns a stamp-mismatch error if any durable executor component changed.
pub fn verify_executor_fence(
    claimed: &TaskExecutorStamp,
    durable: &TaskExecutorStamp,
) -> Result<(), ExecutorFenceError> {
    if claimed != durable {
        return Err(ExecutorFenceError::StampMismatch {
            claimed: claimed.clone(),
            durable: durable.clone(),
        });
    }
    Ok(())
}

#[derive(Debug, Error, Clone, PartialEq, Eq)]
pub enum ExecutorFenceError {
    #[error("claim runtime scope must include at least one namespace")]
    EmptyNamespaceScope,
    #[error("task executor stamp changed after claim")]
    StampMismatch {
        claimed: TaskExecutorStamp,
        durable: TaskExecutorStamp,
    },
}

#[cfg(test)]
mod tests {
    use newsly_domain::{OwnershipVersion, ResourceKey, RuntimeOwner};

    use super::{ClaimRuntimeScope, TaskExecutorStamp, verify_executor_fence};

    fn stamp(runtime: RuntimeOwner, version: i64) -> TaskExecutorStamp {
        TaskExecutorStamp {
            runtime,
            ownership_version: OwnershipVersion::new(version).unwrap(),
            namespace: ResourceKey::new("run_llm_task").unwrap(),
        }
    }

    #[test]
    fn runtime_scope_never_claims_the_other_runtime() {
        let python = ClaimRuntimeScope::all_namespaces(RuntimeOwner::Python);
        assert!(python.allows(&stamp(RuntimeOwner::Python, 1)));
        assert!(!python.allows(&stamp(RuntimeOwner::Rust, 1)));
    }

    #[test]
    fn finalization_rejects_version_changes() {
        assert!(
            verify_executor_fence(
                &stamp(RuntimeOwner::Python, 1),
                &stamp(RuntimeOwner::Python, 2),
            )
            .is_err()
        );
    }
}
