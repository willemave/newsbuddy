use std::env;
use std::path::{Component, Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use object_store::aws::AmazonS3Builder;
use object_store::path::Path as ObjectPath;
use object_store::{ClientOptions, Error as ObjectStoreError, ObjectStore, ObjectStoreExt};
use reqwest::Url;
use secrecy::{ExposeSecret, SecretString};
use thiserror::Error;

#[derive(Clone)]
pub(super) enum ContentBodyStore {
    Local { root: PathBuf },
    S3 { store: Arc<dyn ObjectStore> },
}

impl std::fmt::Debug for ContentBodyStore {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Local { root } => formatter
                .debug_struct("LocalContentBodyStore")
                .field("root", root)
                .finish(),
            Self::S3 { .. } => formatter
                .debug_struct("S3ContentBodyStore")
                .finish_non_exhaustive(),
        }
    }
}

impl ContentBodyStore {
    pub(super) fn from_environment() -> Result<Self, ContentBodyStoreError> {
        let provider =
            env::var("CONTENT_BODY_STORAGE_PROVIDER").unwrap_or_else(|_| "local".to_owned());
        match provider.as_str() {
            "local" => {
                let configured = env::var_os("CONTENT_BODY_LOCAL_ROOT")
                    .map_or_else(|| PathBuf::from("data/content_bodies"), PathBuf::from);
                let root = if configured.is_absolute() {
                    configured
                } else {
                    env::current_dir()?.join(configured)
                };
                validate_root(&root)?;
                Ok(Self::Local { root })
            }
            "s3_compatible" => {
                let bucket = required("CONTENT_BODY_STORAGE_BUCKET")?;
                let endpoint = optional_url("CONTENT_BODY_STORAGE_ENDPOINT")?;
                let region = optional("CONTENT_BODY_STORAGE_REGION");
                let access_key =
                    optional("CONTENT_BODY_STORAGE_ACCESS_KEY").map(SecretString::from);
                let secret_key =
                    optional("CONTENT_BODY_STORAGE_SECRET_KEY").map(SecretString::from);
                if access_key.is_some() != secret_key.is_some() {
                    return Err(ContentBodyStoreError::IncompleteCredentials);
                }
                let timeout_seconds = env::var("CONTENT_BODY_STORAGE_TIMEOUT_SECONDS")
                    .ok()
                    .map(|value| {
                        value
                            .parse::<u64>()
                            .map_err(|_| ContentBodyStoreError::InvalidTimeout(value.clone()))
                    })
                    .transpose()?
                    .unwrap_or(30);
                if !(1..=300).contains(&timeout_seconds) {
                    return Err(ContentBodyStoreError::InvalidTimeout(
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
                    .map_err(|error| ContentBodyStoreError::ObjectStore(error.to_string()))?;
                Ok(Self::S3 {
                    store: Arc::new(store),
                })
            }
            _ => Err(ContentBodyStoreError::UnsupportedProvider(provider)),
        }
    }

    pub(super) async fn get_text(
        &self,
        key: &str,
    ) -> Result<Option<String>, ContentBodyStoreError> {
        validate_object_key(key)?;
        match self {
            Self::Local { root } => read_local(root, key).await,
            Self::S3 { store } => {
                let path = ObjectPath::parse(key)
                    .map_err(|error| ContentBodyStoreError::UnsafeObjectKey(error.to_string()))?;
                let result = match store.get(&path).await {
                    Ok(result) => result,
                    Err(ObjectStoreError::NotFound { .. }) => return Ok(None),
                    Err(error) => {
                        return Err(ContentBodyStoreError::ObjectStore(error.to_string()));
                    }
                };
                let bytes = result
                    .bytes()
                    .await
                    .map_err(|error| ContentBodyStoreError::ObjectStore(error.to_string()))?;
                String::from_utf8(bytes.to_vec())
                    .map(Some)
                    .map_err(ContentBodyStoreError::Utf8)
            }
        }
    }
}

async fn read_local(root: &Path, key: &str) -> Result<Option<String>, ContentBodyStoreError> {
    let canonical_root = match tokio::fs::canonicalize(root).await {
        Ok(root) => root,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(source) => {
            return Err(ContentBodyStoreError::File {
                path: root.to_path_buf(),
                source,
            });
        }
    };
    let requested = root.join(key);
    let canonical_path = match tokio::fs::canonicalize(&requested).await {
        Ok(path) => path,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(source) => {
            return Err(ContentBodyStoreError::File {
                path: requested,
                source,
            });
        }
    };
    if !canonical_path.starts_with(&canonical_root) {
        return Err(ContentBodyStoreError::UnsafeLocalPath(canonical_path));
    }
    tokio::fs::read_to_string(&canonical_path)
        .await
        .map(Some)
        .map_err(|source| ContentBodyStoreError::File {
            path: canonical_path,
            source,
        })
}

fn validate_root(root: &Path) -> Result<(), ContentBodyStoreError> {
    if root == Path::new("/")
        || !root.is_absolute()
        || root.components().any(|component| {
            matches!(
                component,
                Component::ParentDir | Component::CurDir | Component::Prefix(_)
            )
        })
    {
        return Err(ContentBodyStoreError::UnsafeRoot(root.to_path_buf()));
    }
    Ok(())
}

fn validate_object_key(key: &str) -> Result<(), ContentBodyStoreError> {
    let path = Path::new(key);
    if key.is_empty()
        || key.len() > 2_048
        || key.contains(['\0', '\\'])
        || path.is_absolute()
        || path
            .components()
            .any(|component| !matches!(component, Component::Normal(_)))
    {
        return Err(ContentBodyStoreError::UnsafeObjectKey(key.to_owned()));
    }
    Ok(())
}

fn required(name: &'static str) -> Result<String, ContentBodyStoreError> {
    optional(name).ok_or(ContentBodyStoreError::Missing(name))
}

fn optional(name: &'static str) -> Option<String> {
    env::var(name)
        .ok()
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty())
}

fn optional_url(name: &'static str) -> Result<Option<Url>, ContentBodyStoreError> {
    optional(name)
        .map(|value| {
            let url = value
                .parse::<Url>()
                .map_err(|_| ContentBodyStoreError::InvalidUrl(name, value.clone()))?;
            if !matches!(url.scheme(), "http" | "https") || url.cannot_be_a_base() {
                return Err(ContentBodyStoreError::InvalidUrl(name, value));
            }
            Ok(url)
        })
        .transpose()
}

#[derive(Debug, Error)]
pub(super) enum ContentBodyStoreError {
    #[error("missing required object-storage setting {0}")]
    Missing(&'static str),
    #[error("unsupported CONTENT_BODY_STORAGE_PROVIDER {0:?}")]
    UnsupportedProvider(String),
    #[error(
        "CONTENT_BODY_STORAGE_ACCESS_KEY and CONTENT_BODY_STORAGE_SECRET_KEY must be set together"
    )]
    IncompleteCredentials,
    #[error("invalid object-storage timeout {0:?}")]
    InvalidTimeout(String),
    #[error("invalid URL for {0}: {1:?}")]
    InvalidUrl(&'static str, String),
    #[error("unsafe local content-body root {0:?}")]
    UnsafeRoot(PathBuf),
    #[error("unsafe local content-body path {0:?}")]
    UnsafeLocalPath(PathBuf),
    #[error("unsafe content-body object key {0:?}")]
    UnsafeObjectKey(String),
    #[error("content-body object-store operation failed: {0}")]
    ObjectStore(String),
    #[error("content-body local file operation failed for {path:?}")]
    File {
        path: PathBuf,
        #[source]
        source: std::io::Error,
    },
    #[error("content-body object is not valid UTF-8")]
    Utf8(#[from] std::string::FromUtf8Error),
    #[error("could not resolve the current directory")]
    CurrentDirectory(#[from] std::io::Error),
}
