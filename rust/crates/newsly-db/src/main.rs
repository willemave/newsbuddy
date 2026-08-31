use anyhow::{Context, Result, bail};
use clap::{Parser, Subcommand};
use newsly_db::{
    Database, DatabaseConfig, adopt_existing_database, embedded_migration_count,
    run_migrations_with_barrier, verify_existing_baseline,
};
use secrecy::SecretString;

#[derive(Debug, Parser)]
#[command(name = "newsly-db", about = "Newsly SQLx database operator")]
struct Cli {
    #[arg(long, env = "DATABASE_URL", hide_env_values = true)]
    database_url: Option<SecretString>,

    #[arg(
        long,
        env = "NEWSLY_RUST_DATABASE_MAX_CONNECTIONS",
        default_value_t = 2
    )]
    max_connections: u32,

    #[command(subcommand)]
    command: Command,
}

#[derive(Debug, Subcommand)]
enum Command {
    /// Check that `PostgreSQL` is reachable and accepts a query.
    Check,
    /// Apply all embedded `SQLx` migrations.
    Migrate {
        /// Attest that every application writer has been stopped for an authority migration.
        #[arg(
            long,
            env = "NEWSLY_MAINTENANCE_BARRIER_CONFIRMED",
            default_value_t = false
        )]
        maintenance_barrier_confirmed: bool,
    },
    /// Adopt an existing database at the frozen Alembic head, then apply pending `SQLx` migrations.
    Baseline {
        /// Attest that every application and migration writer has been stopped and drained.
        #[arg(
            long,
            env = "NEWSLY_MAINTENANCE_BARRIER_CONFIRMED",
            default_value_t = false
        )]
        maintenance_barrier_confirmed: bool,
    },
    /// Verify that an existing database is exactly eligible for baseline adoption without writing.
    VerifyBaseline,
    /// Print the number of migrations embedded in this exact binary.
    MigrationCount,
}

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "newsly_db=info".into()),
        )
        .with_target(true)
        .init();

    let cli = Cli::parse();
    if cli.max_connections == 0 {
        bail!("NEWSLY_RUST_DATABASE_MAX_CONNECTIONS must be greater than zero");
    }

    if matches!(cli.command, Command::MigrationCount) {
        println!("{}", embedded_migration_count());
        return Ok(());
    }

    let database_url = cli
        .database_url
        .context("DATABASE_URL is required for this command")?;
    let mut config = DatabaseConfig::new(database_url, "newsly-db");
    config.max_connections = cli.max_connections;
    match cli.command {
        Command::Check => {
            let database =
                Database::connect_lazy(&config).context("invalid database configuration")?;
            database.check().await.context("database check failed")?;
            println!("database is reachable");
            database.close().await;
        }
        Command::Migrate {
            maintenance_barrier_confirmed,
        } => {
            let database =
                Database::connect_lazy(&config).context("invalid database configuration")?;
            run_migrations_with_barrier(database.pool(), maintenance_barrier_confirmed)
                .await
                .context("migration run failed")?;
            println!("embedded migrations applied");
            database.close().await;
        }
        Command::Baseline {
            maintenance_barrier_confirmed,
        } => {
            if !maintenance_barrier_confirmed {
                bail!(
                    "baseline adoption requires --maintenance-barrier-confirmed after every application and migration writer is stopped and drained"
                );
            }
            let report = adopt_existing_database(&config)
                .await
                .context("baseline adoption failed")?;
            println!(
                "SQLx baseline {} adopted; {} pending migrations applied",
                report.baseline_version, report.pending_migrations_applied
            );
        }
        Command::VerifyBaseline => {
            verify_existing_baseline(&config)
                .await
                .context("baseline verification failed")?;
            println!("database exactly matches the frozen Alembic-head baseline");
        }
        Command::MigrationCount => unreachable!("handled before database initialization"),
    }
    Ok(())
}
