use std::env;
use std::fmt::{self, Debug, Formatter};
use std::path::{Component, Path, PathBuf};
use std::time::Duration;

use newsly_db::DatabaseConfig;
use newsly_domain::RelationThresholds;
use newsly_providers::{ModelProvider, ModelSpec, OpenRouterPrivacyPolicy, ProviderCredentials};
use secrecy::SecretString;
use thiserror::Error;
use url::Url;

const DEFAULT_EXTRACTOR_URL: &str = "http://127.0.0.1:8200/";
const DEFAULT_FIRECRAWL_URL: &str = "https://api.firecrawl.dev/v2/scrape";
const DEFAULT_X_TOKEN_URL: &str = "https://api.x.com/2/oauth2/token";
const DEFAULT_X_API_BASE_URL: &str = "https://api.x.com/2";
const DEFAULT_ELEVENLABS_API_BASE_URL: &str = "https://api.elevenlabs.io";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WorkerLogFormat {
    Json,
    Pretty,
}

impl WorkerLogFormat {
    fn parse(value: &str) -> Result<Self, WorkerConfigError> {
        match value.trim().to_ascii_lowercase().as_str() {
            "json" => Ok(Self::Json),
            "pretty" | "text" => Ok(Self::Pretty),
            _ => Err(WorkerConfigError::InvalidValue {
                name: "NEWSLY_RUST_LOG_FORMAT",
                value: value.to_owned(),
                expected: "json or pretty",
            }),
        }
    }
}

#[derive(Clone)]
pub struct ContentWorkerProcessConfig {
    database_url: SecretString,
    pub database: DatabaseConfig,
    pub worker_id: String,
    pub lease_duration: Duration,
    pub max_retries: i32,
    pub log_filter: String,
    pub log_format: WorkerLogFormat,
    pub extractor_url: Url,
    pub extractor_secret: SecretString,
    pub extractor_timeout: Duration,
    pub extractor_max_response_bytes: usize,
    pub firecrawl_url: Url,
    pub firecrawl_api_key: Option<SecretString>,
    pub firecrawl_timeout: Duration,
    pub firecrawl_credit_cost_usd: Option<f64>,
    pub x_api_base_url: Url,
    pub x_app_bearer_token: Option<SecretString>,
    pub content_body_local_root: PathBuf,
    pub content_body_storage_prefix: PathBuf,
}

impl Debug for ContentWorkerProcessConfig {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ContentWorkerProcessConfig")
            .field("database_url", &"[REDACTED]")
            .field("database", &self.database)
            .field("worker_id", &self.worker_id)
            .field("lease_duration", &self.lease_duration)
            .field("max_retries", &self.max_retries)
            .field("log_filter", &self.log_filter)
            .field("log_format", &self.log_format)
            .field("extractor_url", &self.extractor_url)
            .field("extractor_secret", &"[REDACTED]")
            .field("extractor_timeout", &self.extractor_timeout)
            .field(
                "extractor_max_response_bytes",
                &self.extractor_max_response_bytes,
            )
            .field("firecrawl_url", &self.firecrawl_url)
            .field(
                "firecrawl_api_key",
                &self.firecrawl_api_key.as_ref().map(|_| "[REDACTED]"),
            )
            .field("firecrawl_timeout", &self.firecrawl_timeout)
            .field("firecrawl_credit_cost_usd", &self.firecrawl_credit_cost_usd)
            .field("x_api_base_url", &self.x_api_base_url)
            .field(
                "x_app_bearer_token",
                &self.x_app_bearer_token.as_ref().map(|_| "[REDACTED]"),
            )
            .field("content_body_local_root", &self.content_body_local_root)
            .field(
                "content_body_storage_prefix",
                &self.content_body_storage_prefix,
            )
            .finish()
    }
}

