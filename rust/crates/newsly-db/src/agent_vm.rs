//! Durable ownership and immutable corpus preparation for persistent agent VMs.
//!
//! This repository never performs E2B or filesystem I/O. A caller acquires one expiring,
//! cross-process namespace lease in a short transaction, performs external lifecycle work from
//! the returned snapshot, and publishes replacement state through a fresh compare-and-set
//! transaction. Corpus rows are copied into an immutable transfer plan before archive creation.

use std::collections::BTreeSet;
use std::time::Duration;

use chrono::{DateTime, Utc};
use serde_json::{Map, Value};
use sqlx::{FromRow, PgPool, Postgres, Transaction};
use thiserror::Error;
use uuid::Uuid;

const RUST_RUNTIME_OWNER: &str = "rust";
const ACTIVE_TRANSITION_STATE: &str = "active";
const USER_NAMESPACE_FALLBACK: &str = "user:*";
const MAX_LEASE_SECONDS: u64 = 7_200;
const MAX_IDENTITY_BYTES: usize = 255;
const MAX_PATH_BYTES: usize = 1_024;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AgentVmPersistentState {
    pub sandbox_id: Option<String>,
    pub sandbox_template_revision: Option<String>,
    pub snapshot_id: Option<String>,
    pub snapshot_template_revision: Option<String>,
    pub corpus_revision: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AgentVmStateReplacement {
    pub sandbox_id: Option<String>,
    pub sandbox_template_revision: Option<String>,
    pub snapshot_id: Option<String>,
    pub snapshot_template_revision: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AgentVmNamespaceLeaseGrant {
    pub vm_namespace: String,
    pub ownership_resource_key: String,
    pub ownership_version: i64,
    pub token: Uuid,
    pub holder: String,
    pub task_id: Option<i64>,
    pub template_revision: String,
    pub acquired_at: DateTime<Utc>,
    pub expires_at: DateTime<Utc>,
    pub persistent_state: AgentVmPersistentState,
}

#[derive(Debug, Clone, PartialEq)]
pub struct AgentCorpusFile {
    pub path: String,
    pub checksum_sha256: String,
    pub index_record: Map<String, Value>,
    pub byte_size: u64,
}

#[derive(Debug, Clone, PartialEq)]
pub struct PreparedAgentCorpusTransfer {
    pub user_id: i64,
    pub from_revision: u64,
    pub to_revision: u64,
    pub full: bool,
    pub active_files: Vec<AgentCorpusFile>,
    pub deleted_paths: Vec<String>,
    pub total_file_count: u64,
    pub generated_at: DateTime<Utc>,
}

#[derive(Debug, Clone, FromRow)]
struct OwnershipRow {
    resource_key: String,
    active_owner: String,
    active_version: i64,
    transition_state: String,
}

#[derive(Debug, Clone, FromRow)]
struct UserVmRow {
    is_active: bool,
    agent_vm_sandbox_id: Option<String>,
    agent_vm_template_revision: Option<String>,
    agent_vm_snapshot_id: Option<String>,
    agent_vm_snapshot_template_revision: Option<String>,
    agent_data_revision: Option<i64>,
}

#[derive(Debug, Clone, FromRow)]
struct NamespaceLeaseRow {
    vm_namespace: String,
    ownership_resource_key: String,
    runtime_owner: String,
    ownership_version: i64,
    lease_token: Uuid,
    lease_holder: String,
    task_id: Option<i64>,
    template_revision: String,
    acquired_at: DateTime<Utc>,
    lease_expires_at: DateTime<Utc>,
}

#[derive(Debug, Clone, FromRow)]
struct CorpusRow {
    path: String,
    stale_paths: Value,
    checksum_sha256: String,
    index_record: Value,
    byte_size: i32,
    deleted_at: Option<DateTime<Utc>>,
}

/// Acquires one exclusive persistent user-VM namespace without keeping a transaction or pooled
/// connection alive across E2B work.
pub async fn acquire_agent_vm_namespace_lease(
    pool: &PgPool,
    user_id: i64,
    vm_namespace: &str,
    holder: &str,
    task_id: Option<i64>,
    template_revision: &str,
    lease_duration: Duration,
) -> Result<AgentVmNamespaceLeaseGrant, AgentVmRepositoryError> {
    validate_user_namespace(user_id, vm_namespace)?;
    validate_identity(holder, "lease holder")?;
    validate_identity(template_revision, "template revision")?;
    if task_id.is_some_and(|value| value <= 0) {
        return Err(AgentVmRepositoryError::InvalidInput(
            "task id must be positive".to_owned(),
        ));
    }
    let lease_seconds = duration_seconds(lease_duration)?;
    let token = Uuid::new_v4();
    let mut transaction = pool.begin().await?;
    let ownership = lock_vm_namespace_ownership(&mut transaction, vm_namespace).await?;
    if ownership.transition_state != ACTIVE_TRANSITION_STATE {
        return Err(AgentVmRepositoryError::OwnershipTransition {
            resource_key: ownership.resource_key,
        });
    }
    if ownership.active_owner != RUST_RUNTIME_OWNER {
        return Err(AgentVmRepositoryError::WrongRuntimeOwner {
            resource_key: ownership.resource_key,
            actual_owner: ownership.active_owner,
        });
    }
    if ownership.active_version <= 0 {
        return Err(AgentVmRepositoryError::InvalidDurableState(
            "VM ownership version is not positive".to_owned(),
        ));
    }

    let inserted = sqlx::query_as::<_, NamespaceLeaseRow>(
        r#"
        INSERT INTO agent_vm_namespace_leases (
            vm_namespace, ownership_resource_key, runtime_owner, ownership_version,
            lease_token, lease_holder, task_id, template_revision,
            acquired_at, renewed_at, lease_expires_at
        )
        VALUES (
            $1, $2, 'rust', $3, $4, $5, $6, $7,
            clock_timestamp(), clock_timestamp(),
            clock_timestamp() + $8 * interval '1 second'
        )
        ON CONFLICT (vm_namespace) DO UPDATE
        SET ownership_resource_key = EXCLUDED.ownership_resource_key,
            runtime_owner = EXCLUDED.runtime_owner,
            ownership_version = EXCLUDED.ownership_version,
            lease_token = EXCLUDED.lease_token,
            lease_holder = EXCLUDED.lease_holder,
            task_id = EXCLUDED.task_id,
            template_revision = EXCLUDED.template_revision,
            acquired_at = EXCLUDED.acquired_at,
            renewed_at = EXCLUDED.renewed_at,
            lease_expires_at = EXCLUDED.lease_expires_at
        WHERE agent_vm_namespace_leases.lease_expires_at <= clock_timestamp()
        RETURNING vm_namespace, ownership_resource_key, runtime_owner, ownership_version,
                  lease_token, lease_holder, task_id, template_revision,
                  acquired_at, lease_expires_at
        "#,
    )
    .bind(vm_namespace)
    .bind(&ownership.resource_key)
    .bind(ownership.active_version)
    .bind(token)
    .bind(holder)
    .bind(task_id)
    .bind(template_revision)
    .bind(lease_seconds)
    .fetch_optional(&mut *transaction)
    .await?;
    let Some(lease) = inserted else {
        let expires_at = sqlx::query_scalar::<_, DateTime<Utc>>(
            "SELECT lease_expires_at FROM agent_vm_namespace_leases WHERE vm_namespace = $1",
        )
        .bind(vm_namespace)
        .fetch_one(&mut *transaction)
        .await?;
        return Err(AgentVmRepositoryError::NamespaceBusy {
            vm_namespace: vm_namespace.to_owned(),
            expires_at,
        });
    };
    validate_lease_row(&lease)?;

    let user = lock_user_vm_state(&mut transaction, user_id).await?;
    if !user.is_active {
        return Err(AgentVmRepositoryError::UserMissingOrInactive);
    }
    let persistent_state = persistent_state(&user)?;
    transaction.commit().await?;
    Ok(AgentVmNamespaceLeaseGrant {
        vm_namespace: lease.vm_namespace,
        ownership_resource_key: lease.ownership_resource_key,
        ownership_version: lease.ownership_version,
        token: lease.lease_token,
        holder: lease.lease_holder,
        task_id: lease.task_id,
        template_revision: lease.template_revision,
        acquired_at: lease.acquired_at,
        expires_at: lease.lease_expires_at,
        persistent_state,
    })
}

/// Extends only the exact live lease generation. Ownership may be preparing to transfer, but its
/// still-active owner/version must remain Rust so an in-flight command can drain normally.
pub async fn renew_agent_vm_namespace_lease(
    pool: &PgPool,
    lease: &AgentVmNamespaceLeaseGrant,
    lease_duration: Duration,
) -> Result<DateTime<Utc>, AgentVmRepositoryError> {
    let lease_seconds = duration_seconds(lease_duration)?;
    let mut transaction = pool.begin().await?;
    verify_runtime_ownership(&mut transaction, lease).await?;
    let expires_at = sqlx::query_scalar::<_, DateTime<Utc>>(
        r#"
        UPDATE agent_vm_namespace_leases
        SET renewed_at = clock_timestamp(),
            lease_expires_at = clock_timestamp() + $1 * interval '1 second'
        WHERE vm_namespace = $2
          AND lease_token = $3
          AND ownership_resource_key = $4
          AND ownership_version = $5
          AND runtime_owner = 'rust'
          AND lease_expires_at > clock_timestamp()
        RETURNING lease_expires_at
        "#,
    )
    .bind(lease_seconds)
    .bind(&lease.vm_namespace)
    .bind(lease.token)
    .bind(&lease.ownership_resource_key)
    .bind(lease.ownership_version)
    .fetch_optional(&mut *transaction)
    .await?
    .ok_or(AgentVmRepositoryError::LeaseLost)?;
    transaction.commit().await?;
    Ok(expires_at)
}

/// Releases the exact namespace lease. Release remains allowed after an owner transition so a
/// draining worker cannot leave a live row behind.
pub async fn release_agent_vm_namespace_lease(
    pool: &PgPool,
    lease: &AgentVmNamespaceLeaseGrant,
) -> Result<bool, AgentVmRepositoryError> {
    let result = sqlx::query(
        "DELETE FROM agent_vm_namespace_leases WHERE vm_namespace = $1 AND lease_token = $2",
    )
    .bind(&lease.vm_namespace)
    .bind(lease.token)
    .execute(pool)
    .await?;
    Ok(result.rows_affected() == 1)
}

/// Publishes a connected sandbox/snapshot identity through a fresh exact-lease and old-state CAS.
pub async fn replace_agent_vm_persistent_state(
    pool: &PgPool,
    user_id: i64,
    lease: &AgentVmNamespaceLeaseGrant,
    expected: &AgentVmPersistentState,
    replacement: &AgentVmStateReplacement,
) -> Result<(), AgentVmRepositoryError> {
    validate_user_namespace(user_id, &lease.vm_namespace)?;
    validate_optional_identity(replacement.sandbox_id.as_deref(), "sandbox id")?;
    validate_optional_identity(
        replacement.sandbox_template_revision.as_deref(),
        "sandbox template revision",
    )?;
    validate_optional_identity(replacement.snapshot_id.as_deref(), "snapshot id")?;
    validate_optional_identity(
        replacement.snapshot_template_revision.as_deref(),
        "snapshot template revision",
    )?;
    validate_state_shape(replacement)?;

    let mut transaction = pool.begin().await?;
    assert_active_lease(&mut transaction, lease).await?;
    let updated = sqlx::query(
        r#"
        UPDATE users
        SET agent_vm_sandbox_id = $1,
            agent_vm_template_revision = $2,
            agent_vm_snapshot_id = $3,
            agent_vm_snapshot_template_revision = $4,
            updated_at = timezone('UTC', clock_timestamp())
        WHERE id::bigint = $5
          AND is_active = TRUE
          AND agent_vm_sandbox_id IS NOT DISTINCT FROM $6
          AND agent_vm_template_revision IS NOT DISTINCT FROM $7
          AND agent_vm_snapshot_id IS NOT DISTINCT FROM $8
          AND agent_vm_snapshot_template_revision IS NOT DISTINCT FROM $9
        "#,
    )
    .bind(&replacement.sandbox_id)
    .bind(&replacement.sandbox_template_revision)
    .bind(&replacement.snapshot_id)
    .bind(&replacement.snapshot_template_revision)
    .bind(user_id)
    .bind(&expected.sandbox_id)
    .bind(&expected.sandbox_template_revision)
    .bind(&expected.snapshot_id)
    .bind(&expected.snapshot_template_revision)
    .execute(&mut *transaction)
    .await?;
    if updated.rows_affected() != 1 {
        return Err(AgentVmRepositoryError::PersistentStateConflict);
    }
    transaction.commit().await?;
    Ok(())
}

/// Copies the coherent database half of a full or delta corpus transfer. Archive construction and
/// checksum verification happen after this function commits.
pub async fn prepare_agent_corpus_transfer(
    pool: &PgPool,
    user_id: i64,
    lease: &AgentVmNamespaceLeaseGrant,
    remote_revision: u64,
    force_full: bool,
) -> Result<Option<PreparedAgentCorpusTransfer>, AgentVmRepositoryError> {
    validate_user_namespace(user_id, &lease.vm_namespace)?;
    let remote_revision = i64::try_from(remote_revision).map_err(|_| {
        AgentVmRepositoryError::InvalidInput("remote corpus revision is too large".to_owned())
    })?;
    let mut transaction = pool.begin().await?;
    assert_active_lease(&mut transaction, lease).await?;
    let user = lock_user_vm_state(&mut transaction, user_id).await?;
    if !user.is_active {
        return Err(AgentVmRepositoryError::UserMissingOrInactive);
    }
    let target_revision = user.agent_data_revision.unwrap_or(0);
    if target_revision < 0 {
        return Err(AgentVmRepositoryError::InvalidDurableState(
            "host corpus revision is negative".to_owned(),
        ));
    }
    if remote_revision > target_revision {
        return Err(AgentVmRepositoryError::RemoteCorpusAhead {
            remote_revision: u64::try_from(remote_revision).unwrap_or(u64::MAX),
            host_revision: u64::try_from(target_revision).unwrap_or(0),
        });
    }
    if remote_revision == target_revision && !force_full {
        transaction.commit().await?;
        return Ok(None);
    }
    let full = force_full || remote_revision == 0;
    let rows = sqlx::query_as::<_, CorpusRow>(
        r#"
        SELECT path, stale_paths, checksum_sha256, index_record, byte_size, deleted_at
        FROM agent_data_files
        WHERE user_id::bigint = $1
          AND ($2 OR (revision > $3 AND revision <= $4))
        ORDER BY path
        "#,
    )
    .bind(user_id)
    .bind(full)
    .bind(remote_revision)
    .bind(target_revision)
    .fetch_all(&mut *transaction)
    .await?;
    let total_file_count = sqlx::query_scalar::<_, i64>(
        r#"
        SELECT count(*)::bigint
        FROM agent_data_files
        WHERE user_id::bigint = $1 AND deleted_at IS NULL
        "#,
    )
    .bind(user_id)
    .fetch_one(&mut *transaction)
    .await?;
    transaction.commit().await?;

    let mut deleted_paths = BTreeSet::new();
    let mut active_files = Vec::new();
    for row in rows {
        validate_corpus_path(&row.path)?;
        for stale_path in json_string_array(&row.stale_paths)? {
            validate_corpus_path(&stale_path)?;
            deleted_paths.insert(stale_path);
        }
        if row.deleted_at.is_some() {
            deleted_paths.insert(row.path);
            continue;
        }
        deleted_paths.remove(&row.path);
        validate_checksum(&row.checksum_sha256)?;
        let index_record = row.index_record.as_object().cloned().ok_or_else(|| {
            AgentVmRepositoryError::InvalidDurableState(format!(
                "agent corpus index record is not an object for {}",
                row.path
            ))
        })?;
        if index_record.get("path").and_then(Value::as_str) != Some(row.path.as_str()) {
            return Err(AgentVmRepositoryError::InvalidDurableState(format!(
                "agent corpus index path does not match its ledger path for {}",
                row.path
            )));
        }
        let byte_size = u64::try_from(row.byte_size).map_err(|_| {
            AgentVmRepositoryError::InvalidDurableState(format!(
                "agent corpus byte size is negative for {}",
                row.path
            ))
        })?;
        active_files.push(AgentCorpusFile {
            path: row.path,
            checksum_sha256: row.checksum_sha256,
            index_record,
            byte_size,
        });
    }
    Ok(Some(PreparedAgentCorpusTransfer {
        user_id,
        from_revision: u64::try_from(remote_revision).unwrap_or(0),
        to_revision: u64::try_from(target_revision).unwrap_or(0),
        full,
        active_files,
        deleted_paths: deleted_paths.into_iter().collect(),
        total_file_count: u64::try_from(total_file_count).map_err(|_| {
            AgentVmRepositoryError::InvalidDurableState(
                "agent corpus total file count is negative".to_owned(),
            )
        })?,
        generated_at: Utc::now(),
    }))
}

/// Used by ownership drain checks. Expired rows are deliberately ignored and may be reclaimed by
/// the next acquisition.
pub async fn count_active_agent_vm_namespace_leases(
    pool: &PgPool,
    ownership_resource_key: &str,
) -> Result<i64, AgentVmRepositoryError> {
    validate_identity(ownership_resource_key, "ownership resource key")?;
    Ok(sqlx::query_scalar::<_, i64>(
        r#"
        SELECT count(*)::bigint
        FROM agent_vm_namespace_leases
        WHERE ownership_resource_key = $1
          AND lease_expires_at > clock_timestamp()
        "#,
    )
    .bind(ownership_resource_key)
    .fetch_one(pool)
    .await?)
}

async fn assert_active_lease(
    transaction: &mut Transaction<'_, Postgres>,
    lease: &AgentVmNamespaceLeaseGrant,
) -> Result<(), AgentVmRepositoryError> {
    verify_runtime_ownership(transaction, lease).await?;
    let row = sqlx::query_as::<_, NamespaceLeaseRow>(
        r#"
        SELECT vm_namespace, ownership_resource_key, runtime_owner, ownership_version,
               lease_token, lease_holder, task_id, template_revision,
               acquired_at, lease_expires_at
        FROM agent_vm_namespace_leases
        WHERE vm_namespace = $1
          AND lease_token = $2
          AND ownership_resource_key = $3
          AND ownership_version = $4
          AND runtime_owner = 'rust'
          AND lease_expires_at > clock_timestamp()
        FOR SHARE
        "#,
    )
    .bind(&lease.vm_namespace)
    .bind(lease.token)
    .bind(&lease.ownership_resource_key)
    .bind(lease.ownership_version)
    .fetch_optional(&mut **transaction)
    .await?
    .ok_or(AgentVmRepositoryError::LeaseLost)?;
    validate_lease_row(&row)
}

async fn verify_runtime_ownership(
    transaction: &mut Transaction<'_, Postgres>,
    lease: &AgentVmNamespaceLeaseGrant,
) -> Result<(), AgentVmRepositoryError> {
    let row = sqlx::query_as::<_, (String, i64)>(
        r#"
        SELECT active_owner, active_version
        FROM runtime_ownership
        WHERE resource_kind = 'vm_namespace' AND resource_key = $1
        FOR SHARE
        "#,
    )
    .bind(&lease.ownership_resource_key)
    .fetch_optional(&mut **transaction)
    .await?
    .ok_or_else(|| AgentVmRepositoryError::OwnershipMissing {
        vm_namespace: lease.vm_namespace.clone(),
    })?;
    if row.0 != RUST_RUNTIME_OWNER || row.1 != lease.ownership_version {
        return Err(AgentVmRepositoryError::LeaseOwnershipChanged);
    }
    Ok(())
}

async fn lock_vm_namespace_ownership(
    transaction: &mut Transaction<'_, Postgres>,
    vm_namespace: &str,
) -> Result<OwnershipRow, AgentVmRepositoryError> {
    sqlx::query_as::<_, OwnershipRow>(
        r#"
        SELECT resource_key, active_owner, active_version, transition_state
        FROM runtime_ownership
        WHERE resource_kind = 'vm_namespace'
          AND (resource_key = $1 OR resource_key = $2)
        ORDER BY CASE WHEN resource_key = $1 THEN 0 ELSE 1 END
        LIMIT 1
        FOR SHARE
        "#,
    )
    .bind(vm_namespace)
    .bind(USER_NAMESPACE_FALLBACK)
    .fetch_optional(&mut **transaction)
    .await?
    .ok_or_else(|| AgentVmRepositoryError::OwnershipMissing {
        vm_namespace: vm_namespace.to_owned(),
    })
}

async fn lock_user_vm_state(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
) -> Result<UserVmRow, AgentVmRepositoryError> {
    sqlx::query_as::<_, UserVmRow>(
        r#"
        SELECT is_active, agent_vm_sandbox_id, agent_vm_template_revision,
               agent_vm_snapshot_id, agent_vm_snapshot_template_revision,
               agent_data_revision
        FROM users
        WHERE id::bigint = $1
        FOR SHARE
        "#,
    )
    .bind(user_id)
    .fetch_optional(&mut **transaction)
    .await?
    .ok_or(AgentVmRepositoryError::UserMissingOrInactive)
}

fn persistent_state(row: &UserVmRow) -> Result<AgentVmPersistentState, AgentVmRepositoryError> {
    let corpus_revision = row.agent_data_revision.unwrap_or(0);
    if corpus_revision < 0 {
        return Err(AgentVmRepositoryError::InvalidDurableState(
            "host corpus revision is negative".to_owned(),
        ));
    }
    let state = AgentVmPersistentState {
        sandbox_id: durable_optional_identity(row.agent_vm_sandbox_id.clone(), "sandbox id")?,
        sandbox_template_revision: durable_optional_identity(
            row.agent_vm_template_revision.clone(),
            "sandbox template revision",
        )?,
        snapshot_id: durable_optional_identity(row.agent_vm_snapshot_id.clone(), "snapshot id")?,
        snapshot_template_revision: durable_optional_identity(
            row.agent_vm_snapshot_template_revision.clone(),
            "snapshot template revision",
        )?,
        corpus_revision: u64::try_from(corpus_revision).unwrap_or(0),
    };
    if state.sandbox_id.is_some() != state.sandbox_template_revision.is_some()
        || state.snapshot_id.is_some() != state.snapshot_template_revision.is_some()
    {
        return Err(AgentVmRepositoryError::InvalidDurableState(
            "agent VM ids and template revisions must be set or cleared together".to_owned(),
        ));
    }
    Ok(state)
}

fn validate_lease_row(row: &NamespaceLeaseRow) -> Result<(), AgentVmRepositoryError> {
    if row.runtime_owner != RUST_RUNTIME_OWNER
        || row.ownership_version <= 0
        || row.lease_token.is_nil()
        || row.lease_expires_at <= row.acquired_at
    {
        return Err(AgentVmRepositoryError::InvalidDurableState(
            "agent VM namespace lease row is malformed".to_owned(),
        ));
    }
    validate_identity(&row.vm_namespace, "VM namespace")?;
    validate_identity(&row.ownership_resource_key, "ownership resource key")?;
    validate_identity(&row.lease_holder, "lease holder")?;
    validate_identity(&row.template_revision, "template revision")?;
    if row.task_id.is_some_and(|value| value <= 0) {
        return Err(AgentVmRepositoryError::InvalidDurableState(
            "agent VM namespace lease task id is not positive".to_owned(),
        ));
    }
    Ok(())
}

fn validate_state_shape(
    replacement: &AgentVmStateReplacement,
) -> Result<(), AgentVmRepositoryError> {
    if replacement.sandbox_id.is_some() != replacement.sandbox_template_revision.is_some() {
        return Err(AgentVmRepositoryError::InvalidInput(
            "sandbox id and template revision must be set or cleared together".to_owned(),
        ));
    }
    if replacement.snapshot_id.is_some() != replacement.snapshot_template_revision.is_some() {
        return Err(AgentVmRepositoryError::InvalidInput(
            "snapshot id and template revision must be set or cleared together".to_owned(),
        ));
    }
    Ok(())
}

fn validate_user_namespace(user_id: i64, vm_namespace: &str) -> Result<(), AgentVmRepositoryError> {
    if user_id <= 0 || vm_namespace != format!("user:{user_id}") {
        return Err(AgentVmRepositoryError::InvalidInput(
            "persistent user VM namespace must exactly match its positive user id".to_owned(),
        ));
    }
    validate_identity(vm_namespace, "VM namespace")
}

fn validate_optional_identity(
    value: Option<&str>,
    label: &str,
) -> Result<(), AgentVmRepositoryError> {
    if let Some(value) = value {
        validate_identity(value, label)?;
    }
    Ok(())
}

fn validate_identity(value: &str, label: &str) -> Result<(), AgentVmRepositoryError> {
    if value.is_empty()
        || value.trim() != value
        || value.len() > MAX_IDENTITY_BYTES
        || value.chars().any(char::is_control)
    {
        return Err(AgentVmRepositoryError::InvalidInput(format!(
            "{label} must be non-empty, unpadded, bounded text"
        )));
    }
    Ok(())
}

fn validate_corpus_path(path: &str) -> Result<(), AgentVmRepositoryError> {
    if path.is_empty()
        || path.len() > MAX_PATH_BYTES
        || path.starts_with('/')
        || path.starts_with("workspace/")
        || path == "workspace"
        || path
            .split('/')
            .any(|part| part.is_empty() || part == "." || part == "..")
        || path.contains('\\')
        || path.chars().any(char::is_control)
    {
        return Err(AgentVmRepositoryError::InvalidDurableState(format!(
            "unsafe agent corpus path {path:?}"
        )));
    }
    Ok(())
}

fn validate_checksum(value: &str) -> Result<(), AgentVmRepositoryError> {
    if value.len() != 64 || !value.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err(AgentVmRepositoryError::InvalidDurableState(
            "agent corpus checksum is not a SHA-256 digest".to_owned(),
        ));
    }
    Ok(())
}

fn json_string_array(value: &Value) -> Result<Vec<String>, AgentVmRepositoryError> {
    value
        .as_array()
        .ok_or_else(|| {
            AgentVmRepositoryError::InvalidDurableState(
                "agent corpus stale_paths is not an array".to_owned(),
            )
        })?
        .iter()
        .map(|item| {
            item.as_str().map(str::to_owned).ok_or_else(|| {
                AgentVmRepositoryError::InvalidDurableState(
                    "agent corpus stale_paths contains a non-string".to_owned(),
                )
            })
        })
        .collect()
}

fn duration_seconds(duration: Duration) -> Result<i64, AgentVmRepositoryError> {
    let seconds = duration.as_secs();
    if seconds == 0 || seconds > MAX_LEASE_SECONDS || duration.subsec_nanos() != 0 {
        return Err(AgentVmRepositoryError::InvalidInput(format!(
            "namespace lease duration must be a whole 1-{MAX_LEASE_SECONDS} seconds"
        )));
    }
    i64::try_from(seconds).map_err(|_| {
        AgentVmRepositoryError::InvalidInput("namespace lease duration is too large".to_owned())
    })
}

fn durable_optional_identity(
    value: Option<String>,
    label: &str,
) -> Result<Option<String>, AgentVmRepositoryError> {
    if let Some(value) = &value
        && (value.is_empty()
            || value.trim() != value
            || value.len() > MAX_IDENTITY_BYTES
            || value.chars().any(char::is_control))
    {
        return Err(AgentVmRepositoryError::InvalidDurableState(format!(
            "agent VM {label} is malformed"
        )));
    }
    Ok(value)
}

#[derive(Debug, Error)]
pub enum AgentVmRepositoryError {
    #[error("agent VM repository input is invalid: {0}")]
    InvalidInput(String),
    #[error("agent VM database operation failed")]
    Sqlx(#[from] sqlx::Error),
    #[error("agent VM runtime ownership is missing for {vm_namespace}")]
    OwnershipMissing { vm_namespace: String },
    #[error("agent VM ownership for {resource_key} is preparing to transition")]
    OwnershipTransition { resource_key: String },
    #[error("agent VM ownership for {resource_key} belongs to {actual_owner}, not Rust")]
    WrongRuntimeOwner {
        resource_key: String,
        actual_owner: String,
    },
    #[error("agent VM namespace {vm_namespace} is busy until {expires_at}")]
    NamespaceBusy {
        vm_namespace: String,
        expires_at: DateTime<Utc>,
    },
    #[error("agent VM namespace lease was lost or expired")]
    LeaseLost,
    #[error("agent VM runtime ownership changed while the namespace lease was active")]
    LeaseOwnershipChanged,
    #[error("agent VM persistent state changed after preparation")]
    PersistentStateConflict,
    #[error("agent VM user is missing or inactive")]
    UserMissingOrInactive,
    #[error(
        "remote agent corpus revision {remote_revision} is ahead of host revision {host_revision}"
    )]
    RemoteCorpusAhead {
        remote_revision: u64,
        host_revision: u64,
    },
    #[error("agent VM durable state is invalid: {0}")]
    InvalidDurableState(String),
}
