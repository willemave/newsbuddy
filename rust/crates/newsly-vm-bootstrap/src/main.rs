use std::io;
use std::process::ExitCode;

use clap::{Parser, Subcommand};
use newsly_vm_bootstrap::{BootstrapError, capabilities};

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
}

fn run(cli: &Cli) -> newsly_vm_bootstrap::Result<()> {
    match &cli.command {
        Command::Capabilities => capabilities::write_capabilities(io::stdout().lock()),
    }
}

fn main() -> ExitCode {
    match run(&Cli::parse()) {
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