impl ContentWorkerProcessConfig {
    /// Load the standalone content worker's fail-closed configuration.
    ///
    /// # Errors
    ///
    /// Returns a field-specific error for missing secrets, invalid bounds, unsupported body
    /// storage, or malformed service URLs.
    #[allow(clippy::too_many_lines)]
    pub fn from_env() -> Result<Self, WorkerConfigError> {
        let database_url = required_secret("DATABASE_URL")?;
        let mut database = DatabaseConfig::new(database_url.clone(), "newsly-content-worker");
        database.max_connections = parse_u32("NEWSLY_RUST_WORKER_DATABASE_MAX_CONNECTIONS", 8)?;
        database.min_connections = parse_u32("NEWSLY_RUST_WORKER_DATABASE_MIN_CONNECTIONS", 0)?;
        database.acquire_timeout = Duration::from_millis(parse_u64(
            "NEWSLY_RUST_WORKER_DATABASE_ACQUIRE_TIMEOUT_MS",
            5_000,
        )?);
        if database.max_connections == 0 || database.min_connections > database.max_connections {
            return Err(WorkerConfigError::InvalidRange(
                "NEWSLY_RUST_WORKER_DATABASE connection bounds",
            ));
        }

        let worker_id = env::var("NEWSLY_RUST_WORKER_ID").unwrap_or_else(|_| {
            let host = env::var("HOSTNAME").unwrap_or_else(|_| "local".to_owned());
            format!("rust-content-{host}-{}", std::process::id())
        });
        if worker_id.trim().is_empty() || worker_id.len() > 100 {
            return Err(WorkerConfigError::InvalidRange("NEWSLY_RUST_WORKER_ID"));
        }

        let lease_duration =
            Duration::from_secs(parse_u64("NEWSLY_RUST_WORKER_LEASE_SECONDS", 300)?);
        if lease_duration.is_zero() {
            return Err(WorkerConfigError::InvalidRange(
                "NEWSLY_RUST_WORKER_LEASE_SECONDS",
            ));
        }
        let max_retries = parse_i32("MAX_TASK_RETRIES", 3)?;
        if max_retries < 0 {
            return Err(WorkerConfigError::InvalidRange("MAX_TASK_RETRIES"));
        }

        let extractor_url = parse_url(
            "NEWSLY_DOCUMENT_EXTRACTOR_URL",
            env_alias("NEWSLY_DOCUMENT_EXTRACTOR_URL", "DOCUMENT_EXTRACTOR_URL")
                .unwrap_or_else(|| DEFAULT_EXTRACTOR_URL.to_owned()),
        )?;
        let extractor_secret = required_secret_alias(
            "NEWSLY_DOCUMENT_EXTRACTOR_SHARED_SECRET",
            "DOCUMENT_EXTRACTOR_SHARED_SECRET",
        )?;
        let extractor_timeout = Duration::from_secs(parse_u64_alias(
            "NEWSLY_DOCUMENT_EXTRACTOR_TIMEOUT_SECONDS",
            "DOCUMENT_EXTRACTOR_TIMEOUT_SECONDS",
            190,
        )?);
        if !(Duration::from_secs(1)..=Duration::from_secs(300)).contains(&extractor_timeout) {
            return Err(WorkerConfigError::InvalidRange(
                "NEWSLY_DOCUMENT_EXTRACTOR_TIMEOUT_SECONDS",
            ));
        }
        let extractor_max_response_bytes = parse_usize_alias(
            "NEWSLY_DOCUMENT_EXTRACTOR_MAX_RESPONSE_BYTES",
            "DOCUMENT_EXTRACTOR_MAX_RESPONSE_BYTES",
            2_500_000,
        )?;
        if !(65_536..=5_000_000).contains(&extractor_max_response_bytes) {
            return Err(WorkerConfigError::InvalidRange(
                "NEWSLY_DOCUMENT_EXTRACTOR_MAX_RESPONSE_BYTES",
            ));
        }

        let firecrawl_url = parse_url(
            "NEWSLY_FIRECRAWL_URL",
            env::var("NEWSLY_FIRECRAWL_URL").unwrap_or_else(|_| DEFAULT_FIRECRAWL_URL.to_owned()),
        )?;
        let firecrawl_api_key = optional_secret("FIRECRAWL_API_KEY")?;
        let firecrawl_timeout = Duration::from_secs(parse_u64("FIRECRAWL_TIMEOUT_SECONDS", 45)?);
        if !(Duration::from_secs(1)..=Duration::from_secs(300)).contains(&firecrawl_timeout) {
            return Err(WorkerConfigError::InvalidRange("FIRECRAWL_TIMEOUT_SECONDS"));
        }
        let firecrawl_credit_cost_usd = parse_optional_f64("FIRECRAWL_CREDIT_COST_USD")?;
        let x_api_base_url = parse_url(
            "X_API_BASE_URL",
            env::var("X_API_BASE_URL").unwrap_or_else(|_| DEFAULT_X_API_BASE_URL.to_owned()),
        )?;
        let x_app_bearer_token =
            optional_trimmed_secret_alias("X_APP_BEARER_TOKEN", "TWITTER_AUTH_TOKEN");

        let storage_provider =
            env::var("CONTENT_BODY_STORAGE_PROVIDER").unwrap_or_else(|_| "local".to_owned());
        if storage_provider != "local" {
            return Err(WorkerConfigError::UnsupportedStorageProvider(
                storage_provider,
            ));
        }
        let content_body_local_root = env::var_os("CONTENT_BODY_LOCAL_ROOT")
            .map_or_else(|| PathBuf::from("./data/content_bodies"), PathBuf::from);
        let content_body_storage_prefix = PathBuf::from(
            env::var("CONTENT_BODY_STORAGE_PREFIX").unwrap_or_else(|_| "content".to_owned()),
        );
        validate_relative_prefix(&content_body_storage_prefix)?;

        Ok(Self {
            database_url,
            database,
            worker_id,
            lease_duration,
            max_retries,
            log_filter: env::var("RUST_LOG")
                .unwrap_or_else(|_| "newsly_worker=info,newsly_queue=info".to_owned()),
            log_format: WorkerLogFormat::parse(
                &env::var("NEWSLY_RUST_LOG_FORMAT").unwrap_or_else(|_| "json".to_owned()),
            )?,
            extractor_url,
            extractor_secret,
            extractor_timeout,
            extractor_max_response_bytes,
            firecrawl_url,
            firecrawl_api_key,
            firecrawl_timeout,
            firecrawl_credit_cost_usd,
            x_api_base_url,
            x_app_bearer_token,
            content_body_local_root,
            content_body_storage_prefix,
        })
    }

    pub const fn database_url(&self) -> &SecretString {
        &self.database_url
    }
}

#[derive(Clone)]
pub struct NewsItemWorkerProcessConfig {
    database_url: SecretString,
    pub database: DatabaseConfig,
    pub worker_id: String,
    pub lease_duration: Duration,
    pub max_retries: i32,
    pub log_filter: String,
    pub log_format: WorkerLogFormat,
    pub extractor_url: Url,
    pub extractor_secret: SecretString,
    pub extractor_timeout: Duration,
    pub extractor_max_response_bytes: usize,
    pub firecrawl_url: Url,
    pub firecrawl_api_key: Option<SecretString>,
    pub firecrawl_timeout: Duration,
    pub firecrawl_credit_cost_usd: Option<f64>,
    pub content_body_local_root: PathBuf,
    pub content_body_storage_prefix: PathBuf,
    pub relation_thresholds: RelationThresholds,
    pub briefing_debounce_seconds: i64,
    pub briefing_batch_minimum: i64,
}

impl Debug for NewsItemWorkerProcessConfig {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("NewsItemWorkerProcessConfig")
            .field("database_url", &"[REDACTED]")
            .field("database", &self.database)
            .field("worker_id", &self.worker_id)
            .field("lease_duration", &self.lease_duration)
            .field("max_retries", &self.max_retries)
            .field("log_filter", &self.log_filter)
            .field("log_format", &self.log_format)
            .field("extractor_url", &self.extractor_url)
            .field("extractor_secret", &"[REDACTED]")
            .field("extractor_timeout", &self.extractor_timeout)
            .field(
                "extractor_max_response_bytes",
                &self.extractor_max_response_bytes,
            )
            .field("firecrawl_url", &self.firecrawl_url)
            .field(
                "firecrawl_api_key",
                &self.firecrawl_api_key.as_ref().map(|_| "[REDACTED]"),
            )
            .field("firecrawl_timeout", &self.firecrawl_timeout)
            .field("firecrawl_credit_cost_usd", &self.firecrawl_credit_cost_usd)
            .field("content_body_local_root", &self.content_body_local_root)
            .field(
                "content_body_storage_prefix",
                &self.content_body_storage_prefix,
            )
            .field("relation_thresholds", &self.relation_thresholds)
            .field("briefing_debounce_seconds", &self.briefing_debounce_seconds)
            .field("briefing_batch_minimum", &self.briefing_batch_minimum)
            .finish()
    }
}

