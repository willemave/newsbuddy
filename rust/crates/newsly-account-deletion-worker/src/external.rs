use std::fmt::{self, Debug, Formatter};
use std::path::{Component, Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use async_trait::async_trait;
use base64::Engine;
use base64::engine::general_purpose::URL_SAFE;
use fernet::Fernet;
use newsly_e2b::{
    ControlPlaneConfig, DirectE2bProvider, E2bError, FileLimits, SandboxId, SandboxProvider,
    SnapshotId,
};
use object_store::aws::AmazonS3Builder;
use object_store::path::Path as ObjectPath;
use object_store::{ClientOptions, ObjectStore, ObjectStoreExt};
use reqwest::StatusCode;
use secrecy::{ExposeSecret, SecretString};
use sha2::{Digest, Sha256};
use thiserror::Error;
use url::Url;

use crate::ArtifactStorageConfig;
use crate::repository::AccountCleanupPlan;

#[derive(Clone)]
pub struct AccountExternalServices {
    pub vm: Arc<dyn AgentVmDestroyer>,
    pub x: Arc<dyn XGrantRevoker>,
    pub objects: Arc<dyn ObjectArtifactStore>,
}

impl Debug for AccountExternalServices {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("AccountExternalServices")
            .field("vm", &self.vm)
            .field("x", &self.x)
            .field("objects", &self.objects)
            .finish()
    }
}

#[async_trait]
pub trait AgentVmDestroyer: Debug + Send + Sync + 'static {
    async fn destroy(
        &self,
        sandbox_id: Option<&str>,
        snapshot_id: Option<&str>,
    ) -> Result<(), ExternalCleanupError>;
}

#[derive(Clone)]
pub struct DirectAgentVmDestroyer {
    provider: Option<Arc<dyn SandboxProvider>>,
}

impl Debug for DirectAgentVmDestroyer {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("DirectAgentVmDestroyer")
            .field("provider_configured", &self.provider.is_some())
            .finish()
    }
}

impl DirectAgentVmDestroyer {
    /// Builds the direct E2B cleanup boundary, remaining unconfigured when no key is supplied.
    ///
    /// # Errors
    ///
    /// Returns an error when a supplied E2B key cannot initialize the direct provider.
    pub fn from_api_key(api_key: Option<SecretString>) -> Result<Self, ExternalCleanupError> {
        let provider = api_key
            .map(|api_key| {
                let config = ControlPlaneConfig::production(api_key)?;
                DirectE2bProvider::new(config, FileLimits::default())
                    .map(|provider| Arc::new(provider) as Arc<dyn SandboxProvider>)
            })
            .transpose()?;
        Ok(Self { provider })
    }

    pub fn new(provider: Arc<dyn SandboxProvider>) -> Self {
        Self {
            provider: Some(provider),
        }
    }
}

#[async_trait]
impl AgentVmDestroyer for DirectAgentVmDestroyer {
    async fn destroy(
        &self,
        sandbox_id: Option<&str>,
        snapshot_id: Option<&str>,
    ) -> Result<(), ExternalCleanupError> {
        if sandbox_id.is_none() && snapshot_id.is_none() {
            return Ok(());
        }
        let provider = self
            .provider
            .as_ref()
            .ok_or(ExternalCleanupError::MissingE2bApiKey)?;
        if let Some(value) = sandbox_id {
            let sandbox_id = SandboxId::parse(value.to_owned())?;
            // The provider normalizes a documented 404 to `false`, making retries idempotent.
            let _existed = provider.kill_sandbox(&sandbox_id).await?;
        }
        if let Some(value) = snapshot_id {
            let snapshot_id = SnapshotId::parse(value.to_owned())?;
            let _existed = provider.delete_snapshot(&snapshot_id).await?;
        }
        Ok(())
    }
}

#[async_trait]
pub trait XGrantRevoker: Debug + Send + Sync + 'static {
    async fn revoke(
        &self,
        encrypted_token: &str,
        token_type_hint: &str,
    ) -> Result<(), XRevokeError>;
}

#[derive(Clone)]
pub struct ReqwestXGrantRevoker {
    http: reqwest::Client,
    revoke_url: Url,
    client_id: SecretString,
    client_secret: Option<SecretString>,
    cipher: Arc<Fernet>,
}

