use std::env;
use std::fmt::{self, Debug, Formatter};
use std::time::Duration;

use base64::Engine as _;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use chrono::{DateTime, Utc};
use jsonwebtoken::crypto::sign;
use jsonwebtoken::{Algorithm, DecodingKey, EncodingKey, Validation, decode};
use secrecy::{ExposeSecret, SecretString};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use thiserror::Error;

use crate::encoding::hex_encode;

const PYJWT_HS256_HEADER: &str = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9";
const DEFAULT_SIGNED_URL_TTL_SECONDS: u64 = 900;
const MIN_SIGNED_URL_TTL_SECONDS: u64 = 60;
const MAX_SIGNED_URL_TTL_SECONDS: u64 = 86_400;

#[derive(Clone)]
pub(super) struct LearningDeckTokenSigner {
    secret: SecretString,
    private_ttl: Duration,
}

impl Debug for LearningDeckTokenSigner {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("LearningDeckTokenSigner")
            .field("secret", &"[REDACTED]")
            .field("private_ttl", &self.private_ttl)
            .finish()
    }
}

impl LearningDeckTokenSigner {
    /// Loads the same HS256 key and private-URL lifetime as the coexistence Python runtime.
    ///
    /// # Errors
    ///
    /// Returns an error for missing/unsupported signing configuration.
    pub(super) fn from_environment() -> Result<Self, LearningDeckTokenError> {
        let algorithm = env::var("JWT_ALGORITHM").unwrap_or_else(|_| "HS256".to_owned());
        if algorithm != "HS256" {
            return Err(LearningDeckTokenError::UnsupportedAlgorithm(algorithm));
        }
        let secret = env::var("JWT_SECRET_KEY")
            .map(SecretString::from)
            .map_err(|_| LearningDeckTokenError::MissingSecret)?;
        if secret.expose_secret().is_empty() {
            return Err(LearningDeckTokenError::MissingSecret);
        }
        let ttl_seconds = env::var("LEARNING_DECK_SIGNED_URL_TTL_SECONDS")
            .ok()
            .map(|value| {
                value
                    .parse::<u64>()
                    .map_err(|_| LearningDeckTokenError::InvalidLifetime(value))
            })
            .transpose()?
            .unwrap_or(DEFAULT_SIGNED_URL_TTL_SECONDS);
        if !(MIN_SIGNED_URL_TTL_SECONDS..=MAX_SIGNED_URL_TTL_SECONDS).contains(&ttl_seconds) {
            return Err(LearningDeckTokenError::InvalidLifetime(
                ttl_seconds.to_string(),
            ));
        }
        Ok(Self {
            secret,
            private_ttl: Duration::from_secs(ttl_seconds),
        })
    }

    pub(super) fn private_token(
        &self,
        deck_id: i64,
        user_id: i64,
        now: DateTime<Utc>,
    ) -> Result<SignedLearningDeckToken, LearningDeckTokenError> {
        let lifetime = i64::try_from(self.private_ttl.as_secs())
            .map_err(|_| LearningDeckTokenError::LifetimeOverflow)?;
        let expires_at = now
            .checked_add_signed(chrono::Duration::seconds(lifetime))
            .ok_or(LearningDeckTokenError::LifetimeOverflow)?;
        let claims = PrivateClaims {
            token_type: "learning_deck_signed",
            deck_id,
            user_id,
            exp: expires_at.timestamp(),
        };
        Ok(SignedLearningDeckToken {
            token: self.encode_python_compatible(&claims)?,
            expires_at,
        })
    }

    pub(super) fn share_token(
        &self,
        deck_id: i64,
        nonce: &str,
    ) -> Result<String, LearningDeckTokenError> {
        self.encode_python_compatible(&ShareClaims {
            token_type: "learning_deck_share",
            deck_id,
            nonce,
        })
    }

    pub(super) fn decode_private_token(
        &self,
        token: &str,
    ) -> Result<PrivateLearningDeckToken, LearningDeckTokenError> {
        let mut validation = Validation::new(Algorithm::HS256);
        validation.set_required_spec_claims(&["exp"]);
        validation.leeway = 0;
        let claims = decode::<DecodedPrivateClaims>(
            token,
            &DecodingKey::from_secret(self.secret.expose_secret().as_bytes()),
            &validation,
        )?
        .claims;
        if claims.token_type != "learning_deck_signed" || claims.deck_id <= 0 || claims.user_id <= 0
        {
            return Err(LearningDeckTokenError::InvalidClaims);
        }
        Ok(PrivateLearningDeckToken {
            deck_id: claims.deck_id,
            user_id: claims.user_id,
        })
    }

    pub(super) fn decode_share_token(
        &self,
        token: &str,
    ) -> Result<ShareLearningDeckToken, LearningDeckTokenError> {
        let mut validation = Validation::new(Algorithm::HS256);
        validation.required_spec_claims.clear();
        validation.validate_exp = false;
        let claims = decode::<DecodedShareClaims>(
            token,
            &DecodingKey::from_secret(self.secret.expose_secret().as_bytes()),
            &validation,
        )?
        .claims;
        if claims.token_type != "learning_deck_share"
            || claims.deck_id <= 0
            || claims.nonce.is_empty()
        {
            return Err(LearningDeckTokenError::InvalidClaims);
        }
        Ok(ShareLearningDeckToken {
            deck_id: claims.deck_id,
            nonce: claims.nonce,
        })
    }

