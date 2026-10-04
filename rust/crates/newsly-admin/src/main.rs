use std::path::PathBuf;
use std::process::ExitCode;
use std::str::FromStr;

use anyhow::{Context, Result as AnyResult, bail};
use chrono::{DateTime, NaiveDate, NaiveDateTime, Utc};
use clap::{Args, Parser, Subcommand, ValueEnum};
use newsly_db::{Database, DatabaseConfig, OwnershipRepository};
use newsly_domain::{OwnershipTarget, OwnershipVersion, ResourceKey, ResourceKind};
use secrecy::SecretString;
use serde::Serialize;
mod database;
mod e2e;
mod evals;
mod operator;
mod ownership_commands;
mod ownership_manifest;
mod task_commands;
mod transcript_commands;

use operator::{QueryWindow, UsageGroupBy};
use ownership_manifest::load_ownership_policy_manifest;

#[derive(Debug, Parser)]
#[command(name = "newsly-admin", about = "Newsly Rust runtime operator")]
struct Cli {
    #[arg(long, env = "DATABASE_URL", hide_env_values = true)]
    database_url: Option<SecretString>,

    #[arg(
        long,
        env = "NEWSLY_RUST_DATABASE_MAX_CONNECTIONS",
        default_value_t = 2
    )]
    max_connections: u32,

    #[arg(long, value_enum, default_value_t, global = true)]
    output: OutputFormat,

    #[command(subcommand)]
    command: Command,
}

#[derive(Debug, Subcommand)]
enum Command {
    /// Inspect `PostgreSQL` through bounded, read-only operator queries.
    Db(DbArgs),
    /// Inspect or mutate the canonical runtime ownership registry.
    Ownership(OwnershipArgs),
    /// Inspect coarse service and queue health through read-only `PostgreSQL` queries.
    Health(HealthArgs),
    /// Inspect bounded processing-task failures without exposing task payloads.
    Tasks(TasksArgs),
    /// Summarize persisted model and vendor usage costs.
    Usage(UsageArgs),
    /// Export database inputs for offline Python model and embedding evaluations.
    Evals(EvalsArgs),
    /// Seed deterministic local-only data for iOS `AXe` and Maestro tests.
    E2e(E2eArgs),
}

impl Command {
    const fn name(&self) -> &'static str {
        match self {
            Self::Db(args) => args.command.name(),
            Self::Ownership(args) => args.command.name(),
            Self::Health(args) => args.command.name(),
            Self::Tasks(args) => args.command.name(),
            Self::Usage(_) => "usage.summary",
            Self::Evals(_) => "evals.export-title-clustering",
            Self::E2e(_) => "e2e.seed",
        }
    }
}

#[derive(Debug, Args)]
struct DbArgs {
    #[command(subcommand)]
    command: DbCommand,
}

#[derive(Debug, Subcommand)]
enum DbCommand {
    /// List ordinary tables and views in one `PostgreSQL` schema.
    Tables {
        #[arg(long, default_value = "public")]
        schema: String,
        #[arg(long, default_value_t = 200)]
        limit: i64,
    },
    /// Describe columns and primary-key membership for one table or a bounded schema slice.
    Schema {
        #[arg(long, default_value = "public")]
        schema: String,
        #[arg(long)]
        table: Option<String>,
        #[arg(long, default_value_t = 200)]
        limit: i64,
    },
    /// Run one bounded SELECT/WITH statement in an enforced read-only transaction.
    Query {
        #[arg(long)]
        sql: String,
        #[arg(long, default_value_t = 200)]
        limit: i64,
        /// Return sensitive fields without the normal recursive redaction pass.
        #[arg(long)]
        unsafe_raw: bool,
    },
    /// Return a JSON `PostgreSQL` plan without executing the query.
    Explain {
        #[arg(long)]
        sql: String,
    },
    /// Count canonical, legacy-shape and invalid stored chat transcripts without returning payloads.
    TranscriptCompatibility {
        #[arg(long, default_value_t = 0)]
        after_id: i64,
        #[arg(long, default_value_t = 1_000)]
        limit: i64,
    },
    /// Dry-run or apply a bounded compare-and-swap conversion of legacy chat transcripts.
    TranscriptBackfill {
        #[arg(long, default_value_t = 0)]
        after_id: i64,
        #[arg(long, default_value_t = 1_000)]
        limit: i64,
        #[arg(long)]
        batch_id: Option<uuid::Uuid>,
        #[arg(long)]
        actor: Option<String>,
        #[arg(long)]
        reason: Option<String>,
        #[arg(long)]
        apply: bool,
    },
}