impl Debug for ReqwestXGrantRevoker {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ReqwestXGrantRevoker")
            .field("revoke_url", &self.revoke_url)
            .field("client_id", &"[REDACTED]")
            .field(
                "client_secret",
                &self.client_secret.as_ref().map(|_| "[REDACTED]"),
            )
            .field("cipher", &"[REDACTED]")
            .finish_non_exhaustive()
    }
}

impl ReqwestXGrantRevoker {
    /// Builds the best-effort X revocation adapter with Python-compatible Fernet derivation.
    ///
    /// # Errors
    ///
    /// Returns an error for missing X settings, an invalid URL/key, or HTTP-client setup failure.
    pub fn new(
        token_url: &str,
        client_id: Option<SecretString>,
        client_secret: Option<SecretString>,
        encryption_key: Option<SecretString>,
    ) -> Result<Self, XRevokeError> {
        let client_id = client_id.ok_or(XRevokeError::MissingConfiguration("X_CLIENT_ID"))?;
        let encryption_key =
            encryption_key.ok_or(XRevokeError::MissingConfiguration("X_TOKEN_ENCRYPTION_KEY"))?;
        let revoke_url = revoke_url(token_url)?;
        let cipher = Arc::new(build_fernet(&encryption_key)?);
        let http = reqwest::Client::builder()
            .connect_timeout(Duration::from_secs(10))
            .timeout(Duration::from_secs(30))
            .user_agent(format!(
                "newsly-account-deletion-worker/{}",
                env!("CARGO_PKG_VERSION")
            ))
            .build()
            .map_err(XRevokeError::HttpClient)?;
        Ok(Self {
            http,
            revoke_url,
            client_id,
            client_secret,
            cipher,
        })
    }
}

#[async_trait]
impl XGrantRevoker for ReqwestXGrantRevoker {
    async fn revoke(
        &self,
        encrypted_token: &str,
        token_type_hint: &str,
    ) -> Result<(), XRevokeError> {
        let decrypted = self
            .cipher
            .decrypt(encrypted_token)
            .map_err(|_| XRevokeError::InvalidEncryptedToken)?;
        let token = String::from_utf8(decrypted).map_err(|_| XRevokeError::InvalidTokenUtf8)?;
        let token = token.trim();
        if token.is_empty() {
            return Err(XRevokeError::EmptyToken);
        }
        let client_id: &str = self.client_id.expose_secret();
        let form = [
            ("token", token),
            ("token_type_hint", token_type_hint),
            ("client_id", client_id),
        ];
        let mut request = self.http.post(self.revoke_url.clone()).form(&form);
        if let Some(client_secret) = &self.client_secret {
            request = request.basic_auth(client_id, Some(client_secret.expose_secret()));
        }
        let response = request.send().await.map_err(XRevokeError::Transport)?;
        if response.status().is_success() {
            return Ok(());
        }
        Err(XRevokeError::Remote(response.status()))
    }
}

#[derive(Debug, Clone)]
pub struct UnavailableXGrantRevoker {
    reason: Arc<str>,
}

impl UnavailableXGrantRevoker {
    pub fn new(reason: impl Into<String>) -> Self {
        Self {
            reason: Arc::from(reason.into()),
        }
    }
}

#[async_trait]
impl XGrantRevoker for UnavailableXGrantRevoker {
    async fn revoke(
        &self,
        _encrypted_token: &str,
        _token_type_hint: &str,
    ) -> Result<(), XRevokeError> {
        Err(XRevokeError::Unavailable(self.reason.to_string()))
    }
}

#[async_trait]
pub trait ObjectArtifactStore: Debug + Send + Sync + 'static {
    async fn delete(&self, key: &str) -> Result<(), ExternalCleanupError>;
}

#[derive(Clone)]
pub enum ConfiguredArtifactStore {
    Local { root: PathBuf },
    S3 { store: Arc<dyn ObjectStore> },
}

impl Debug for ConfiguredArtifactStore {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        match self {
            Self::Local { root } => formatter
                .debug_struct("LocalArtifactStore")
                .field("root", root)
                .finish(),
            Self::S3 { .. } => formatter
                .debug_struct("S3ArtifactStore")
                .finish_non_exhaustive(),
        }
    }
}

