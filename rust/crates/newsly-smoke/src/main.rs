mod api;
mod report;
mod scenarios;

use std::path::PathBuf;
use std::time::Duration;

use anyhow::{Context, Result, bail};
use clap::Parser;
use report::SmokeReport;
use scenarios::SmokeConfig;
use url::Url;

#[derive(Debug, Parser)]
#[command(about = "Run Newsly's live local-staging API smoke scenarios")]
struct Args {
    #[arg(long)]
    base_url: Url,
    #[arg(long)]
    source_url: Url,
    #[arg(long)]
    run_id: String,
    #[arg(long)]
    report_path: PathBuf,
    #[arg(long, default_value_t = 900)]
    scenario_timeout_seconds: u64,
    #[arg(long, default_value_t = 2)]
    poll_interval_seconds: u64,
}

#[tokio::main]
async fn main() -> Result<()> {
    let args = Args::parse();
    validate_loopback(&args.base_url)?;
    if args.run_id.trim().is_empty() {
        bail!("run-id must not be empty");
    }

    let config = SmokeConfig {
        base_url: args.base_url,
        source_url: args.source_url,
        run_id: args.run_id,
        scenario_timeout: Duration::from_secs(args.scenario_timeout_seconds),
        poll_interval: Duration::from_secs(args.poll_interval_seconds),
    };
    let report = scenarios::run(config).await;
    write_report(&args.report_path, &report).await?;
    report.print_summary();
    if report.succeeded() {
        Ok(())
    } else {
        bail!("one or more local-staging smoke scenarios failed")
    }
}

fn validate_loopback(base_url: &Url) -> Result<()> {
    if !matches!(base_url.scheme(), "http" | "https") {
        bail!("base-url must use HTTP or HTTPS");
    }
    let host = base_url
        .host_str()
        .context("base-url must include a host")?;
    if !matches!(host, "127.0.0.1" | "localhost" | "::1") {
        bail!("local-staging smoke refuses non-loopback base-url {host:?}");
    }
    Ok(())
}

async fn write_report(path: &PathBuf, report: &SmokeReport) -> Result<()> {
    if let Some(parent) = path.parent() {
        tokio::fs::create_dir_all(parent)
            .await
            .with_context(|| format!("could not create report directory {}", parent.display()))?;
    }
    let body = serde_json::to_vec_pretty(report).context("could not encode smoke report")?;
    tokio::fs::write(path, body)
        .await
        .with_context(|| format!("could not write smoke report {}", path.display()))
}

#[cfg(test)]
mod tests {
    use super::validate_loopback;
    use url::Url;

    #[test]
    fn local_staging_rejects_external_origins() {
        let external = Url::parse("https://news.example.com").unwrap();
        assert!(validate_loopback(&external).is_err());
        for value in ["http://127.0.0.1:18000", "http://localhost:18000"] {
            assert!(validate_loopback(&Url::parse(value).unwrap()).is_ok());
        }
    }
}
