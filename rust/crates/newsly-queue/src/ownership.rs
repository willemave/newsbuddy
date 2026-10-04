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

#[derive(Debug, Error, Clone, PartialEq, Eq)]
pub enum ExecutorFenceError {
    #[error("claim runtime scope must include at least one namespace")]
    EmptyNamespaceScope,
}
