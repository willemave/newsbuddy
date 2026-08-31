use std::io;
use std::path::PathBuf;
use std::process::ExitCode;

use clap::{Parser, Subcommand};
use newsly_vm_bootstrap::{BootstrapError, capabilities, corpus, feed};

#[derive(Debug, Parser)]
#[command(name = "newsly-vm-bootstrap")]
#[command(about = "Credential-free helper for Newsly-managed sandboxes")]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Debug, Subcommand)]
enum Command {
    /// Probe the generic tools supplied by the sandbox template.
    Capabilities,
    /// Install or update the read-only Newsly corpus.
    Corpus {
        #[command(subcommand)]
        command: CorpusCommand,
    },
    /// Perform bounded feed-research operations.
    Feed {
        #[command(subcommand)]
        command: FeedCommand,
    },
}

#[derive(Debug, Subcommand)]
enum CorpusCommand {
    /// Apply one full or delta archive to /data and remove the archive.
    Install { archive: PathBuf },
}

#[derive(Debug, Subcommand)]
enum FeedCommand {
    /// Read one JSON request from stdin and emit ordered JSONL rows to stdout.
    FetchBatch,
}

fn run(cli: Cli) -> newsly_vm_bootstrap::Result<()> {
    match cli.command {
        Command::Capabilities => capabilities::write_capabilities(io::stdout().lock()),
        Command::Corpus {
            command: CorpusCommand::Install { archive },
        } => corpus::install_vm_archive(&archive),
        Command::Feed {
            command: FeedCommand::FetchBatch,
        } => feed::fetch_batch(io::stdin().lock(), io::stdout().lock()),
    }
}

fn main() -> ExitCode {
    match run(Cli::parse()) {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            report_error(&error);
            ExitCode::FAILURE
        }
    }
}

fn report_error(error: &BootstrapError) {
    eprintln!("newsly-vm-bootstrap: {error}");
}
