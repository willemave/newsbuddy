use serde::Deserialize;
use serde_json::Value;
use sha2::{Digest, Sha256};
use sqlx::{PgConnection, query_scalar};
use thiserror::Error;

use crate::migrations::BASELINE_VERSION;

pub(crate) const EXPECTED_ALEMBIC_HEAD: &str = "20260829_02";

const BASELINE_SQL: &str =
    include_str!("../migrations/20260830000000_alembic_20260829_02_baseline.sql");
const MANIFEST_JSON: &str = include_str!("../baseline/manifest.json");
const EXPECTED_CATALOG_JSON: &str = include_str!("../baseline/catalog-inventory.json");
const EXPECTED_DATA_JSON: &str = include_str!("../baseline/data-invariants.json");
const EXPECTED_ROLE_POLICY_JSON: &str = include_str!("../baseline/role-policy.json");

const CATALOG_INVENTORY_SQL: &str = include_str!("catalog_inventory.sql");
const DATA_INVARIANTS_SQL: &str = include_str!("data_invariants.sql");
const ROLE_POLICY_SQL: &str = include_str!("role_policy.sql");

const LEARNING_DECK_ACTIVE_PREDICATE: &str = "((status)::text = ANY ((ARRAY['queued'::character varying, 'preparing'::character varying, 'generating'::character varying, 'validating'::character varying, 'publishing'::character varying])::text[]))";
const LEGACY_LEARNING_DECK_ACTIVE_PREDICATE: &str = "((status)::text = ANY (ARRAY[('queued'::character varying)::text, ('preparing'::character varying)::text, ('generating'::character varying)::text, ('validating'::character varying)::text, ('publishing'::character varying)::text]))";
const PROCESSING_TASK_ACTIVE_PREDICATE: &str = "((dedupe_key IS NOT NULL) AND ((status)::text = ANY ((ARRAY['pending'::character varying, 'processing'::character varying])::text[])))";
const LEGACY_PROCESSING_TASK_ACTIVE_PREDICATE: &str = "((dedupe_key IS NOT NULL) AND ((status)::text = ANY (ARRAY[('pending'::character varying)::text, ('processing'::character varying)::text])))";

struct LegacyIndexRendering {
    name: &'static str,
    definition_prefix: &'static str,
    canonical_predicate: &'static str,
    legacy_predicate: &'static str,
}

const LEGACY_INDEX_RENDERINGS: &[LegacyIndexRendering] = &[
    LegacyIndexRendering {
        name: "uq_learning_deck_runs_user_active",
        definition_prefix: "CREATE UNIQUE INDEX uq_learning_deck_runs_user_active ON public.learning_deck_runs USING btree (user_id) WHERE ",
        canonical_predicate: LEARNING_DECK_ACTIVE_PREDICATE,
        legacy_predicate: LEGACY_LEARNING_DECK_ACTIVE_PREDICATE,
    },
    LegacyIndexRendering {
        name: "uq_processing_tasks_dedupe_key_active",
        definition_prefix: "CREATE UNIQUE INDEX uq_processing_tasks_dedupe_key_active ON public.processing_tasks USING btree (dedupe_key) WHERE ",
        canonical_predicate: PROCESSING_TASK_ACTIVE_PREDICATE,
        legacy_predicate: LEGACY_PROCESSING_TASK_ACTIVE_PREDICATE,
    },
];

#[derive(Debug, Deserialize)]
struct BaselineManifest {
    format_version: u32,
    baseline_version: i64,
    alembic_head: String,
    minimum_postgresql_major: i32,
    baseline_sql_sha256: String,
    catalog_sha256: String,
    legacy_alembic_catalog_sha256: String,
    data_invariants_sha256: String,
    role_policy_sha256: String,
}

#[derive(Debug)]
pub(crate) struct BaselineEvidence {
    manifest: BaselineManifest,
    catalog: Value,
    legacy_alembic_catalog: Value,
    data: Value,
    role_policy: Value,
}