impl DbCommand {
    const fn name(&self) -> &'static str {
        match self {
            Self::Tables { .. } => "db.tables",
            Self::Schema { .. } => "db.schema",
            Self::Query { .. } => "db.query",
            Self::Explain { .. } => "db.explain",
            Self::TranscriptCompatibility { .. } => "db.transcript-compatibility",
            Self::TranscriptBackfill { .. } => "db.transcript-backfill",
        }
    }
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, ValueEnum)]
enum OutputFormat {
    #[default]
    Text,
    Json,
}

#[derive(Debug, Args)]
struct HealthArgs {
    #[command(subcommand)]
    command: HealthCommand,
}

#[derive(Debug, Subcommand)]
enum HealthCommand {
    /// Snapshot content, task, and usage freshness counts.
    Snapshot,
    /// Inspect queue backlogs, leases, throughput, latency, and grouped failures.
    Queue {
        #[arg(long, default_value_t = 24)]
        window_hours: i64,
        #[arg(long, default_value_t = 10)]
        top_errors_limit: i64,
    },
}

impl HealthCommand {
    const fn name(&self) -> &'static str {
        match self {
            Self::Snapshot => "health.snapshot",
            Self::Queue { .. } => "health.queue",
        }
    }
}

#[derive(Debug, Args)]
struct TasksArgs {
    #[command(subcommand)]
    command: TasksCommand,
}

#[derive(Debug, Subcommand)]
enum TasksCommand {
    /// Dry-run by default. Cancel reviewed accidental feed work without deleting content or reads.
    ReconcileArchive {
        #[arg(long, value_delimiter = ',', required = true)]
        task_ids: Vec<i64>,
        #[arg(long)]
        user_id: i64,
        #[arg(long)]
        config_id: i64,
        #[arg(long)]
        created_from: chrono::NaiveDateTime,
        #[arg(long)]
        created_before: chrono::NaiveDateTime,
        #[arg(long)]
        published_before: chrono::NaiveDateTime,
        #[arg(long)]
        batch_id: uuid::Uuid,
        #[arg(long)]
        actor: String,
        #[arg(long)]
        reason: String,
        #[arg(long)]
        apply: bool,
    },
    /// Preview or apply at most 100 reviewed historical artwork jobs. Existing attempts are excluded.
    ArtworkBackfill {
        #[arg(long, value_delimiter = ',', required = true)]
        content_ids: Vec<i64>,
        #[arg(long)]
        batch_id: uuid::Uuid,
        #[arg(long)]
        actor: String,
        #[arg(long)]
        reason: String,
        #[arg(long)]
        apply: bool,
    },
    /// List recent failed tasks with bounded diagnostics and no task payloads.
    Failures {
        #[arg(long, default_value_t = 24)]
        window_hours: i64,
        #[arg(long, default_value_t = 20)]
        limit: i64,
    },
}

impl TasksCommand {
    const fn name(&self) -> &'static str {
        match self {
            Self::ReconcileArchive { .. } => "tasks.reconcile-archive",
            Self::ArtworkBackfill { .. } => "tasks.artwork-backfill",
            Self::Failures { .. } => "tasks.failures",
        }
    }
}

#[derive(Debug, Args)]
struct UsageArgs {
    #[command(subcommand)]
    command: UsageCommand,
}

#[derive(Debug, Args)]
struct EvalsArgs {
    #[command(subcommand)]
    command: EvalsCommand,
}

