//! Sandbox egress policy mutation and deny-by-default reset.

use std::net::IpAddr;

use serde::{Deserialize, Serialize};

use crate::error::E2bError;

const MAX_NETWORK_ENTRIES: usize = 256;

/// Replacement policy for an E2B sandbox's complete egress configuration.
#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct NetworkPolicy {
    #[serde(rename = "allowOut", default, skip_serializing_if = "Vec::is_empty")]
    pub allow_out: Vec<String>,
    #[serde(rename = "denyOut", default, skip_serializing_if = "Vec::is_empty")]
    pub deny_out: Vec<String>,
    #[serde(
        rename = "allow_internet_access",
        skip_serializing_if = "Option::is_none"
    )]
    pub allow_internet_access: Option<bool>,
}

impl NetworkPolicy {
    #[must_use]
    pub fn deny_all() -> Self {
        Self {
            allow_internet_access: Some(false),
            ..Self::default()
        }
    }

    pub fn validate(&self) -> Result<(), E2bError> {
        if self.allow_out.len() > MAX_NETWORK_ENTRIES || self.deny_out.len() > MAX_NETWORK_ENTRIES {
            return Err(E2bError::InvalidInput(
                "network policy contains too many entries".to_owned(),
            ));
        }
        for destination in &self.allow_out {
            if !valid_network_destination(destination, true) {
                return Err(E2bError::InvalidInput(format!(
                    "invalid allowed network destination {destination:?}"
                )));
            }
        }
        for destination in &self.deny_out {
            if !valid_network_destination(destination, false) {
                return Err(E2bError::InvalidInput(format!(
                    "invalid denied network destination {destination:?}"
                )));
            }
        }
        Ok(())
    }
}

fn valid_network_destination(destination: &str, allow_domain: bool) -> bool {
    if destination.is_empty() || destination.trim() != destination || destination.len() > 253 {
        return false;
    }
    if destination.parse::<IpAddr>().is_ok() {
        return true;
    }
    if let Some((address, prefix)) = destination.rsplit_once('/') {
        let Ok(address) = address.parse::<IpAddr>() else {
            return false;
        };
        let Ok(prefix) = prefix.parse::<u8>() else {
            return false;
        };
        return match address {
            IpAddr::V4(_) => prefix <= 32,
            IpAddr::V6(_) => prefix <= 128,
        };
    }
    allow_domain && valid_domain_pattern(destination)
}

fn valid_domain_pattern(pattern: &str) -> bool {
    if pattern.is_empty() || pattern.trim() != pattern || pattern.len() > 253 {
        return false;
    }
    let domain = pattern.strip_prefix("*.").unwrap_or(pattern);
    !domain.is_empty()
        && domain.split('.').all(|label| {
            !label.is_empty()
                && label.len() <= 63
                && label
                    .bytes()
                    .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-')
                && label
                    .as_bytes()
                    .first()
                    .is_some_and(u8::is_ascii_alphanumeric)
                && label
                    .as_bytes()
                    .last()
                    .is_some_and(u8::is_ascii_alphanumeric)
        })
}

#[cfg(test)]
mod tests {
    use super::NetworkPolicy;

    #[test]
    fn deny_all_is_a_valid_replacement_policy() {
        let policy = NetworkPolicy::deny_all();
        policy.validate().expect("deny-all policy");
        assert_eq!(policy.allow_internet_access, Some(false));
        assert!(policy.allow_out.is_empty());
        assert!(policy.deny_out.is_empty());
    }
}
