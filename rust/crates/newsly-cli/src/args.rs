use std::path::PathBuf;
use std::time::Duration;

use clap::{Args, Parser, Subcommand, ValueEnum};

use crate::output::OutputFormat;

#[derive(Debug, Parser)]
#[command(name = "newsbuddy", about = "Newsbuddy API client")]
pub struct Cli {
    /// Override the CLI config path.
    #[arg(long, global = true)]
    pub config: Option<PathBuf>,
    /// Override the Newsly server URL.
    #[arg(long, global = true)]
    pub server: Option<String>,
    /// Override the API key for this command.
    #[arg(long, global = true, hide_env_values = true)]
    pub api_key: Option<String>,
    /// Output format: json or text.
    #[arg(long, global = true, value_enum, default_value_t)]
    pub output: OutputFormat,
    /// Shortcut for --output json.
    #[arg(long, global = true)]
    pub json: bool,
    /// HTTP timeout.
    #[arg(
        long,
        global = true,
        value_parser = parse_duration,
        default_value = "30s"
    )]
    pub timeout: Duration,
    #[command(subcommand)]
    pub command: Command,
}

#[derive(Debug, Subcommand)]
pub enum Command {
    /// Manage local CLI configuration.
    Config(ConfigArgs),
    /// Authenticate and link the CLI to a Newsbuddy account.
    Auth(AuthArgs),
    /// Inspect async jobs.
    Jobs(JobsArgs),
    /// List, inspect, and submit content.
    Content(ContentArgs),
    /// Sync the personal markdown library to local disk.
    Library(LibraryArgs),
    /// Search provider-backed sources.
    Search(SearchArgs),
    /// Manage runtime feed subscriptions.
    Sources(SourcesArgs),
    /// Run simplified onboarding flows.
    Onboarding(OnboardingArgs),
    /// List, inspect, and convert visible news items.
    News(NewsArgs),
    /// Generate shell completion scripts.
    Completion {
        #[arg(value_enum)]
        shell: CompletionShell,
    },
    /// Print the CLI version.
    Version,
}

#[derive(Debug, Args)]
pub struct ConfigArgs {
    #[command(subcommand)]
    pub command: ConfigCommand,
}

#[derive(Debug, Subcommand)]
pub enum ConfigCommand {
    /// Set one configuration value.
    Set(ConfigSetArgs),
    /// Show the effective CLI configuration.
    Show,
}

#[derive(Debug, Args)]
pub struct ConfigSetArgs {
    #[command(subcommand)]
    pub command: ConfigSetCommand,
}

#[derive(Debug, Subcommand)]
pub enum ConfigSetCommand {
    /// Persist the server URL.
    Server { url: String },
    /// Persist the API key.
    ApiKey { key: String },
    /// Persist the local markdown sync directory.
    LibraryRoot { path: PathBuf },
}

#[derive(Debug, Args)]
pub struct AuthArgs {
    #[command(subcommand)]
    pub command: AuthCommand,
}

#[derive(Debug, Subcommand)]
pub enum AuthCommand {
    /// Start QR login and persist the linked API key.
    Login {
        #[arg(long)]
        device_name: Option<String>,
        #[arg(long, value_parser = parse_duration, default_value = "2s")]
        poll_interval: Duration,
        #[arg(long, value_parser = parse_duration, default_value = "2m")]
        poll_timeout: Duration,
    },
}

#[derive(Debug, Args)]
pub struct JobsArgs {
    #[command(subcommand)]
    pub command: JobsCommand,
}

#[derive(Debug, Subcommand)]
pub enum JobsCommand {
    /// Fetch one async job.
    Get { job_id: i64 },
    /// Poll a job until it reaches a terminal state.
    Wait {
        job_id: i64,
        #[command(flatten)]
        wait: WaitArgs,
    },
}

#[derive(Debug, Args)]
pub struct ContentArgs {
    #[command(subcommand)]
    pub command: ContentCommand,
}

#[derive(Debug, Subcommand)]
pub enum ContentCommand {
    /// List content cards.
    List {
        #[arg(long, default_value_t = 25)]
        limit: usize,
        #[arg(long)]
        cursor: Option<String>,
        #[arg(long, value_delimiter = ',', action = clap::ArgAction::Append)]
        content_type: Vec<String>,
        #[arg(long)]
        date: Option<String>,
        #[arg(long, default_value = "all")]
        read_filter: String,
    },
    /// Fetch one content item.
    Get { content_id: i64 },
    /// Submit a URL for processing.
    Submit(SubmitArgs),
    /// Submit, save to Knowledge, and mark a URL read.
    Summarize(SubmitArgs),
    /// Inspect user-submitted content statuses.
    Submissions(ContentSubmissionsArgs),
}

