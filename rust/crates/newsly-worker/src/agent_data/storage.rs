use std::env;
use std::path::{Component, Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use object_store::aws::AmazonS3Builder;
use object_store::path::Path as ObjectPath;
use object_store::{ClientOptions, Error as ObjectStoreError, ObjectStore, ObjectStoreExt};
use reqwest::Url;
use secrecy::{ExposeSecret, SecretString};
use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};
use thiserror::Error;
use tokio::fs;

use super::documents::AgentDataDocument;

#[derive(Debug, Clone)]
pub struct AgentDataMirrorStore {
    root: PathBuf,
    content_body_backend: Option<ContentBodyBackend>,
}

#[derive(Clone)]
enum ContentBodyBackend {
    Local { root: PathBuf },
    S3 { store: Arc<dyn ObjectStore> },
}

impl std::fmt::Debug for ContentBodyBackend {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Local { root } => formatter
                .debug_struct("LocalContentBodyBackend")
                .field("root", root)
                .finish(),
            Self::S3 { .. } => formatter
                .debug_struct("S3ContentBodyBackend")
                .finish_non_exhaustive(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct StagedIndexPublication {
    pub(super) user_id: i64,
    pub(super) staging_directory: PathBuf,
    pub(super) index_path: PathBuf,
    pub(super) manifest_path: PathBuf,
}

#[derive(Debug, Clone, PartialEq)]
pub(super) struct StagedAgentDataDocument {
    pub(super) document_kind: String,
    pub(super) document_key: String,
    pub(super) path: String,
    pub(super) checksum_sha256: String,
    pub(super) byte_size: i32,
    pub(super) index_record: Value,
    pub(super) staging_path: PathBuf,
    pub(super) filesystem_matches: bool,
}

#[derive(Debug, Clone, PartialEq)]
pub(super) struct StagedDocumentPublication {
    pub(super) user_id: i64,
    pub(super) staging_directory: PathBuf,
    pub(super) documents: Vec<StagedAgentDataDocument>,
}

impl AgentDataMirrorStore {
    /// Creates the credential-free host mirror rooted below a dedicated data directory.
    ///
    /// # Errors
    ///
    /// Relative paths fail when the current directory cannot be resolved.
    pub fn new(root: PathBuf) -> Result<Self, AgentDataMirrorStoreError> {
        let root = if root.is_absolute() {
            root
        } else {
            std::env::current_dir()
                .map_err(AgentDataMirrorStoreError::CurrentDirectory)?
                .join(root)
        };
        Ok(Self {
            root,
            content_body_backend: None,
        })
    }

    /// Configures the immutable content-body root used while rendering corpus documents.
    pub fn with_content_body_root(
        mut self,
        root: PathBuf,
    ) -> Result<Self, AgentDataMirrorStoreError> {
        let root = if root.is_absolute() {
            root
        } else {
            std::env::current_dir()
                .map_err(AgentDataMirrorStoreError::CurrentDirectory)?
                .join(root)
        };
        self.content_body_backend = Some(ContentBodyBackend::Local { root });
        Ok(self)
    }

    /// Configures the content-body reader from the same local or S3-compatible settings used by
    /// the API and artifact workers.
    pub fn with_content_body_environment(
        mut self,
        local_root: PathBuf,
    ) -> Result<Self, AgentDataMirrorStoreError> {
        let provider =
            env::var("CONTENT_BODY_STORAGE_PROVIDER").unwrap_or_else(|_| "local".to_owned());
        self.content_body_backend = Some(match provider.as_str() {
            "local" => {
                let root = if local_root.is_absolute() {
                    local_root
                } else {
                    env::current_dir()
                        .map_err(AgentDataMirrorStoreError::CurrentDirectory)?
                        .join(local_root)
                };
                ContentBodyBackend::Local { root }
            }
            "s3_compatible" => {
                let bucket = required_env("CONTENT_BODY_STORAGE_BUCKET")?;
                let endpoint = optional_url("CONTENT_BODY_STORAGE_ENDPOINT")?;
                let region = optional_env("CONTENT_BODY_STORAGE_REGION");
                let access_key =
                    optional_env("CONTENT_BODY_STORAGE_ACCESS_KEY").map(SecretString::from);
                let secret_key =
                    optional_env("CONTENT_BODY_STORAGE_SECRET_KEY").map(SecretString::from);
                if access_key.is_some() != secret_key.is_some() {
                    return Err(AgentDataMirrorStoreError::IncompleteCredentials);
                }
                let timeout_seconds = env::var("CONTENT_BODY_STORAGE_TIMEOUT_SECONDS")
                    .ok()
                    .map(|value| {
                        value.parse::<u64>().map_err(|_| {
                            AgentDataMirrorStoreError::InvalidStorageTimeout(value.clone())
                        })
                    })
                    .transpose()?
                    .unwrap_or(30);
                if !(1..=300).contains(&timeout_seconds) {
                    return Err(AgentDataMirrorStoreError::InvalidStorageTimeout(
                        timeout_seconds.to_string(),
                    ));
                }
                let mut builder = AmazonS3Builder::from_env()
                    .with_bucket_name(bucket)
                    .with_disable_bulk_delete(true)
                    .with_client_options(
                        ClientOptions::new().with_timeout(Duration::from_secs(timeout_seconds)),
                    );
                if let Some(endpoint) = endpoint {
                    builder = builder
                        .with_allow_http(endpoint.scheme() == "http")
                        .with_endpoint(endpoint.to_string());
                }
                if let Some(region) = region {
                    builder = builder.with_region(region);
                }
                if let (Some(access_key), Some(secret_key)) = (access_key, secret_key) {
                    builder = builder
                        .with_access_key_id(access_key.expose_secret())
                        .with_secret_access_key(secret_key.expose_secret());
                }
                let store = builder
                    .build()
                    .map_err(|error| AgentDataMirrorStoreError::ObjectStore(error.to_string()))?;
                ContentBodyBackend::S3 {
                    store: Arc::new(store),
                }
            }
            _ => return Err(AgentDataMirrorStoreError::UnsupportedBodyProvider(provider)),
        });
        Ok(self)
    }

    pub(super) async fn stage_index(
        &self,
        user_id: i64,
        task_id: i64,
        retry_count: i32,
        revision: i64,
        records: &[(String, Value)],
        mark_complete: bool,
    ) -> Result<StagedIndexPublication, AgentDataMirrorStoreError> {
        if user_id <= 0 || task_id <= 0 || retry_count < 0 || revision < 0 {
            return Err(AgentDataMirrorStoreError::InvalidIdentity);
        }
        let user_root = self.user_root(user_id)?;
        let staging_directory = user_root
            .join(".staging")
            .join(format!("index-{task_id}-{retry_count}"));
        fs::create_dir_all(&staging_directory)
            .await
            .map_err(AgentDataMirrorStoreError::Io)?;

        let mut sorted = records.to_vec();
        sorted.sort_by(|left, right| left.0.cmp(&right.0));
        let mut index = Vec::new();
        for (_, record) in &sorted {
            serde_json::to_writer(&mut index, record)?;
            index.push(b'\n');
        }
        let index_path = staging_directory.join("index.jsonl");
        fs::write(&index_path, index)
            .await
            .map_err(AgentDataMirrorStoreError::Io)?;

        let complete = if mark_complete {
            true
        } else {
            self.existing_manifest_complete(user_id).await
        };
        let manifest = json!({
            "version": 1,
            "user_id": user_id,
            "revision": revision,
            "generated_at": chrono::Utc::now().to_rfc3339(),
            "file_count": sorted.len(),
            "complete": complete,
        });
        let mut manifest_bytes = serde_json::to_vec_pretty(&manifest)?;
        manifest_bytes.push(b'\n');
        let manifest_path = staging_directory.join("manifest.json");
        fs::write(&manifest_path, manifest_bytes)
            .await
            .map_err(AgentDataMirrorStoreError::Io)?;
        Ok(StagedIndexPublication {
            user_id,
            staging_directory,
            index_path,
            manifest_path,
        })
    }

    pub(super) async fn stage_documents(
        &self,
        user_id: i64,
        task_id: i64,
        retry_count: i32,
        documents: &[AgentDataDocument],
    ) -> Result<StagedDocumentPublication, AgentDataMirrorStoreError> {
        if user_id <= 0 || task_id <= 0 || retry_count < 0 {
            return Err(AgentDataMirrorStoreError::InvalidIdentity);
        }
        let user_root = self.user_root(user_id)?;
        let staging_directory = user_root
            .join(".staging")
            .join(format!("sync-{task_id}-{retry_count}"));
        fs::create_dir_all(&staging_directory)
            .await
            .map_err(AgentDataMirrorStoreError::Io)?;

        let mut staged_documents = Vec::with_capacity(documents.len());
        for document in documents {
            let relative_path = Path::new(&document.path);
            validate_relative_path(relative_path)?;
            let staging_path = staging_directory.join(relative_path);
            if let Some(parent) = staging_path.parent() {
                fs::create_dir_all(parent)
                    .await
                    .map_err(AgentDataMirrorStoreError::Io)?;
            }
            fs::write(&staging_path, &document.content_bytes)
                .await
                .map_err(AgentDataMirrorStoreError::Io)?;
            let target = user_root.join(relative_path);
            let filesystem_matches = checksum_matches(&target, &document.checksum_sha256).await;
            let byte_size = i32::try_from(document.content_bytes.len())
                .map_err(|_| AgentDataMirrorStoreError::DocumentTooLarge)?;
            staged_documents.push(StagedAgentDataDocument {
                document_kind: document.document_kind.clone(),
                document_key: document.document_key.clone(),
                path: document.path.clone(),
                checksum_sha256: document.checksum_sha256.clone(),
                byte_size,
                index_record: document.index_record.clone(),
                staging_path,
                filesystem_matches,
            });
        }
        Ok(StagedDocumentPublication {
            user_id,
            staging_directory,
            documents: staged_documents,
        })
    }

    pub(super) async fn publish_document(
        &self,
        user_id: i64,
        document: &StagedAgentDataDocument,
    ) -> Result<(), AgentDataMirrorStoreError> {
        if user_id <= 0 {
            return Err(AgentDataMirrorStoreError::InvalidIdentity);
        }
        let relative_path = Path::new(&document.path);
        validate_relative_path(relative_path)?;
        let target = self.user_root(user_id)?.join(relative_path);
        if let Some(parent) = target.parent() {
            fs::create_dir_all(parent)
                .await
                .map_err(AgentDataMirrorStoreError::Io)?;
        }
        fs::rename(&document.staging_path, target)
            .await
            .map_err(AgentDataMirrorStoreError::Io)
    }

    pub(super) async fn delete_document(
        &self,
        user_id: i64,
        relative_path: &str,
    ) -> Result<(), AgentDataMirrorStoreError> {
        let relative_path = Path::new(relative_path);
        validate_relative_path(relative_path)?;
        match fs::remove_file(self.user_root(user_id)?.join(relative_path)).await {
            Ok(()) => Ok(()),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
            Err(error) => Err(AgentDataMirrorStoreError::Io(error)),
        }
    }

    pub(super) async fn cleanup_document_staging(&self, staged: &StagedDocumentPublication) {
        let _ = fs::remove_dir_all(&staged.staging_directory).await;
    }

    pub(super) async fn publish_index(
        &self,
        staged: &StagedIndexPublication,
    ) -> Result<(), AgentDataMirrorStoreError> {
        let user_root = self.user_root(staged.user_id)?;
        fs::create_dir_all(&user_root)
            .await
            .map_err(AgentDataMirrorStoreError::Io)?;
        fs::rename(&staged.index_path, user_root.join("index.jsonl"))
            .await
            .map_err(AgentDataMirrorStoreError::Io)?;
        // Manifest-last publication is the VM hydration consistency boundary.
        fs::rename(&staged.manifest_path, user_root.join("manifest.json"))
            .await
            .map_err(AgentDataMirrorStoreError::Io)?;
        Ok(())
    }

    pub(super) async fn cleanup_staging(&self, staged: &StagedIndexPublication) {
        let _ = fs::remove_dir_all(&staged.staging_directory).await;
    }

    pub(super) async fn read_content_body(
        &self,
        storage_provider: &str,
        storage_key: &str,
    ) -> Result<Option<String>, AgentDataMirrorStoreError> {
        let backend = self
            .content_body_backend
            .as_ref()
            .ok_or(AgentDataMirrorStoreError::BodyRootMissing)?;
        let key = Path::new(storage_key);
        validate_relative_path(key)?;
        let bytes = match (storage_provider, backend) {
            ("local", ContentBodyBackend::Local { root }) => match fs::read(root.join(key)).await {
                Ok(bytes) => bytes,
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
                Err(error) => return Err(AgentDataMirrorStoreError::Io(error)),
            },
            ("s3_compatible", ContentBodyBackend::S3 { store }) => {
                let path = ObjectPath::parse(storage_key).map_err(|error| {
                    AgentDataMirrorStoreError::UnsafeObjectKey(error.to_string())
                })?;
                let result = match store.get(&path).await {
                    Ok(result) => result,
                    Err(ObjectStoreError::NotFound { .. }) => return Ok(None),
                    Err(error) => {
                        return Err(AgentDataMirrorStoreError::ObjectStore(error.to_string()));
                    }
                };
                result
                    .bytes()
                    .await
                    .map_err(|error| AgentDataMirrorStoreError::ObjectStore(error.to_string()))?
                    .to_vec()
            }
            _ => {
                return Err(AgentDataMirrorStoreError::BodyProviderMismatch(
                    storage_provider.to_owned(),
                ));
            }
        };
        String::from_utf8(bytes)
            .map(Some)
            .map_err(AgentDataMirrorStoreError::InvalidUtf8)
    }

    pub(super) async fn manifest_state(&self, user_id: i64) -> Option<(i64, bool)> {
        let root = self.user_root(user_id).ok()?;
        let bytes = fs::read(root.join("manifest.json")).await.ok()?;
        let value = serde_json::from_slice::<Map<String, Value>>(&bytes).ok()?;
        Some((
            value.get("revision")?.as_i64()?,
            value
                .get("complete")
                .and_then(Value::as_bool)
                .unwrap_or(false),
        ))
    }

    async fn existing_manifest_complete(&self, user_id: i64) -> bool {
        let Ok(root) = self.user_root(user_id) else {
            return false;
        };
        fs::read(root.join("manifest.json"))
            .await
            .ok()
            .and_then(|bytes| serde_json::from_slice::<Map<String, Value>>(&bytes).ok())
            .and_then(|value| value.get("complete").and_then(Value::as_bool))
            .unwrap_or(false)
    }

    fn user_root(&self, user_id: i64) -> Result<PathBuf, AgentDataMirrorStoreError> {
        if user_id <= 0 {
            return Err(AgentDataMirrorStoreError::InvalidIdentity);
        }
        let component = user_id.to_string();
        validate_relative_path(Path::new(&component))?;
        Ok(self.root.join(component))
    }
}

fn validate_relative_path(path: &Path) -> Result<(), AgentDataMirrorStoreError> {
    if path.as_os_str().is_empty()
        || path.is_absolute()
        || path
            .components()
            .any(|component| !matches!(component, Component::Normal(_)))
    {
        return Err(AgentDataMirrorStoreError::UnsafePath);
    }
    Ok(())
}

async fn checksum_matches(path: &Path, expected: &str) -> bool {
    let Ok(bytes) = fs::read(path).await else {
        return false;
    };
    hex_sha256(&bytes) == expected
}

fn hex_sha256(value: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let digest = Sha256::digest(value);
    let mut encoded = String::with_capacity(digest.len() * 2);
    for byte in digest {
        encoded.push(char::from(HEX[usize::from(byte >> 4)]));
        encoded.push(char::from(HEX[usize::from(byte & 0x0f)]));
    }
    encoded
}

fn required_env(name: &'static str) -> Result<String, AgentDataMirrorStoreError> {
    optional_env(name).ok_or(AgentDataMirrorStoreError::MissingStorageSetting(name))
}

fn optional_env(name: &'static str) -> Option<String> {
    env::var(name)
        .ok()
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty())
}

fn optional_url(name: &'static str) -> Result<Option<Url>, AgentDataMirrorStoreError> {
    optional_env(name)
        .map(|value| {
            let url = value
                .parse::<Url>()
                .map_err(|_| AgentDataMirrorStoreError::InvalidStorageUrl(name, value.clone()))?;
            if !matches!(url.scheme(), "http" | "https") || url.cannot_be_a_base() {
                return Err(AgentDataMirrorStoreError::InvalidStorageUrl(name, value));
            }
            Ok(url)
        })
        .transpose()
}

#[derive(Debug, Error)]
pub enum AgentDataMirrorStoreError {
    #[error("could not resolve the agent-data mirror root")]
    CurrentDirectory(#[source] std::io::Error),
    #[error("agent-data identity is invalid")]
    InvalidIdentity,
    #[error("agent-data mirror path is unsafe")]
    UnsafePath,
    #[error("agent-data mirror I/O failed")]
    Io(#[source] std::io::Error),
    #[error("agent-data mirror JSON serialization failed")]
    Json(#[from] serde_json::Error),
    #[error("agent-data content-body backend is not configured")]
    BodyRootMissing,
    #[error("agent-data content body provider {0:?} is not supported")]
    UnsupportedBodyProvider(String),
    #[error("agent-data content body provider {0:?} does not match the configured backend")]
    BodyProviderMismatch(String),
    #[error("missing required object-storage setting {0}")]
    MissingStorageSetting(&'static str),
    #[error(
        "CONTENT_BODY_STORAGE_ACCESS_KEY and CONTENT_BODY_STORAGE_SECRET_KEY must be set together"
    )]
    IncompleteCredentials,
    #[error("invalid object-storage timeout {0:?}")]
    InvalidStorageTimeout(String),
    #[error("invalid URL for {0}: {1:?}")]
    InvalidStorageUrl(&'static str, String),
    #[error("agent-data content-body object key is unsafe: {0}")]
    UnsafeObjectKey(String),
    #[error("agent-data content-body object-store operation failed: {0}")]
    ObjectStore(String),
    #[error("agent-data content body is not valid UTF-8")]
    InvalidUtf8(#[source] std::string::FromUtf8Error),
    #[error("agent-data document exceeds PostgreSQL byte-size range")]
    DocumentTooLarge,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_parent_paths() {
        assert!(validate_relative_path(Path::new("../other-user")).is_err());
        assert!(validate_relative_path(Path::new("42")).is_ok());
    }
}
