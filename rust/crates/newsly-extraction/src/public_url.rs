use std::fmt::{self, Display, Formatter};
use std::net::{IpAddr, Ipv4Addr, Ipv6Addr, SocketAddr};

use schemars::JsonSchema;
use serde::{Deserialize, Deserializer, Serialize, Serializer};
use url::{Host, Url};

use crate::ExtractionClientError;

const MAX_PUBLIC_URL_CHARACTERS: usize = 2_083;

/// A syntactically safe public HTTP URL.
///
/// Construction rejects credentials and non-public IP literals. The client additionally resolves
/// DNS immediately before the request; the Python service independently revalidates its fetch and
/// every redirect.
#[derive(Clone, Debug, PartialEq, Eq, Hash, JsonSchema)]
#[schemars(with = "String")]
pub struct PublicUrl(Url);

impl PublicUrl {
    /// Parse and validate an absolute HTTP(S) URL.
    ///
    /// # Errors
    ///
    /// Returns [`ExtractionClientError::InvalidPublicUrl`] for credentials, unsupported schemes,
    /// missing hosts, or non-public IP literals.
    pub fn parse(value: &str) -> Result<Self, ExtractionClientError> {
        if value.is_empty() || value.chars().count() > MAX_PUBLIC_URL_CHARACTERS {
            return Err(ExtractionClientError::InvalidPublicUrl {
                reason: "URL must contain between 1 and 2083 characters",
                source: None,
            });
        }
        let parsed =
            Url::parse(value).map_err(|source| ExtractionClientError::InvalidPublicUrl {
                reason: "URL could not be parsed",
                source: Some(source),
            })?;
        if !matches!(parsed.scheme(), "http" | "https") {
            return Err(ExtractionClientError::InvalidPublicUrl {
                reason: "only http and https URLs are accepted",
                source: None,
            });
        }
        if parsed.cannot_be_a_base() || parsed.host().is_none() {
            return Err(ExtractionClientError::InvalidPublicUrl {
                reason: "an absolute URL hostname is required",
                source: None,
            });
        }
        if !parsed.username().is_empty() || parsed.password().is_some() {
            return Err(ExtractionClientError::InvalidPublicUrl {
                reason: "URLs containing credentials are not accepted",
                source: None,
            });
        }
        if let Some(Host::Ipv4(address)) = parsed.host()
            && !is_public_ipv4(address)
        {
            return Err(ExtractionClientError::NonPublicAddress(address.into()));
        }
        if let Some(Host::Ipv6(address)) = parsed.host()
            && !is_public_ipv6(address)
        {
            return Err(ExtractionClientError::NonPublicAddress(address.into()));
        }
        Ok(Self(parsed))
    }

    pub fn as_url(&self) -> &Url {
        &self.0
    }

    pub fn as_str(&self) -> &str {
        self.0.as_str()
    }

    /// Resolve the host and require every returned address to be public.
    ///
    /// # Errors
    ///
    /// Returns an error when DNS resolution fails, returns no addresses, or includes a local,
    /// private, documentation, multicast, or otherwise non-public address.
    pub async fn validate_dns(&self) -> Result<(), ExtractionClientError> {
        self.resolve_public_addresses().await.map(|_| ())
    }

    /// Resolve the URL to the exact public socket addresses an HTTP client may use.
    ///
    /// Returning the validated addresses lets callers pin request dispatch to this resolution,
    /// closing the gap where a second resolver lookup could be DNS-rebound to a private address.
    ///
    /// # Errors
    ///
    /// Returns an error when DNS resolution fails, yields no addresses, or yields any address
    /// outside the public network.
    pub async fn resolve_public_addresses(&self) -> Result<Vec<SocketAddr>, ExtractionClientError> {
        let host = self
            .0
            .host_str()
            .ok_or(ExtractionClientError::InvalidPublicUrl {
                reason: "URL hostname is required",
                source: None,
            })?;
        let port =
            self.0
                .port_or_known_default()
                .ok_or(ExtractionClientError::InvalidPublicUrl {
                    reason: "URL port could not be inferred",
                    source: None,
                })?;
        let addresses = if let Ok(address) = host.parse::<IpAddr>() {
            vec![SocketAddr::new(address, port)]
        } else {
            tokio::net::lookup_host((host, port))
                .await
                .map_err(|source| ExtractionClientError::DnsResolution {
                    host: host.to_owned(),
                    source,
                })?
                .collect::<Vec<_>>()
        };
        if addresses.is_empty() {
            return Err(ExtractionClientError::NoDnsAddresses(host.to_owned()));
        }
        for socket in &addresses {
            let public = match socket.ip() {
                IpAddr::V4(ipv4) => is_public_ipv4(ipv4),
                IpAddr::V6(ipv6) => is_public_ipv6(ipv6),
            };
            if !public {
                return Err(ExtractionClientError::NonPublicAddress(socket.ip()));
            }
        }
        Ok(addresses)
    }
}

