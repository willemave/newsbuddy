use std::path::PathBuf;

/// Stable local failures emitted by the credential-free VM helper.
#[derive(Debug, thiserror::Error)]
pub enum BootstrapError {
    #[error("invalid input: {0}")]
    InvalidInput(String),

    #[error("invalid corpus archive: {0}")]
    InvalidArchive(String),

    #[error("{operation} failed for {path}: {source}")]
    Io {
        operation: &'static str,
        path: PathBuf,
        #[source]
        source: std::io::Error,
    },

    #[error("JSON {operation} failed: {source}")]
    Json {
        operation: &'static str,
        #[source]
        source: serde_json::Error,
    },

    #[error("subprocess failed: {0}")]
    Process(String),

    #[error("a helper worker thread panicked")]
    WorkerPanicked,
}

impl BootstrapError {
    pub(crate) fn io(
        operation: &'static str,
        path: impl Into<PathBuf>,
        source: std::io::Error,
    ) -> Self {
        Self::Io {
            operation,
            path: path.into(),
            source,
        }
    }

    pub(crate) fn json(operation: &'static str, source: serde_json::Error) -> Self {
        Self::Json { operation, source }
    }
}

pub type Result<T> = std::result::Result<T, BootstrapError>;