impl ConfiguredArtifactStore {
    /// Builds the configured local or S3-compatible object deletion boundary.
    ///
    /// # Errors
    ///
    /// Returns an error when the S3-compatible store configuration cannot be initialized.
    pub fn new(config: ArtifactStorageConfig) -> Result<Self, ExternalCleanupError> {
        match config {
            ArtifactStorageConfig::Local { root } => Ok(Self::Local { root }),
            ArtifactStorageConfig::S3Compatible {
                bucket,
                endpoint,
                region,
                access_key,
                secret_key,
                timeout,
            } => {
                let mut builder = AmazonS3Builder::from_env()
                    .with_bucket_name(bucket)
                    .with_disable_bulk_delete(true)
                    .with_client_options(ClientOptions::new().with_timeout(timeout));
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
                    .map_err(|error| ExternalCleanupError::ObjectStore(error.to_string()))?;
                Ok(Self::S3 {
                    store: Arc::new(store),
                })
            }
        }
    }
}

#[async_trait]
impl ObjectArtifactStore for ConfiguredArtifactStore {
    async fn delete(&self, key: &str) -> Result<(), ExternalCleanupError> {
        validate_object_key(key)?;
        match self {
            Self::Local { root } => {
                let path = root.join(key);
                remove_file_beneath_root(root, &path).await
            }
            Self::S3 { store } => {
                let path = ObjectPath::parse(key)
                    .map_err(|error| ExternalCleanupError::UnsafeObjectKey(error.to_string()))?;
                match store.delete(&path).await {
                    Ok(()) | Err(object_store::Error::NotFound { .. }) => Ok(()),
                    Err(error) => Err(ExternalCleanupError::ObjectStore(error.to_string())),
                }
            }
        }
    }
}

pub(crate) async fn remove_local_files(
    plan: &AccountCleanupPlan,
) -> Result<(), ExternalCleanupError> {
    for path in &plan.audio_paths {
        remove_file_beneath_root(&plan.media_audio_root, path).await?;
    }
    remove_directory_if_present(&plan.personal_markdown_root).await?;
    remove_directory_if_present(&plan.agent_data_root).await?;
    Ok(())
}

async fn remove_file_beneath_root(root: &Path, path: &Path) -> Result<(), ExternalCleanupError> {
    match tokio::fs::symlink_metadata(path).await {
        Ok(_) => {}
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(()),
        Err(error) => {
            return Err(ExternalCleanupError::File {
                path: path.to_path_buf(),
                source: error,
            });
        }
    }
    let canonical_root =
        tokio::fs::canonicalize(root)
            .await
            .map_err(|source| ExternalCleanupError::File {
                path: root.to_path_buf(),
                source,
            })?;
    let parent = path
        .parent()
        .ok_or_else(|| ExternalCleanupError::UnsafeLocalPath {
            root: root.to_path_buf(),
            path: path.to_path_buf(),
        })?;
    let canonical_parent =
        tokio::fs::canonicalize(parent)
            .await
            .map_err(|source| ExternalCleanupError::File {
                path: parent.to_path_buf(),
                source,
            })?;
    if !canonical_parent.starts_with(&canonical_root) {
        return Err(ExternalCleanupError::UnsafeLocalPath {
            root: canonical_root,
            path: path.to_path_buf(),
        });
    }
    remove_file_if_present(path).await
}

async fn remove_file_if_present(path: &Path) -> Result<(), ExternalCleanupError> {
    match tokio::fs::remove_file(path).await {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(ExternalCleanupError::File {
            path: path.to_path_buf(),
            source: error,
        }),
    }
}

async fn remove_directory_if_present(path: &Path) -> Result<(), ExternalCleanupError> {
    match tokio::fs::remove_dir_all(path).await {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(ExternalCleanupError::File {
            path: path.to_path_buf(),
            source: error,
        }),
    }
}

fn validate_object_key(key: &str) -> Result<(), ExternalCleanupError> {
    let path = Path::new(key);
    if key.is_empty()
        || key.len() > 2_048
        || key.contains('\0')
        || key.contains('\\')
        || path.is_absolute()
        || path
            .components()
            .any(|component| !matches!(component, Component::Normal(_)))
    {
        return Err(ExternalCleanupError::UnsafeObjectKey(key.to_owned()));
    }
    Ok(())
}

