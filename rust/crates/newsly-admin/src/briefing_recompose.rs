//! `newsly-admin tasks briefing-recompose`: preview live Articles and Podcasts cards written by
//! an older card prompt and optionally start the sweeps that recompose them.

use super::{AnyResult, Database, OutputFormat, emit_success};

/// Normal sweeps recompose stale cards a few per refresh, keeping their positions; `--apply`
/// starts each affected user's sweep now instead of at its next scheduled time.
#[derive(Debug, clap::Args)]
pub(super) struct BriefingRecomposeArgs {
    /// Limit to these users. Defaults to every active user with stale cards.
    #[arg(long, value_delimiter = ',')]
    pub(super) user_ids: Vec<i64>,
    #[arg(long, default_value_t = 500)]
    pub(super) limit: i64,
    #[arg(long, required_if_eq("apply", "true"))]
    pub(super) batch_id: Option<uuid::Uuid>,
    #[arg(long, required_if_eq("apply", "true"))]
    pub(super) actor: Option<String>,
    #[arg(long, required_if_eq("apply", "true"))]
    pub(super) reason: Option<String>,
    #[arg(long)]
    pub(super) apply: bool,
}

struct BriefingRecomposeCommand<'a> {
    user_ids: &'a [i64],
    limit: i64,
    batch_id: Option<uuid::Uuid>,
    actor: Option<&'a str>,
    reason: Option<&'a str>,
    apply: bool,
}

pub(super) async fn execute(
    database: &Database,
    args: &BriefingRecomposeArgs,
    output: OutputFormat,
) -> AnyResult<()> {
    let command = BriefingRecomposeCommand {
        user_ids: &args.user_ids,
        limit: args.limit,
        batch_id: args.batch_id,
        actor: args.actor.as_deref(),
        reason: args.reason.as_deref(),
        apply: args.apply,
    };
    let report = briefing_recompose(database.pool(), command).await?;
    emit_success(
        output,
        "tasks.briefing-recompose",
        &report,
        &report.to_string(),
    )?;
    Ok(())
}

async fn briefing_recompose(
    pool: &sqlx::PgPool,
    command: BriefingRecomposeCommand<'_>,
) -> AnyResult<serde_json::Value> {
    let mut user_ids = command.user_ids.to_vec();
    user_ids.sort_unstable();
    user_ids.dedup();
    anyhow::ensure!(
        user_ids.iter().all(|id| *id > 0),
        "user IDs must be positive"
    );
    anyhow::ensure!(
        (1..=1_000).contains(&command.limit),
        "limit must be between 1 and 1000 users"
    );
    let authority = if command.apply {
        let (Some(batch_id), Some(actor), Some(reason)) =
            (command.batch_id, command.actor, command.reason)
        else {
            anyhow::bail!("--apply requires --batch-id, --actor, and --reason");
        };
        anyhow::ensure!(
            !actor.trim().is_empty() && !reason.trim().is_empty(),
            "actor and reason are required"
        );
        Some((batch_id, actor, reason))
    } else {
        None
    };

    let mut tx = pool.begin().await?;
    let inventory = newsly_db::stale_card_inventory(&mut tx, &user_ids, command.limit).await?;
    let affected_users = inventory.iter().map(|row| row.user_id).collect::<Vec<_>>();
    let mut replayed = false;
    let mut queued_task_ids = Vec::new();
    let mut already_pending_task_ids = Vec::new();
    if let Some((batch_id, actor, reason)) = authority
        && !affected_users.is_empty()
    {
        // The requested scope, not the computed user set, so replaying a batch ID after some
        // users finish draining reports a replay instead of a conflict.
        let targets = serde_json::json!({
            "card_prompt_version": newsly_db::BRIEFING_CARD_PROMPT_VERSION,
            "requested_user_ids": user_ids,
            "limit": command.limit,
        });
        let fresh = newsly_db::incident_batches::record_batch(
            &mut tx,
            batch_id,
            "briefing_card_recompose",
            actor,
            reason,
            &targets,
        )
        .await?;
        replayed = !fresh;
        if fresh {
            (queued_task_ids, already_pending_task_ids) =
                enqueue_briefing_sweeps(pool, &mut tx, &affected_users).await?;
        }
    }
    if command.apply {
        tx.commit().await?;
    } else {
        tx.rollback().await?;
    }

    let per_refresh = i64::try_from(newsly_db::STALE_CARD_RECOMPOSE_LIMIT)?;
    let total_stale_cards = inventory.iter().map(|row| row.stale_cards).sum::<i64>();
    let refreshes_needed = inventory
        .iter()
        .map(|row| (row.stale_cards + per_refresh - 1) / per_refresh)
        .max()
        .unwrap_or(0);
    let report = serde_json::json!({
        "card_prompt_version": newsly_db::BRIEFING_CARD_PROMPT_VERSION,
        "cards_per_refresh": per_refresh,
        "applied": command.apply,
        "replayed": replayed,
        "truncated": i64::try_from(inventory.len())? == command.limit,
        "users": inventory,
        "total_stale_cards": total_stale_cards,
        "refreshes_needed": refreshes_needed,
        "queued_task_ids": queued_task_ids,
        "already_pending_task_ids": already_pending_task_ids,
    });
    Ok(report)
}