#[derive(Debug, Args)]
struct E2eArgs {
    #[command(subcommand)]
    command: E2eCommand,
}

#[derive(Debug, Subcommand)]
enum E2eCommand {
    /// Replace one namespaced local iOS fixture graph and emit its manifest.
    Seed {
        #[arg(long, default_value = "local")]
        namespace: String,
        #[arg(long, default_value_t = 8000)]
        server_port: u16,
        /// Confirm that fixture writes are intended for the loopback development database.
        #[arg(long)]
        confirm_local: bool,
    },
}

#[derive(Debug, Subcommand)]
enum EvalsCommand {
    /// Write a bounded, read-only title-clustering source snapshot as versioned JSONL.
    ExportTitleClustering {
        #[arg(long)]
        path: PathBuf,
        #[arg(long, default_value_t = 10_000)]
        limit: i64,
    },
}

#[derive(Debug, Serialize)]
struct EvalExportReceipt {
    path: PathBuf,
    record_count: usize,
    schema_version: u8,
    artifact_type: &'static str,
    database_access: &'static str,
}

#[derive(Debug, Subcommand)]
enum UsageCommand {
    /// Summarize bounded persisted usage and estimated cost.
    Summary {
        /// Inclusive RFC3339 timestamp or UTC YYYY-MM-DD date.
        #[arg(long)]
        since: Option<String>,
        /// Inclusive RFC3339 timestamp or UTC YYYY-MM-DD date.
        #[arg(long)]
        until: Option<String>,
        /// Default lookback when --since is omitted (maximum 2160 hours).
        #[arg(long, default_value_t = 24)]
        window_hours: i64,
        #[arg(long, value_enum, default_value_t = UsageGroupBy::Feature)]
        group_by: UsageGroupBy,
    },
}

#[derive(Debug, Args)]
struct OwnershipArgs {
    #[command(subcommand)]
    command: OwnershipCommand,
}

#[derive(Debug, Subcommand)]
enum OwnershipCommand {
    /// Validate the checked-in desired-state manifest and optionally its live registry coverage.
    ValidateManifest {
        #[arg(long, default_value = "contracts/policy-manifest.toml")]
        manifest: PathBuf,
        #[arg(long)]
        live: bool,
    },
    /// Register missing manifest resources at their baseline owner without changing existing rows.
    SeedManifest {
        #[arg(long, default_value = "contracts/policy-manifest.toml")]
        manifest: PathBuf,
        #[command(flatten)]
        audit: AuditArgs,
    },
    /// Read one live ownership decision.
    Show {
        #[arg(long, value_parser = parse_resource)]
        resource: ResourceArg,
    },
    /// Prepare an atomic owner/version cutover batch.
    Prepare {
        #[arg(long, required = true, value_parser = parse_target)]
        target: Vec<TargetArg>,
        #[command(flatten)]
        audit: AuditArgs,
    },
    /// Prepare an atomic rollback without rewriting any stamped durable work.
    PrepareRollback {
        #[arg(long, required = true, value_parser = parse_target)]
        target: Vec<TargetArg>,
        #[command(flatten)]
        audit: AuditArgs,
    },
    /// Advance a gateway acknowledgement for a prepared desired version.
    Acknowledge {
        #[arg(long, value_parser = parse_resource)]
        resource: ResourceArg,
        #[arg(long)]
        desired_version: i64,
        #[arg(long)]
        replica_id: String,
        #[arg(long)]
        readiness: String,
        #[arg(long, env = "NEWSLY_APPLICATION_SHA")]
        application_sha: String,
    },
    /// Promote a prepared cutover after exact-SHA acknowledgements and drain proofs.
    Promote {
        #[arg(long, required = true, value_parser = parse_target)]
        target: Vec<TargetArg>,
        #[arg(long = "replica", required = true)]
        replicas: Vec<String>,
        #[command(flatten)]
        audit: AuditArgs,
    },
    /// Promote a prepared rollback after the same barriers used by a forward cutover.
    PromoteRollback {
        #[arg(long, required = true, value_parser = parse_target)]
        target: Vec<TargetArg>,
        #[arg(long = "replica", required = true)]
        replicas: Vec<String>,
        #[command(flatten)]
        audit: AuditArgs,
    },
    /// Inspect active source work for one task-type transition.
    DrainStatus {
        #[arg(long)]
        task_type: String,
        #[arg(long)]
        source_owner: String,
    },
    /// Clear promotion acknowledgements after a verified rollback window.
    ClearAcknowledgements {
        #[arg(long, value_parser = parse_resource)]
        resource: ResourceArg,
        #[arg(long)]
        expected_owner: String,
        #[arg(long)]
        expected_version: i64,
        #[arg(long)]
        minimum_age_seconds: i64,
        #[command(flatten)]
        audit: AuditArgs,
    },
}

