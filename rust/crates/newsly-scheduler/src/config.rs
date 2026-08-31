use std::env;
use std::fmt::{self, Debug, Formatter};
use std::time::Duration;

use newsly_db::DatabaseConfig;
use secrecy::SecretString;
use thiserror::Error;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SchedulerLogFormat {
    Json,
    Pretty,
}

pub struct SchedulerConfig {
    pub database: DatabaseConfig,
    pub instance_id: String,
    pub poll_interval: Duration,
    pub x_sync_enabled: bool,
    pub feed_discovery_min_reads: i64,
    pub queue_backpressure_max_pending_content: i64,
    pub queue_backpressure_max_pending_process_news_item: i64,
    pub orphan_lease_grace: Duration,
    pub terminal_retention_days: i64,
    pub terminal_cleanup_batch_size: i64,
    pub terminal_cleanup_max_delete: i64,
    pub watchdog_alert_threshold: i64,
    pub watchdog_slack_webhook_url: Option<SecretString>,
    pub log_filter: String,
    pub log_format: SchedulerLogFormat,
}

impl Debug for SchedulerConfig {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("SchedulerConfig")
            .field("database", &self.database)
            .field("instance_id", &self.instance_id)
            .field("poll_interval", &self.poll_interval)
            .field("x_sync_enabled", &self.x_sync_enabled)
            .field("feed_discovery_min_reads", &self.feed_discovery_min_reads)
            .field(
                "queue_backpressure_max_pending_content",
                &self.queue_backpressure_max_pending_content,
            )
            .field(
                "queue_backpressure_max_pending_process_news_item",
                &self.queue_backpressure_max_pending_process_news_item,
            )
            .field("orphan_lease_grace", &self.orphan_lease_grace)
            .field("terminal_retention_days", &self.terminal_retention_days)
            .field(
                "terminal_cleanup_batch_size",
                &self.terminal_cleanup_batch_size,
            )
            .field(
                "terminal_cleanup_max_delete",
                &self.terminal_cleanup_max_delete,
            )
            .field("watchdog_alert_threshold", &self.watchdog_alert_threshold)
            .field(
                "watchdog_slack_webhook_url",
                &self
                    .watchdog_slack_webhook_url
                    .as_ref()
                    .map(|_| "[REDACTED]"),
            )
            .field("log_filter", &self.log_filter)
            .field("log_format", &self.log_format)
            .finish()
    }
}

impl SchedulerConfig {
    /// Load the scheduler's bounded process configuration from environment variables.
    ///
    /// # Errors
    ///
    /// Returns a field-specific error for missing, malformed, or unsafe values.
    pub fn from_env() -> Result<Self, SchedulerConfigError> {
        let database_url = required_secret("DATABASE_URL")?;
        let mut database = DatabaseConfig::new(database_url, "newsly-scheduler");
        database.max_connections = parse_u32("NEWSLY_SCHEDULER_DATABASE_MAX_CONNECTIONS", 8)?;
        database.min_connections = parse_u32("NEWSLY_SCHEDULER_DATABASE_MIN_CONNECTIONS", 0)?;
        database.acquire_timeout = Duration::from_millis(parse_u64(
            "NEWSLY_SCHEDULER_DATABASE_ACQUIRE_TIMEOUT_MS",
            5_000,
        )?);
        if database.max_connections == 0 || database.min_connections > database.max_connections {
            return Err(SchedulerConfigError::Range(
                "NEWSLY_SCHEDULER_DATABASE connection bounds",
            ));
        }

        let host = env::var("HOSTNAME").unwrap_or_else(|_| "local".to_owned());
        let instance_id = env::var("NEWSLY_SCHEDULER_INSTANCE_ID")
            .unwrap_or_else(|_| format!("rust-scheduler-{host}-{}", std::process::id()));
        if instance_id.trim().is_empty() || instance_id.len() > 255 {
            return Err(SchedulerConfigError::Range("NEWSLY_SCHEDULER_INSTANCE_ID"));
        }

        let poll_interval = Duration::from_secs(parse_u64("NEWSLY_SCHEDULER_POLL_SECONDS", 15)?);
        if !(Duration::from_secs(1)..=Duration::from_secs(60)).contains(&poll_interval) {
            return Err(SchedulerConfigError::Range("NEWSLY_SCHEDULER_POLL_SECONDS"));
        }

        let terminal_cleanup_batch_size = parse_i64("TASK_CLEANUP_BATCH_SIZE", 5_000)?;
        let terminal_cleanup_max_delete = parse_i64("TASK_CLEANUP_MAX_DELETE", 50_000)?;
        if terminal_cleanup_batch_size <= 0
            || terminal_cleanup_max_delete <= 0
            || terminal_cleanup_batch_size > terminal_cleanup_max_delete
            || terminal_cleanup_max_delete > 1_000_000
        {
            return Err(SchedulerConfigError::Range("terminal task cleanup bounds"));
        }

        let config = Self {
            database,
            instance_id,
            poll_interval,
            x_sync_enabled: parse_bool("X_BOOKMARK_SYNC_ENABLED", false)?,
            feed_discovery_min_reads: parse_i64("DISCOVERY_MIN_RECENT_READS", 0)?,
            queue_backpressure_max_pending_content: parse_i64(
                "QUEUE_BACKPRESSURE_MAX_PENDING_CONTENT",
                150,
            )?,
            queue_backpressure_max_pending_process_news_item: parse_i64(
                "QUEUE_BACKPRESSURE_MAX_PENDING_PROCESS_NEWS_ITEM",
                75,
            )?,
            orphan_lease_grace: Duration::from_secs(parse_u64(
                "QUEUE_WATCHDOG_ORPHAN_LEASE_GRACE_SECONDS",
                600,
            )?),
            terminal_retention_days: parse_i64("TASK_RETENTION_DAYS", 14)?,
            terminal_cleanup_batch_size,
            terminal_cleanup_max_delete,
            watchdog_alert_threshold: parse_i64("QUEUE_WATCHDOG_ALERT_THRESHOLD", 1)?,
            watchdog_slack_webhook_url: optional_secret("QUEUE_WATCHDOG_SLACK_WEBHOOK_URL"),
            log_filter: env::var("RUST_LOG")
                .unwrap_or_else(|_| "newsly_scheduler=info,newsly_queue=info".to_owned()),
            log_format: parse_log_format()?,
        };
        config.validate()?;
        Ok(config)
    }

