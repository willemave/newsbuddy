use super::{AnyResult, Database, OutputFormat, TasksArgs, TasksCommand, emit_success, operator};

async fn execute_archive_command(
    database: &Database,
    command: &TasksCommand,
    output: OutputFormat,
) -> AnyResult<()> {
    match command {
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
            let mut ids = task_ids.clone();
            ids.sort_unstable();
            ids.dedup();
            anyhow::ensure!(
                !ids.is_empty() && ids.len() <= 100 && ids.iter().all(|id| *id > 0),
                "supply 1..100 positive task IDs"
            );
            anyhow::ensure!(
                *user_id > 0
                    && *config_id > 0
                    && created_from < created_before
                    && !actor.trim().is_empty()
                    && !reason.trim().is_empty(),
                "positive owner/config, bounded creation window, actor and reason are required"
            );
            let scope = newsly_db::incident_batches::ArchiveIncidentScope {
                task_ids: ids.clone(),
                user_id: *user_id,
                config_id: *config_id,
                created_from: *created_from,
                created_before: *created_before,
                published_before: *published_before,
            };
            let mut tx = database.pool().begin().await?;
            sqlx::query("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
                .execute(&mut *tx)
                .await?;
            let fresh = if *apply {
                newsly_db::incident_batches::record_batch(
                    &mut tx,
                    *batch_id,
                    "archive_incident",
                    actor,
                    reason,
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
            if *apply && fresh {
                anyhow::ensure!(
                    candidates == ids,
                    "inventory changed or protected user state exists; rerun dry-run and review exact candidates"
                );
                newsly_db::incident_batches::apply_archive(&mut tx, &scope, reason).await?;
            }
            if *apply {
                tx.commit().await?;
            } else {
                tx.rollback().await?;
            }
            let report = serde_json::json!({"batch_id":batch_id,"applied":apply,"replayed":!fresh,"eligible_task_ids":candidates});
            emit_success(
                output,
                "tasks.reconcile_archive",
                &report,
                &report.to_string(),
            )?;
        }
        _ => unreachable!("archive command dispatch"),
    }
    Ok(())
}

pub(super) async fn execute_tasks_command(
    database: &Database,
    args: &TasksArgs,
    output: OutputFormat,
) -> AnyResult<()> {
    match &args.command {
        TasksCommand::ReconcileArchive { .. } => {
            execute_archive_command(database, &args.command, output).await?;
        }
        TasksCommand::ArtworkBackfill {
            content_ids,
            batch_id,
            actor,
            reason,
            apply,
        } => {
            let mut ids = content_ids.clone();
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
            let fresh = if *apply {
                newsly_db::incident_batches::record_batch(
                    &mut tx,
                    *batch_id,
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
            if *apply && fresh {
                anyhow::ensure!(
                    candidates == ids,
                    "inventory changed or includes ineligible rows; rerun dry-run and review exact candidates"
                );
                let queue = newsly_queue::QueueKernel::new(database.pool().clone());
                let requests = candidates
                    .iter()
                    .map(|id| {
                        let mut request = newsly_queue::EnqueueRequest::new(
                            newsly_queue::TaskType::GenerateImage,
                        );
                        request.content_id = Some(*id);
                        request.dedupe = Some(true);
                        request
                    })
                    .collect::<Vec<_>>();
                queue.enqueue_many_in_transaction(&mut tx, requests).await?;
                newsly_db::incident_batches::mark_artwork_pending(&mut tx, &candidates).await?;
            }
            if *apply {
                tx.commit().await?;
            } else {
                tx.rollback().await?;
            }
            let report = serde_json::json!({"batch_id":batch_id,"applied":apply,"replayed":!fresh,"eligible_content_ids":candidates});
            emit_success(
                output,
                "tasks.artwork_backfill",
                &report,
                &report.to_string(),
            )?;
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
