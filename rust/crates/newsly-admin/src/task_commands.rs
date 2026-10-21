use super::{AnyResult, Database, OutputFormat, TasksArgs, TasksCommand, emit_success, operator};

struct ArchiveCommand<'a> {
    task_ids: &'a [i64],
    user_id: i64,
    config_id: i64,
    created_from: chrono::NaiveDateTime,
    created_before: chrono::NaiveDateTime,
    published_before: chrono::NaiveDateTime,
    batch_id: uuid::Uuid,
    actor: &'a str,
    reason: &'a str,
    apply: bool,
}

async fn execute_archive_command(
    database: &Database,
    command: ArchiveCommand<'_>,
    output: OutputFormat,
) -> AnyResult<()> {
    let mut ids = command.task_ids.to_vec();
    ids.sort_unstable();
    ids.dedup();
    anyhow::ensure!(
        !ids.is_empty() && ids.len() <= 100 && ids.iter().all(|id| *id > 0),
        "supply 1..100 positive task IDs"
    );
    anyhow::ensure!(
        command.user_id > 0
            && command.config_id > 0
            && command.created_from < command.created_before
            && !command.actor.trim().is_empty()
            && !command.reason.trim().is_empty(),
        "positive owner/config, bounded creation window, actor and reason are required"
    );
    let scope = newsly_db::incident_batches::ArchiveIncidentScope {
        task_ids: ids.clone(),
        user_id: command.user_id,
        config_id: command.config_id,
        created_from: command.created_from,
        created_before: command.created_before,
        published_before: command.published_before,
    };
    let mut tx = database.pool().begin().await?;
    sqlx::query("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        .execute(&mut *tx)
        .await?;
    let fresh = if command.apply {
        newsly_db::incident_batches::record_batch(
            &mut tx,
            command.batch_id,
            "archive_incident",
            command.actor,
            command.reason,
            &serde_json::to_value(&scope)?,
        )
        .await?
    } else {
        true
    };
    let candidates = if fresh {
        newsly_db::incident_batches::archive_candidates(&mut tx, &scope).await?
    } else {
        vec![]
    };
    if command.apply && fresh {
        anyhow::ensure!(
            candidates == ids,
            "inventory changed or protected user state exists; rerun dry-run and review exact candidates"
        );
        newsly_db::incident_batches::apply_archive(&mut tx, &scope, command.reason).await?;
    }
    if command.apply {
        tx.commit().await?;
    } else {
        tx.rollback().await?;
    }
    let report = serde_json::json!({"batch_id":command.batch_id,"applied":command.apply,"replayed":!fresh,"eligible_task_ids":candidates});
    emit_success(
        output,
        "tasks.reconcile-archive",
        &report,
        &report.to_string(),
    )?;
    Ok(())
}

async fn execute_artwork_backfill(
    database: &Database,
    content_ids: &[i64],
    batch_id: uuid::Uuid,
    actor: &str,
    reason: &str,
    apply: bool,
    output: OutputFormat,
) -> AnyResult<()> {
    let mut ids = content_ids.to_vec();
    ids.sort_unstable();
    ids.dedup();
    anyhow::ensure!(
        !ids.is_empty() && ids.len() <= 100 && ids.iter().all(|id| *id > 0),
        "supply 1..100 positive content IDs"
    );
    anyhow::ensure!(
        !actor.trim().is_empty() && !reason.trim().is_empty(),
        "actor and reason are required"
    );
    let targets = serde_json::json!(ids);
    let mut tx = database.pool().begin().await?;
    let fresh = if apply {
        newsly_db::incident_batches::record_batch(
            &mut tx,
            batch_id,
            "artwork_backfill",
            actor,
            reason,
            &targets,
        )
        .await?
    } else {
        true
    };
    let candidates = if fresh {
        newsly_db::incident_batches::artwork_candidates(&mut tx, &ids).await?
    } else {
        vec![]
    };
    if apply && fresh {
        anyhow::ensure!(
            candidates == ids,
            "inventory changed or includes ineligible rows; rerun dry-run and review exact candidates"
        );
        let queue = newsly_queue::QueueKernel::new(database.pool().clone());
        let requests = candidates
            .iter()
            .map(|id| {
                let mut request =
                    newsly_queue::EnqueueRequest::new(newsly_queue::TaskType::GenerateImage);
                request.content_id = Some(*id);
                request.dedupe = Some(true);
                request
            })
            .collect::<Vec<_>>();
        queue.enqueue_many_in_transaction(&mut tx, requests).await?;
        newsly_db::incident_batches::mark_artwork_pending(&mut tx, &candidates).await?;
    }
    if apply {
        tx.commit().await?;
    } else {
        tx.rollback().await?;
    }
    let report = serde_json::json!({"batch_id":batch_id,"applied":apply,"replayed":!fresh,"eligible_content_ids":candidates});
    emit_success(
        output,
        "tasks.artwork-backfill",
        &report,
        &report.to_string(),
    )?;
    Ok(())
}

pub(super) async fn execute_tasks_command(
    database: &Database,
    args: &TasksArgs,
    output: OutputFormat,
) -> AnyResult<()> {
    match &args.command {
        command @ TasksCommand::ReconcileBookmarks { .. } => {
            super::bookmark_commands::execute(database, command, output).await?;
        }
        TasksCommand::ReconcileArchive {
            task_ids,
            user_id,
            config_id,
            created_from,
            created_before,
            published_before,
            batch_id,
            actor,
            reason,
            apply,
        } => {
            execute_archive_command(
                database,
                ArchiveCommand {
                    task_ids,
                    user_id: *user_id,
                    config_id: *config_id,
                    created_from: *created_from,
                    created_before: *created_before,
                    published_before: *published_before,
                    batch_id: *batch_id,
                    actor,
                    reason,
                    apply: *apply,
                },
                output,
            )
            .await?;
        }
        TasksCommand::ArtworkBackfill {
            content_ids,
            batch_id,
            actor,
            reason,
            apply,
        } => {
            execute_artwork_backfill(
                database,
                content_ids,
                *batch_id,
                actor,
                reason,
                *apply,
                output,
            )
            .await?;
        }
        TasksCommand::Failures {
            window_hours,
            limit,
        } => {
            let failures =
                operator::load_recent_task_failures(database.pool(), *window_hours, *limit).await?;
            emit_success(output, "tasks.failures", &failures, &failures.render_text())?;
        }
    }
    Ok(())
}