impl OwnershipCommand {
    const fn name(&self) -> &'static str {
        match self {
            Self::ValidateManifest { .. } => "ownership.validate-manifest",
            Self::SeedManifest { .. } => "ownership.seed-manifest",
            Self::Show { .. } => "ownership.show",
            Self::Prepare { .. } => "ownership.prepare",
            Self::PrepareRollback { .. } => "ownership.prepare-rollback",
            Self::Acknowledge { .. } => "ownership.acknowledge",
            Self::Promote { .. } => "ownership.promote",
            Self::PromoteRollback { .. } => "ownership.promote-rollback",
            Self::DrainStatus { .. } => "ownership.drain-status",
            Self::ClearAcknowledgements { .. } => "ownership.clear-acknowledgements",
        }
    }
}

#[derive(Debug, Args)]
struct AuditArgs {
    #[arg(long, env = "NEWSLY_APPLICATION_SHA")]
    application_sha: String,
    #[arg(long)]
    actor: String,
    #[arg(long)]
    reason: String,
}

#[derive(Debug, Clone)]
struct ResourceArg {
    kind: ResourceKind,
    key: ResourceKey,
}

#[derive(Debug, Clone)]
struct TargetArg(OwnershipTarget);

#[tokio::main]
async fn main() -> ExitCode {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "newsly_admin=info".into()),
        )
        .with_target(true)
        .init();

    let cli = Cli::parse();
    let command = cli.command.name();
    match run(&cli).await {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            emit_error(cli.output, command, &error);
            ExitCode::FAILURE
        }
    }
}

async fn run(cli: &Cli) -> AnyResult<()> {
    if cli.max_connections == 0 {
        bail!("NEWSLY_RUST_DATABASE_MAX_CONNECTIONS must be greater than zero");
    }
    if let Command::Ownership(OwnershipArgs {
        command:
            OwnershipCommand::ValidateManifest {
                manifest,
                live: false,
            },
    }) = &cli.command
    {
        let manifest = load_ownership_policy_manifest(manifest)?;
        let resource_count = manifest.registry_seeds()?.len();
        emit_success(
            cli.output,
            "ownership.validate-manifest",
            &serde_json::json!({"resource_count": resource_count, "live": false}),
            &format!("ownership manifest valid: {resource_count} resources"),
        )?;
        return Ok(());
    }

    let database = connect_database(cli)?;
    let result = execute_command(&database, &cli.command, cli.output).await;
    database.close().await;
    result
}

async fn execute_command(
    database: &Database,
    command: &Command,
    output: OutputFormat,
) -> AnyResult<()> {
    match command {
        Command::Db(db) => execute_db_command(database, db, output).await,
        Command::Ownership(ownership) => {
            let repository = OwnershipRepository::new(database.pool().clone());
            ownership_commands::execute_ownership_command(&repository, &ownership.command, output)
                .await
        }
        Command::Health(health) => execute_health_command(database, health, output).await,
        Command::Tasks(tasks) => {
            task_commands::execute_tasks_command(database, tasks, output).await
        }
        Command::Usage(usage) => execute_usage_command(database, usage, output).await,
        Command::Evals(evals) => execute_evals_command(database, evals, output).await,
        Command::E2e(e2e_args) => execute_e2e_command(database, e2e_args, output).await,
    }
}

