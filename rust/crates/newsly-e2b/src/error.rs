//! Stable E2B error and delivery classification.

use serde::{Deserialize, Serialize};
use thiserror::Error;

/// Whether an operation is known to have reached E2B.
#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DeliveryState {
    NotDelivered,
    Delivered,
    Unknown,
}

/// Retry semantics independent of any particular queue implementation.
#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ErrorDisposition {
    Retryable,
    Terminal,
    Ambiguous,
}

/// Errors exposed by the Newsly-owned E2B boundary.
#[derive(Debug, Error)]
pub enum E2bError {
    #[error("invalid E2B configuration: {0}")]
    Configuration(String),
    #[error("invalid E2B boundary input: {0}")]
    InvalidInput(String),
    #[error("E2B authentication failed")]
    Authentication,
    #[error("E2B resource was not found: {resource}")]
    NotFound { resource: String },
    #[error("E2B quota or capacity is unavailable: {message}")]
    Quota { message: String },
    #[error("E2B operation reached its absolute deadline")]
    Deadline,
    #[error("E2B operation was cancelled")]
    Cancelled,
    #[error("E2B protocol violation: {0}")]
    Protocol(String),
    #[error("E2B transport failed before delivery: {message}")]
    TransportBeforeDelivery { message: String },
    #[error("retryable E2B {operation} transport failed with unknown delivery: {message}")]
    RetryableTransport { operation: String, message: String },
    #[error("E2B {operation} stream was interrupted: {message}")]
    StreamInterrupted { operation: String, message: String },
    #[error("E2B transport failed after delivery may have occurred: {operation}")]
    AmbiguousDelivery {
        operation: String,
        execution_tag: Option<String>,
        message: String,
    },
    #[error("E2B remote request failed with status {status}: {message}")]
    Remote {
        status: u16,
        code: Option<String>,
        message: String,
    },
    #[error("unsupported envd capability {capability} on version {version}")]
    UnsupportedCapability { capability: String, version: String },
    #[error("{channel} output exceeded {limit_bytes} bytes (observed {observed_bytes})")]
    OutputLimitExceeded {
        channel: &'static str,
        limit_bytes: usize,
        observed_bytes: usize,
    },
    #[error("file transfer exceeded {limit_bytes} bytes (observed {observed_bytes})")]
    FileTooLarge {
        limit_bytes: usize,
        observed_bytes: u64,
    },
    #[error("command stream ended without a terminal event")]
    MissingTerminalEvent,
    #[error("execution {execution_tag} could not be recovered without risking duplicate start")]
    RecoveryUnavailable { execution_tag: String },
    #[error("VM bootstrap {operation} failed with exit code {exit_code}: {message}")]
    VmBootstrapFailed {
        operation: &'static str,
        exit_code: i32,
        message: String,
    },
    #[error("VM template is missing required capabilities: {capabilities}")]
    MissingVmCapabilities { capabilities: String },
}

impl E2bError {
    #[must_use]
    pub fn disposition(&self) -> ErrorDisposition {
        match self {
            Self::AmbiguousDelivery { .. } | Self::RecoveryUnavailable { .. } => {
                ErrorDisposition::Ambiguous
            }
            Self::TransportBeforeDelivery { .. }
            | Self::RetryableTransport { .. }
            | Self::StreamInterrupted { .. }
            | Self::Deadline
            | Self::Quota { .. }
            | Self::Remote {
                status: 408 | 425 | 429 | 500..=599,
                ..
            } => ErrorDisposition::Retryable,
            Self::Configuration(_)
            | Self::InvalidInput(_)
            | Self::Authentication
            | Self::NotFound { .. }
            | Self::Cancelled
            | Self::Protocol(_)
            | Self::Remote { .. }
            | Self::UnsupportedCapability { .. }
            | Self::OutputLimitExceeded { .. }
            | Self::FileTooLarge { .. }
            | Self::MissingTerminalEvent
            | Self::VmBootstrapFailed { .. }
            | Self::MissingVmCapabilities { .. } => ErrorDisposition::Terminal,
        }
    }

    #[must_use]
    pub fn delivery_state(&self) -> DeliveryState {
        match self {
            Self::TransportBeforeDelivery { .. } => DeliveryState::NotDelivered,
            Self::RetryableTransport { .. }
            | Self::AmbiguousDelivery { .. }
            | Self::RecoveryUnavailable { .. } => DeliveryState::Unknown,
            _ => DeliveryState::Delivered,
        }
    }
}