impl BaselineEvidence {
    pub(crate) fn load() -> Result<Self, FingerprintError> {
        let manifest: BaselineManifest = serde_json::from_str(MANIFEST_JSON).map_err(|source| {
            FingerprintError::InvalidEmbeddedJson {
                artifact: "manifest.json",
                source,
            }
        })?;
        let catalog = parse_embedded_json("catalog-inventory.json", EXPECTED_CATALOG_JSON)?;
        let legacy_alembic_catalog = legacy_alembic_catalog_snapshot(&catalog)?;
        let data = parse_embedded_json("data-invariants.json", EXPECTED_DATA_JSON)?;
        let role_policy = parse_embedded_json("role-policy.json", EXPECTED_ROLE_POLICY_JSON)?;

        if manifest.format_version != 2 {
            return Err(FingerprintError::ManifestInvariant(format!(
                "unsupported baseline manifest format {}",
                manifest.format_version
            )));
        }
        if manifest.baseline_version != BASELINE_VERSION {
            return Err(FingerprintError::ManifestInvariant(format!(
                "manifest baseline version {} does not match embedded version {BASELINE_VERSION}",
                manifest.baseline_version
            )));
        }
        if manifest.alembic_head != EXPECTED_ALEMBIC_HEAD {
            return Err(FingerprintError::ManifestInvariant(format!(
                "manifest Alembic head {} does not match {EXPECTED_ALEMBIC_HEAD}",
                manifest.alembic_head
            )));
        }

        verify_hash(
            "baseline SQL",
            BASELINE_SQL.as_bytes(),
            &manifest.baseline_sql_sha256,
        )?;
        verify_json_hash("catalog inventory", &catalog, &manifest.catalog_sha256)?;
        verify_json_hash(
            "legacy Alembic catalog inventory",
            &legacy_alembic_catalog,
            &manifest.legacy_alembic_catalog_sha256,
        )?;
        verify_json_hash("data invariants", &data, &manifest.data_invariants_sha256)?;
        verify_json_hash("role policy", &role_policy, &manifest.role_policy_sha256)?;

        Ok(Self {
            manifest,
            catalog,
            legacy_alembic_catalog,
            data,
            role_policy,
        })
    }
}

pub(crate) async fn verify_baseline_fingerprint(
    connection: &mut PgConnection,
) -> Result<(), FingerprintError> {
    let evidence = BaselineEvidence::load()?;
    verify_server_version(connection, evidence.manifest.minimum_postgresql_major).await?;
    verify_alembic_head(connection).await?;

    let actual_catalog = query_json(connection, CATALOG_INVENTORY_SQL).await?;
    compare_catalog_snapshot(
        &evidence.catalog,
        &evidence.legacy_alembic_catalog,
        &actual_catalog,
    )?;

    let actual_data = query_json(connection, DATA_INVARIANTS_SQL).await?;
    compare_snapshot("data invariants", &evidence.data, &actual_data)?;

    let actual_role_policy = query_json(connection, ROLE_POLICY_SQL).await?;
    compare_snapshot("role policy", &evidence.role_policy, &actual_role_policy)?;
    Ok(())
}

pub(crate) async fn verify_post_migration_catalog(
    connection: &mut PgConnection,
) -> Result<(), FingerprintError> {
    let evidence = BaselineEvidence::load()?;
    verify_server_version(connection, evidence.manifest.minimum_postgresql_major).await?;
    verify_alembic_head(connection).await?;

    let actual_data = query_json(connection, DATA_INVARIANTS_SQL).await?;
    compare_snapshot("data invariants", &evidence.data, &actual_data)?;

    let invalid_constraints: i64 = query_scalar(
        r"
        SELECT count(*)
        FROM pg_catalog.pg_constraint AS constraint_record
        JOIN pg_catalog.pg_class AS relation ON relation.oid = constraint_record.conrelid
        JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname !~ '^pg_'
          AND namespace.nspname <> 'information_schema'
          AND relation.relname <> '_sqlx_migrations'
          AND NOT constraint_record.convalidated
        ",
    )
    .fetch_one(&mut *connection)
    .await?;
    if invalid_constraints != 0 {
        return Err(FingerprintError::PostMigrationCatalog(format!(
            "{invalid_constraints} application constraints are not validated"
        )));
    }

    let unhealthy_indexes: i64 = query_scalar(
        r"
        SELECT count(*)
        FROM pg_catalog.pg_index AS index_record
        JOIN pg_catalog.pg_class AS relation ON relation.oid = index_record.indrelid
        JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname !~ '^pg_'
          AND namespace.nspname <> 'information_schema'
          AND relation.relname <> '_sqlx_migrations'
          AND (NOT index_record.indisvalid OR NOT index_record.indisready OR NOT index_record.indislive)
        ",
    )
    .fetch_one(&mut *connection)
    .await?;
    if unhealthy_indexes != 0 {
        return Err(FingerprintError::PostMigrationCatalog(format!(
            "{unhealthy_indexes} application indexes are invalid, unready, or not live"
        )));
    }

    let policy = query_json(connection, ROLE_POLICY_SQL).await?;
    validate_post_migration_role_policy(&policy)?;
    Ok(())
}