/// Queues the same deduplicated sweep the hourly scheduler queues, so a pending sweep absorbs
/// the request instead of doubling the user's refresh work. Returns newly queued task IDs and
/// the IDs of sweeps that were already pending.
async fn enqueue_briefing_sweeps(
    pool: &sqlx::PgPool,
    tx: &mut sqlx::Transaction<'_, sqlx::Postgres>,
    user_ids: &[i64],
) -> AnyResult<(Vec<i64>, Vec<i64>)> {
    let requests = user_ids
        .iter()
        .map(|user_id| {
            let mut request =
                newsly_queue::EnqueueRequest::new(newsly_queue::TaskType::BriefingRefresh);
            request.payload = Some(serde_json::Map::from_iter([
                ("user_id".to_owned(), serde_json::Value::from(*user_id)),
                ("mode".to_owned(), serde_json::Value::from("sweep")),
            ]));
            request.owner_user_id = Some(*user_id);
            request.dedupe = Some(true);
            request.dedupe_key = Some(format!("briefing_refresh:{user_id}:sweep"));
            request
        })
        .collect::<Vec<_>>();
    let queue = newsly_queue::QueueKernel::new(pool.clone());
    let result = queue.enqueue_many_in_transaction(tx, requests).await?;
    let already_pending = result
        .task_ids
        .iter()
        .copied()
        .filter(|id| !result.inserted_task_ids.contains(id))
        .collect::<Vec<_>>();
    // A finished sweep chains its successor a few minutes out, so one is usually pending.
    // Bring it forward instead of waiting; retries keep their backoff.
    sqlx::query(
        "UPDATE processing_tasks SET available_at = LEAST(available_at, timezone('UTC', clock_timestamp())) WHERE id = ANY($1::bigint[]) AND status = 'pending' AND retry_count = 0",
    )
    .bind(&already_pending)
    .execute(&mut **tx)
    .await?;
    Ok((result.inserted_task_ids, already_pending))
}

#[cfg(test)]
mod tests {
    use super::{BriefingRecomposeArgs, BriefingRecomposeCommand, briefing_recompose};
    use crate::{Cli, Command, TasksArgs, TasksCommand};
    use clap::Parser;
    use sqlx::PgPool;

