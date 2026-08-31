use std::process::ExitCode;

#[tokio::main]
async fn main() -> ExitCode {
    let mut stdout = std::io::stdout().lock();
    let mut stderr = std::io::stderr().lock();
    let version = option_env!("NEWSBUDDY_VERSION").unwrap_or(env!("CARGO_PKG_VERSION"));
    ExitCode::from(newsly_cli::run(std::env::args_os(), &mut stdout, &mut stderr, version).await)
}