impl Display for PublicUrl {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        self.0.fmt(formatter)
    }
}

impl Serialize for PublicUrl {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: Serializer,
    {
        serializer.serialize_str(self.as_str())
    }
}

impl<'de> Deserialize<'de> for PublicUrl {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        let value = String::deserialize(deserializer)?;
        Self::parse(&value).map_err(serde::de::Error::custom)
    }
}

fn is_public_ipv4(address: Ipv4Addr) -> bool {
    let octets = address.octets();
    let protocol_assignment = octets[..3] == [192, 0, 0] && !matches!(octets[3], 9 | 10);
    !(address.is_unspecified()
        || address.is_loopback()
        || address.is_private()
        || address.is_link_local()
        || address.is_multicast()
        || address.is_broadcast()
        || octets[0] == 0
        || octets[0] >= 240
        || (octets[0] == 100 && (64..=127).contains(&octets[1]))
        || protocol_assignment
        || (octets[0] == 192 && octets[1] == 0 && octets[2] == 2)
        || (octets[0] == 198 && (octets[1] == 18 || octets[1] == 19))
        || (octets[0] == 198 && octets[1] == 51 && octets[2] == 100)
        || (octets[0] == 203 && octets[1] == 0 && octets[2] == 113))
}

fn is_public_ipv6(address: Ipv6Addr) -> bool {
    if let Some(ipv4) = address.to_ipv4_mapped() {
        return is_public_ipv4(ipv4);
    }
    let segments = address.segments();
    let local_nat64 = segments[0] == 0x0064 && segments[1] == 0xff9b && segments[2] == 1;
    let discard_only = segments[..4] == [0x0100, 0, 0, 0];
    let special_protocol_assignment = segments[0] == 0x2001
        && segments[1] <= 0x01ff
        && !matches!(segments, [0x2001, 1, 0, 0, 0, 0, 0, 1 | 2])
        && segments[1] != 3
        && !(segments[1] == 4 && segments[2] == 0x0112)
        && !(0x20..=0x3f).contains(&segments[1]);
    let documentation = segments[0] == 0x2001 && segments[1] == 0x0db8;
    let documentation_v2 = segments[0] == 0x3fff && segments[1] <= 0x0fff;
    !(address.is_unspecified()
        || address.is_loopback()
        || address.is_multicast()
        || (segments[0] & 0xfe00) == 0xfc00
        || (segments[0] & 0xffc0) == 0xfe80
        || (segments[0] & 0xffc0) == 0xfec0
        || (segments[0] == 0 && segments[1..6] == [0, 0, 0, 0, 0])
        || local_nat64
        || discard_only
        || special_protocol_assignment
        || segments[0] == 0x2002
        || documentation
        || documentation_v2)
}

#[cfg(test)]
mod tests {
    use super::PublicUrl;

    #[test]
    fn rejects_private_literals_and_credentials() {
        assert!(PublicUrl::parse("http://127.0.0.1/private").is_err());
        assert!(PublicUrl::parse("http://[::1]/private").is_err());
        assert!(PublicUrl::parse("http://[::ffff:127.0.0.1]/private").is_err());
        assert!(PublicUrl::parse("https://user:secret@example.com/").is_err());
        assert!(PublicUrl::parse("https://192.0.0.1/private").is_err());
        assert!(PublicUrl::parse("https://[64:ff9b:1::1]/private").is_err());
        assert!(PublicUrl::parse("https://[2001:2::1]/private").is_err());
        assert!(PublicUrl::parse("https://[3fff::1]/private").is_err());
        assert!(PublicUrl::parse(&format!("https://example.com/{}", "x".repeat(2_100))).is_err());
    }

    #[test]
    fn accepts_public_https_hostname() {
        let url = PublicUrl::parse("https://example.com/article").expect("valid public URL");
        assert_eq!(url.as_str(), "https://example.com/article");
    }
}
