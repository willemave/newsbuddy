use anyhow::bail;
use newsly_db::{OwnershipMutationContext, OwnershipRepository};
use newsly_domain::{
    ApplicationSha, OwnershipTarget, OwnershipVersion, ReadinessState, ReplicaId, ResourceKey,
    RuntimeOwner, TransitionIntent,
};

use super::{
    AnyResult, AuditArgs, OutputFormat, OwnershipCommand, TargetArg, emit_success,
    load_ownership_policy_manifest,
};

#[allow(clippy::too_many_lines)]
pub(super) async fn execute_ownership_command(
    repository: &OwnershipRepository,
    command: &OwnershipCommand,
    output: OutputFormat,
) -> AnyResult<()> {
    let command_name = command.name();
    match command {
        OwnershipCommand::ValidateManifest {
            manifest,
            live: true,
        } => {
            let manifest = load_ownership_policy_manifest(manifest)?;
            let seeds = manifest.registry_seeds()?;
            let missing = repository.missing_resources(&seeds).await?;
            if !missing.is_empty() {
                let keys = missing
                    .iter()
                    .map(|seed| format!("{}:{}", seed.resource_kind, seed.resource_key))
                    .collect::<Vec<_>>()
                    .join(", ");
                bail!("live runtime ownership registry is missing: {keys}");
            }
            emit_success(
                output,
                command_name,
                &serde_json::json!({"resource_count": seeds.len(), "live": true}),
                "ownership manifest and live registry valid",
            )?;
        }
        OwnershipCommand::ValidateManifest { live: false, .. } => {
            unreachable!("offline validation exits before database initialization");
        }
        OwnershipCommand::SeedManifest { manifest, audit } => {
            let manifest = load_ownership_policy_manifest(manifest)?;
            let context = mutation_context(audit, TransitionIntent::Cutover)?;
            let created = repository
                .seed_missing(&manifest.registry_seeds()?, &context)
                .await?;
            emit_success(
                output,
                command_name,
                &serde_json::json!({"created_count": created.len()}),
                &format!("registered {} missing ownership resources", created.len()),
            )?;
        }
        OwnershipCommand::Show { resource } => {
            let record = repository.get(resource.kind, &resource.key).await?;
            emit_success(
                output,
                command_name,
                &record,
                &serde_json::to_string_pretty(&record)?,
            )?;
        }
        OwnershipCommand::Prepare { target, audit } => {
            let context = mutation_context(audit, TransitionIntent::Cutover)?;
            let prepared = repository
                .prepare_batch(&ownership_targets(target), &context)
                .await?;
            emit_success(
                output,
                command_name,
                &serde_json::json!({"prepared_count": prepared.len()}),
                &format!("prepared {} ownership resources", prepared.len()),
            )?;
        }
        OwnershipCommand::PrepareRollback { target, audit } => {
            let context = mutation_context(audit, TransitionIntent::Rollback)?;
            let prepared = repository
                .prepare_batch(&ownership_targets(target), &context)
                .await?;
            emit_success(
                output,
                command_name,
                &serde_json::json!({"prepared_count": prepared.len()}),
                &format!(
                    "prepared rollback for {} ownership resources",
                    prepared.len()
                ),
            )?;
        }
        OwnershipCommand::Acknowledge {
            resource,
            desired_version,
            replica_id,
            readiness,
            application_sha,
        } => {
            let acknowledgement = repository
                .acknowledge(
                    resource.kind,
                    &resource.key,
                    OwnershipVersion::new(*desired_version)?,
                    &ReplicaId::new(replica_id)?,
                    readiness.parse::<ReadinessState>()?,
                    &ApplicationSha::new(application_sha)?,
                )
                .await?;
            let text = format!(
                "acknowledged {}:{} version {} as {:?} by {}",
                acknowledgement.resource_kind,
                acknowledgement.resource_key,
                acknowledgement.desired_version.get(),
                acknowledgement.readiness_state,
                acknowledgement.replica_id.as_str(),
            );
            emit_success(
                output,
                command_name,
                &serde_json::json!({
                    "resource_kind": acknowledgement.resource_kind,
                    "resource_key": acknowledgement.resource_key,
                    "desired_version": acknowledgement.desired_version.get(),
                    "readiness_state": acknowledgement.readiness_state,
                    "replica_id": acknowledgement.replica_id.as_str(),
                }),
                &text,
            )?;
        }
        OwnershipCommand::Promote {
            target,
            replicas,
            audit,
        } => {
            let promoted = promote(
                repository,
                target,
                replicas,
                audit,
                TransitionIntent::Cutover,
            )
            .await?;
            emit_success(
                output,
                command_name,
                &serde_json::json!({"promoted_count": promoted}),
                &format!("promoted {promoted} ownership resources"),
            )?;
        }
        OwnershipCommand::PromoteRollback {
            target,
            replicas,
            audit,
        } => {
            let promoted = promote(
                repository,
                target,
                replicas,
                audit,
                TransitionIntent::Rollback,
            )
            .await?;
            emit_success(
                output,
                command_name,
                &serde_json::json!({"promoted_count": promoted}),
                &format!("promoted {promoted} ownership resources"),
            )?;
        }
        OwnershipCommand::DrainStatus {
            task_type,
            source_owner,
        } => {
            let status = repository
                .task_drain_status(
                    &ResourceKey::new(task_type)?,
                    source_owner.parse::<RuntimeOwner>()?,
                )
                .await?;
            let text = format!(
                "pending={} processing={} drained={}",
                status.pending,
                status.processing,
                status.is_drained()
            );
            emit_success(
                output,
                command_name,
                &serde_json::json!({
                    "pending": status.pending,
                    "processing": status.processing,
                    "drained": status.is_drained(),
                }),
                &text,
            )?;
        }
        OwnershipCommand::ClearAcknowledgements {
            resource,
            expected_owner,
            expected_version,
            minimum_age_seconds,
            audit,
        } => {
            let context = mutation_context(audit, TransitionIntent::Cutover)?;
            let deleted = repository
                .clear_acknowledgements(
                    resource.kind,
                    &resource.key,
                    expected_owner.parse::<RuntimeOwner>()?,
                    OwnershipVersion::new(*expected_version)?,
                    *minimum_age_seconds,
                    &context,
                )
                .await?;
            emit_success(
                output,
                command_name,
                &serde_json::json!({"cleared_count": deleted}),
                &format!("cleared {deleted} ownership acknowledgements"),
            )?;
        }
    }
    Ok(())
}

async fn promote(
    repository: &OwnershipRepository,
    target: &[TargetArg],
    replicas: &[String],
    audit: &AuditArgs,
    intent: TransitionIntent,
) -> AnyResult<usize> {
    let context = mutation_context(audit, intent)?;
    let replicas = replicas
        .iter()
        .map(ReplicaId::new)
        .collect::<std::result::Result<Vec<_>, _>>()?;
    let promoted = repository
        .promote_batch(&ownership_targets(target), &replicas, &context)
        .await?;
    Ok(promoted.len())
}

fn mutation_context(
    audit: &AuditArgs,
    intent: TransitionIntent,
) -> AnyResult<OwnershipMutationContext> {
    Ok(OwnershipMutationContext::new(
        ApplicationSha::new(&audit.application_sha)?,
        &audit.actor,
        &audit.reason,
        intent,
    )?)
}

fn ownership_targets(targets: &[TargetArg]) -> Vec<OwnershipTarget> {
    targets.iter().map(|target| target.0.clone()).collect()
}