#[derive(Debug, Args)]
pub struct SubmitArgs {
    pub url: String,
    #[arg(long)]
    pub note: Option<String>,
    #[arg(long)]
    pub crawl_links: bool,
    #[arg(long)]
    pub title: Option<String>,
    #[arg(long)]
    pub platform: Option<String>,
    #[arg(long)]
    pub content_type: Option<String>,
    #[command(flatten)]
    pub wait: OptionalWaitArgs,
}

#[derive(Debug, Args)]
pub struct ContentSubmissionsArgs {
    #[command(subcommand)]
    pub command: ContentSubmissionsCommand,
}

#[derive(Debug, Subcommand)]
pub enum ContentSubmissionsCommand {
    /// List active or failed user submissions.
    List {
        #[arg(long, default_value_t = 25)]
        limit: usize,
        #[arg(long)]
        cursor: Option<String>,
    },
}

#[derive(Debug, Args)]
pub struct LibraryArgs {
    #[command(subcommand)]
    pub command: LibraryCommand,
}

#[derive(Debug, Subcommand)]
pub enum LibraryCommand {
    /// Download the current markdown library diff to local disk.
    Sync {
        #[arg(long)]
        dir: Option<PathBuf>,
        #[arg(
            long,
            default_value_t = true,
            action = clap::ArgAction::Set,
            num_args = 0..=1,
            default_missing_value = "true"
        )]
        include_source: bool,
        #[arg(long)]
        allow_prune_all: bool,
    },
}

#[derive(Debug, Args)]
pub struct SearchArgs {
    pub query: String,
    #[arg(long, default_value_t = 10)]
    pub limit: usize,
    #[arg(
        long,
        default_value_t = true,
        action = clap::ArgAction::Set,
        num_args = 0..=1,
        default_missing_value = "true"
    )]
    pub include_podcasts: bool,
}

#[derive(Debug, Args)]
pub struct SourcesArgs {
    #[command(subcommand)]
    pub command: SourcesCommand,
}

#[derive(Debug, Subcommand)]
pub enum SourcesCommand {
    /// List configured sources.
    List {
        #[arg(long = "type")]
        source_type: Option<String>,
    },
    /// Subscribe to a feed.
    Add {
        feed_url: String,
        #[arg(long)]
        feed_type: String,
        #[arg(long)]
        display_name: Option<String>,
    },
}

#[derive(Debug, Args)]
pub struct OnboardingArgs {
    #[command(subcommand)]
    pub command: OnboardingCommand,
}

#[derive(Debug, Subcommand)]
pub enum OnboardingCommand {
    /// Start onboarding discovery.
    #[command(alias = "run")]
    Start {
        #[arg(long)]
        brief: String,
        #[arg(long, value_delimiter = ',', action = clap::ArgAction::Append)]
        seed_url: Vec<String>,
        #[arg(long, value_delimiter = ',', action = clap::ArgAction::Append)]
        seed_feed: Vec<String>,
        #[command(flatten)]
        wait: OptionalWaitArgs,
    },
    /// Fetch onboarding run status.
    Status { run_id: i64 },
    /// Complete onboarding selections.
    Complete {
        run_id: i64,
        #[arg(long)]
        accept_all: bool,
        #[arg(long, value_delimiter = ',', action = clap::ArgAction::Append)]
        suggestion_id: Vec<i64>,
        #[arg(long, value_delimiter = ',', action = clap::ArgAction::Append)]
        aggregator: Vec<String>,
    },
}

#[derive(Debug, Args)]
pub struct NewsArgs {
    #[command(subcommand)]
    pub command: NewsCommand,
}

#[derive(Debug, Subcommand)]
pub enum NewsCommand {
    /// List visible news items.
    List {
        #[arg(long, default_value_t = 25)]
        limit: usize,
        #[arg(long)]
        cursor: Option<String>,
        #[arg(long, default_value = "unread")]
        read_filter: String,
    },
    /// Fetch one news item.
    Get { news_item_id: i64 },
    /// Convert one news item into an article.
    Convert { news_item_id: i64 },
    /// Mark visible news items as read.
    MarkRead {
        #[arg(required = true, num_args = 1..)]
        news_item_id: Vec<i64>,
    },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, ValueEnum)]
pub enum CompletionShell {
    Bash,
    Zsh,
    Fish,
    Powershell,
}