impl NewsItemWorkerProcessConfig {
    /// Loads the fenced news-item worker and rejects Python-only model paths.
    ///
    /// Local `SentenceTransformers` and Qwen reranking remain offline evaluation tools. Production
    /// relation decisions use the hosted `OpenRouter` embedding adapter and the shared Rust policy.
    pub fn from_env() -> Result<Self, WorkerConfigError> {
        let reranker_enabled = parse_bool("NEWS_LIST_RERANKER_ENABLED", false)?;
        let embedding_model = env::var("NEWS_EMBEDDING_MODEL")
            .unwrap_or_else(|_| "openrouter:qwen/qwen3-embedding-8b".to_owned());
        validate_production_news_models(reranker_enabled, &embedding_model)?;

        let content = ContentWorkerProcessConfig::from_env()?;
        let ContentWorkerProcessConfig {
            database_url,
            mut database,
            max_retries,
            log_filter,
            log_format,
            extractor_url,
            extractor_secret,
            extractor_timeout,
            extractor_max_response_bytes,
            firecrawl_url,
            firecrawl_api_key,
            firecrawl_timeout,
            firecrawl_credit_cost_usd,
            content_body_local_root,
            content_body_storage_prefix,
            ..
        } = content;
        "newsly-news-item-worker".clone_into(&mut database.application_name);
        database.max_connections = parse_u32(
            "NEWSLY_NEWS_ITEM_DATABASE_MAX_CONNECTIONS",
            database.max_connections,
        )?;
        database.min_connections = parse_u32(
            "NEWSLY_NEWS_ITEM_DATABASE_MIN_CONNECTIONS",
            database.min_connections,
        )?;
        if database.max_connections == 0 || database.min_connections > database.max_connections {
            return Err(WorkerConfigError::InvalidRange(
                "NEWSLY_NEWS_ITEM_DATABASE connection bounds",
            ));
        }
        let worker_id = env::var("NEWSLY_NEWS_ITEM_WORKER_ID").unwrap_or_else(|_| {
            let host = env::var("HOSTNAME").unwrap_or_else(|_| "local".to_owned());
            format!("rust-news-item-{host}-{}", std::process::id())
        });
        if worker_id.trim().is_empty() || worker_id.len() > 100 {
            return Err(WorkerConfigError::InvalidRange(
                "NEWSLY_NEWS_ITEM_WORKER_ID",
            ));
        }
        let lease_duration = Duration::from_secs(parse_u64("NEWSLY_NEWS_ITEM_LEASE_SECONDS", 300)?);
        if lease_duration.is_zero() {
            return Err(WorkerConfigError::InvalidRange(
                "NEWSLY_NEWS_ITEM_LEASE_SECONDS",
            ));
        }
        let primary = parse_f64("NEWS_LIST_PRIMARY_SIMILARITY_THRESHOLD", 0.85)?;
        let secondary = parse_f64("NEWS_LIST_SECONDARY_SIMILARITY_THRESHOLD", 0.75)?;
        if !primary.is_finite()
            || !secondary.is_finite()
            || !(0.0..=1.0).contains(&primary)
            || !(0.0..=1.0).contains(&secondary)
            || primary < secondary
        {
            return Err(WorkerConfigError::InvalidRange(
                "NEWS_LIST similarity thresholds",
            ));
        }
        let briefing_debounce_seconds = parse_i64("BRIEFING_DEBOUNCE_SECONDS", 900)?;
        if !(0..=86_400).contains(&briefing_debounce_seconds) {
            return Err(WorkerConfigError::InvalidRange("BRIEFING_DEBOUNCE_SECONDS"));
        }
        let briefing_batch_minimum = parse_i64("BRIEFING_WINDOW_MIN", 3)?;
        if !(1..=12).contains(&briefing_batch_minimum) {
            return Err(WorkerConfigError::InvalidRange("BRIEFING_WINDOW_MIN"));
        }

        Ok(Self {
            database_url,
            database,
            worker_id,
            lease_duration,
            max_retries,
            log_filter,
            log_format,
            extractor_url,
            extractor_secret,
            extractor_timeout,
            extractor_max_response_bytes,
            firecrawl_url,
            firecrawl_api_key,
            firecrawl_timeout,
            firecrawl_credit_cost_usd,
            content_body_local_root,
            content_body_storage_prefix,
            relation_thresholds: RelationThresholds { primary, secondary },
            briefing_debounce_seconds,
            briefing_batch_minimum,
        })
    }

    pub const fn database_url(&self) -> &SecretString {
        &self.database_url
    }
}

fn validate_production_news_models(
    reranker_enabled: bool,
    embedding_model: &str,
) -> Result<(), WorkerConfigError> {
    if reranker_enabled {
        return Err(WorkerConfigError::UnsupportedNewsReranker);
    }
    if embedding_model
        .strip_prefix("openrouter:")
        .is_none_or(|model| model.trim().is_empty())
    {
        return Err(WorkerConfigError::UnsupportedNewsEmbedding(
            embedding_model.to_owned(),
        ));
    }
    Ok(())
}

#[derive(Clone)]
pub struct SummarizationWorkerProcessConfig {
    database_url: SecretString,
    pub database: DatabaseConfig,
    pub worker_id: String,
    pub lease_duration: Duration,
    pub max_retries: i32,
    pub log_filter: String,
    pub log_format: WorkerLogFormat,
    pub content_body_local_root: PathBuf,
    pub briefing_debounce_seconds: i64,
    pub briefing_batch_minimum: i64,
}

impl Debug for SummarizationWorkerProcessConfig {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("SummarizationWorkerProcessConfig")
            .field("database_url", &"[REDACTED]")
            .field("database", &self.database)
            .field("worker_id", &self.worker_id)
            .field("lease_duration", &self.lease_duration)
            .field("max_retries", &self.max_retries)
            .field("log_filter", &self.log_filter)
            .field("log_format", &self.log_format)
            .field("content_body_local_root", &self.content_body_local_root)
            .field("briefing_debounce_seconds", &self.briefing_debounce_seconds)
            .field("briefing_batch_minimum", &self.briefing_batch_minimum)
            .finish()
    }
}

