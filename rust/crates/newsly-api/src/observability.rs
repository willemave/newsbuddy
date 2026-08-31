use thiserror::Error;
use tracing_subscriber::EnvFilter;

use crate::LogFormat;

/// Installs the process-wide tracing subscriber.
///
/// # Errors
///
/// Returns [`ObservabilityError`] when the filter is invalid or a subscriber is already installed.
pub fn initialize_observability(
    log_filter: &str,
    log_format: LogFormat,
) -> Result<(), ObservabilityError> {
    let filter = EnvFilter::try_new(log_filter).map_err(ObservabilityError::InvalidFilter)?;
    match log_format {
        LogFormat::Json => tracing_subscriber::fmt()
            .with_env_filter(filter)
            .json()
            .with_current_span(true)
            .with_span_list(true)
            .try_init()
            .map_err(ObservabilityError::Install),
        LogFormat::Pretty => tracing_subscriber::fmt()
            .with_env_filter(filter)
            .pretty()
            .try_init()
            .map_err(ObservabilityError::Install),
    }
}

#[derive(Debug, Error)]
pub enum ObservabilityError {
    #[error("RUST_LOG contains an invalid tracing filter")]
    InvalidFilter(#[source] tracing_subscriber::filter::ParseError),
    #[error("failed to install the tracing subscriber")]
    Install(#[source] Box<dyn std::error::Error + Send + Sync>),
}
