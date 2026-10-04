use std::path::PathBuf;

use anyhow::{Context, bail};

#[cfg(test)]
#[path = "../contract_export.rs"]
mod contract_export;

fn main() -> anyhow::Result<()> {
    let options = Options::parse()?;
    let document = serde_json::to_value(newsly_api::openapi_document())
        .context("failed to serialize Newsly Rust OpenAPI")?;
    let serialized = serde_json::to_string_pretty(&document)
        .context("failed to serialize Newsly Rust OpenAPI")?;
    if let Some(output) = options.output {
        if let Some(parent) = output.parent() {
            std::fs::create_dir_all(parent).with_context(|| {
                format!(
                    "failed to create OpenAPI output directory {}",
                    parent.display()
                )
            })?;
        }
        std::fs::write(&output, format!("{serialized}\n"))
            .with_context(|| format!("failed to write OpenAPI output {}", output.display()))?;
    } else {
        println!("{serialized}");
    }
    Ok(())
}

struct Options {
    output: Option<PathBuf>,
}

impl Options {
    fn parse() -> anyhow::Result<Self> {
        let mut output = None;
        let mut arguments = std::env::args_os().skip(1);
        while let Some(argument) = arguments.next() {
            match argument.to_str() {
                Some("--output") => {
                    let path = arguments
                        .next()
                        .context("--output requires a filesystem path")?;
                    output = Some(PathBuf::from(path));
                }
                Some("--help" | "-h") => {
                    println!(
                        "Usage: export_openapi [--output PATH]\n\n\
                         Exports the Rust-owned public OpenAPI document."
                    );
                    std::process::exit(0);
                }
                Some(other) => bail!("unknown export_openapi argument: {other}"),
                None => bail!("export_openapi arguments must be valid UTF-8"),
            }
        }
        Ok(Self { output })
    }
}
