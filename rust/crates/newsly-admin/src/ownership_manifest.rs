use std::collections::BTreeSet;
use std::fs;
use std::path::{Path, PathBuf};

use newsly_db::OwnershipSeed;
use newsly_domain::{ResourceKey, ResourceKind, RuntimeOwner};
use serde::Deserialize;
use thiserror::Error;

const SUPPORTED_MANIFEST_VERSION: u32 = 2;

#[derive(Debug, Clone, Deserialize)]
pub struct OwnershipPolicyManifest {
    pub version: u32,
    pub scope: String,
    pub runtime_registry: String,
    #[serde(default)]
    routes: Vec<RoutePolicy>,
    #[serde(default)]
    tasks: Vec<TaskPolicy>,
    #[serde(default)]
    e2b_namespaces: Vec<NamespacePolicy>,
    #[serde(skip)]
    source_directory: PathBuf,
}

impl OwnershipPolicyManifest {
    /// Validates policy completeness and converts the authoritative-state document into registry
    /// seeds.
    ///
    /// The returned seeds create missing resources only. They never overwrite established live
    /// ownership decisions.
    ///
    /// # Errors
    ///
    /// Returns [`ManifestValidationError`] for malformed ownership policy or missing task schemas.
    pub fn registry_seeds(&self) -> Result<Vec<OwnershipSeed>, ManifestValidationError> {
        self.validate_header()?;
        let mut seen = BTreeSet::new();
        let mut seeds =
            Vec::with_capacity(self.routes.len() + self.tasks.len() + self.e2b_namespaces.len());
        for route in &self.routes {
            validate_policy_fields(
                &route.operation_id,
                &route.current_owner,
                &route.database_writer,
            )?;
            if route.method.trim().is_empty() || route.path.trim().is_empty() {
                return Err(ManifestValidationError::IncompleteResource(
                    route.operation_id.clone(),
                ));
            }
            push_seed(
                &mut seeds,
                &mut seen,
                ResourceKind::RouteGroup,
                &route.operation_id,
                &route.current_owner,
            )?;
        }
        for task in &self.tasks {
            validate_policy_fields(&task.task_type, &task.current_owner, &task.database_writer)?;
            if task.queue.trim().is_empty()
                || task.handler.trim().is_empty()
                || task.payload_schema.trim().is_empty()
            {
                return Err(ManifestValidationError::IncompleteResource(
                    task.task_type.clone(),
                ));
            }
            let schema_path = self.source_directory.join(&task.payload_schema);
            if !schema_path.is_file() {
                return Err(ManifestValidationError::MissingPayloadSchema(schema_path));
            }
            push_seed(
                &mut seeds,
                &mut seen,
                ResourceKind::TaskType,
                &task.task_type,
                &task.current_owner,
            )?;
        }
        for namespace in &self.e2b_namespaces {
            validate_policy_fields(
                &namespace.namespace,
                &namespace.current_owner,
                &namespace.database_writer,
            )?;
            push_seed(
                &mut seeds,
                &mut seen,
                ResourceKind::VmNamespace,
                &namespace.namespace,
                &namespace.current_owner,
            )?;
        }
        if self.routes.is_empty() || self.tasks.is_empty() || self.e2b_namespaces.is_empty() {
            return Err(ManifestValidationError::MissingResourceFamily);
        }
        Ok(seeds)
    }

    fn validate_header(&self) -> Result<(), ManifestValidationError> {
        if self.version != SUPPORTED_MANIFEST_VERSION {
            return Err(ManifestValidationError::UnsupportedVersion(self.version));
        }
        if self.scope.trim().is_empty() || self.runtime_registry != "runtime_ownership" {
            return Err(ManifestValidationError::InvalidHeader);
        }
        Ok(())
    }
}