#[derive(Debug, Clone, Copy, Args)]
pub struct WaitArgs {
    /// Kept for compatibility; `jobs wait` always waits.
    #[arg(long)]
    pub wait: bool,
    #[arg(long, value_parser = parse_duration, default_value = "2s")]
    pub wait_interval: Duration,
    #[arg(long, value_parser = parse_duration, default_value = "2m")]
    pub wait_timeout: Duration,
}

#[derive(Debug, Clone, Copy, Args)]
pub struct OptionalWaitArgs {
    #[arg(long)]
    pub wait: bool,
    #[arg(long, value_parser = parse_duration, default_value = "2s")]
    pub wait_interval: Duration,
    #[arg(long, value_parser = parse_duration, default_value = "2m")]
    pub wait_timeout: Duration,
}

/// Parse the duration syntax accepted by CLI timeout and polling flags.
///
/// # Errors
///
/// Returns an error when the magnitude is invalid, a unit is missing or unsupported, or the
/// duration is outside `std::time::Duration`'s range.
pub fn parse_duration(raw: &str) -> Result<Duration, String> {
    let raw = raw.trim();
    if raw.is_empty() {
        return Err("duration must not be empty".to_owned());
    }
    let mut remaining = raw;
    let mut seconds = 0.0;
    while !remaining.is_empty() {
        let split = remaining
            .find(|character: char| !character.is_ascii_digit() && character != '.')
            .ok_or_else(|| "duration must include a unit such as ms, s, or m".to_owned())?;
        if split == 0 {
            return Err(format!("invalid duration {raw:?}"));
        }
        let magnitude: f64 = remaining[..split]
            .parse()
            .map_err(|_| format!("invalid duration {raw:?}"))?;
        if !magnitude.is_finite() || magnitude < 0.0 {
            return Err("duration must be non-negative and finite".to_owned());
        }

        let unit_and_rest = &remaining[split..];
        let (multiplier, unit_length) = [
            ("ns", 1.0 / 1_000_000_000.0),
            ("us", 1.0 / 1_000_000.0),
            ("µs", 1.0 / 1_000_000.0),
            ("μs", 1.0 / 1_000_000.0),
            ("ms", 1.0 / 1_000.0),
            ("s", 1.0),
            ("m", 60.0),
            ("h", 3_600.0),
        ]
        .into_iter()
        .find_map(|(unit, multiplier)| {
            unit_and_rest
                .starts_with(unit)
                .then_some((multiplier, unit.len()))
        })
        .ok_or_else(|| format!("unsupported duration unit {unit_and_rest:?}"))?;
        seconds += magnitude * multiplier;
        remaining = &unit_and_rest[unit_length..];
    }

    Duration::try_from_secs_f64(seconds).map_err(|_| format!("duration {raw:?} is out of range"))
}

#[cfg(test)]
mod tests {
    use clap::Parser as _;

    use super::*;

    #[test]
    fn parses_compatible_duration_units() {
        assert_eq!(parse_duration("2m").unwrap(), Duration::from_secs(120));
        assert_eq!(parse_duration("250ms").unwrap(), Duration::from_millis(250));
        assert_eq!(
            parse_duration("1.5s").unwrap(),
            Duration::from_millis(1_500)
        );
        assert_eq!(
            parse_duration("1h30m250ms").unwrap(),
            Duration::from_millis(5_400_250)
        );
    }

    #[test]
    fn accepts_global_flags_after_subcommands() {
        let cli = Cli::try_parse_from([
            "newsbuddy",
            "content",
            "list",
            "--server",
            "https://example.com",
        ])
        .unwrap();
        assert_eq!(cli.server.as_deref(), Some("https://example.com"));
    }

    #[test]
    fn preserves_onboarding_run_alias() {
        let cli = Cli::try_parse_from(["newsbuddy", "onboarding", "run", "--brief", "Rust news"])
            .unwrap();
        assert!(matches!(
            cli.command,
            Command::Onboarding(OnboardingArgs {
                command: OnboardingCommand::Start { .. }
            })
        ));
    }

    #[test]
    fn boolean_defaults_accept_bare_and_explicit_false_flags() {
        let bare =
            Cli::try_parse_from(["newsbuddy", "library", "sync", "--include-source"]).unwrap();
        assert!(matches!(
            bare.command,
            Command::Library(LibraryArgs {
                command: LibraryCommand::Sync {
                    include_source: true,
                    ..
                }
            })
        ));

        let disabled =
            Cli::try_parse_from(["newsbuddy", "search", "rust", "--include-podcasts=false"])
                .unwrap();
        assert!(matches!(
            disabled.command,
            Command::Search(SearchArgs {
                include_podcasts: false,
                ..
            })
        ));
    }
}