async fn verify_server_version(
    connection: &mut PgConnection,
    minimum_major: i32,
) -> Result<(), FingerprintError> {
    let version_number: i32 = query_scalar("SELECT current_setting('server_version_num')::integer")
        .fetch_one(connection)
        .await?;
    let major = version_number / 10_000;
    if major < minimum_major {
        return Err(FingerprintError::UnsupportedPostgres {
            minimum: minimum_major,
            actual: major,
        });
    }
    Ok(())
}

async fn verify_alembic_head(connection: &mut PgConnection) -> Result<(), FingerprintError> {
    let table_exists: bool =
        query_scalar("SELECT pg_catalog.to_regclass('public.alembic_version') IS NOT NULL")
            .fetch_one(&mut *connection)
            .await?;
    if !table_exists {
        return Err(FingerprintError::AlembicHead {
            expected: EXPECTED_ALEMBIC_HEAD,
            actual: Vec::new(),
        });
    }

    let heads: Vec<String> =
        sqlx::query_scalar("SELECT version_num FROM public.alembic_version ORDER BY version_num")
            .fetch_all(connection)
            .await?;
    if heads != [EXPECTED_ALEMBIC_HEAD.to_owned()] {
        return Err(FingerprintError::AlembicHead {
            expected: EXPECTED_ALEMBIC_HEAD,
            actual: heads,
        });
    }
    Ok(())
}

async fn query_json(
    connection: &mut PgConnection,
    sql: &'static str,
) -> Result<Value, FingerprintError> {
    query_scalar(sql)
        .fetch_one(connection)
        .await
        .map_err(FingerprintError::Sqlx)
}

fn validate_post_migration_role_policy(policy: &Value) -> Result<(), FingerprintError> {
    if policy
        .get("database_owner_is_current_role")
        .and_then(Value::as_bool)
        != Some(true)
    {
        return Err(FingerprintError::PostMigrationCatalog(
            "the migration role is not the database owner".to_owned(),
        ));
    }

    let owners = policy
        .get("object_ownership")
        .and_then(Value::as_array)
        .ok_or_else(|| {
            FingerprintError::PostMigrationCatalog(
                "role policy did not contain object ownership".to_owned(),
            )
        })?;
    if owners
        .iter()
        .any(|owner| owner.get("owner_kind").and_then(Value::as_str) == Some("EXTERNAL_ROLE"))
    {
        return Err(FingerprintError::PostMigrationCatalog(
            "an application object is owned by an external role".to_owned(),
        ));
    }

    let grants = policy
        .get("schema_grants")
        .and_then(Value::as_array)
        .ok_or_else(|| {
            FingerprintError::PostMigrationCatalog(
                "role policy did not contain schema grants".to_owned(),
            )
        })?;
    let public_usage = grants.iter().any(|grant| {
        grant.get("schema").and_then(Value::as_str) == Some("public")
            && grant.get("grantee").and_then(Value::as_str) == Some("PUBLIC")
            && grant.get("privilege").and_then(Value::as_str) == Some("USAGE")
    });
    if !public_usage {
        return Err(FingerprintError::PostMigrationCatalog(
            "PUBLIC is missing USAGE on the public schema".to_owned(),
        ));
    }
    Ok(())
}

fn parse_embedded_json(artifact: &'static str, source: &str) -> Result<Value, FingerprintError> {
    serde_json::from_str(source)
        .map_err(|source| FingerprintError::InvalidEmbeddedJson { artifact, source })
}

fn verify_json_hash(
    artifact: &'static str,
    value: &Value,
    expected_hash: &str,
) -> Result<(), FingerprintError> {
    let bytes = serde_json::to_vec(value).map_err(|source| {
        FingerprintError::ManifestInvariant(format!(
            "could not serialize embedded {artifact}: {source}"
        ))
    })?;
    verify_hash(artifact, &bytes, expected_hash)
}

fn verify_hash(
    artifact: &'static str,
    bytes: &[u8],
    expected_hash: &str,
) -> Result<(), FingerprintError> {
    let actual_hash = hex_sha256(bytes);
    if actual_hash != expected_hash {
        return Err(FingerprintError::EmbeddedHashMismatch {
            artifact,
            expected: expected_hash.to_owned(),
            actual: actual_hash,
        });
    }
    Ok(())
}