    fn encode_python_compatible<T: Serialize>(
        &self,
        claims: &T,
    ) -> Result<String, LearningDeckTokenError> {
        let payload = URL_SAFE_NO_PAD.encode(serde_json::to_vec(claims)?);
        let signing_input = format!("{PYJWT_HS256_HEADER}.{payload}");
        let signature = sign(
            signing_input.as_bytes(),
            &EncodingKey::from_secret(self.secret.expose_secret().as_bytes()),
            Algorithm::HS256,
        )?;
        Ok(format!("{signing_input}.{signature}"))
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct SignedLearningDeckToken {
    pub token: String,
    pub expires_at: DateTime<Utc>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct PrivateLearningDeckToken {
    pub deck_id: i64,
    pub user_id: i64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct ShareLearningDeckToken {
    pub deck_id: i64,
    pub nonce: String,
}

#[derive(Serialize)]
struct PrivateClaims<'a> {
    #[serde(rename = "type")]
    token_type: &'a str,
    deck_id: i64,
    user_id: i64,
    exp: i64,
}

#[derive(Serialize)]
struct ShareClaims<'a> {
    #[serde(rename = "type")]
    token_type: &'a str,
    deck_id: i64,
    nonce: &'a str,
}

#[derive(Deserialize)]
struct DecodedPrivateClaims {
    #[serde(rename = "type")]
    token_type: String,
    deck_id: i64,
    user_id: i64,
    #[serde(rename = "exp")]
    _exp: i64,
}

#[derive(Deserialize)]
struct DecodedShareClaims {
    #[serde(rename = "type")]
    token_type: String,
    deck_id: i64,
    nonce: String,
}

pub(super) fn generate_share_nonce() -> Result<String, getrandom::Error> {
    let mut bytes = [0_u8; 24];
    getrandom::fill(&mut bytes)?;
    Ok(URL_SAFE_NO_PAD.encode(bytes))
}

#[must_use]
pub(super) fn hash_learning_deck_token(token: &str) -> String {
    hex_encode(&Sha256::digest(token.as_bytes()))
}

#[derive(Debug, Error)]
pub(super) enum LearningDeckTokenError {
    #[error("JWT_SECRET_KEY is required for Learning Deck URLs")]
    MissingSecret,
    #[error("unsupported JWT algorithm {0:?}; Learning Deck coexistence requires HS256")]
    UnsupportedAlgorithm(String),
    #[error("invalid Learning Deck signed URL lifetime {0:?}")]
    InvalidLifetime(String),
    #[error("Learning Deck signed URL lifetime overflowed")]
    LifetimeOverflow,
    #[error("Learning Deck claims could not be serialized")]
    Serialization(#[from] serde_json::Error),
    #[error("Learning Deck token claims are invalid")]
    InvalidClaims,
    #[error("Learning Deck JWT signing failed")]
    Jwt(#[from] jsonwebtoken::errors::Error),
}

#[cfg(test)]
mod tests {
    use super::*;

    const PYTHON_SHARE_TOKEN: &str = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ0eXBlIjoibGVhcm5pbmdfZGVja19zaGFyZSIsImRlY2tfaWQiOjQyLCJub25jZSI6InB5dGhvbi1ub25jZSJ9.LcVBk9TI8hESL13uJqEhmDf1XmlI-aDp9Wkc_q0AWP0";
    const PYTHON_PRIVATE_TOKEN: &str = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ0eXBlIjoibGVhcm5pbmdfZGVja19zaWduZWQiLCJkZWNrX2lkIjo0MiwidXNlcl9pZCI6NywiZXhwIjo0MTAyNDQ0ODAwfQ.9trgF8avMx4pimf_Z7YeNtMRBpOOPiPGImjiCiA8ixc";

    fn python_signer() -> LearningDeckTokenSigner {
        LearningDeckTokenSigner {
            secret: SecretString::from("python-coexistence-secret"),
            private_ttl: Duration::from_secs(DEFAULT_SIGNED_URL_TTL_SECONDS),
        }
    }

    #[test]
    fn decodes_python_generated_share_token() {
        assert_eq!(
            python_signer()
                .decode_share_token(PYTHON_SHARE_TOKEN)
                .unwrap(),
            ShareLearningDeckToken {
                deck_id: 42,
                nonce: "python-nonce".to_owned(),
            }
        );
    }

    #[test]
    fn decodes_python_generated_private_token() {
        assert_eq!(
            python_signer()
                .decode_private_token(PYTHON_PRIVATE_TOKEN)
                .unwrap(),
            PrivateLearningDeckToken {
                deck_id: 42,
                user_id: 7,
            }
        );
    }

    #[test]
    fn rejects_share_token_as_private_token() {
        assert!(
            python_signer()
                .decode_private_token(PYTHON_SHARE_TOKEN)
                .is_err()
        );
    }

    #[test]
    fn rejects_expired_private_token() {
        let signer = python_signer();
        let expired = signer
            .private_token(42, 7, DateTime::UNIX_EPOCH)
            .unwrap()
            .token;
        assert!(signer.decode_private_token(&expired).is_err());
    }
}
