//! Rust-owned generation of Newsly's Swift wire contracts.

#![forbid(unsafe_code)]

mod policy;
mod schema;
mod swift;

use anyhow::Context;

pub use policy::{ClientTarget, Policy};

/// Policy format understood by this generator.
pub const POLICY_VERSION: u32 = 1;

/// All generated client source files for one `OpenAPI` document and policy.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GeneratedClients {
    pub app_swift_contracts: String,
    pub app_swift_models: String,
    pub share_swift_contracts: String,
    pub share_swift_models: String,
}

/// Parse and validate a reviewed policy.
///
/// # Errors
///
/// Returns an error for invalid TOML or an unsupported policy version.
pub fn parse_policy(source: &str) -> anyhow::Result<Policy> {
    let policy: Policy = toml::from_str(source).context("invalid client codegen policy TOML")?;
    policy.validate()?;
    Ok(policy)
}

/// Generate every checked-in client artifact from the authoritative `OpenAPI` document.
///
/// # Errors
///
/// Returns an error when the `OpenAPI` document is invalid or a reviewed policy entry no longer
/// matches its schema.
pub fn generate_clients(
    openapi_source: &str,
    policy_source: &str,
) -> anyhow::Result<GeneratedClients> {
    let policy = parse_policy(policy_source)?;
    let document = schema::Document::parse(openapi_source, &policy)?;
    Ok(GeneratedClients {
        app_swift_contracts: swift::render_enums(&document, &policy, ClientTarget::AppSwift)?,
        app_swift_models: swift::render_models(&document, &policy, ClientTarget::AppSwift)?,
        share_swift_contracts: swift::render_enums(&document, &policy, ClientTarget::ShareSwift)?,
        share_swift_models: swift::render_models(&document, &policy, ClientTarget::ShareSwift)?,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    const REVIEWED_POLICY: &str = include_str!("../../../../contracts/client_codegen_policy.toml");

    #[test]
    fn checked_in_policy_parses() {
        let policy = parse_policy(REVIEWED_POLICY).expect("checked-in policy is valid");
        assert_eq!(policy.version, POLICY_VERSION);
        assert!(policy.enums.len() > 40);
        assert!(policy.models.len() > 90);
    }
}