fn revoke_url(token_url: &str) -> Result<Url, XRevokeError> {
    let mut url = token_url
        .parse::<Url>()
        .map_err(|_| XRevokeError::InvalidTokenUrl)?;
    if !matches!(url.scheme(), "http" | "https") || url.cannot_be_a_base() {
        return Err(XRevokeError::InvalidTokenUrl);
    }
    let path = url.path().trim_end_matches('/');
    let base = path.strip_suffix("/token").unwrap_or(path);
    url.set_path(&format!("{base}/revoke"));
    url.set_query(None);
    url.set_fragment(None);
    Ok(url)
}

fn build_fernet(raw_key: &SecretString) -> Result<Fernet, XRevokeError> {
    let raw_key = raw_key.expose_secret();
    if let Some(cipher) = Fernet::new(raw_key) {
        return Ok(cipher);
    }
    let digest = Sha256::digest(raw_key.as_bytes());
    let derived = URL_SAFE.encode(digest);
    Fernet::new(&derived).ok_or(XRevokeError::InvalidEncryptionKey)
}

#[derive(Debug, Error)]
pub enum ExternalCleanupError {
    #[error("E2B_API_KEY is required to destroy this user's external sandbox data")]
    MissingE2bApiKey,
    #[error(transparent)]
    E2b(#[from] E2bError),
    #[error("unsafe object-storage key: {0}")]
    UnsafeObjectKey(String),
    #[error("local account artifact {path} escapes configured root {root}")]
    UnsafeLocalPath { root: PathBuf, path: PathBuf },
    #[error("object-storage deletion failed: {0}")]
    ObjectStore(String),
    #[error("local account artifact deletion failed for {path}")]
    File {
        path: PathBuf,
        #[source]
        source: std::io::Error,
    },
}

#[derive(Debug, Error)]
pub enum XRevokeError {
    #[error("{0} is required for X grant revocation")]
    MissingConfiguration(&'static str),
    #[error("X OAuth token URL is invalid")]
    InvalidTokenUrl,
    #[error("X token encryption key is invalid")]
    InvalidEncryptionKey,
    #[error("X token could not be decrypted")]
    InvalidEncryptedToken,
    #[error("decrypted X token is not UTF-8")]
    InvalidTokenUtf8,
    #[error("decrypted X token is empty")]
    EmptyToken,
    #[error("X revocation HTTP client could not be built")]
    HttpClient(#[source] reqwest::Error),
    #[error("X revocation transport failed")]
    Transport(#[source] reqwest::Error),
    #[error("X revocation returned HTTP {0}")]
    Remote(StatusCode),
    #[error("X grant revocation is unavailable: {0}")]
    Unavailable(String),
}

#[cfg(test)]
mod tests {
    use std::fs;

    use secrecy::SecretString;
    use tempfile::tempdir;

    use super::{
        ExternalCleanupError, build_fernet, remove_file_beneath_root, revoke_url,
        validate_object_key,
    };

    #[test]
    fn python_compatible_fernet_key_derivation_round_trips() {
        let cipher = build_fernet(&SecretString::from("human-readable-key".to_owned())).unwrap();
        let token = cipher.encrypt(b"private-token");
        assert_eq!(cipher.decrypt(&token).unwrap(), b"private-token");
    }

    #[test]
    fn token_endpoint_maps_to_revoke_endpoint() {
        assert_eq!(
            revoke_url("https://api.x.com/2/oauth2/token")
                .unwrap()
                .as_str(),
            "https://api.x.com/2/oauth2/revoke"
        );
    }

    #[test]
    fn object_keys_reject_root_and_traversal() {
        assert!(validate_object_key("learning-decks/1/deck.html").is_ok());
        assert!(validate_object_key("../private").is_err());
        assert!(validate_object_key("/absolute").is_err());
        assert!(validate_object_key("").is_err());
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn local_deletion_does_not_follow_a_parent_symlink() {
        let scratch = tempdir().unwrap();
        let root = scratch.path().join("objects");
        let outside = scratch.path().join("outside");
        fs::create_dir_all(&root).unwrap();
        fs::create_dir_all(&outside).unwrap();
        let secret = outside.join("secret.txt");
        fs::write(&secret, b"keep").unwrap();
        std::os::unix::fs::symlink(&outside, root.join("escaped")).unwrap();

        let error = remove_file_beneath_root(&root, &root.join("escaped/secret.txt"))
            .await
            .unwrap_err();

        assert!(matches!(
            error,
            ExternalCleanupError::UnsafeLocalPath { .. }
        ));
        assert!(secret.exists());
    }
}