fn hex_sha256(bytes: &[u8]) -> String {
    let digest = Sha256::digest(bytes);
    let mut output = String::with_capacity(digest.len() * 2);
    for byte in digest {
        use std::fmt::Write as _;
        write!(&mut output, "{byte:02x}").expect("writing to a String cannot fail");
    }
    output
}

fn compare_snapshot(
    artifact: &'static str,
    expected: &Value,
    actual: &Value,
) -> Result<(), FingerprintError> {
    if expected == actual {
        return Ok(());
    }
    Err(FingerprintError::SnapshotMismatch {
        artifact,
        expected_sha256: hex_sha256(
            &serde_json::to_vec(expected).expect("serializing parsed JSON cannot fail"),
        ),
        actual_sha256: hex_sha256(
            &serde_json::to_vec(actual).expect("serializing parsed JSON cannot fail"),
        ),
        first_difference: first_difference(expected, actual, "$"),
    })
}

fn legacy_alembic_catalog_snapshot(canonical: &Value) -> Result<Value, FingerprintError> {
    let mut legacy = canonical.clone();
    let indexes = legacy
        .get_mut("indexes")
        .and_then(Value::as_array_mut)
        .ok_or_else(|| {
            FingerprintError::ManifestInvariant(
                "catalog inventory does not contain an indexes array".to_owned(),
            )
        })?;
    for index in indexes {
        let Some(rendering) = LEGACY_INDEX_RENDERINGS
            .iter()
            .find(|rendering| index.get("name").and_then(Value::as_str) == Some(rendering.name))
        else {
            continue;
        };
        let canonical_definition = format!(
            "{}{canonical}",
            rendering.definition_prefix,
            canonical = rendering.canonical_predicate
        );
        replace_exact_catalog_field(
            index,
            "predicate",
            rendering.canonical_predicate,
            rendering.legacy_predicate,
        )?;
        replace_exact_catalog_field(
            index,
            "definition",
            &canonical_definition,
            &format!(
                "{}{legacy}",
                rendering.definition_prefix,
                legacy = rendering.legacy_predicate
            ),
        )?;
    }
    Ok(legacy)
}

fn replace_exact_catalog_field(
    index: &mut Value,
    field: &'static str,
    expected: &str,
    replacement: &str,
) -> Result<(), FingerprintError> {
    let value = index.get(field).and_then(Value::as_str).ok_or_else(|| {
        FingerprintError::ManifestInvariant(format!(
            "catalog index does not contain string field {field}"
        ))
    })?;
    if value != expected {
        return Err(FingerprintError::ManifestInvariant(format!(
            "catalog index {field} did not match the pinned canonical rendering"
        )));
    }
    *index.get_mut(field).expect("field was checked") = Value::String(replacement.to_owned());
    Ok(())
}

fn compare_catalog_snapshot(
    canonical: &Value,
    legacy_alembic: &Value,
    actual: &Value,
) -> Result<(), FingerprintError> {
    if actual == canonical || actual == legacy_alembic {
        return Ok(());
    }

    let canonical_difference = first_difference(canonical, actual, "$");
    let legacy_difference = first_difference(legacy_alembic, actual, "$");
    Err(FingerprintError::SnapshotMismatch {
        artifact: "catalog inventory",
        expected_sha256: format!(
            "{} or {}",
            hex_sha256(
                &serde_json::to_vec(canonical).expect("serializing parsed JSON cannot fail")
            ),
            hex_sha256(
                &serde_json::to_vec(legacy_alembic).expect("serializing parsed JSON cannot fail")
            )
        ),
        actual_sha256: hex_sha256(
            &serde_json::to_vec(actual).expect("serializing parsed JSON cannot fail"),
        ),
        first_difference: format!(
            "canonical {canonical_difference}; legacy Alembic {legacy_difference}"
        ),
    })
}

fn first_difference(expected: &Value, actual: &Value, path: &str) -> String {
    match (expected, actual) {
        (Value::Object(expected_map), Value::Object(actual_map)) => {
            for (key, expected_value) in expected_map {
                let child_path = format!("{path}.{key}");
                let Some(actual_value) = actual_map.get(key) else {
                    return format!("{child_path} is missing");
                };
                if expected_value != actual_value {
                    return first_difference(expected_value, actual_value, &child_path);
                }
            }
            for key in actual_map.keys() {
                if !expected_map.contains_key(key) {
                    return format!("{path}.{key} is unexpected");
                }
            }
            format!("{path} differs")
        }
        (Value::Array(expected_array), Value::Array(actual_array)) => {
            let shared_length = expected_array.len().min(actual_array.len());
            for index in 0..shared_length {
                if expected_array[index] != actual_array[index] {
                    return first_difference(
                        &expected_array[index],
                        &actual_array[index],
                        &format!("{path}[{index}]"),
                    );
                }
            }
            format!(
                "{path} length differs: expected {}, actual {}",
                expected_array.len(),
                actual_array.len()
            )
        }
        _ => format!("{path} differs: expected {expected}, actual {actual}"),
    }
}