impl SummarizationWorkerProcessConfig {
    /// Loads the isolated summarization worker configuration.
    ///
    /// The worker accepts only local immutable body pointers. Provider credentials and model
    /// selection are validated by `SummarizationGateway` before the queue loop starts.
    pub fn from_env() -> Result<Self, WorkerConfigError> {
        let database_url = required_secret("DATABASE_URL")?;
        let mut database = DatabaseConfig::new(database_url.clone(), "newsly-summarization-worker");
        database.max_connections = parse_u32("NEWSLY_SUMMARIZATION_DATABASE_MAX_CONNECTIONS", 8)?;
        database.min_connections = parse_u32("NEWSLY_SUMMARIZATION_DATABASE_MIN_CONNECTIONS", 0)?;
        database.acquire_timeout = Duration::from_millis(parse_u64(
            "NEWSLY_SUMMARIZATION_DATABASE_ACQUIRE_TIMEOUT_MS",
            5_000,
        )?);
        if database.max_connections == 0 || database.min_connections > database.max_connections {
            return Err(WorkerConfigError::InvalidRange(
                "NEWSLY_SUMMARIZATION_DATABASE connection bounds",
            ));
        }

        let worker_id = env::var("NEWSLY_SUMMARIZATION_WORKER_ID").unwrap_or_else(|_| {
            let host = env::var("HOSTNAME").unwrap_or_else(|_| "local".to_owned());
            format!("rust-summarization-{host}-{}", std::process::id())
        });
        if worker_id.trim().is_empty() || worker_id.len() > 100 {
            return Err(WorkerConfigError::InvalidRange(
                "NEWSLY_SUMMARIZATION_WORKER_ID",
            ));
        }
        let lease_duration =
            Duration::from_secs(parse_u64("NEWSLY_SUMMARIZATION_LEASE_SECONDS", 300)?);
        if lease_duration.is_zero() {
            return Err(WorkerConfigError::InvalidRange(
                "NEWSLY_SUMMARIZATION_LEASE_SECONDS",
            ));
        }
        let max_retries = parse_i32("MAX_TASK_RETRIES", 3)?;
        if max_retries < 0 {
            return Err(WorkerConfigError::InvalidRange("MAX_TASK_RETRIES"));
        }

        let storage_provider =
            env::var("CONTENT_BODY_STORAGE_PROVIDER").unwrap_or_else(|_| "local".to_owned());
        if storage_provider != "local" {
            return Err(WorkerConfigError::UnsupportedStorageProvider(
                storage_provider,
            ));
        }
        let briefing_debounce_seconds = parse_i64("BRIEFING_DEBOUNCE_SECONDS", 900)?;
        if !(0..=86_400).contains(&briefing_debounce_seconds) {
            return Err(WorkerConfigError::InvalidRange("BRIEFING_DEBOUNCE_SECONDS"));
        }
        let briefing_batch_minimum = parse_i64("BRIEFING_WINDOW_MIN", 3)?;
        if !(1..=12).contains(&briefing_batch_minimum) {
            return Err(WorkerConfigError::InvalidRange("BRIEFING_WINDOW_MIN"));
        }

        Ok(Self {
            database_url,
            database,
            worker_id,
            lease_duration,
            max_retries,
            log_filter: env::var("RUST_LOG").unwrap_or_else(|_| {
                "newsly_worker=info,newsly_queue=info,newsly_providers=info".to_owned()
            }),
            log_format: WorkerLogFormat::parse(
                &env::var("NEWSLY_RUST_LOG_FORMAT").unwrap_or_else(|_| "json".to_owned()),
            )?,
            content_body_local_root: env::var_os("CONTENT_BODY_LOCAL_ROOT")
                .map_or_else(|| PathBuf::from("./data/content_bodies"), PathBuf::from),
            briefing_debounce_seconds,
            briefing_batch_minimum,
        })
    }

    pub const fn database_url(&self) -> &SecretString {
        &self.database_url
    }
}

#[derive(Clone)]
pub struct DiscussionWorkerProcessConfig {
    database_url: SecretString,
    pub database: DatabaseConfig,
    pub worker_id: String,
    pub lease_duration: Duration,
    pub max_retries: i32,
    pub log_filter: String,
    pub log_format: WorkerLogFormat,
    pub content_body_local_root: PathBuf,
    pub content_body_storage_prefix: PathBuf,
}

impl Debug for DiscussionWorkerProcessConfig {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("DiscussionWorkerProcessConfig")
            .field("database_url", &"[REDACTED]")
            .field("database", &self.database)
            .field("worker_id", &self.worker_id)
            .field("lease_duration", &self.lease_duration)
            .field("max_retries", &self.max_retries)
            .field("log_filter", &self.log_filter)
            .field("log_format", &self.log_format)
            .field("content_body_local_root", &self.content_body_local_root)
            .field(
                "content_body_storage_prefix",
                &self.content_body_storage_prefix,
            )
            .finish()
    }
}

impl DiscussionWorkerProcessConfig {
    /// Loads the isolated Hacker News and Reddit discussion worker configuration.
    ///
    /// The worker deliberately supports only immutable local object storage. Provider
    /// credentials and the typed summary model are validated by `ContentMiscGateway`.
    pub fn from_env() -> Result<Self, WorkerConfigError> {
        let database_url = required_secret("DATABASE_URL")?;
        let mut database = DatabaseConfig::new(database_url.clone(), "newsly-discussion-worker");
        database.max_connections = parse_u32("NEWSLY_DISCUSSION_DATABASE_MAX_CONNECTIONS", 8)?;
        database.min_connections = parse_u32("NEWSLY_DISCUSSION_DATABASE_MIN_CONNECTIONS", 0)?;
        database.acquire_timeout = Duration::from_millis(parse_u64(
            "NEWSLY_DISCUSSION_DATABASE_ACQUIRE_TIMEOUT_MS",
            5_000,
        )?);
        if database.max_connections == 0 || database.min_connections > database.max_connections {
            return Err(WorkerConfigError::InvalidRange(
                "NEWSLY_DISCUSSION_DATABASE connection bounds",
            ));
        }

        let worker_id = env::var("NEWSLY_DISCUSSION_WORKER_ID").unwrap_or_else(|_| {
            let host = env::var("HOSTNAME").unwrap_or_else(|_| "local".to_owned());
            format!("rust-discussion-{host}-{}", std::process::id())
        });
        if worker_id.trim().is_empty() || worker_id.len() > 100 {
            return Err(WorkerConfigError::InvalidRange(
                "NEWSLY_DISCUSSION_WORKER_ID",
            ));
        }
        let lease_duration =
            Duration::from_secs(parse_u64("NEWSLY_DISCUSSION_LEASE_SECONDS", 300)?);
        if lease_duration.is_zero() {
            return Err(WorkerConfigError::InvalidRange(
                "NEWSLY_DISCUSSION_LEASE_SECONDS",
            ));
        }
        let max_retries = parse_i32("MAX_TASK_RETRIES", 3)?;
        if max_retries < 0 {
            return Err(WorkerConfigError::InvalidRange("MAX_TASK_RETRIES"));
        }

        let storage_provider =
            env::var("CONTENT_BODY_STORAGE_PROVIDER").unwrap_or_else(|_| "local".to_owned());
        if storage_provider != "local" {
            return Err(WorkerConfigError::UnsupportedStorageProvider(
                storage_provider,
            ));
        }
        let content_body_storage_prefix = PathBuf::from(
            env::var("CONTENT_BODY_STORAGE_PREFIX").unwrap_or_else(|_| "content".to_owned()),
        );
        validate_relative_prefix(&content_body_storage_prefix)?;

        Ok(Self {
            database_url,
            database,
            worker_id,
            lease_duration,
            max_retries,
            log_filter: env::var("RUST_LOG").unwrap_or_else(|_| {
                "newsly_worker=info,newsly_queue=info,newsly_providers=info".to_owned()
            }),
            log_format: WorkerLogFormat::parse(
                &env::var("NEWSLY_RUST_LOG_FORMAT").unwrap_or_else(|_| "json".to_owned()),
            )?,
            content_body_local_root: env::var_os("CONTENT_BODY_LOCAL_ROOT")
                .map_or_else(|| PathBuf::from("./data/content_bodies"), PathBuf::from),
            content_body_storage_prefix,
        })
    }

    pub const fn database_url(&self) -> &SecretString {
        &self.database_url
    }
}