async fn execute_e2e_command(
    database: &Database,
    args: &E2eArgs,
    output: OutputFormat,
) -> AnyResult<()> {
    match &args.command {
        E2eCommand::Seed {
            namespace,
            server_port,
            confirm_local,
        } => {
            let manifest =
                e2e::seed(database.pool(), namespace, *server_port, *confirm_local).await?;
            emit_success(output, "e2e.seed", &manifest, &manifest.render_text())?;
        }
    }
    Ok(())
}

async fn execute_db_command(
    database: &Database,
    args: &DbArgs,
    output: OutputFormat,
) -> AnyResult<()> {
    match &args.command {
        DbCommand::Tables { schema, limit } => {
            let result = database::list_tables(database.pool(), schema, *limit).await?;
            emit_success(output, "db.tables", &result, &result.render_text())?;
        }
        DbCommand::Schema {
            schema,
            table,
            limit,
        } => {
            let result =
                database::describe_schema(database.pool(), schema, table.as_deref(), *limit)
                    .await?;
            emit_success(output, "db.schema", &result, &result.render_text())?;
        }
        DbCommand::Query {
            sql,
            limit,
            unsafe_raw,
        } => {
            let result = database::run_query(database.pool(), sql, *limit, *unsafe_raw).await?;
            emit_success(output, "db.query", &result, &result.render_text())?;
        }
        DbCommand::Explain { sql } => {
            let result = database::explain_query(database.pool(), sql).await?;
            emit_success(output, "db.explain", &result, &result.render_text())?;
        }
        DbCommand::TranscriptCompatibility { after_id, limit } => {
            transcript_commands::execute_compatibility(database, *after_id, *limit, output).await?;
        }
        DbCommand::TranscriptBackfill {
            after_id,
            limit,
            batch_id,
            actor,
            reason,
            apply,
        } => {
            transcript_commands::execute_backfill(
                database,
                transcript_commands::TranscriptBackfillCommand {
                    after_id: *after_id,
                    limit: *limit,
                    batch_id: *batch_id,
                    actor: actor.as_deref(),
                    reason: reason.as_deref(),
                    apply: *apply,
                },
                output,
            )
            .await?;
        }
    }
    Ok(())
}

async fn execute_evals_command(
    database: &Database,
    args: &EvalsArgs,
    output: OutputFormat,
) -> AnyResult<()> {
    match &args.command {
        EvalsCommand::ExportTitleClustering { path, limit } => {
            let rows = evals::load_title_clustering_source(database.pool(), *limit).await?;
            if let Some(parent) = path
                .parent()
                .filter(|parent| !parent.as_os_str().is_empty())
            {
                tokio::fs::create_dir_all(parent)
                    .await
                    .with_context(|| format!("could not create {}", parent.display()))?;
            }
            let mut contents = Vec::new();
            for row in &rows {
                let mut value = serde_json::to_value(row)?;
                let object = value
                    .as_object_mut()
                    .context("title-clustering row did not serialize as an object")?;
                object.insert("schema_version".to_owned(), serde_json::json!(1));
                object.insert(
                    "artifact_type".to_owned(),
                    serde_json::json!("newsly.title_clustering.source_row"),
                );
                serde_json::to_writer(&mut contents, &value)?;
                contents.push(b'\n');
            }
            tokio::fs::write(path, contents)
                .await
                .with_context(|| format!("could not write {}", path.display()))?;
            let receipt = EvalExportReceipt {
                path: path.clone(),
                record_count: rows.len(),
                schema_version: 1,
                artifact_type: "newsly.title_clustering.source_snapshot",
                database_access: "read_only",
            };
            emit_success(
                output,
                "evals.export-title-clustering",
                &receipt,
                &format!(
                    "exported {} title-clustering rows to {}",
                    receipt.record_count,
                    receipt.path.display()
                ),
            )?;
        }
    }
    Ok(())
}

