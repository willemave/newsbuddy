use anyhow::{Context, Result};
use newsly_api::{ServerConfig, initialize_observability, serve};

#[tokio::main]
async fn main() -> Result<()> {
    let config = ServerConfig::from_env().context("invalid Newsly Rust API configuration")?;
    initialize_observability(&config.log_filter, config.log_format)
        .context("observability initialization failed")?;
    serve(config).await.context("Newsly Rust API failed")
}