    async fn seed_stale_card(pool: &PgPool) -> i64 {
        newsly_db::run_migrations(pool).await.unwrap();
        let user_id: i64 = sqlx::query_scalar(
            "INSERT INTO users(apple_id,email,is_active,is_admin) VALUES('recompose','recompose@example.com',true,false) RETURNING id::bigint",
        )
        .fetch_one(pool)
        .await
        .unwrap();
        let lens_id: i64 = sqlx::query_scalar(
            "INSERT INTO briefing_lenses(user_id,key,tier,title,deck,position,status,centroid_weight) VALUES($1,'articles','longform','Articles','',0,'active',0) RETURNING id::bigint",
        )
        .bind(user_id)
        .fetch_one(pool)
        .await
        .unwrap();
        for (version, keys) in [
            ("briefing-v8-density", r#"["content:1"]"#),
            (newsly_db::BRIEFING_CARD_PROMPT_VERSION, r#"["content:2"]"#),
            ("briefing-v8-density", r#"["content:3","content:4"]"#),
        ] {
            sqlx::query("INSERT INTO briefing_segments(lens_id,user_id,blocks,markdown_raw,narration_text,source_keys,status,model,prompt_version,warnings) VALUES($1,$2,'[]','','',$3::jsonb,'active','test',$4,'[]')")
                .bind(lens_id)
                .bind(user_id)
                .bind(keys)
                .bind(version)
                .execute(pool)
                .await
                .unwrap();
        }
        user_id
    }

    fn command(user_ids: &[i64], apply: bool, batch: uuid::Uuid) -> BriefingRecomposeCommand<'_> {
        BriefingRecomposeCommand {
            user_ids,
            limit: 10,
            batch_id: Some(batch),
            actor: Some("operator"),
            reason: Some("card prompt rollout"),
            apply,
        }
    }

    #[sqlx::test(migrations = false)]
    async fn preview_counts_only_stale_one_source_cards_and_writes_nothing(pool: PgPool) {
        let user_id = seed_stale_card(&pool).await;
        let report = briefing_recompose(&pool, command(&[], false, uuid::Uuid::new_v4()))
            .await
            .unwrap();
        assert_eq!(report["total_stale_cards"], 1);
        assert_eq!(report["users"][0]["user_id"], user_id);
        assert_eq!(report["applied"], false);
        let tasks: i64 = sqlx::query_scalar("SELECT count(*) FROM processing_tasks")
            .fetch_one(&pool)
            .await
            .unwrap();
        assert_eq!(tasks, 0);
    }

    #[sqlx::test(migrations = false)]
    async fn apply_starts_a_pending_sweep_now_and_replays_cleanly(pool: PgPool) {
        let user_id = seed_stale_card(&pool).await;
        // The chained sweep a finished refresh leaves a few minutes out.
        let mut request =
            newsly_queue::EnqueueRequest::new(newsly_queue::TaskType::BriefingRefresh);
        request.payload = Some(serde_json::Map::from_iter([
            ("user_id".to_owned(), serde_json::Value::from(user_id)),
            ("mode".to_owned(), serde_json::Value::from("sweep")),
        ]));
        request.owner_user_id = Some(user_id);
        request.dedupe = Some(true);
        request.dedupe_key = Some(format!("briefing_refresh:{user_id}:sweep"));
        request.available_at = Some(chrono::Utc::now() + chrono::Duration::minutes(5));
        let mut tx = pool.begin().await.unwrap();
        let pending = newsly_queue::QueueKernel::new(pool.clone())
            .enqueue_many_in_transaction(&mut tx, vec![request])
            .await
            .unwrap()
            .task_ids[0];
        tx.commit().await.unwrap();
        let batch = uuid::Uuid::new_v4();
        let report = briefing_recompose(&pool, command(&[user_id], true, batch))
            .await
            .unwrap();
        assert_eq!(report["applied"], true);
        assert_eq!(report["replayed"], false);
        assert_eq!(report["queued_task_ids"], serde_json::json!([]));
        assert_eq!(
            report["already_pending_task_ids"],
            serde_json::json!([pending])
        );
        let due: bool = sqlx::query_scalar(
            "SELECT available_at <= timezone('UTC', now()) FROM processing_tasks WHERE id = $1",
        )
        .bind(pending)
        .fetch_one(&pool)
        .await
        .unwrap();
        assert!(due, "the pending sweep runs now");

        let replay = briefing_recompose(&pool, command(&[user_id], true, batch))
            .await
            .unwrap();
        assert_eq!(replay["replayed"], true);
        let tasks: i64 = sqlx::query_scalar("SELECT count(*) FROM processing_tasks")
            .fetch_one(&pool)
            .await
            .unwrap();
        assert_eq!(tasks, 1, "no duplicate sweep");
    }

    #[test]
    fn briefing_recompose_previews_freely_and_applies_only_with_authority() {
        let preview = Cli::try_parse_from(["newsly-admin", "tasks", "briefing-recompose"]).unwrap();
        assert_eq!(preview.command.name(), "tasks.briefing-recompose");
        assert!(matches!(
            preview.command,
            Command::Tasks(TasksArgs {
                command: TasksCommand::BriefingRecompose(BriefingRecomposeArgs {
                    apply: false,
                    ..
                })
            })
        ));
        let scoped = Cli::try_parse_from([
            "newsly-admin",
            "tasks",
            "briefing-recompose",
            "--user-ids",
            "3,1,3",
        ])
        .unwrap();
        assert!(matches!(
            scoped.command,
            Command::Tasks(TasksArgs {
                command: TasksCommand::BriefingRecompose(BriefingRecomposeArgs { ref user_ids, .. })
            }) if user_ids == &[3, 1, 3]
        ));
        assert!(
            Cli::try_parse_from(["newsly-admin", "tasks", "briefing-recompose", "--apply"])
                .is_err(),
            "--apply needs a batch ID, actor, and reason"
        );
        assert!(
            Cli::try_parse_from([
                "newsly-admin",
                "tasks",
                "briefing-recompose",
                "--apply",
                "--batch-id",
                "aabfae23-b7bd-4c36-92ea-6922bbc9d4e0",
                "--actor",
                "operator",
                "--reason",
                "card prompt rollout",
            ])
            .is_ok()
        );
    }
}