#[derive(Clone)]
pub struct ImageWorkerProcessConfig {
    database_url: SecretString,
    pub database: DatabaseConfig,
    pub worker_id: String,
    pub lease_duration: Duration,
    pub max_retries: i32,
    pub log_filter: String,
    pub log_format: WorkerLogFormat,
    pub images_base_dir: PathBuf,
}

impl Debug for ImageWorkerProcessConfig {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ImageWorkerProcessConfig")
            .field("database_url", &"[REDACTED]")
            .field("database", &self.database)
            .field("worker_id", &self.worker_id)
            .field("lease_duration", &self.lease_duration)
            .field("max_retries", &self.max_retries)
            .field("log_filter", &self.log_filter)
            .field("log_format", &self.log_format)
            .field("images_base_dir", &self.images_base_dir)
            .finish()
    }
}

impl ImageWorkerProcessConfig {
    /// Loads queue, `PostgreSQL`, and local storage settings for the isolated image partition.
    /// Provider credentials and model fallback policy are validated separately by the provider
    /// gateway before the queue loop starts.
    pub fn from_env() -> Result<Self, WorkerConfigError> {
        let database_url = required_secret("DATABASE_URL")?;
        let mut database = DatabaseConfig::new(database_url.clone(), "newsly-image-worker");
        database.max_connections = parse_u32("NEWSLY_IMAGE_DATABASE_MAX_CONNECTIONS", 8)?;
        database.min_connections = parse_u32("NEWSLY_IMAGE_DATABASE_MIN_CONNECTIONS", 0)?;
        database.acquire_timeout = Duration::from_millis(parse_u64(
            "NEWSLY_IMAGE_DATABASE_ACQUIRE_TIMEOUT_MS",
            5_000,
        )?);
        if database.max_connections == 0 || database.min_connections > database.max_connections {
            return Err(WorkerConfigError::InvalidRange(
                "NEWSLY_IMAGE_DATABASE connection bounds",
            ));
        }
        let worker_id = env::var("NEWSLY_IMAGE_WORKER_ID").unwrap_or_else(|_| {
            let host = env::var("HOSTNAME").unwrap_or_else(|_| "local".to_owned());
            format!("rust-image-{host}-{}", std::process::id())
        });
        if worker_id.trim().is_empty() || worker_id.len() > 100 {
            return Err(WorkerConfigError::InvalidRange("NEWSLY_IMAGE_WORKER_ID"));
        }
        let lease_duration =
            Duration::from_secs(parse_u64("NEWSLY_IMAGE_WORKER_LEASE_SECONDS", 300)?);
        if lease_duration.is_zero() {
            return Err(WorkerConfigError::InvalidRange(
                "NEWSLY_IMAGE_WORKER_LEASE_SECONDS",
            ));
        }
        let max_retries = parse_i32("MAX_TASK_RETRIES", 3)?;
        if max_retries < 0 {
            return Err(WorkerConfigError::InvalidRange("MAX_TASK_RETRIES"));
        }
        let images_base_dir = env::var_os("IMAGES_BASE_DIR")
            .map_or_else(|| PathBuf::from("./data/images"), PathBuf::from);
        Ok(Self {
            database_url,
            database,
            worker_id,
            lease_duration,
            max_retries,
            log_filter: env::var("RUST_LOG").unwrap_or_else(|_| {
                "newsly_worker=info,newsly_queue=info,newsly_providers=info".to_owned()
            }),
            log_format: WorkerLogFormat::parse(
                &env::var("NEWSLY_RUST_LOG_FORMAT").unwrap_or_else(|_| "json".to_owned()),
            )?,
            images_base_dir,
        })
    }

    pub const fn database_url(&self) -> &SecretString {
        &self.database_url
    }
}

#[derive(Clone)]
pub struct AudioEpisodeWorkerProcessConfig {
    database_url: SecretString,
    pub database: DatabaseConfig,
    pub worker_id: String,
    pub lease_duration: Duration,
    pub max_retries: i32,
    pub log_filter: String,
    pub log_format: WorkerLogFormat,
    pub provider_credentials: ProviderCredentials,
    pub openrouter_policy: OpenRouterPrivacyPolicy,
    pub script_model: String,
    pub script_timeout: Duration,
    pub elevenlabs_api_base: Url,
    pub elevenlabs_api_key: SecretString,
    pub host_voice_id: String,
    pub guest_voice_id: String,
    pub tts_model: String,
    pub output_format: String,
    pub voice_speed: f32,
    pub max_parallel_tts_requests: usize,
    pub max_tts_response_bytes: usize,
    pub ffmpeg_binary: PathBuf,
    pub media_root: PathBuf,
}

impl Debug for AudioEpisodeWorkerProcessConfig {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("AudioEpisodeWorkerProcessConfig")
            .field("database_url", &"[REDACTED]")
            .field("database", &self.database)
            .field("worker_id", &self.worker_id)
            .field("lease_duration", &self.lease_duration)
            .field("max_retries", &self.max_retries)
            .field("log_filter", &self.log_filter)
            .field("log_format", &self.log_format)
            .field("provider_credentials", &self.provider_credentials)
            .field("openrouter_policy", &self.openrouter_policy)
            .field("script_model", &self.script_model)
            .field("script_timeout", &self.script_timeout)
            .field("elevenlabs_api_base", &self.elevenlabs_api_base)
            .field("elevenlabs_api_key", &"[REDACTED]")
            .field("host_voice_id", &"[REDACTED]")
            .field("guest_voice_id", &"[REDACTED]")
            .field("tts_model", &self.tts_model)
            .field("output_format", &self.output_format)
            .field("voice_speed", &self.voice_speed)
            .field("max_parallel_tts_requests", &self.max_parallel_tts_requests)
            .field("max_tts_response_bytes", &self.max_tts_response_bytes)
            .field("ffmpeg_binary", &self.ffmpeg_binary)
            .field("media_root", &self.media_root)
            .finish()
    }
}