#[derive(Debug, Error)]
pub enum FingerprintError {
    #[error("embedded {artifact} is not valid JSON")]
    InvalidEmbeddedJson {
        artifact: &'static str,
        #[source]
        source: serde_json::Error,
    },
    #[error("baseline manifest invariant failed: {0}")]
    ManifestInvariant(String),
    #[error("embedded {artifact} hash mismatch: expected {expected}, actual {actual}")]
    EmbeddedHashMismatch {
        artifact: &'static str,
        expected: String,
        actual: String,
    },
    #[error("PostgreSQL {minimum}+ is required for this baseline; connected server is {actual}")]
    UnsupportedPostgres { minimum: i32, actual: i32 },
    #[error("Alembic head mismatch: expected {expected}, actual {actual:?}")]
    AlembicHead {
        expected: &'static str,
        actual: Vec<String>,
    },
    #[error(
        "{artifact} mismatch: expected {expected_sha256}, actual {actual_sha256}; {first_difference}"
    )]
    SnapshotMismatch {
        artifact: &'static str,
        expected_sha256: String,
        actual_sha256: String,
        first_difference: String,
    },
    #[error("post-migration catalog validation failed: {0}")]
    PostMigrationCatalog(String),
    #[error("catalog query failed")]
    Sqlx(#[from] sqlx::Error),
}

#[cfg(test)]
mod tests {
    use serde_json::{Value, json};

    use super::{
        BaselineEvidence, LEGACY_INDEX_RENDERINGS, compare_catalog_snapshot, first_difference,
        legacy_alembic_catalog_snapshot,
    };

    #[test]
    fn embedded_evidence_is_internally_consistent() {
        BaselineEvidence::load().expect("embedded evidence should validate");
    }

    #[test]
    fn difference_reports_the_first_nested_path() {
        let expected = json!({"relations": [{"name": "alpha", "valid": true}]});
        let actual = json!({"relations": [{"name": "alpha", "valid": false}]});
        assert_eq!(
            first_difference(&expected, &actual, "$"),
            "$.relations[0].valid differs: expected true, actual false"
        );
    }

    #[test]
    fn accepts_only_the_two_complete_pinned_catalog_histories() {
        let evidence = BaselineEvidence::load().expect("embedded evidence should validate");
        compare_catalog_snapshot(
            &evidence.catalog,
            &evidence.legacy_alembic_catalog,
            &evidence.catalog,
        )
        .expect("canonical snapshot should match");
        compare_catalog_snapshot(
            &evidence.catalog,
            &evidence.legacy_alembic_catalog,
            &evidence.legacy_alembic_catalog,
        )
        .expect("legacy Alembic snapshot should match");
    }

    #[test]
    fn rejects_a_third_catalog_rendering() {
        let evidence = BaselineEvidence::load().expect("embedded evidence should validate");
        let mut unexpected = evidence.legacy_alembic_catalog.clone();
        unexpected["indexes"][0]["definition"] = Value::String("unexpected definition".into());
        compare_catalog_snapshot(
            &evidence.catalog,
            &evidence.legacy_alembic_catalog,
            &unexpected,
        )
        .expect_err("an unpinned full catalog must fail closed");
    }

    #[test]
    fn legacy_snapshot_changes_only_the_audited_indexes() {
        let evidence = BaselineEvidence::load().expect("embedded evidence should validate");
        let legacy = legacy_alembic_catalog_snapshot(&evidence.catalog)
            .expect("legacy catalog should derive exactly");
        let changed_names = evidence.catalog["indexes"]
            .as_array()
            .expect("canonical indexes")
            .iter()
            .zip(legacy["indexes"].as_array().expect("legacy indexes"))
            .filter(|(canonical, legacy)| canonical != legacy)
            .map(|(canonical, _)| canonical["name"].as_str().expect("index name"))
            .collect::<Vec<_>>();
        assert_eq!(
            changed_names,
            LEGACY_INDEX_RENDERINGS
                .iter()
                .map(|rendering| rendering.name)
                .collect::<Vec<_>>()
        );
    }
}