/// Loads and validates the checked-in ownership policy manifest.
///
/// # Errors
///
/// Returns [`ManifestValidationError`] for file, TOML, or policy errors.
pub fn load_ownership_policy_manifest(
    path: &Path,
) -> Result<OwnershipPolicyManifest, ManifestValidationError> {
    let contents = fs::read_to_string(path).map_err(|source| ManifestValidationError::Read {
        path: path.to_path_buf(),
        source,
    })?;
    let mut manifest: OwnershipPolicyManifest =
        toml::from_str(&contents).map_err(ManifestValidationError::Parse)?;
    manifest.source_directory = path
        .parent()
        .unwrap_or_else(|| Path::new("."))
        .to_path_buf();
    manifest.registry_seeds()?;
    Ok(manifest)
}

fn validate_policy_fields(
    resource: &str,
    current_owner: &str,
    database_writer: &str,
) -> Result<(), ManifestValidationError> {
    if [resource, current_owner, database_writer]
        .iter()
        .any(|value| value.trim().is_empty())
    {
        return Err(ManifestValidationError::IncompleteResource(
            resource.to_owned(),
        ));
    }
    let current = current_owner.parse::<RuntimeOwner>()?;
    let writer = database_writer.parse::<RuntimeOwner>()?;
    if current != writer {
        return Err(ManifestValidationError::DatabaseWriterMismatch {
            resource: resource.to_owned(),
            owner: current,
            writer,
        });
    }
    Ok(())
}

fn push_seed(
    seeds: &mut Vec<OwnershipSeed>,
    seen: &mut BTreeSet<(String, String)>,
    resource_kind: ResourceKind,
    resource_key: &str,
    active_owner: &str,
) -> Result<(), ManifestValidationError> {
    let key = ResourceKey::new(resource_key)?;
    if !seen.insert((resource_kind.as_str().to_owned(), key.to_string())) {
        return Err(ManifestValidationError::DuplicateResource {
            resource_kind,
            resource_key: key.to_string(),
        });
    }
    seeds.push(OwnershipSeed {
        resource_kind,
        resource_key: key,
        active_owner: active_owner.parse()?,
    });
    Ok(())
}

#[derive(Debug, Clone, Deserialize)]
struct RoutePolicy {
    method: String,
    path: String,
    operation_id: String,
    current_owner: String,
    database_writer: String,
}

#[derive(Debug, Clone, Deserialize)]
struct TaskPolicy {
    task_type: String,
    queue: String,
    payload_schema: String,
    handler: String,
    current_owner: String,
    database_writer: String,
}

#[derive(Debug, Clone, Deserialize)]
struct NamespacePolicy {
    namespace: String,
    current_owner: String,
    database_writer: String,
}

#[derive(Debug, Error)]
pub enum ManifestValidationError {
    #[error("unable to read ownership manifest {path}")]
    Read {
        path: PathBuf,
        #[source]
        source: std::io::Error,
    },
    #[error("ownership manifest is not valid TOML")]
    Parse(#[source] toml::de::Error),
    #[error("unsupported ownership manifest version {0}")]
    UnsupportedVersion(u32),
    #[error("ownership manifest header is incomplete or names a noncanonical runtime registry")]
    InvalidHeader,
    #[error("ownership manifest must include routes, task types, and E2B namespaces")]
    MissingResourceFamily,
    #[error("ownership manifest resource {0:?} is incomplete")]
    IncompleteResource(String),
    #[error(
        "ownership manifest resource {resource:?} is owned by {owner} but declares database writer {writer}"
    )]
    DatabaseWriterMismatch {
        resource: String,
        owner: RuntimeOwner,
        writer: RuntimeOwner,
    },
    #[error("task payload schema does not exist: {0}")]
    MissingPayloadSchema(PathBuf),
    #[error("ownership manifest contains duplicate {resource_kind}:{resource_key}")]
    DuplicateResource {
        resource_kind: ResourceKind,
        resource_key: String,
    },
    #[error(transparent)]
    InvalidValue(#[from] newsly_domain::InvalidOwnershipValue),
}

#[cfg(test)]
mod tests {
    use std::path::Path;

    use super::load_ownership_policy_manifest;

    #[test]
    fn checked_in_manifest_is_accepted() {
        let path =
            Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../contracts/policy-manifest.toml");
        let manifest = load_ownership_policy_manifest(&path).unwrap();
        assert!(!manifest.registry_seeds().unwrap().is_empty());
    }
}
