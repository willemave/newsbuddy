//! Sandbox egress policy mutation and deny-by-default reset.

use std::collections::BTreeMap;
use std::fmt;
use std::net::IpAddr;

use serde::{Deserialize, Serialize};

use crate::error::E2bError;

const MAX_NETWORK_ENTRIES: usize = 256;
const MAX_RULES_PER_HOST: usize = 32;
const MAX_TRANSFORM_HEADERS: usize = 64;
const MAX_HEADER_VALUE_BYTES: usize = 8 * 1024;

#[derive(Clone, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct NetworkTransform {
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub headers: BTreeMap<String, String>,
}

impl fmt::Debug for NetworkTransform {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let names = self.headers.keys().collect::<Vec<_>>();
        f.debug_struct("NetworkTransform")
            .field("header_names", &names)
            .field("header_values", &"[REDACTED]")
            .finish()
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct NetworkRule {
    pub transform: NetworkTransform,
}

/// Replacement policy for an E2B sandbox's complete egress configuration.
#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct NetworkPolicy {
    #[serde(rename = "allowOut", default, skip_serializing_if = "Vec::is_empty")]
    pub allow_out: Vec<String>,
    #[serde(rename = "denyOut", default, skip_serializing_if = "Vec::is_empty")]
    pub deny_out: Vec<String>,
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub rules: BTreeMap<String, Vec<NetworkRule>>,
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

    pub fn allow_hosts(hosts: impl IntoIterator<Item = String>) -> Result<Self, E2bError> {
        let mut allow_out = Vec::new();
        for host in hosts {
            let host = host.trim().to_ascii_lowercase();
            if !valid_domain_pattern(&host) {
                return Err(E2bError::InvalidInput(format!(
                    "invalid egress host {host:?}"
                )));
            }
            allow_out.push(host);
        }
        allow_out.sort();
        allow_out.dedup();
        Ok(Self {
            allow_out,
            // E2B accepts the IPv4 catch-all selector but rejects `::/0` as an invalid denied
            // CIDR. Internet access remains disabled unless an allowed hostname matches, so do
            // not send a selector the live control plane cannot apply.
            deny_out: vec!["0.0.0.0/0".to_owned()],
            allow_internet_access: Some(false),
            rules: BTreeMap::new(),
        })
    }

    pub fn validate(&self) -> Result<(), E2bError> {
        if self.allow_out.len() > MAX_NETWORK_ENTRIES
            || self.deny_out.len() > MAX_NETWORK_ENTRIES
            || self.rules.len() > MAX_NETWORK_ENTRIES
        {
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
        for (host, rules) in &self.rules {
            if !valid_domain_pattern(host) || rules.len() > MAX_RULES_PER_HOST {
                return Err(E2bError::InvalidInput(format!(
                    "invalid network transform host {host:?}"
                )));
            }
            for rule in rules {
                if rule.transform.headers.len() > MAX_TRANSFORM_HEADERS {
                    return Err(E2bError::InvalidInput(format!(
                        "network transform for {host:?} contains too many headers"
                    )));
                }
                for (name, value) in &rule.transform.headers {
                    if http::HeaderName::from_bytes(name.as_bytes()).is_err()
                        || value.len() > MAX_HEADER_VALUE_BYTES
                        || http::HeaderValue::from_str(value).is_err()
                    {
                        return Err(E2bError::InvalidInput(format!(
                            "invalid network transform header {name:?}"
                        )));
                    }
                }
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
    fn host_allowlist_accepts_domains_and_leading_wildcards_only() {
        NetworkPolicy::allow_hosts(["api.example.com".to_owned(), "*.cdn.example".to_owned()])
            .expect("valid domains");
        assert!(NetworkPolicy::allow_hosts(["bad host".to_owned()]).is_err());
        assert!(NetworkPolicy::allow_hosts(["api.*.example".to_owned()]).is_err());
    }

    #[test]
    fn deny_all_is_a_valid_replacement_policy() {
        let policy = NetworkPolicy::deny_all();
        policy.validate().expect("deny-all policy");
        assert_eq!(policy.allow_internet_access, Some(false));
        assert!(policy.allow_out.is_empty());
        assert!(policy.deny_out.is_empty());
    }

    #[test]
    fn host_allowlist_omits_the_ipv6_catch_all_rejected_by_e2b() {
        let policy = NetworkPolicy::allow_hosts(["api.example.com".to_owned()])
            .expect("valid allowlist policy");
        assert_eq!(policy.deny_out, ["0.0.0.0/0"]);
        assert!(!policy.deny_out.iter().any(|selector| selector == "::/0"));
    }
}
