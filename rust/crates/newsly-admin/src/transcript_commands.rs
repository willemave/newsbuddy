use anyhow::Context as _;
use serde::Serialize;

use super::{AnyResult, Database, OutputFormat, emit_success};

pub(super) struct TranscriptBackfillCommand<'a> {
    pub(super) after_id: i64,
    pub(super) limit: i64,
    pub(super) batch_id: Option<uuid::Uuid>,
    pub(super) actor: Option<&'a str>,
    pub(super) reason: Option<&'a str>,
    pub(super) apply: bool,
}

#[derive(Debug, Serialize)]
struct TranscriptBackfillReport {
    batch_id: Option<uuid::Uuid>,
    requested_apply: bool,
    replayed: bool,
    #[serde(flatten)]
    page: newsly_db::ChatTranscriptBackfillPage,
}

pub(super) async fn execute_compatibility(
    database: &Database,
    after_id: i64,
    limit: i64,
    output: OutputFormat,
) -> AnyResult<()> {
    let result =
        newsly_db::audit_chat_transcript_compatibility(database.pool(), after_id, limit).await?;
    let text = format!(
        "{} transcript row(s): {} canonical, {} legacy-shape, {} invalid; last_id={} more={}",
        result.scanned_count,
        result.canonical_count,
        result.legacy_shape_count,
        result.invalid_count,
        optional_id(result.last_id),
        result.more
    );
    emit_success(output, "db.transcript-compatibility", &result, &text)
}

pub(super) async fn execute_backfill(
    database: &Database,
    command: TranscriptBackfillCommand<'_>,
    output: OutputFormat,
) -> AnyResult<()> {
    let authority = transcript_backfill_authority(
        command.apply,
        command.batch_id,
        command.actor,
        command.reason,
    )?;
    let fresh = if let Some(authority) = authority {
        let targets = serde_json::json!({
            "after_id": command.after_id,
            "limit": command.limit,
        });
        let mut transaction = database.pool().begin().await?;
        let fresh = newsly_db::incident_batches::record_batch(
            &mut transaction,
            authority.batch_id,
            "chat_transcript_backfill",
            authority.actor,
            authority.reason,
            &targets,
        )
        .await?;
        transaction.commit().await?;
        fresh
    } else {
        true
    };
    // Replaying the same recorded batch remains an apply. Each row is idempotent and CAS-fenced,
    // so a partial run can resume after the last successful autocommit.
    let page = newsly_db::backfill_chat_transcript_compatibility(
        database.pool(),
        command.after_id,
        command.limit,
        command.apply,
    )
    .await?;
    let report = TranscriptBackfillReport {
        batch_id: command.batch_id,
        requested_apply: command.apply,
        replayed: command.apply && !fresh,
        page,
    };
    let text = format!(
        "{} transcript row(s): {} canonical, {} legacy-shape, {} invalid, {} updated, {} conflicted; last_id={} last_applied_id={} more={} applied={} replayed={}",
        report.page.scanned_count,
        report.page.canonical_count,
        report.page.legacy_shape_count,
        report.page.invalid_count,
        report.page.updated_count,
        report.page.conflict_count,
        optional_id(report.page.last_id),
        optional_id(report.page.last_applied_id),
        report.page.more,
        report.page.applied,
        report.replayed,
    );
    emit_success(output, "db.transcript-backfill", &report, &text)
}

#[derive(Debug, Clone, Copy)]
struct TranscriptBackfillAuthority<'a> {
    batch_id: uuid::Uuid,
    actor: &'a str,
    reason: &'a str,
}

fn transcript_backfill_authority<'a>(
    apply: bool,
    batch_id: Option<uuid::Uuid>,
    actor: Option<&'a str>,
    reason: Option<&'a str>,
) -> AnyResult<Option<TranscriptBackfillAuthority<'a>>> {
    if !apply {
        return Ok(None);
    }
    let batch_id = batch_id.context("--batch-id is required with --apply")?;
    let actor = actor
        .filter(|value| !value.trim().is_empty())
        .context("--actor is required with --apply")?;
    let reason = reason
        .filter(|value| !value.trim().is_empty())
        .context("--reason is required with --apply")?;
    Ok(Some(TranscriptBackfillAuthority {
        batch_id,
        actor,
        reason,
    }))
}

fn optional_id(id: Option<i64>) -> String {
    id.map_or_else(|| "none".to_owned(), |value| value.to_string())
}

#[cfg(test)]
mod tests {
    use super::transcript_backfill_authority;

    #[test]
    fn apply_requires_complete_operator_authority() {
        let batch_id = uuid::Uuid::new_v4();
        assert!(transcript_backfill_authority(false, None, None, None).is_ok());
        assert!(transcript_backfill_authority(true, None, Some("ops"), Some("migration")).is_err());
        assert!(
            transcript_backfill_authority(true, Some(batch_id), Some(""), Some("migration"))
                .is_err()
        );
        assert!(
            transcript_backfill_authority(true, Some(batch_id), Some("ops"), Some(" ")).is_err()
        );
        let authority =
            transcript_backfill_authority(true, Some(batch_id), Some("ops"), Some("migration"))
                .unwrap()
                .unwrap();
        assert_eq!(authority.batch_id, batch_id);
    }
}
