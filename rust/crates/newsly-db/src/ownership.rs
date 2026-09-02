use std::collections::{BTreeMap, BTreeSet};

use chrono::{DateTime, Utc};
use newsly_domain::{
    ApplicationSha, InvalidOwnershipValue, OwnershipRecord, OwnershipTarget, OwnershipVersion,
    ReadinessState, ReplicaId, ResourceKey, ResourceKind, RuntimeOwner, TransitionIntent,
    TransitionState,
};
use sqlx::{FromRow, PgPool, Postgres, Transaction};
use thiserror::Error;
use uuid::Uuid;

const OWNERSHIP_ADVISORY_LOCK_KEY: &str = "newsly:runtime_ownership:v1";

#[derive(Debug, Clone)]
pub struct OwnershipMutationContext {
    pub batch_id: Uuid,
    pub application_sha: ApplicationSha,
    pub actor: String,
    pub reason: String,
    pub intent: TransitionIntent,
}

impl OwnershipMutationContext {
    /// Builds audit context shared by every mutation in an atomic batch.
    ///
    /// # Errors
    ///
    /// Returns [`OwnershipRepositoryError::InvalidMutationContext`] for empty actor or reason.
    pub fn new(
        application_sha: ApplicationSha,
        actor: impl Into<String>,
        reason: impl Into<String>,
        intent: TransitionIntent,
    ) -> Result<Self, OwnershipRepositoryError> {
        let actor = actor.into();
        let reason = reason.into();
        if actor.trim().is_empty() || reason.trim().is_empty() {
            return Err(OwnershipRepositoryError::InvalidMutationContext);
        }
        Ok(Self {
            batch_id: Uuid::new_v4(),
            application_sha,
            actor,
            reason,
            intent,
        })
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OwnershipAcknowledgement {
    pub resource_kind: ResourceKind,
    pub resource_key: ResourceKey,
    pub desired_version: OwnershipVersion,
    pub replica_id: ReplicaId,
    pub readiness_state: ReadinessState,
    pub application_sha: ApplicationSha,
    pub acknowledged_at: DateTime<Utc>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OwnershipDrainStatus {
    pub pending: i64,
    pub processing: i64,
}

impl OwnershipDrainStatus {
    pub const fn is_drained(&self) -> bool {
        self.pending == 0 && self.processing == 0
    }

    pub const fn active_count(&self) -> i64 {
        self.pending + self.processing
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OwnershipSeed {
    pub resource_kind: ResourceKind,
    pub resource_key: ResourceKey,
    pub active_owner: RuntimeOwner,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PreparedRouteTransition {
    pub resource_key: ResourceKey,
    pub desired_version: OwnershipVersion,
}

#[derive(Debug, Clone)]
pub struct OwnershipRepository {
    pool: PgPool,
}

impl OwnershipRepository {
    pub fn new(pool: PgPool) -> Self {
        Self { pool }
    }

    /// Loads the canonical live ownership decision.
    ///
    /// # Errors
    ///
    /// Returns [`OwnershipRepositoryError::NotFound`] for an unregistered resource and a database
    /// or validation error for invalid durable state.
    pub async fn get(
        &self,
        resource_kind: ResourceKind,
        resource_key: &ResourceKey,
    ) -> Result<OwnershipRecord, OwnershipRepositoryError> {
        let row = sqlx::query_as::<_, OwnershipRow>(
            r"
            SELECT
                resource_kind,
                resource_key,
                active_owner,
                active_version,
                desired_owner,
                desired_version,
                transition_state,
                transition_started_at,
                updated_at,
                updated_by,
                reason
            FROM runtime_ownership
            WHERE resource_kind = $1 AND resource_key = $2
            ",
        )
        .bind(resource_kind.as_str())
        .bind(resource_key.as_str())
        .fetch_optional(&self.pool)
        .await?
        .ok_or_else(|| OwnershipRepositoryError::NotFound {
            resource_kind,
            resource_key: resource_key.to_string(),
        })?;
        row.try_into()
    }

    /// Lists prepared route transitions for gateway replica write-barrier coordination.
    ///
    /// The result is ordered by resource key so replica polling and acknowledgements are stable.
    /// Routes absent from this result are not currently preparing and any local barrier retained
    /// from an earlier snapshot can be released after the replica refreshes the active decision.
    ///
    /// # Errors
    ///
    /// Returns a database or durable-value validation error.
    pub async fn prepared_route_transitions(
        &self,
    ) -> Result<Vec<PreparedRouteTransition>, OwnershipRepositoryError> {
        let rows = sqlx::query_as::<_, PreparedRouteTransitionRow>(
            r"
            SELECT resource_key, desired_version
            FROM runtime_ownership
            WHERE
                resource_kind = 'route_group'
                AND transition_state = 'preparing'
                AND desired_version IS NOT NULL
            ORDER BY resource_key
            ",
        )
        .fetch_all(&self.pool)
        .await?;
        rows.into_iter().map(TryInto::try_into).collect()
    }

    /// Creates missing manifest resources without changing established owners.
    ///
    /// # Errors
    ///
    /// Returns an error if the database operation or audit write fails.
    pub async fn seed_missing(
        &self,
        seeds: &[OwnershipSeed],
        context: &OwnershipMutationContext,
    ) -> Result<Vec<OwnershipRecord>, OwnershipRepositoryError> {
        validate_unique_seeds(seeds)?;
        let mut transaction = self.pool.begin().await?;
        acquire_transition_lock(&mut transaction).await?;
        let mut created = Vec::new();
        for seed in sorted_seeds(seeds) {
            let row = sqlx::query_as::<_, OwnershipRow>(
                r"
                INSERT INTO runtime_ownership (
                    resource_kind,
                    resource_key,
                    active_owner,
                    active_version,
                    updated_by,
                    reason
                )
                VALUES ($1, $2, $3, 1, $4, $5)
                ON CONFLICT (resource_kind, resource_key) DO NOTHING
                RETURNING
                    resource_kind,
                    resource_key,
                    active_owner,
                    active_version,
                    desired_owner,
                    desired_version,
                    transition_state,
                    transition_started_at,
                    updated_at,
                    updated_by,
                    reason
                ",
            )
            .bind(seed.resource_kind.as_str())
            .bind(seed.resource_key.as_str())
            .bind(seed.active_owner.as_str())
            .bind(&context.actor)
            .bind(&context.reason)
            .fetch_optional(&mut *transaction)
            .await?;
            let Some(row) = row else {
                continue;
            };
            insert_audit(
                &mut transaction,
                context,
                "created",
                seed.resource_kind,
                &seed.resource_key,
                seed.active_owner,
                OwnershipVersion::new(1)?,
                seed.active_owner,
                OwnershipVersion::new(1)?,
            )
            .await?;
            created.push(row.try_into()?);
        }
        transaction.commit().await?;
        Ok(created)
    }

    /// Installs desired owner/version state with compare-and-set protection for an atomic batch.
    ///
    /// # Errors
    ///
    /// Returns a compare-and-set or transition-state error without modifying any target if one
    /// target is stale.
    pub async fn prepare_batch(
        &self,
        targets: &[OwnershipTarget],
        context: &OwnershipMutationContext,
    ) -> Result<Vec<OwnershipRecord>, OwnershipRepositoryError> {
        validate_unique_targets(targets)?;
        let mut transaction = self.pool.begin().await?;
        acquire_transition_lock(&mut transaction).await?;
        let mut prepared = Vec::with_capacity(targets.len());
        for target in sorted_targets(targets) {
            let current =
                fetch_for_update(&mut transaction, target.resource_kind, &target.resource_key)
                    .await?;
            verify_active_target(&current, target)?;
            let desired_version = target.expected_version.next()?;
            let row = sqlx::query_as::<_, OwnershipRow>(
                r"
                UPDATE runtime_ownership
                SET
                    desired_owner = $1,
                    desired_version = $2,
                    transition_state = 'preparing',
                    transition_started_at = NOW(),
                    updated_at = NOW(),
                    updated_by = $3,
                    reason = $4
                WHERE
                    resource_kind = $5
                    AND resource_key = $6
                    AND active_owner = $7
                    AND active_version = $8
                    AND transition_state = 'active'
                    AND desired_owner IS NULL
                    AND desired_version IS NULL
                RETURNING
                    resource_kind,
                    resource_key,
                    active_owner,
                    active_version,
                    desired_owner,
                    desired_version,
                    transition_state,
                    transition_started_at,
                    updated_at,
                    updated_by,
                    reason
                ",
            )
            .bind(target.desired_owner.as_str())
            .bind(desired_version.get())
            .bind(&context.actor)
            .bind(&context.reason)
            .bind(target.resource_kind.as_str())
            .bind(target.resource_key.as_str())
            .bind(target.expected_owner.as_str())
            .bind(target.expected_version.get())
            .fetch_optional(&mut *transaction)
            .await?
            .ok_or_else(|| stale_target(target))?;
            insert_audit(
                &mut transaction,
                context,
                context.intent.prepare_audit_action(),
                target.resource_kind,
                &target.resource_key,
                target.expected_owner,
                target.expected_version,
                target.desired_owner,
                desired_version,
            )
            .await?;
            prepared.push(row.try_into()?);
        }
        transaction.commit().await?;
        Ok(prepared)
    }

    /// Advances one replica's acknowledgement monotonically for a prepared desired version.
    ///
    /// # Errors
    ///
    /// Returns an error for a stale desired version or readiness-state regression.
    pub async fn acknowledge(
        &self,
        resource_kind: ResourceKind,
        resource_key: &ResourceKey,
        desired_version: OwnershipVersion,
        replica_id: &ReplicaId,
        readiness_state: ReadinessState,
        application_sha: &ApplicationSha,
    ) -> Result<OwnershipAcknowledgement, OwnershipRepositoryError> {
        let mut transaction = self.pool.begin().await?;
        let current = fetch_for_update(&mut transaction, resource_kind, resource_key).await?;
        let current_record: OwnershipRecord = current.try_into()?;
        if current_record.transition_state != TransitionState::Preparing
            || current_record.desired_version != Some(desired_version)
        {
            return Err(OwnershipRepositoryError::DesiredVersionMismatch {
                resource_kind,
                resource_key: resource_key.to_string(),
                desired_version: desired_version.get(),
            });
        }

        let previous = sqlx::query_as::<_, AcknowledgementRow>(
            r"
            SELECT
                resource_kind,
                resource_key,
                desired_version,
                replica_id,
                readiness_state,
                application_sha,
                acknowledged_at
            FROM runtime_ownership_ack
            WHERE
                resource_kind = $1
                AND resource_key = $2
                AND desired_version = $3
                AND replica_id = $4
            FOR UPDATE
            ",
        )
        .bind(resource_kind.as_str())
        .bind(resource_key.as_str())
        .bind(desired_version.get())
        .bind(replica_id.as_str())
        .fetch_optional(&mut *transaction)
        .await?;
        if let Some(previous) = previous {
            let previous_state = previous.readiness_state.parse::<ReadinessState>()?;
            if readiness_state < previous_state {
                return Err(OwnershipRepositoryError::ReadinessRegression {
                    replica_id: replica_id.as_str().to_owned(),
                    previous: previous_state,
                    requested: readiness_state,
                });
            }
        }

        let row = sqlx::query_as::<_, AcknowledgementRow>(
            r"
            INSERT INTO runtime_ownership_ack (
                resource_kind,
                resource_key,
                desired_version,
                replica_id,
                readiness_state,
                application_sha,
                acknowledged_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, NOW())
            ON CONFLICT (resource_kind, resource_key, desired_version, replica_id)
            DO UPDATE SET
                readiness_state = EXCLUDED.readiness_state,
                application_sha = EXCLUDED.application_sha,
                acknowledged_at = NOW()
            RETURNING
                resource_kind,
                resource_key,
                desired_version,
                replica_id,
                readiness_state,
                application_sha,
                acknowledged_at
            ",
        )
        .bind(resource_kind.as_str())
        .bind(resource_key.as_str())
        .bind(desired_version.get())
        .bind(replica_id.as_str())
        .bind(readiness_state.as_str())
        .bind(application_sha.as_str())
        .fetch_one(&mut *transaction)
        .await?;
        transaction.commit().await?;
        row.try_into()
    }

    /// Promotes every desired owner/version atomically after acknowledgements and drain proofs.
    ///
    /// # Errors
    ///
    /// Returns an error if any compare-and-set precondition, replica acknowledgement, application
    /// SHA, or resource-specific drain condition is not satisfied.
    pub async fn promote_batch(
        &self,
        targets: &[OwnershipTarget],
        required_replicas: &[ReplicaId],
        context: &OwnershipMutationContext,
    ) -> Result<Vec<OwnershipRecord>, OwnershipRepositoryError> {
        validate_unique_targets(targets)?;
        let required_replicas = validate_required_replicas(required_replicas)?;
        let mut transaction = self.pool.begin().await?;
        acquire_transition_lock(&mut transaction).await?;
        let mut promoted = Vec::with_capacity(targets.len());
        for target in sorted_targets(targets) {
            let raw =
                fetch_for_update(&mut transaction, target.resource_kind, &target.resource_key)
                    .await?;
            let current: OwnershipRecord = raw.try_into()?;
            let desired_version = target.expected_version.next()?;
            verify_prepared_target(&current, target, desired_version)?;
            verify_prepare_context(&mut transaction, target, desired_version, context).await?;
            verify_ready_replicas(
                &mut transaction,
                target,
                desired_version,
                &required_replicas,
                &context.application_sha,
            )
            .await?;
            verify_drain(&mut transaction, target, context.intent).await?;

            let row = sqlx::query_as::<_, OwnershipRow>(
                r"
                UPDATE runtime_ownership
                SET
                    active_owner = desired_owner,
                    active_version = desired_version,
                    desired_owner = NULL,
                    desired_version = NULL,
                    transition_state = 'active',
                    transition_started_at = NULL,
                    updated_at = NOW(),
                    updated_by = $1,
                    reason = $2
                WHERE
                    resource_kind = $3
                    AND resource_key = $4
                    AND active_owner = $5
                    AND active_version = $6
                    AND desired_owner = $7
                    AND desired_version = $8
                    AND transition_state = 'preparing'
                RETURNING
                    resource_kind,
                    resource_key,
                    active_owner,
                    active_version,
                    desired_owner,
                    desired_version,
                    transition_state,
                    transition_started_at,
                    updated_at,
                    updated_by,
                    reason
                ",
            )
            .bind(&context.actor)
            .bind(&context.reason)
            .bind(target.resource_kind.as_str())
            .bind(target.resource_key.as_str())
            .bind(target.expected_owner.as_str())
            .bind(target.expected_version.get())
            .bind(target.desired_owner.as_str())
            .bind(desired_version.get())
            .fetch_optional(&mut *transaction)
            .await?
            .ok_or_else(|| stale_target(target))?;
            insert_audit(
                &mut transaction,
                context,
                context.intent.promotion_audit_action(),
                target.resource_kind,
                &target.resource_key,
                target.expected_owner,
                target.expected_version,
                target.desired_owner,
                desired_version,
            )
            .await?;
            promoted.push(row.try_into()?);
        }
        transaction.commit().await?;
        Ok(promoted)
    }

    /// Returns the active source task count used by task-type promotion.
    ///
    /// # Errors
    ///
    /// Returns a database error if the count cannot be read.
    pub async fn task_drain_status(
        &self,
        task_type: &ResourceKey,
        source_owner: RuntimeOwner,
    ) -> Result<OwnershipDrainStatus, OwnershipRepositoryError> {
        task_drain_status(&self.pool, task_type, source_owner).await
    }

    /// Clears old acknowledgements only after the requested rollback window.
    ///
    /// # Errors
    ///
    /// Returns a compare-and-set error if the resource is transitioning or its active owner/version
    /// changed, and [`OwnershipRepositoryError::RollbackWindowOpen`] while the window is open.
    pub async fn clear_acknowledgements(
        &self,
        resource_kind: ResourceKind,
        resource_key: &ResourceKey,
        expected_owner: RuntimeOwner,
        expected_version: OwnershipVersion,
        minimum_age_seconds: i64,
        context: &OwnershipMutationContext,
    ) -> Result<u64, OwnershipRepositoryError> {
        if minimum_age_seconds < 0 {
            return Err(OwnershipRepositoryError::InvalidRollbackWindow(
                minimum_age_seconds,
            ));
        }
        let mut transaction = self.pool.begin().await?;
        acquire_transition_lock(&mut transaction).await?;
        let raw = fetch_for_update(&mut transaction, resource_kind, resource_key).await?;
        let current: OwnershipRecord = raw.try_into()?;
        if current.active_owner != expected_owner
            || current.active_version != expected_version
            || current.transition_state != TransitionState::Active
        {
            return Err(OwnershipRepositoryError::CompareAndSet {
                resource_kind,
                resource_key: resource_key.to_string(),
                expected_owner,
                expected_version: expected_version.get(),
                actual_owner: current.active_owner,
                actual_version: current.active_version.get(),
                transition_state: current.transition_state,
            });
        }
        let old_enough = sqlx::query_scalar::<_, bool>(
            r"
            SELECT updated_at <= NOW() - ($3 * INTERVAL '1 second')
            FROM runtime_ownership
            WHERE resource_kind = $1 AND resource_key = $2
            ",
        )
        .bind(resource_kind.as_str())
        .bind(resource_key.as_str())
        .bind(minimum_age_seconds)
        .fetch_one(&mut *transaction)
        .await?;
        if !old_enough {
            return Err(OwnershipRepositoryError::RollbackWindowOpen {
                resource_kind,
                resource_key: resource_key.to_string(),
            });
        }
        let deleted = sqlx::query(
            r"
            DELETE FROM runtime_ownership_ack
            WHERE
                resource_kind = $1
                AND resource_key = $2
                AND desired_version <= $3
            ",
        )
        .bind(resource_kind.as_str())
        .bind(resource_key.as_str())
        .bind(expected_version.get())
        .execute(&mut *transaction)
        .await?
        .rows_affected();
        insert_audit(
            &mut transaction,
            context,
            "acks_cleared",
            resource_kind,
            resource_key,
            expected_owner,
            expected_version,
            expected_owner,
            expected_version,
        )
        .await?;
        transaction.commit().await?;
        Ok(deleted)
    }

    /// Reports manifest resources missing from the live registry.
    ///
    /// # Errors
    ///
    /// Returns a database error if the live key set cannot be read.
    pub async fn missing_resources(
        &self,
        seeds: &[OwnershipSeed],
    ) -> Result<Vec<OwnershipSeed>, OwnershipRepositoryError> {
        validate_unique_seeds(seeds)?;
        let mut missing = Vec::new();
        for seed in sorted_seeds(seeds) {
            let present = sqlx::query_scalar::<_, bool>(
                r"
                SELECT EXISTS (
                    SELECT 1
                    FROM runtime_ownership
                    WHERE resource_kind = $1 AND resource_key = $2
                )
                ",
            )
            .bind(seed.resource_kind.as_str())
            .bind(seed.resource_key.as_str())
            .fetch_one(&self.pool)
            .await?;
            if !present {
                missing.push(seed.clone());
            }
        }
        Ok(missing)
    }
}

async fn acquire_transition_lock(
    transaction: &mut Transaction<'_, Postgres>,
) -> Result<(), sqlx::Error> {
    sqlx::query("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))")
        .bind(OWNERSHIP_ADVISORY_LOCK_KEY)
        .execute(&mut **transaction)
        .await?;
    Ok(())
}

async fn fetch_for_update(
    transaction: &mut Transaction<'_, Postgres>,
    resource_kind: ResourceKind,
    resource_key: &ResourceKey,
) -> Result<OwnershipRow, OwnershipRepositoryError> {
    sqlx::query_as::<_, OwnershipRow>(
        r"
        SELECT
            resource_kind,
            resource_key,
            active_owner,
            active_version,
            desired_owner,
            desired_version,
            transition_state,
            transition_started_at,
            updated_at,
            updated_by,
            reason
        FROM runtime_ownership
        WHERE resource_kind = $1 AND resource_key = $2
        FOR UPDATE
        ",
    )
    .bind(resource_kind.as_str())
    .bind(resource_key.as_str())
    .fetch_optional(&mut **transaction)
    .await?
    .ok_or_else(|| OwnershipRepositoryError::NotFound {
        resource_kind,
        resource_key: resource_key.to_string(),
    })
}

fn verify_active_target(
    raw: &OwnershipRow,
    target: &OwnershipTarget,
) -> Result<(), OwnershipRepositoryError> {
    let current = OwnershipRecord::try_from(raw.clone())?;
    if current.active_owner != target.expected_owner
        || current.active_version != target.expected_version
        || current.transition_state != TransitionState::Active
    {
        return Err(OwnershipRepositoryError::CompareAndSet {
            resource_kind: target.resource_kind,
            resource_key: target.resource_key.to_string(),
            expected_owner: target.expected_owner,
            expected_version: target.expected_version.get(),
            actual_owner: current.active_owner,
            actual_version: current.active_version.get(),
            transition_state: current.transition_state,
        });
    }
    Ok(())
}

fn verify_prepared_target(
    current: &OwnershipRecord,
    target: &OwnershipTarget,
    desired_version: OwnershipVersion,
) -> Result<(), OwnershipRepositoryError> {
    if current.active_owner != target.expected_owner
        || current.active_version != target.expected_version
        || current.desired_owner != Some(target.desired_owner)
        || current.desired_version != Some(desired_version)
        || current.transition_state != TransitionState::Preparing
    {
        return Err(OwnershipRepositoryError::CompareAndSet {
            resource_kind: target.resource_kind,
            resource_key: target.resource_key.to_string(),
            expected_owner: target.expected_owner,
            expected_version: target.expected_version.get(),
            actual_owner: current.active_owner,
            actual_version: current.active_version.get(),
            transition_state: current.transition_state,
        });
    }
    Ok(())
}

async fn verify_ready_replicas(
    transaction: &mut Transaction<'_, Postgres>,
    target: &OwnershipTarget,
    desired_version: OwnershipVersion,
    required_replicas: &BTreeSet<String>,
    application_sha: &ApplicationSha,
) -> Result<(), OwnershipRepositoryError> {
    let rows = sqlx::query_as::<_, AcknowledgementRow>(
        r"
        SELECT
            resource_kind,
            resource_key,
            desired_version,
            replica_id,
            readiness_state,
            application_sha,
            acknowledged_at
        FROM runtime_ownership_ack
        WHERE
            resource_kind = $1
            AND resource_key = $2
            AND desired_version = $3
        FOR UPDATE
        ",
    )
    .bind(target.resource_kind.as_str())
    .bind(target.resource_key.as_str())
    .bind(desired_version.get())
    .fetch_all(&mut **transaction)
    .await?;
    let by_replica: BTreeMap<_, _> = rows
        .into_iter()
        .map(|row| (row.replica_id.clone(), row))
        .collect();
    let mut missing_or_stale = Vec::new();
    for replica in required_replicas {
        let Some(row) = by_replica.get(replica) else {
            missing_or_stale.push(replica.clone());
            continue;
        };
        if row.readiness_state.parse::<ReadinessState>()? != ReadinessState::Ready
            || row.application_sha != application_sha.as_str()
        {
            missing_or_stale.push(replica.clone());
        }
    }
    if !missing_or_stale.is_empty() {
        return Err(OwnershipRepositoryError::ReplicasNotReady {
            resource_kind: target.resource_kind,
            resource_key: target.resource_key.to_string(),
            replicas: missing_or_stale,
        });
    }
    Ok(())
}

async fn verify_prepare_context(
    transaction: &mut Transaction<'_, Postgres>,
    target: &OwnershipTarget,
    desired_version: OwnershipVersion,
    context: &OwnershipMutationContext,
) -> Result<(), OwnershipRepositoryError> {
    let row = sqlx::query_as::<_, PrepareAuditRow>(
        r"
        SELECT action, application_sha
        FROM runtime_ownership_audit
        WHERE
            resource_kind = $1
            AND resource_key = $2
            AND old_owner = $3
            AND old_version = $4
            AND new_owner = $5
            AND new_version = $6
            AND action IN ('prepare', 'rollback_prepare')
        ORDER BY id DESC
        LIMIT 1
        ",
    )
    .bind(target.resource_kind.as_str())
    .bind(target.resource_key.as_str())
    .bind(target.expected_owner.as_str())
    .bind(target.expected_version.get())
    .bind(target.desired_owner.as_str())
    .bind(desired_version.get())
    .fetch_optional(&mut **transaction)
    .await?;
    let expected_action = context.intent.prepare_audit_action();
    if row.as_ref().is_none_or(|row| {
        row.action != expected_action || row.application_sha != context.application_sha.as_str()
    }) {
        return Err(OwnershipRepositoryError::PrepareContextMismatch {
            resource_kind: target.resource_kind,
            resource_key: target.resource_key.to_string(),
            expected_action,
        });
    }
    Ok(())
}

async fn verify_drain(
    transaction: &mut Transaction<'_, Postgres>,
    target: &OwnershipTarget,
    intent: TransitionIntent,
) -> Result<(), OwnershipRepositoryError> {
    match target.resource_kind {
        ResourceKind::TaskType if intent == TransitionIntent::Cutover => {
            let status = task_drain_status(
                &mut **transaction,
                &target.resource_key,
                target.expected_owner,
            )
            .await?;
            if !status.is_drained() {
                return Err(OwnershipRepositoryError::SourceNotDrained {
                    resource_kind: target.resource_kind,
                    resource_key: target.resource_key.to_string(),
                    active_count: status.active_count(),
                });
            }
        }
        // Rollback only changes ownership for newly enqueued work, so stamped tasks keep draining
        // under Rust. Route and state-writer drain is proven by each replica's Ready barrier ack.
        ResourceKind::TaskType | ResourceKind::RouteGroup | ResourceKind::StateWriter => {}
    }
    Ok(())
}

async fn task_drain_status<'executor, E>(
    executor: E,
    task_type: &ResourceKey,
    source_owner: RuntimeOwner,
) -> Result<OwnershipDrainStatus, OwnershipRepositoryError>
where
    E: sqlx::Executor<'executor, Database = Postgres>,
{
    let row = sqlx::query_as::<_, DrainRow>(
        r"
        SELECT
            COUNT(*) FILTER (WHERE status = 'pending') AS pending,
            COUNT(*) FILTER (WHERE status = 'processing') AS processing
        FROM processing_tasks
        WHERE
            task_type = $1
            AND executor_runtime = $2
            AND status IN ('pending', 'processing')
        ",
    )
    .bind(task_type.as_str())
    .bind(source_owner.as_str())
    .fetch_one(executor)
    .await?;
    Ok(OwnershipDrainStatus {
        pending: row.pending.unwrap_or(0),
        processing: row.processing.unwrap_or(0),
    })
}

#[allow(clippy::too_many_arguments)]
async fn insert_audit(
    transaction: &mut Transaction<'_, Postgres>,
    context: &OwnershipMutationContext,
    action: &str,
    resource_kind: ResourceKind,
    resource_key: &ResourceKey,
    old_owner: RuntimeOwner,
    old_version: OwnershipVersion,
    new_owner: RuntimeOwner,
    new_version: OwnershipVersion,
) -> Result<(), sqlx::Error> {
    sqlx::query(
        r"
        INSERT INTO runtime_ownership_audit (
            batch_id,
            action,
            resource_kind,
            resource_key,
            old_owner,
            old_version,
            new_owner,
            new_version,
            application_sha,
            actor,
            reason
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        ",
    )
    .bind(context.batch_id)
    .bind(action)
    .bind(resource_kind.as_str())
    .bind(resource_key.as_str())
    .bind(old_owner.as_str())
    .bind(old_version.get())
    .bind(new_owner.as_str())
    .bind(new_version.get())
    .bind(context.application_sha.as_str())
    .bind(&context.actor)
    .bind(&context.reason)
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

fn validate_unique_targets(targets: &[OwnershipTarget]) -> Result<(), OwnershipRepositoryError> {
    if targets.is_empty() {
        return Err(OwnershipRepositoryError::EmptyBatch);
    }
    let mut keys = BTreeSet::new();
    for target in targets {
        if !keys.insert((target.resource_kind.as_str(), target.resource_key.as_str())) {
            return Err(OwnershipRepositoryError::DuplicateResource {
                resource_kind: target.resource_kind,
                resource_key: target.resource_key.to_string(),
            });
        }
    }
    Ok(())
}

fn validate_unique_seeds(seeds: &[OwnershipSeed]) -> Result<(), OwnershipRepositoryError> {
    let mut keys = BTreeSet::new();
    for seed in seeds {
        if !keys.insert((seed.resource_kind.as_str(), seed.resource_key.as_str())) {
            return Err(OwnershipRepositoryError::DuplicateResource {
                resource_kind: seed.resource_kind,
                resource_key: seed.resource_key.to_string(),
            });
        }
    }
    Ok(())
}

fn validate_required_replicas(
    required_replicas: &[ReplicaId],
) -> Result<BTreeSet<String>, OwnershipRepositoryError> {
    if required_replicas.is_empty() {
        return Err(OwnershipRepositoryError::NoRequiredReplicas);
    }
    let replicas = required_replicas
        .iter()
        .map(|replica| replica.as_str().to_owned())
        .collect::<BTreeSet<_>>();
    if replicas.len() != required_replicas.len() {
        return Err(OwnershipRepositoryError::DuplicateReplica);
    }
    Ok(replicas)
}

fn sorted_targets(targets: &[OwnershipTarget]) -> Vec<&OwnershipTarget> {
    let mut targets = targets.iter().collect::<Vec<_>>();
    targets.sort_by(|left, right| {
        (left.resource_kind.as_str(), left.resource_key.as_str())
            .cmp(&(right.resource_kind.as_str(), right.resource_key.as_str()))
    });
    targets
}

fn sorted_seeds(seeds: &[OwnershipSeed]) -> Vec<&OwnershipSeed> {
    let mut seeds = seeds.iter().collect::<Vec<_>>();
    seeds.sort_by(|left, right| {
        (left.resource_kind.as_str(), left.resource_key.as_str())
            .cmp(&(right.resource_kind.as_str(), right.resource_key.as_str()))
    });
    seeds
}

fn stale_target(target: &OwnershipTarget) -> OwnershipRepositoryError {
    OwnershipRepositoryError::StaleMutation {
        resource_kind: target.resource_kind,
        resource_key: target.resource_key.to_string(),
    }
}

#[derive(Debug, Clone, FromRow)]
struct OwnershipRow {
    resource_kind: String,
    resource_key: String,
    active_owner: String,
    active_version: i64,
    desired_owner: Option<String>,
    desired_version: Option<i64>,
    transition_state: String,
    transition_started_at: Option<DateTime<Utc>>,
    updated_at: DateTime<Utc>,
    updated_by: String,
    reason: String,
}

impl TryFrom<OwnershipRow> for OwnershipRecord {
    type Error = OwnershipRepositoryError;

    fn try_from(row: OwnershipRow) -> Result<Self, Self::Error> {
        Ok(Self {
            resource_kind: row.resource_kind.parse()?,
            resource_key: ResourceKey::new(row.resource_key)?,
            active_owner: row.active_owner.parse()?,
            active_version: OwnershipVersion::new(row.active_version)?,
            desired_owner: row.desired_owner.map(|owner| owner.parse()).transpose()?,
            desired_version: row.desired_version.map(OwnershipVersion::new).transpose()?,
            transition_state: row.transition_state.parse()?,
            transition_started_at: row.transition_started_at,
            updated_at: row.updated_at,
            updated_by: row.updated_by,
            reason: row.reason,
        })
    }
}

#[derive(Debug, Clone, FromRow)]
struct AcknowledgementRow {
    resource_kind: String,
    resource_key: String,
    desired_version: i64,
    replica_id: String,
    readiness_state: String,
    application_sha: String,
    acknowledged_at: DateTime<Utc>,
}

impl TryFrom<AcknowledgementRow> for OwnershipAcknowledgement {
    type Error = OwnershipRepositoryError;

    fn try_from(row: AcknowledgementRow) -> Result<Self, Self::Error> {
        Ok(Self {
            resource_kind: row.resource_kind.parse()?,
            resource_key: ResourceKey::new(row.resource_key)?,
            desired_version: OwnershipVersion::new(row.desired_version)?,
            replica_id: ReplicaId::new(row.replica_id)?,
            readiness_state: row.readiness_state.parse()?,
            application_sha: ApplicationSha::new(row.application_sha)?,
            acknowledged_at: row.acknowledged_at,
        })
    }
}

#[derive(Debug, FromRow)]
struct DrainRow {
    pending: Option<i64>,
    processing: Option<i64>,
}

#[derive(Debug, FromRow)]
struct PreparedRouteTransitionRow {
    resource_key: String,
    desired_version: i64,
}

#[derive(Debug, FromRow)]
struct PrepareAuditRow {
    action: String,
    application_sha: String,
}

impl TryFrom<PreparedRouteTransitionRow> for PreparedRouteTransition {
    type Error = OwnershipRepositoryError;

    fn try_from(row: PreparedRouteTransitionRow) -> Result<Self, Self::Error> {
        Ok(Self {
            resource_key: ResourceKey::new(row.resource_key)?,
            desired_version: OwnershipVersion::new(row.desired_version)?,
        })
    }
}

#[derive(Debug, Error)]
pub enum OwnershipRepositoryError {
    #[error("runtime ownership database operation failed")]
    Sqlx(#[from] sqlx::Error),
    #[error(transparent)]
    InvalidValue(#[from] InvalidOwnershipValue),
    #[error("ownership mutation actor and reason must be non-empty")]
    InvalidMutationContext,
    #[error("ownership batch must contain at least one target")]
    EmptyBatch,
    #[error("ownership batch contains duplicate {resource_kind}:{resource_key}")]
    DuplicateResource {
        resource_kind: ResourceKind,
        resource_key: String,
    },
    #[error("runtime ownership resource {resource_kind}:{resource_key} is not registered")]
    NotFound {
        resource_kind: ResourceKind,
        resource_key: String,
    },
    #[error(
        "ownership compare-and-set failed for {resource_kind}:{resource_key}; expected {expected_owner}@{expected_version}, found {actual_owner}@{actual_version} in {transition_state:?}"
    )]
    CompareAndSet {
        resource_kind: ResourceKind,
        resource_key: String,
        expected_owner: RuntimeOwner,
        expected_version: i64,
        actual_owner: RuntimeOwner,
        actual_version: i64,
        transition_state: TransitionState,
    },
    #[error("ownership mutation became stale for {resource_kind}:{resource_key}")]
    StaleMutation {
        resource_kind: ResourceKind,
        resource_key: String,
    },
    #[error(
        "resource {resource_kind}:{resource_key} is not preparing desired version {desired_version}"
    )]
    DesiredVersionMismatch {
        resource_kind: ResourceKind,
        resource_key: String,
        desired_version: i64,
    },
    #[error(
        "prepared transition context does not match {expected_action} on the exact application SHA for {resource_kind}:{resource_key}"
    )]
    PrepareContextMismatch {
        resource_kind: ResourceKind,
        resource_key: String,
        expected_action: &'static str,
    },
    #[error("replica {replica_id} readiness cannot regress from {previous:?} to {requested:?}")]
    ReadinessRegression {
        replica_id: String,
        previous: ReadinessState,
        requested: ReadinessState,
    },
    #[error("promotion requires at least one healthy gateway replica")]
    NoRequiredReplicas,
    #[error("promotion replica list contains duplicates")]
    DuplicateReplica,
    #[error(
        "replicas are not ready on the exact application SHA for {resource_kind}:{resource_key}: {replicas:?}"
    )]
    ReplicasNotReady {
        resource_kind: ResourceKind,
        resource_key: String,
        replicas: Vec<String>,
    },
    #[error(
        "source runtime still owns {active_count} active tasks for {resource_kind}:{resource_key}"
    )]
    SourceNotDrained {
        resource_kind: ResourceKind,
        resource_key: String,
        active_count: i64,
    },
    #[error("no durable drain proof is implemented for {resource_kind}:{resource_key}")]
    DrainProofUnavailable {
        resource_kind: ResourceKind,
        resource_key: String,
    },
    #[error("rollback window must be nonnegative, got {0}")]
    InvalidRollbackWindow(i64),
    #[error("rollback window is still open for {resource_kind}:{resource_key}")]
    RollbackWindowOpen {
        resource_kind: ResourceKind,
        resource_key: String,
    },
}

#[cfg(test)]
mod tests {
    use newsly_domain::{
        OwnershipTarget, OwnershipVersion, ResourceKey, ResourceKind, RuntimeOwner,
    };

    use super::{OwnershipRepositoryError, validate_unique_targets};

    #[test]
    fn atomic_batch_rejects_duplicate_resources() {
        let target = OwnershipTarget::new(
            ResourceKind::TaskType,
            ResourceKey::new("run_llm_task").unwrap(),
            RuntimeOwner::Python,
            OwnershipVersion::new(1).unwrap(),
            RuntimeOwner::Rust,
        )
        .unwrap();
        assert!(matches!(
            validate_unique_targets(&[target.clone(), target]),
            Err(OwnershipRepositoryError::DuplicateResource { .. })
        ));
    }
}