impl AudioEpisodeWorkerProcessConfig {
    /// Loads fail-closed provider, storage, queue, and `PostgreSQL` settings for the isolated audio
    /// partition worker.
    pub fn from_env() -> Result<Self, WorkerConfigError> {
        let database_url = required_secret("DATABASE_URL")?;
        let mut database = DatabaseConfig::new(database_url.clone(), "newsly-audio-worker");
        database.max_connections = parse_u32("NEWSLY_AUDIO_DATABASE_MAX_CONNECTIONS", 8)?;
        database.min_connections = parse_u32("NEWSLY_AUDIO_DATABASE_MIN_CONNECTIONS", 0)?;
        database.acquire_timeout = Duration::from_millis(parse_u64(
            "NEWSLY_AUDIO_DATABASE_ACQUIRE_TIMEOUT_MS",
            5_000,
        )?);
        if database.max_connections == 0 || database.min_connections > database.max_connections {
            return Err(WorkerConfigError::InvalidRange(
                "NEWSLY_AUDIO_DATABASE connection bounds",
            ));
        }
        let worker_id = env::var("NEWSLY_AUDIO_WORKER_ID").unwrap_or_else(|_| {
            let host = env::var("HOSTNAME").unwrap_or_else(|_| "local".to_owned());
            format!("rust-audio-{host}-{}", std::process::id())
        });
        if worker_id.trim().is_empty() || worker_id.len() > 100 {
            return Err(WorkerConfigError::InvalidRange("NEWSLY_AUDIO_WORKER_ID"));
        }
        let lease_duration =
            Duration::from_secs(parse_u64("NEWSLY_AUDIO_WORKER_LEASE_SECONDS", 300)?);
        if lease_duration.is_zero() {
            return Err(WorkerConfigError::InvalidRange(
                "NEWSLY_AUDIO_WORKER_LEASE_SECONDS",
            ));
        }
        let max_retries = parse_i32("MAX_TASK_RETRIES", 3)?;
        if max_retries < 0 {
            return Err(WorkerConfigError::InvalidRange("MAX_TASK_RETRIES"));
        }

        let script_model =
            env::var("AUDIO_EPISODE_MODEL").unwrap_or_else(|_| "openai:gpt-5.6-luna".to_owned());
        let parsed_model =
            ModelSpec::parse(&script_model).map_err(|_| WorkerConfigError::InvalidValue {
                name: "AUDIO_EPISODE_MODEL",
                value: script_model.clone(),
                expected: "a supported provider:model specification",
            })?;
        let provider_credentials = ProviderCredentials {
            openai: optional_trimmed_secret("OPENAI_API_KEY"),
            anthropic: optional_trimmed_secret("ANTHROPIC_API_KEY"),
            google: optional_trimmed_secret("GOOGLE_API_KEY")
                .or_else(|| optional_trimmed_secret("GEMINI_API_KEY")),
            openrouter: optional_trimmed_secret("OPENROUTER_API_KEY"),
        };
        if provider_credentials
            .key_for(parsed_model.provider)
            .is_none()
        {
            return Err(WorkerConfigError::Missing(match parsed_model.provider {
                ModelProvider::OpenAi => "OPENAI_API_KEY",
                ModelProvider::Anthropic => "ANTHROPIC_API_KEY",
                ModelProvider::Google => "GOOGLE_API_KEY",
                ModelProvider::OpenRouter => "OPENROUTER_API_KEY",
            }));
        }
        let script_timeout =
            Duration::from_secs(parse_u64("AUDIO_EPISODE_SCRIPT_TIMEOUT_SECONDS", 180)?);
        if !(Duration::from_secs(1)..=Duration::from_secs(300)).contains(&script_timeout) {
            return Err(WorkerConfigError::InvalidRange(
                "AUDIO_EPISODE_SCRIPT_TIMEOUT_SECONDS",
            ));
        }

        let host_voice_id = env::var("ELEVENLABS_PODCAST_HOST_VOICE_ID")
            .ok()
            .filter(|value| !value.trim().is_empty())
            .or_else(|| {
                env::var("ELEVENLABS_TTS_VOICE_ID")
                    .ok()
                    .filter(|value| !value.trim().is_empty())
            })
            .unwrap_or_else(|| "JBFqnCBsd6RMkjVDRZzb".to_owned());
        let guest_voice_id = env::var("ELEVENLABS_PODCAST_GUEST_VOICE_ID")
            .ok()
            .filter(|value| !value.trim().is_empty())
            .unwrap_or_else(|| host_voice_id.clone());
        let tts_model = env::var("ELEVENLABS_NARRATION_TTS_MODEL")
            .unwrap_or_else(|_| "eleven_flash_v2_5".to_owned());
        let output_format = env::var("ELEVENLABS_NARRATION_TTS_OUTPUT_FORMAT")
            .unwrap_or_else(|_| "mp3_44100_128".to_owned());
        let voice_speed = parse_f32("ELEVENLABS_NARRATION_TTS_SPEED", 1.0)?;
        if !(0.7..=1.2).contains(&voice_speed) {
            return Err(WorkerConfigError::InvalidRange(
                "ELEVENLABS_NARRATION_TTS_SPEED",
            ));
        }
        let max_parallel_tts_requests = parse_usize("ELEVENLABS_AUDIO_EPISODE_TTS_MAX_WORKERS", 4)?;
        if !(1..=8).contains(&max_parallel_tts_requests) {
            return Err(WorkerConfigError::InvalidRange(
                "ELEVENLABS_AUDIO_EPISODE_TTS_MAX_WORKERS",
            ));
        }
        let max_tts_response_bytes =
            parse_usize("NEWSLY_AUDIO_TTS_MAX_RESPONSE_BYTES", 20_000_000)?;
        if !(1_024..=100_000_000).contains(&max_tts_response_bytes) {
            return Err(WorkerConfigError::InvalidRange(
                "NEWSLY_AUDIO_TTS_MAX_RESPONSE_BYTES",
            ));
        }
        let configured_media_root = env::var_os("MEDIA_BASE_DIR")
            .map_or_else(|| PathBuf::from("data/media"), PathBuf::from);
        let media_root = if configured_media_root.is_absolute() {
            configured_media_root
        } else {
            env::current_dir()
                .map_err(|_| WorkerConfigError::InvalidPath("MEDIA_BASE_DIR"))?
                .join(configured_media_root)
        };

        Ok(Self {
            database_url,
            database,
            worker_id,
            lease_duration,
            max_retries,
            log_filter: env::var("RUST_LOG").unwrap_or_else(|_| {
                "newsly_worker=info,newsly_queue=info,newsly_providers=info".to_owned()
            }),
            log_format: WorkerLogFormat::parse(
                &env::var("NEWSLY_RUST_LOG_FORMAT").unwrap_or_else(|_| "json".to_owned()),
            )?,
            provider_credentials,
            openrouter_policy: OpenRouterPrivacyPolicy::default(),
            script_model,
            script_timeout,
            elevenlabs_api_base: parse_url(
                "ELEVENLABS_API_BASE_URL",
                env::var("ELEVENLABS_API_BASE_URL")
                    .unwrap_or_else(|_| DEFAULT_ELEVENLABS_API_BASE_URL.to_owned()),
            )?,
            elevenlabs_api_key: required_secret_alias("ELEVENLABS_API_KEY", "ELEVENLABS")?,
            host_voice_id,
            guest_voice_id,
            tts_model,
            output_format,
            voice_speed,
            max_parallel_tts_requests,
            max_tts_response_bytes,
            ffmpeg_binary: env::var_os("FFMPEG_BINARY")
                .map_or_else(|| PathBuf::from("ffmpeg"), PathBuf::from),
            media_root,
        })
    }

    pub const fn database_url(&self) -> &SecretString {
        &self.database_url
    }
}