    fn validate(&self) -> Result<(), SchedulerConfigError> {
        if self.feed_discovery_min_reads < 0 {
            return Err(SchedulerConfigError::Range("DISCOVERY_MIN_RECENT_READS"));
        }
        if self.queue_backpressure_max_pending_content < 1 {
            return Err(SchedulerConfigError::Range(
                "QUEUE_BACKPRESSURE_MAX_PENDING_CONTENT",
            ));
        }
        if self.queue_backpressure_max_pending_process_news_item < 1 {
            return Err(SchedulerConfigError::Range(
                "QUEUE_BACKPRESSURE_MAX_PENDING_PROCESS_NEWS_ITEM",
            ));
        }
        if !(Duration::from_secs(30)..=Duration::from_secs(86_400))
            .contains(&self.orphan_lease_grace)
        {
            return Err(SchedulerConfigError::Range(
                "QUEUE_WATCHDOG_ORPHAN_LEASE_GRACE_SECONDS",
            ));
        }
        if !(1..=365).contains(&self.terminal_retention_days) {
            return Err(SchedulerConfigError::Range("TASK_RETENTION_DAYS"));
        }
        if self.watchdog_alert_threshold < 1 {
            return Err(SchedulerConfigError::Range(
                "QUEUE_WATCHDOG_ALERT_THRESHOLD",
            ));
        }
        Ok(())
    }
}

fn required_secret(name: &'static str) -> Result<SecretString, SchedulerConfigError> {
    env::var(name)
        .ok()
        .filter(|value| !value.trim().is_empty())
        .map(SecretString::from)
        .ok_or(SchedulerConfigError::Missing(name))
}

fn optional_secret(name: &'static str) -> Option<SecretString> {
    env::var(name)
        .ok()
        .filter(|value| !value.trim().is_empty())
        .map(SecretString::from)
}

fn parse_u64(name: &'static str, default: u64) -> Result<u64, SchedulerConfigError> {
    env::var(name).map_or(Ok(default), |value| {
        value
            .parse::<u64>()
            .map_err(|_| SchedulerConfigError::Invalid { name, value })
    })
}

fn parse_u32(name: &'static str, default: u32) -> Result<u32, SchedulerConfigError> {
    env::var(name).map_or(Ok(default), |value| {
        value
            .parse::<u32>()
            .map_err(|_| SchedulerConfigError::Invalid { name, value })
    })
}

fn parse_i64(name: &'static str, default: i64) -> Result<i64, SchedulerConfigError> {
    env::var(name).map_or(Ok(default), |value| {
        value
            .parse::<i64>()
            .map_err(|_| SchedulerConfigError::Invalid { name, value })
    })
}

fn parse_bool(name: &'static str, default: bool) -> Result<bool, SchedulerConfigError> {
    env::var(name).map_or(Ok(default), |value| {
        match value.trim().to_ascii_lowercase().as_str() {
            "1" | "true" | "yes" | "on" => Ok(true),
            "0" | "false" | "no" | "off" => Ok(false),
            _ => Err(SchedulerConfigError::Invalid { name, value }),
        }
    })
}

fn parse_log_format() -> Result<SchedulerLogFormat, SchedulerConfigError> {
    let value = env::var("NEWSLY_RUST_LOG_FORMAT").unwrap_or_else(|_| "json".to_owned());
    match value.trim().to_ascii_lowercase().as_str() {
        "json" => Ok(SchedulerLogFormat::Json),
        "pretty" | "text" => Ok(SchedulerLogFormat::Pretty),
        _ => Err(SchedulerConfigError::Invalid {
            name: "NEWSLY_RUST_LOG_FORMAT",
            value,
        }),
    }
}

#[derive(Debug, Error, Clone, PartialEq, Eq)]
pub enum SchedulerConfigError {
    #[error("missing required setting {0}")]
    Missing(&'static str),
    #[error("setting {name} has invalid value {value:?}")]
    Invalid { name: &'static str, value: String },
    #[error("{0} is outside its supported range")]
    Range(&'static str),
}
