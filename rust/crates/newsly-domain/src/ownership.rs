use std::fmt::{self, Display, Formatter};
use std::str::FromStr;

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use thiserror::Error;

const MAX_RESOURCE_KEY_LENGTH: usize = 255;
const MAX_REPLICA_ID_LENGTH: usize = 255;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RuntimeOwner {
    Python,
    Rust,
}

impl RuntimeOwner {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Python => "python",
            Self::Rust => "rust",
        }
    }
}

impl Display for RuntimeOwner {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.as_str())
    }
}

impl FromStr for RuntimeOwner {
    type Err = InvalidOwnershipValue;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        match value {
            "python" => Ok(Self::Python),
            "rust" => Ok(Self::Rust),
            _ => Err(InvalidOwnershipValue::RuntimeOwner(value.to_owned())),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ResourceKind {
    RouteGroup,
    TaskType,
    StateWriter,
}

impl ResourceKind {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::RouteGroup => "route_group",
            Self::TaskType => "task_type",
            Self::StateWriter => "state_writer",
        }
    }
}

impl Display for ResourceKind {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.as_str())
    }
}

impl FromStr for ResourceKind {
    type Err = InvalidOwnershipValue;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        match value {
            "route_group" => Ok(Self::RouteGroup),
            "task_type" => Ok(Self::TaskType),
            "state_writer" => Ok(Self::StateWriter),
            _ => Err(InvalidOwnershipValue::ResourceKind(value.to_owned())),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TransitionState {
    Active,
    Preparing,
}

impl TransitionState {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Active => "active",
            Self::Preparing => "preparing",
        }
    }
}

impl FromStr for TransitionState {
    type Err = InvalidOwnershipValue;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        match value {
            "active" => Ok(Self::Active),
            "preparing" => Ok(Self::Preparing),
            _ => Err(InvalidOwnershipValue::TransitionState(value.to_owned())),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ReadinessState {
    Loaded,
    WriteBarrier,
    Ready,
}

impl ReadinessState {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Loaded => "loaded",
            Self::WriteBarrier => "write_barrier",
            Self::Ready => "ready",
        }
    }
}

