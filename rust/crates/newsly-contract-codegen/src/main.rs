use std::fs;
use std::path::{Path, PathBuf};

use anyhow::{Context, ensure};
use clap::Parser;
use newsly_contract_codegen::generate_clients;

#[derive(Debug, Parser)]
#[command(about = "Generate Newsly Swift contracts from Rust OpenAPI")]
struct Arguments {
    #[arg(long)]
    openapi: PathBuf,
    #[arg(long)]
    policy: PathBuf,
    #[arg(long)]
    app_swift_contracts: Option<PathBuf>,
    #[arg(long)]
    app_swift_models: Option<PathBuf>,
    #[arg(long)]
    share_swift_contracts: Option<PathBuf>,
    #[arg(long)]
    share_swift_models: Option<PathBuf>,
}

fn main() -> anyhow::Result<()> {
    let arguments = Arguments::parse();
    ensure!(
        arguments.app_swift_contracts.is_some()
            || arguments.app_swift_models.is_some()
            || arguments.share_swift_contracts.is_some()
            || arguments.share_swift_models.is_some(),
        "at least one client output path is required"
    );
    let openapi = fs::read_to_string(&arguments.openapi)
        .with_context(|| format!("failed reading {}", arguments.openapi.display()))?;
    let policy = fs::read_to_string(&arguments.policy)
        .with_context(|| format!("failed reading {}", arguments.policy.display()))?;
    let generated = generate_clients(&openapi, &policy)?;
    write_optional(
        arguments.app_swift_contracts.as_deref(),
        &generated.app_swift_contracts,
    )?;
    write_optional(
        arguments.app_swift_models.as_deref(),
        &generated.app_swift_models,
    )?;
    write_optional(
        arguments.share_swift_contracts.as_deref(),
        &generated.share_swift_contracts,
    )?;
    write_optional(
        arguments.share_swift_models.as_deref(),
        &generated.share_swift_models,
    )?;
    Ok(())
}

fn write_optional(path: Option<&Path>, contents: &str) -> anyhow::Result<()> {
    let Some(path) = path else {
        return Ok(());
    };
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .with_context(|| format!("failed creating {}", parent.display()))?;
    }
    fs::write(path, contents).with_context(|| format!("failed writing {}", path.display()))
}
