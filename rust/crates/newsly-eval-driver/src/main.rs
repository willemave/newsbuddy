use std::fs;
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use clap::{Parser, Subcommand};
use newsly_eval_driver::{
    PrepareRelationsRequest, ScoreRelationsRequest, prepare_relations, score_relations,
};
use serde::Serialize;
use serde::de::DeserializeOwned;

#[derive(Debug, Parser)]
#[command(name = "newsly-eval-driver")]
#[command(about = "Run production Rust policies from offline model-eval pipelines")]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Debug, Subcommand)]
enum Command {
    PrepareRelations {
        #[arg(long, default_value = "-")]
        input: PathBuf,
        #[arg(long, default_value = "-")]
        output: PathBuf,
    },
    ScoreRelations {
        #[arg(long, default_value = "-")]
        input: PathBuf,
        #[arg(long, default_value = "-")]
        output: PathBuf,
    },
}

fn main() -> Result<()> {
    match Cli::parse().command {
        Command::PrepareRelations { input, output } => {
            let request = read_json::<PrepareRelationsRequest>(&input)?;
            write_json(&output, &prepare_relations(&request)?)
        }
        Command::ScoreRelations { input, output } => {
            let request = read_json::<ScoreRelationsRequest>(&input)?;
            write_json(&output, &score_relations(request)?)
        }
    }
}

fn read_json<T: DeserializeOwned>(path: &Path) -> Result<T> {
    let mut bytes = Vec::new();
    if path == Path::new("-") {
        io::stdin()
            .read_to_end(&mut bytes)
            .context("failed to read JSON from stdin")?;
    } else {
        bytes = fs::read(path)
            .with_context(|| format!("failed to read JSON from {}", path.display()))?;
    }
    serde_json::from_slice(&bytes)
        .with_context(|| format!("invalid JSON input from {}", path.display()))
}

fn write_json(path: &Path, value: &impl Serialize) -> Result<()> {
    let bytes = serde_json::to_vec_pretty(value).context("failed to encode JSON output")?;
    if path == Path::new("-") {
        let mut stdout = io::stdout().lock();
        stdout.write_all(&bytes).context("failed to write stdout")?;
        stdout.write_all(b"\n").context("failed to finish stdout")?;
    } else {
        fs::write(path, bytes)
            .with_context(|| format!("failed to write JSON to {}", path.display()))?;
    }
    Ok(())
}
