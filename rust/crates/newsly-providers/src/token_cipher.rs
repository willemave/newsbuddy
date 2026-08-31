use std::fmt::{self, Debug, Formatter};
use std::sync::Arc;

use base64::Engine as _;
use base64::engine::general_purpose::URL_SAFE;
use fernet::Fernet;
use secrecy::{ExposeSecret, SecretString};
use sha2::{Digest, Sha256};
use thiserror::Error;

/// Python-compatible Fernet cipher for integration credentials stored by Newsly.
///
/// Python accepts either an already encoded Fernet key or an arbitrary secret whose SHA-256
/// digest becomes the Fernet key. Keeping that exact behavior in one provider-layer type lets
/// API and worker processes share encrypted rows without exposing the key or plaintext tokens.
#[derive(Clone)]
pub struct IntegrationTokenCipher {
    cipher: Arc<Fernet>,
}

impl IntegrationTokenCipher {
    /// Builds a cipher from the configured Newsly integration secret.
    ///
    /// # Errors
    ///
    /// Returns an error for an empty or otherwise unusable key.
    pub fn new(raw_key: &SecretString) -> Result<Self, IntegrationTokenCipherError> {
        let raw_key = raw_key.expose_secret().trim();
        if raw_key.is_empty() {
            return Err(IntegrationTokenCipherError::InvalidKey);
        }
        let cipher = Fernet::new(raw_key).or_else(|| {
            let digest = Sha256::digest(raw_key.as_bytes());
            Fernet::new(&URL_SAFE.encode(digest))
        });
        Ok(Self {
            cipher: Arc::new(cipher.ok_or(IntegrationTokenCipherError::InvalidKey)?),
        })
    }

    /// Encrypts one nonempty plaintext credential.
    ///
    /// # Errors
    ///
    /// Returns an error when the plaintext is empty.
    pub fn encrypt(&self, plaintext: &str) -> Result<String, IntegrationTokenCipherError> {
        if plaintext.is_empty() {
            return Err(IntegrationTokenCipherError::EmptyToken);
        }
        Ok(self.cipher.encrypt(plaintext.as_bytes()))
    }

    /// Decrypts one Fernet payload written by either the Python or Rust runtime.
    ///
    /// # Errors
    ///
    /// Returns an error for an empty, unauthenticated, or non-UTF-8 payload.
    pub fn decrypt(&self, encrypted: &str) -> Result<String, IntegrationTokenCipherError> {
        if encrypted.is_empty() {
            return Err(IntegrationTokenCipherError::EmptyToken);
        }
        let plaintext = self
            .cipher
            .decrypt(encrypted)
            .map_err(|_| IntegrationTokenCipherError::InvalidPayload)?;
        String::from_utf8(plaintext).map_err(|_| IntegrationTokenCipherError::InvalidPayload)
    }
}

impl Debug for IntegrationTokenCipher {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("IntegrationTokenCipher")
            .field("key", &"[REDACTED]")
            .finish()
    }
}

#[derive(Debug, Error, Clone, Copy, PartialEq, Eq)]
pub enum IntegrationTokenCipherError {
    #[error("integration token encryption key is invalid")]
    InvalidKey,
    #[error("integration token must not be empty")]
    EmptyToken,
    #[error("invalid encrypted integration token payload")]
    InvalidPayload,
}