async fn execute_health_command(
    database: &Database,
    args: &HealthArgs,
    output: OutputFormat,
) -> AnyResult<()> {
    match &args.command {
        HealthCommand::Snapshot => {
            let snapshot = operator::load_health_snapshot(database.pool()).await?;
            emit_success(
                output,
                "health.snapshot",
                &snapshot,
                &snapshot.render_text(),
            )?;
        }
        HealthCommand::Queue {
            window_hours,
            top_errors_limit,
        } => {
            let snapshot =
                operator::load_queue_health(database.pool(), *window_hours, *top_errors_limit)
                    .await?;
            emit_success(output, "health.queue", &snapshot, &snapshot.render_text())?;
        }
    }
    Ok(())
}

async fn execute_usage_command(
    database: &Database,
    args: &UsageArgs,
    output: OutputFormat,
) -> AnyResult<()> {
    match &args.command {
        UsageCommand::Summary {
            since,
            until,
            window_hours,
            group_by,
        } => {
            let window = resolve_query_window(since.as_deref(), until.as_deref(), *window_hours)?;
            let summary = operator::load_usage_summary(database.pool(), window, *group_by).await?;
            emit_success(output, "usage.summary", &summary, &summary.render_text())?;
        }
    }
    Ok(())
}

fn emit_success<T: Serialize>(
    output: OutputFormat,
    command: &str,
    data: &T,
    text: &str,
) -> AnyResult<()> {
    match output {
        OutputFormat::Text => println!("{text}"),
        OutputFormat::Json => {
            let envelope = success_envelope(command, data);
            println!("{}", serde_json::to_string_pretty(&envelope)?);
        }
    }
    Ok(())
}

fn emit_error(output: OutputFormat, command: &str, error: &anyhow::Error) {
    match output {
        OutputFormat::Text => eprintln!("newsly-admin {command} failed: {error}"),
        OutputFormat::Json => {
            let envelope = error_envelope(command, error);
            match serde_json::to_string_pretty(&envelope) {
                Ok(envelope) => println!("{envelope}"),
                Err(_) => println!(
                    r#"{{"ok":false,"command":"newsly-admin","error":{{"code":"serialization_error","message":"could not serialize operator error"}}}}"#
                ),
            }
        }
    }
}

fn success_envelope<T: Serialize>(command: &str, data: &T) -> serde_json::Value {
    serde_json::json!({
        "ok": true,
        "command": command,
        "data": data,
    })
}

fn error_envelope(command: &str, error: &anyhow::Error) -> serde_json::Value {
    serde_json::json!({
        "ok": false,
        "command": command,
        "error": {
            "code": "operator_error",
            "message": error.to_string(),
        },
    })
}

fn resolve_query_window(
    since: Option<&str>,
    until: Option<&str>,
    window_hours: i64,
) -> AnyResult<QueryWindow> {
    let until = until.map_or_else(|| Ok(Utc::now()), |value| parse_time_bound(value, true))?;
    if let Some(since) = since {
        return Ok(QueryWindow::from_bounds(
            parse_time_bound(since, false)?,
            until,
        )?);
    }
    Ok(QueryWindow::ending_at(until, window_hours)?)
}

fn parse_time_bound(value: &str, end_of_day: bool) -> AnyResult<DateTime<Utc>> {
    if let Ok(value) = DateTime::parse_from_rfc3339(value) {
        return Ok(value.with_timezone(&Utc));
    }
    if let Ok(value) = NaiveDateTime::parse_from_str(value, "%Y-%m-%d %H:%M:%S") {
        return Ok(value.and_utc());
    }
    if let Ok(value) = NaiveDate::parse_from_str(value, "%Y-%m-%d") {
        let value = if end_of_day {
            value
                .and_hms_micro_opt(23, 59, 59, 999_999)
                .context("invalid UTC end date")?
        } else {
            value
                .and_hms_opt(0, 0, 0)
                .context("invalid UTC start date")?
        };
        return Ok(value.and_utc());
    }
    bail!("timestamp must be RFC3339, YYYY-MM-DD, or YYYY-MM-DD HH:MM:SS")
}