#[derive(Clone)]
pub struct XSyncWorkerProcessConfig {
    database_url: SecretString,
    pub database: DatabaseConfig,
    pub worker_id: String,
    pub lease_duration: Duration,
    pub max_retries: i32,
    pub log_filter: String,
    pub log_format: WorkerLogFormat,
    pub sync_enabled: bool,
    pub client_id: Option<SecretString>,
    pub client_secret: Option<SecretString>,
    pub token_encryption_key: Option<SecretString>,
    pub token_url: Url,
    pub api_base_url: Url,
    pub sync_min_interval_minutes: i64,
    pub bookmark_min_interval_minutes: i64,
    pub posts_read_cost_usd: Option<f64>,
    pub users_read_cost_usd: Option<f64>,
}

impl Debug for XSyncWorkerProcessConfig {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("XSyncWorkerProcessConfig")
            .field("database_url", &"[REDACTED]")
            .field("database", &self.database)
            .field("worker_id", &self.worker_id)
            .field("lease_duration", &self.lease_duration)
            .field("max_retries", &self.max_retries)
            .field("log_filter", &self.log_filter)
            .field("log_format", &self.log_format)
            .field("sync_enabled", &self.sync_enabled)
            .field("client_id", &self.client_id.as_ref().map(|_| "[REDACTED]"))
            .field(
                "client_secret",
                &self.client_secret.as_ref().map(|_| "[REDACTED]"),
            )
            .field(
                "token_encryption_key",
                &self.token_encryption_key.as_ref().map(|_| "[REDACTED]"),
            )
            .field("token_url", &self.token_url)
            .field("api_base_url", &self.api_base_url)
            .field("sync_min_interval_minutes", &self.sync_min_interval_minutes)
            .field(
                "bookmark_min_interval_minutes",
                &self.bookmark_min_interval_minutes,
            )
            .field("posts_read_cost_usd", &self.posts_read_cost_usd)
            .field("users_read_cost_usd", &self.users_read_cost_usd)
            .finish()
    }
}

impl XSyncWorkerProcessConfig {
    /// Loads the isolated X synchronization worker configuration.
    ///
    /// X provider credentials are optional while synchronization is disabled, allowing the worker
    /// image to start safely in development. Enabling synchronization makes client identity and
    /// token encryption mandatory.
    ///
    /// # Errors
    ///
    /// Returns a field-specific error for missing enabled-provider secrets, malformed URLs or
    /// numbers, and invalid database, lease, interval, or usage-cost bounds.
    pub fn from_env() -> Result<Self, WorkerConfigError> {
        let database_url = required_secret("DATABASE_URL")?;
        let mut database = DatabaseConfig::new(database_url.clone(), "newsly-x-sync-worker");
        database.max_connections = parse_u32("NEWSLY_X_SYNC_DATABASE_MAX_CONNECTIONS", 8)?;
        database.min_connections = parse_u32("NEWSLY_X_SYNC_DATABASE_MIN_CONNECTIONS", 0)?;
        database.acquire_timeout = Duration::from_millis(parse_u64(
            "NEWSLY_X_SYNC_DATABASE_ACQUIRE_TIMEOUT_MS",
            5_000,
        )?);
        if database.max_connections == 0 || database.min_connections > database.max_connections {
            return Err(WorkerConfigError::InvalidRange(
                "NEWSLY_X_SYNC_DATABASE connection bounds",
            ));
        }

        let worker_id = env::var("NEWSLY_X_SYNC_WORKER_ID").unwrap_or_else(|_| {
            let host = env::var("HOSTNAME").unwrap_or_else(|_| "local".to_owned());
            format!("rust-x-sync-{host}-{}", std::process::id())
        });
        if worker_id.trim().is_empty() || worker_id.len() > 100 {
            return Err(WorkerConfigError::InvalidRange("NEWSLY_X_SYNC_WORKER_ID"));
        }
        let lease_duration = Duration::from_secs(parse_u64("NEWSLY_X_SYNC_LEASE_SECONDS", 300)?);
        if lease_duration.is_zero() {
            return Err(WorkerConfigError::InvalidRange(
                "NEWSLY_X_SYNC_LEASE_SECONDS",
            ));
        }
        let max_retries = parse_i32("MAX_TASK_RETRIES", 3)?;
        if max_retries < 0 {
            return Err(WorkerConfigError::InvalidRange("MAX_TASK_RETRIES"));
        }

        let sync_enabled = parse_bool("X_BOOKMARK_SYNC_ENABLED", false)?;
        let client_id = optional_trimmed_secret("X_CLIENT_ID");
        let client_secret = optional_trimmed_secret("X_CLIENT_SECRET");
        let token_encryption_key = optional_trimmed_secret("X_TOKEN_ENCRYPTION_KEY");
        if sync_enabled && client_id.is_none() {
            return Err(WorkerConfigError::Missing("X_CLIENT_ID"));
        }
        if sync_enabled && token_encryption_key.is_none() {
            return Err(WorkerConfigError::Missing("X_TOKEN_ENCRYPTION_KEY"));
        }
        let sync_min_interval_minutes = parse_i64("X_SYNC_MIN_INTERVAL_MINUTES", 60)?;
        let bookmark_min_interval_minutes = parse_i64("X_BOOKMARK_SYNC_MIN_INTERVAL_MINUTES", 60)?;
        if sync_min_interval_minutes < 1 || bookmark_min_interval_minutes < 1 {
            return Err(WorkerConfigError::InvalidRange(
                "X synchronization interval minutes",
            ));
        }

        Ok(Self {
            database_url,
            database,
            worker_id,
            lease_duration,
            max_retries,
            log_filter: env::var("RUST_LOG").unwrap_or_else(|_| {
                "newsly_worker=info,newsly_queue=info,newsly_providers=info".to_owned()
            }),
            log_format: WorkerLogFormat::parse(
                &env::var("NEWSLY_RUST_LOG_FORMAT").unwrap_or_else(|_| "json".to_owned()),
            )?,
            sync_enabled,
            client_id,
            client_secret,
            token_encryption_key,
            token_url: parse_url(
                "X_OAUTH_TOKEN_URL",
                env::var("X_OAUTH_TOKEN_URL").unwrap_or_else(|_| DEFAULT_X_TOKEN_URL.to_owned()),
            )?,
            api_base_url: parse_url(
                "X_API_BASE_URL",
                env::var("X_API_BASE_URL").unwrap_or_else(|_| DEFAULT_X_API_BASE_URL.to_owned()),
            )?,
            sync_min_interval_minutes,
            bookmark_min_interval_minutes,
            posts_read_cost_usd: parse_optional_f64_with_default("X_POSTS_READ_COST_USD", 0.005)?,
            users_read_cost_usd: parse_optional_f64_with_default("X_USERS_READ_COST_USD", 0.01)?,
        })
    }

    pub const fn database_url(&self) -> &SecretString {
        &self.database_url
    }
}

