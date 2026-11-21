use std::env;

use secrecy::SecretString;
use thiserror::Error;

#[derive(Debug, Error, PartialEq, Eq)]
pub enum ProviderEnvError {
    #[error("{name} must be {expected}")]
    InvalidInteger {
        name: &'static str,
        expected: &'static str,
    },
}

/// Trimmed, non-empty environment value.
pub(crate) fn clean_env(name: &str) -> Option<String> {
    env::var(name)
        .ok()
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty())
}

/// Trimmed, non-empty secret; surrounding whitespace in a pasted key is never sent upstream.
pub(crate) fn secret_env(name: &str) -> Option<SecretString> {
    clean_env(name).map(SecretString::from)
}

pub(crate) fn u64_from_env(
    name: &'static str,
    default: u64,
    expected: &'static str,
) -> Result<u64, ProviderEnvError> {
    u64_from_lookup(name, default, expected, |name| env::var(name).ok())
}

fn u64_from_lookup(
    name: &'static str,
    default: u64,
    expected: &'static str,
    lookup: impl Fn(&str) -> Option<String>,
) -> Result<u64, ProviderEnvError> {
    let Some(value) = lookup(name) else {
        return Ok(default);
    };
    let value = value.trim();
    if value.is_empty() {
        return Ok(default);
    }
    value
        .parse::<u64>()
        .map_err(|_| ProviderEnvError::InvalidInteger { name, expected })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn integer_lookup_defaults_for_missing_or_blank_values() {
        assert_eq!(
            u64_from_lookup("TIMEOUT", 30, "an unsigned integer", |_| None),
            Ok(30)
        );
        assert_eq!(
            u64_from_lookup("TIMEOUT", 30, "an unsigned integer", |_| {
                Some("  ".to_owned())
            }),
            Ok(30)
        );
    }

    #[test]
    fn integer_lookup_redacts_invalid_values() {
        let error = u64_from_lookup("TIMEOUT", 30, "an unsigned integer", |_| {
            Some("30s".to_owned())
        })
        .expect_err("invalid values must fail");
        assert_eq!(
            error,
            ProviderEnvError::InvalidInteger {
                name: "TIMEOUT",
                expected: "an unsigned integer"
            }
        );
        assert!(!error.to_string().contains("30s"));
    }

    #[test]
    fn callers_can_preserve_existing_integer_clamps() {
        let value = u64_from_lookup("TIMEOUT", 30, "an unsigned integer", |_| {
            Some("9999".to_owned())
        })
        .expect("numeric values parse");
        assert_eq!(value.clamp(5, 120), 120);
    }
}
