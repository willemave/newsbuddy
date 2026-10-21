use super::{AnyResult, Database, OutputFormat, TasksCommand, emit_success};

pub(super) async fn execute(
    database: &Database,
    command: &TasksCommand,
    output: OutputFormat,
) -> AnyResult<()> {
    let TasksCommand::ReconcileBookmarks {
        user_id,
        content_ids,
        imported_before,
        expected_version,
        batch_id,
        actor,
        reason,
        apply,
    } = command
    else {
        anyhow::bail!("expected bookmark reconciliation command")
    };
    let mut ids = content_ids.clone();
    ids.sort_unstable();
    ids.dedup();
    let scope = newsly_db::incident_batches::bookmarks::BookmarkReconciliationScope {
        user_id: *user_id,
        content_ids: ids,
        imported_before: *imported_before,
        expected_version: *expected_version,
    };
    let report = newsly_db::incident_batches::bookmarks::reconcile_legacy_bookmarks(
        database.pool(),
        &scope,
        *batch_id,
        actor,
        reason,
        *apply,
    )
    .await?;
    let text = format!(
        "{} bookmark(s), {} segment(s), {} pending; version {} -> {}; applied={} replayed={}",
        report.bookmarks.len(),
        report.segments.len(),
        report.pending.len(),
        report.version_before,
        report.version_after,
        report.applied,
        report.replayed
    );
    emit_success(output, "tasks.reconcile-bookmarks", &report, &text)
}
