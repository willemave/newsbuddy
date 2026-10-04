use std::fmt::{self, Debug, Formatter};
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use async_trait::async_trait;
use newsly_providers::IntegrationTokenCipher;
use object_store::ObjectStoreExt;
use object_store::path::Path as ObjectPath;
use reqwest::StatusCode;
use secrecy::{ExposeSecret, SecretString};
use thiserror::Error;
use url::Url;

use crate::repository::AccountCleanupPlan;

#[derive(Clone)]
pub struct AccountExternalServices {
    pub x: Arc<dyn XGrantRevoker>,
    pub objects: Arc<ConfiguredArtifactStore>,
}

impl Debug for AccountExternalServices {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("AccountExternalServices")
            .field("x", &self.x)
            .field("objects", &self.objects)
            .finish()
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
    cipher: IntegrationTokenCipher,
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
    /// Builds the best-effort X revocation adapter with the persisted Fernet derivation.
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
        let client_id =
            trimmed_secret(client_id).ok_or(XRevokeError::MissingConfiguration("X_CLIENT_ID"))?;
        let client_secret = trimmed_secret(client_secret);
        let encryption_key =
            encryption_key.ok_or(XRevokeError::MissingConfiguration("X_TOKEN_ENCRYPTION_KEY"))?;
        let revoke_url = revoke_url(token_url)?;
        let cipher = IntegrationTokenCipher::new(&encryption_key)
            .map_err(|_| XRevokeError::InvalidEncryptionKey)?;
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

fn trimmed_secret(secret: Option<SecretString>) -> Option<SecretString> {
    secret.and_then(|secret| {
        let value = secret.expose_secret().trim();
        (!value.is_empty()).then(|| SecretString::from(value.to_owned()))
    })
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
        let token = decrypted.trim();
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

#[derive(Clone)]
pub struct ConfiguredArtifactStore(newsly_providers::ObjectStorageBackend);

impl Debug for ConfiguredArtifactStore {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter
            .debug_tuple("ConfiguredArtifactStore")
            .field(&self.0)
            .finish()
    }
}

impl ConfiguredArtifactStore {
    /// Builds the configured local or S3-compatible object deletion boundary.
    pub const fn new(config: newsly_providers::ObjectStorageBackend) -> Self {
        Self(config)
    }

    /// Deletes an account-owned artifact when its key is safe for the configured backend.
    ///
    /// # Errors
    ///
    /// Returns an error for an unsafe key or when the configured object store cannot delete it.
    pub async fn delete(&self, key: &str) -> Result<(), ExternalCleanupError> {
        if !newsly_providers::is_safe_object_key(key) {
            return Err(ExternalCleanupError::UnsafeObjectKey(key.to_owned()));
        }
        match &self.0 {
            newsly_providers::ObjectStorageBackend::Local { root } => {
                newsly_providers::delete_local_object(root, key)
                    .await
                    .map_err(|error| ExternalCleanupError::ObjectStore(error.to_string()))
            }
            newsly_providers::ObjectStorageBackend::S3 { store } => {
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

#[derive(Debug, Error)]
pub enum ExternalCleanupError {
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

    use secrecy::{ExposeSecret, SecretString};
    use tempfile::tempdir;

    use super::{ExternalCleanupError, ReqwestXGrantRevoker, remove_file_beneath_root, revoke_url};

    #[test]
    fn revoker_decrypts_provider_tokens_with_trimmed_encoded_and_derived_keys() {
        for key in [
            "human-readable-key",
            "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
        ] {
            let provider =
                newsly_providers::IntegrationTokenCipher::new(&SecretString::from(key.to_owned()))
                    .unwrap();
            let token = provider.encrypt("private-token").unwrap();
            let revoker = ReqwestXGrantRevoker::new(
                "https://api.x.com/2/oauth2/token",
                Some(SecretString::from(" client\n".to_owned())),
                Some(SecretString::from(" secret\n".to_owned())),
                Some(SecretString::from(format!("  {key}\n"))),
            )
            .unwrap();
            assert_eq!(revoker.client_id.expose_secret(), "client");
            assert_eq!(
                revoker.client_secret.as_ref().unwrap().expose_secret(),
                "secret"
            );
            assert_eq!(revoker.cipher.decrypt(&token).unwrap(), "private-token");
        }
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
        assert!(newsly_providers::is_safe_object_key(
            "learning-decks/1/deck.html"
        ));
        assert!(!newsly_providers::is_safe_object_key("../private"));
        assert!(!newsly_providers::is_safe_object_key("/absolute"));
        assert!(!newsly_providers::is_safe_object_key(""));
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