impl FromStr for ReadinessState {
    type Err = InvalidOwnershipValue;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        match value {
            "loaded" => Ok(Self::Loaded),
            "write_barrier" => Ok(Self::WriteBarrier),
            "ready" => Ok(Self::Ready),
            _ => Err(InvalidOwnershipValue::ReadinessState(value.to_owned())),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TransitionIntent {
    Cutover,
    Rollback,
}

impl TransitionIntent {
    pub const fn prepare_audit_action(self) -> &'static str {
        match self {
            Self::Cutover => "prepare",
            Self::Rollback => "rollback_prepare",
        }
    }

    pub const fn promotion_audit_action(self) -> &'static str {
        match self {
            Self::Cutover => "promote",
            Self::Rollback => "rollback",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(transparent)]
pub struct OwnershipVersion(i64);

impl OwnershipVersion {
    /// Builds a positive durable ownership version.
    ///
    /// # Errors
    ///
    /// Returns [`InvalidOwnershipValue::Version`] for zero or negative values.
    pub fn new(value: i64) -> Result<Self, InvalidOwnershipValue> {
        if value <= 0 {
            return Err(InvalidOwnershipValue::Version(value));
        }
        Ok(Self(value))
    }

    pub const fn get(self) -> i64 {
        self.0
    }

    /// Returns the next ownership version without wrapping.
    ///
    /// # Errors
    ///
    /// Returns [`InvalidOwnershipValue::VersionOverflow`] at `i64::MAX`.
    pub fn next(self) -> Result<Self, InvalidOwnershipValue> {
        self.0
            .checked_add(1)
            .map(Self)
            .ok_or(InvalidOwnershipValue::VersionOverflow)
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(transparent)]
pub struct ResourceKey(String);

impl ResourceKey {
    /// Validates a durable ownership resource key.
    ///
    /// # Errors
    ///
    /// Returns [`InvalidOwnershipValue::ResourceKey`] when the value is empty or too long.
    pub fn new(value: impl Into<String>) -> Result<Self, InvalidOwnershipValue> {
        let value = value.into();
        if value.trim().is_empty() || value.len() > MAX_RESOURCE_KEY_LENGTH {
            return Err(InvalidOwnershipValue::ResourceKey(value));
        }
        Ok(Self(value))
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl Display for ResourceKey {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.0)
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(transparent)]
pub struct ReplicaId(String);

impl ReplicaId {
    /// Validates a gateway replica identifier.
    ///
    /// # Errors
    ///
    /// Returns [`InvalidOwnershipValue::ReplicaId`] when the value is empty or too long.
    pub fn new(value: impl Into<String>) -> Result<Self, InvalidOwnershipValue> {
        let value = value.into();
        if value.trim().is_empty() || value.len() > MAX_REPLICA_ID_LENGTH {
            return Err(InvalidOwnershipValue::ReplicaId(value));
        }
        Ok(Self(value))
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(transparent)]
pub struct ApplicationSha(String);

impl ApplicationSha {
    /// Validates a full Git SHA-1 or SHA-256 identifier.
    ///
    /// # Errors
    ///
    /// Returns [`InvalidOwnershipValue::ApplicationSha`] for abbreviated or non-hex values.
    pub fn new(value: impl Into<String>) -> Result<Self, InvalidOwnershipValue> {
        let value = value.into();
        if !matches!(value.len(), 40 | 64) || !value.bytes().all(|byte| byte.is_ascii_hexdigit()) {
            return Err(InvalidOwnershipValue::ApplicationSha(value));
        }
        Ok(Self(value.to_ascii_lowercase()))
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct OwnershipTarget {
    pub resource_kind: ResourceKind,
    pub resource_key: ResourceKey,
    pub expected_owner: RuntimeOwner,
    pub expected_version: OwnershipVersion,
    pub desired_owner: RuntimeOwner,
}

impl OwnershipTarget {
    /// Builds a compare-and-set ownership target.
    ///
    /// # Errors
    ///
    /// Returns [`InvalidOwnershipValue::SameOwnerTransition`] if it would not change owner.
    pub fn new(
        resource_kind: ResourceKind,
        resource_key: ResourceKey,
        expected_owner: RuntimeOwner,
        expected_version: OwnershipVersion,
        desired_owner: RuntimeOwner,
    ) -> Result<Self, InvalidOwnershipValue> {
        if expected_owner == desired_owner {
            return Err(InvalidOwnershipValue::SameOwnerTransition(expected_owner));
        }
        Ok(Self {
            resource_kind,
            resource_key,
            expected_owner,
            expected_version,
            desired_owner,
        })
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct OwnershipRecord {
    pub resource_kind: ResourceKind,
    pub resource_key: ResourceKey,
    pub active_owner: RuntimeOwner,
    pub active_version: OwnershipVersion,
    pub desired_owner: Option<RuntimeOwner>,
    pub desired_version: Option<OwnershipVersion>,
    pub transition_state: TransitionState,
    pub transition_started_at: Option<DateTime<Utc>>,
    pub updated_at: DateTime<Utc>,
    pub updated_by: String,
    pub reason: String,
}

#[derive(Debug, Error, Clone, PartialEq, Eq)]
pub enum InvalidOwnershipValue {
    #[error("unknown runtime owner {0:?}")]
    RuntimeOwner(String),
    #[error("unknown ownership resource kind {0:?}")]
    ResourceKind(String),
    #[error("unknown ownership transition state {0:?}")]
    TransitionState(String),
    #[error("unknown ownership readiness state {0:?}")]
    ReadinessState(String),
    #[error("ownership version must be positive, got {0}")]
    Version(i64),
    #[error("ownership version overflow")]
    VersionOverflow,
    #[error("invalid ownership resource key {0:?}")]
    ResourceKey(String),
    #[error("invalid ownership replica id {0:?}")]
    ReplicaId(String),
    #[error("application SHA must be a full 40- or 64-character hexadecimal digest")]
    ApplicationSha(String),
    #[error("ownership transition must change the active owner from {0}")]
    SameOwnerTransition(RuntimeOwner),
}

#[cfg(test)]
mod tests {
    use super::{ApplicationSha, OwnershipVersion, ReadinessState, RuntimeOwner};

    #[test]
    fn readiness_states_are_monotonic() {
        assert!(ReadinessState::Loaded < ReadinessState::WriteBarrier);
        assert!(ReadinessState::WriteBarrier < ReadinessState::Ready);
    }

    #[test]
    fn versions_are_positive_and_checked() {
        assert!(OwnershipVersion::new(0).is_err());
        assert_eq!(OwnershipVersion::new(1).unwrap().next().unwrap().get(), 2);
    }

    #[test]
    fn application_sha_rejects_abbreviations() {
        assert!(ApplicationSha::new("c77aa869").is_err());
        assert!(ApplicationSha::new("a".repeat(40)).is_ok());
    }

    #[test]
    fn runtime_owner_round_trips() {
        assert_eq!(
            "python".parse::<RuntimeOwner>().unwrap(),
            RuntimeOwner::Python
        );
        assert_eq!(RuntimeOwner::Rust.to_string(), "rust");
    }
}