fn env_alias(primary: &'static str, fallback: &'static str) -> Option<String> {
    env::var(primary).ok().or_else(|| env::var(fallback).ok())
}

fn required_secret(name: &'static str) -> Result<SecretString, WorkerConfigError> {
    let value = env::var(name).map_err(|_| WorkerConfigError::Missing(name))?;
    if value.is_empty() {
        return Err(WorkerConfigError::Empty(name));
    }
    Ok(SecretString::from(value))
}

fn required_secret_alias(
    primary: &'static str,
    fallback: &'static str,
) -> Result<SecretString, WorkerConfigError> {
    let value = env_alias(primary, fallback).ok_or(WorkerConfigError::Missing(primary))?;
    if value.is_empty() {
        return Err(WorkerConfigError::Empty(primary));
    }
    Ok(SecretString::from(value))
}

fn optional_secret(name: &'static str) -> Result<Option<SecretString>, WorkerConfigError> {
    match env::var(name) {
        Ok(value) if value.is_empty() => Err(WorkerConfigError::Empty(name)),
        Ok(value) => Ok(Some(SecretString::from(value))),
        Err(_) => Ok(None),
    }
}

fn optional_trimmed_secret(name: &'static str) -> Option<SecretString> {
    env::var(name)
        .ok()
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty())
        .map(SecretString::from)
}

fn optional_trimmed_secret_alias(
    primary: &'static str,
    fallback: &'static str,
) -> Option<SecretString> {
    env_alias(primary, fallback)
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty())
        .map(SecretString::from)
}

fn parse_url(name: &'static str, value: String) -> Result<Url, WorkerConfigError> {
    value.parse().map_err(|_| WorkerConfigError::InvalidValue {
        name,
        value,
        expected: "an absolute HTTP(S) URL",
    })
}

fn parse_u32(name: &'static str, default: u32) -> Result<u32, WorkerConfigError> {
    parse_number(name, default)
}

fn parse_u64(name: &'static str, default: u64) -> Result<u64, WorkerConfigError> {
    parse_number(name, default)
}

fn parse_i32(name: &'static str, default: i32) -> Result<i32, WorkerConfigError> {
    parse_number(name, default)
}

fn parse_i64(name: &'static str, default: i64) -> Result<i64, WorkerConfigError> {
    parse_number(name, default)
}

fn parse_usize(name: &'static str, default: usize) -> Result<usize, WorkerConfigError> {
    parse_number(name, default)
}

fn parse_f32(name: &'static str, default: f32) -> Result<f32, WorkerConfigError> {
    parse_number(name, default)
}

fn parse_f64(name: &'static str, default: f64) -> Result<f64, WorkerConfigError> {
    parse_number(name, default)
}

fn parse_bool(name: &'static str, default: bool) -> Result<bool, WorkerConfigError> {
    match env::var(name) {
        Ok(value) => match value.trim().to_ascii_lowercase().as_str() {
            "1" | "true" | "yes" | "on" => Ok(true),
            "0" | "false" | "no" | "off" => Ok(false),
            _ => Err(WorkerConfigError::InvalidValue {
                name,
                value,
                expected: "a boolean",
            }),
        },
        Err(_) => Ok(default),
    }
}

fn parse_u64_alias(
    primary: &'static str,
    fallback: &'static str,
    default: u64,
) -> Result<u64, WorkerConfigError> {
    match env_alias(primary, fallback) {
        Some(value) => value
            .parse()
            .map_err(|_| WorkerConfigError::InvalidNumber(primary, value)),
        None => Ok(default),
    }
}

fn parse_usize_alias(
    primary: &'static str,
    fallback: &'static str,
    default: usize,
) -> Result<usize, WorkerConfigError> {
    match env_alias(primary, fallback) {
        Some(value) => value
            .parse()
            .map_err(|_| WorkerConfigError::InvalidNumber(primary, value)),
        None => Ok(default),
    }
}

fn parse_number<T>(name: &'static str, default: T) -> Result<T, WorkerConfigError>
where
    T: std::str::FromStr,
{
    match env::var(name) {
        Ok(value) => value
            .parse()
            .map_err(|_| WorkerConfigError::InvalidNumber(name, value)),
        Err(_) => Ok(default),
    }
}

fn parse_optional_f64(name: &'static str) -> Result<Option<f64>, WorkerConfigError> {
    let Ok(value) = env::var(name) else {
        return Ok(Some(0.000_83));
    };
    let parsed = value
        .parse::<f64>()
        .map_err(|_| WorkerConfigError::InvalidNumber(name, value))?;
    if !parsed.is_finite() || parsed < 0.0 {
        return Err(WorkerConfigError::InvalidRange(name));
    }
    Ok(Some(parsed))
}

fn parse_optional_f64_with_default(
    name: &'static str,
    default: f64,
) -> Result<Option<f64>, WorkerConfigError> {
    let value = match env::var(name) {
        Ok(value) if value.trim().is_empty() => return Ok(None),
        Ok(value) => value,
        Err(_) => return Ok(Some(default)),
    };
    let parsed = value
        .parse::<f64>()
        .map_err(|_| WorkerConfigError::InvalidNumber(name, value))?;
    if !parsed.is_finite() || parsed < 0.0 {
        return Err(WorkerConfigError::InvalidRange(name));
    }
    Ok(Some(parsed))
}

fn validate_relative_prefix(prefix: &Path) -> Result<(), WorkerConfigError> {
    if prefix.as_os_str().is_empty()
        || prefix.is_absolute()
        || prefix
            .components()
            .any(|component| !matches!(component, Component::Normal(_)))
    {
        return Err(WorkerConfigError::InvalidPath(
            "CONTENT_BODY_STORAGE_PREFIX",
        ));
    }
    Ok(())
}

#[derive(Debug, Error)]
pub enum WorkerConfigError {
    #[error("required environment variable {0} is missing")]
    Missing(&'static str),
    #[error("environment variable {0} cannot be empty")]
    Empty(&'static str),
    #[error("{name} has invalid value {value:?}; expected {expected}")]
    InvalidValue {
        name: &'static str,
        value: String,
        expected: &'static str,
    },
    #[error("{0} is not a valid number")]
    InvalidNumber(&'static str, String),
    #[error("{0} is outside its supported range")]
    InvalidRange(&'static str),
    #[error("{0} must be a safe relative path")]
    InvalidPath(&'static str),
    #[error("Rust content workers currently require local content-body storage, got {0:?}")]
    UnsupportedStorageProvider(String),
    #[error(
        "NEWS_LIST_RERANKER_ENABLED is unsupported in production Rust workers; keep local Qwen reranking in the offline Python eval island"
    )]
    UnsupportedNewsReranker,
    #[error(
        "production news embeddings require an openrouter: model (default qwen/qwen3-embedding-8b), got {0:?}"
    )]
    UnsupportedNewsEmbedding(String),
}