fn connect_database(cli: &Cli) -> AnyResult<Database> {
    let database_url = cli
        .database_url
        .clone()
        .context("DATABASE_URL is required for this command")?;
    let mut config = DatabaseConfig::new(database_url, "newsly-admin");
    config.max_connections = cli.max_connections;
    Database::connect_lazy(&config).context("invalid database configuration")
}

fn parse_resource(value: &str) -> std::result::Result<ResourceArg, String> {
    ResourceArg::from_str(value).map_err(|error| error.to_string())
}

fn parse_target(value: &str) -> std::result::Result<TargetArg, String> {
    TargetArg::from_str(value).map_err(|error| error.to_string())
}

impl FromStr for ResourceArg {
    type Err = anyhow::Error;

    fn from_str(value: &str) -> std::result::Result<Self, Self::Err> {
        let (kind, key) = value.split_once(',').context("resource must be kind,key")?;
        Ok(Self {
            kind: kind.parse()?,
            key: ResourceKey::new(key)?,
        })
    }
}

impl FromStr for TargetArg {
    type Err = anyhow::Error;

    fn from_str(value: &str) -> std::result::Result<Self, Self::Err> {
        let fields = value.split(',').collect::<Vec<_>>();
        if fields.len() != 5 {
            bail!("target must be kind,key,expected_owner,expected_version,desired_owner");
        }
        let expected_version = fields[3]
            .parse::<i64>()
            .context("target expected_version must be an integer")?;
        Ok(Self(OwnershipTarget::new(
            fields[0].parse()?,
            ResourceKey::new(fields[1])?,
            fields[2].parse()?,
            OwnershipVersion::new(expected_version)?,
            fields[4].parse()?,
        )?))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn output_is_a_true_global_option_and_db_names_match_the_operator_contract() {
        for arguments in [
            vec![
                "newsly-admin",
                "--output",
                "json",
                "db",
                "query",
                "--sql",
                "SELECT 1",
            ],
            vec![
                "newsly-admin",
                "db",
                "query",
                "--output",
                "json",
                "--sql",
                "SELECT 1",
            ],
        ] {
            let cli = Cli::try_parse_from(arguments).expect("global output should parse");
            assert_eq!(cli.output, OutputFormat::Json);
            assert_eq!(cli.command.name(), "db.query");
        }
    }

    #[test]
    fn nested_command_names_match_success_and_error_envelopes() {
        let tasks = Cli::try_parse_from(["newsly-admin", "tasks", "failures"]).unwrap();
        assert_eq!(tasks.command.name(), "tasks.failures");

        let ownership = Cli::try_parse_from([
            "newsly-admin",
            "ownership",
            "show",
            "--resource",
            "task_type,scrape",
        ])
        .unwrap();
        assert_eq!(ownership.command.name(), "ownership.show");

        let transcripts = Cli::try_parse_from([
            "newsly-admin",
            "db",
            "transcript-compatibility",
            "--after-id",
            "100",
            "--limit",
            "500",
        ])
        .unwrap();
        assert_eq!(transcripts.command.name(), "db.transcript-compatibility");

        let backfill = Cli::try_parse_from([
            "newsly-admin",
            "db",
            "transcript-backfill",
            "--after-id",
            "100",
            "--limit",
            "500",
        ])
        .unwrap();
        assert_eq!(backfill.command.name(), "db.transcript-backfill");
    }

    #[test]
    fn json_envelopes_preserve_the_legacy_operator_shape() {
        let success = success_envelope("db.tables", &serde_json::json!({"tables": []}));
        assert_eq!(success["ok"], true);
        assert_eq!(success["command"], "db.tables");
        assert_eq!(success["data"], serde_json::json!({"tables": []}));
        assert!(success.get("error").is_none());

        let error = error_envelope("db.query", &anyhow::anyhow!("invalid operator SQL"));
        assert_eq!(error["ok"], false);
        assert_eq!(error["command"], "db.query");
        assert_eq!(error["error"]["code"], "operator_error");
        assert!(error.get("data").is_none());
    }
}
