//! Axum application composition for the Newsly Rust runtime.

#![forbid(unsafe_code)]

mod account_deletion;
mod admin_api_keys;
mod admin_auth;
mod admin_evals;
mod admin_logs;
mod admin_onboarding;
mod admin_pages;
mod admin_shell;
mod agent;
pub(crate) use newsly_worker::aggregator_demand;
mod apple_auth;
mod audio_episode_presentation;
mod audio_episodes;
mod audio_storage;
mod auth;
mod briefing;
mod chat;
mod cli_link;
mod config;
mod content_actions;
mod content_bodies;
mod content_body_storage;
mod content_feeds;
mod content_misc;
mod content_read;
mod content_submission;
mod debug_auth;
mod discussions;
mod error;
mod gateway;
mod health;
mod integrations;
mod jobs;
mod learning_deck_artifacts;
mod learning_deck_tokens;
mod learning_decks;
mod llm_tasks;
mod model_usage;
mod mutations;
mod news_actions;
mod observability;
mod onboarding;
mod onboarding_flow;
mod openai;
mod pagination_cursor;
mod public_pages;
mod refresh;
mod route_manifest;
mod scraper_config_normalization;
mod scraper_configs;
mod share_actions;
mod stats;
mod users;
mod wire_presence;
mod write_support;

use crate::learning_deck_artifacts::LearningDeckArtifactStore;

use std::sync::Arc;
use std::time::Duration;

use axum::Router;
use axum::http::{HeaderMap, HeaderName};
use newsly_db::{Database, OwnershipRepository};
use newsly_providers::{
    BriefingDigGateway, ContentMiscGateway, IntegrationTokenCipher, OnboardingGateway,
    OpenAiTranscriptionGateway, ScrapeGateway, XOAuthGateway,
};
use tower_http::catch_panic::CatchPanicLayer;
use tower_http::request_id::{MakeRequestUuid, PropagateRequestIdLayer, SetRequestIdLayer};
use tower_http::trace::{DefaultMakeSpan, DefaultOnResponse, TraceLayer};
use tracing::Level;

pub use auth::{AuthConfig, AuthConfigError, AuthenticatedUser};
pub use config::{ConfigError, LogFormat, ServerConfig};
pub use gateway::GatewayBuildError;
pub use observability::{ObservabilityError, initialize_observability};

const REQUEST_ID_HEADER: HeaderName = HeaderName::from_static("x-request-id");

#[derive(Debug, Clone)]
pub struct AppState {
    database: Database,
    service_name: Arc<str>,
    readiness_timeout: Duration,
    checkout_timeout: Duration,
    debug_auth_enabled: bool,
    secure_admin_cookie: bool,
    auth: AuthConfig,
    audio_storage: Arc<audio_storage::AudioStorage>,
    content_body_store: Arc<content_body_storage::ContentBodyStore>,
    content_misc: Arc<ContentMiscGateway>,
    briefing_dig: Arc<BriefingDigGateway>,
    scrape: Arc<ScrapeGateway>,
    feed_validator: newsly_providers::FeedValidator,
    onboarding: Arc<OnboardingGateway>,
    transcription: Option<Arc<OpenAiTranscriptionGateway>>,
    integration_token_cipher: Option<IntegrationTokenCipher>,
    learning_deck_artifact_store: Arc<learning_deck_artifacts::LearningDeckArtifactStore>,
    learning_deck_token_signer: Arc<learning_deck_tokens::LearningDeckTokenSigner>,
    x_oauth: Option<XOAuthServices>,
    gateway: gateway::Gateway,
}

#[derive(Debug, Clone)]
struct XOAuthServices {
    gateway: Arc<XOAuthGateway>,
    token_cipher: IntegrationTokenCipher,
}

pub fn build_router(state: AppState) -> Router {
    health::router()
        .merge(account_deletion::router())
        .merge(agent::router())
        .merge(admin_api_keys::router())
        .merge(admin_auth::router())
        .merge(admin_evals::router())
        .merge(admin_logs::router())
        .merge(admin_onboarding::router())
        .merge(admin_pages::router())
        .merge(apple_auth::router())
        .merge(audio_episodes::router())
        .merge(briefing::router())
        .merge(cli_link::router())
        .merge(chat::router())
        .merge(debug_auth::router())
        .merge(discussions::router())
        .merge(integrations::router())
        .merge(jobs::router())
        .merge(learning_decks::router())
        .merge(llm_tasks::router())
        .merge(content_bodies::router())
        .merge(content_feeds::router())
        .merge(content_misc::router())
        .merge(content_read::router())
        .merge(content_actions::router())
        .merge(content_submission::router())
        .merge(mutations::router())
        .merge(news_actions::router())
        .merge(onboarding::router())
        .merge(onboarding_flow::router())
        .merge(openai::router())
        .merge(public_pages::router())
        .merge(refresh::router())
        .merge(scraper_configs::router())
        .merge(share_actions::router())
        .merge(stats::router())
        .merge(users::router())
        .fallback(gateway::not_found_fallback)
        .layer(axum::middleware::from_fn_with_state(
            state.clone(),
            gateway::ownership_gateway,
        ))
        .with_state(state)
        .layer(PropagateRequestIdLayer::new(REQUEST_ID_HEADER.clone()))
        .layer(SetRequestIdLayer::new(
            REQUEST_ID_HEADER.clone(),
            MakeRequestUuid,
        ))
        .layer(
            TraceLayer::new_for_http()
                .make_span_with(
                    DefaultMakeSpan::new()
                        .level(Level::INFO)
                        .include_headers(false),
                )
                .on_response(DefaultOnResponse::new().level(Level::INFO)),
        )
        .layer(CatchPanicLayer::new())
}

pub fn openapi_document() -> utoipa::openapi::OpenApi {
    health::document()
}

/// Binds and runs the API until a termination signal is received.
///
/// # Errors
///
/// Returns an error when database configuration is invalid, the listener cannot bind, or the HTTP
/// server exits with an error.
pub async fn serve(config: ServerConfig) -> anyhow::Result<()> {
    let database = Database::connect_lazy(&config.database)?;
    let transcription = config
        .openai_api_key
        .clone()
        .map(|api_key| {
            OpenAiTranscriptionGateway::new(
                &api_key,
                config.openai_api_base.as_deref(),
                config.openai_transcription_timeout,
            )
            .map(Arc::new)
        })
        .transpose()?;
    let feed_validator = newsly_providers::FeedValidator::new();
    let content_body_store = Arc::new(content_body_storage::ContentBodyStore::from_environment()?);
    let content_misc = Arc::new(ContentMiscGateway::from_env()?);
    let briefing_dig = Arc::new(BriefingDigGateway::from_env()?);
    let scrape = Arc::new(ScrapeGateway::from_env()?);
    let onboarding = Arc::new(OnboardingGateway::from_env()?);
    let audio_storage = Arc::new(audio_storage::AudioStorage::from_environment()?);
    let learning_deck_token_signer = Arc::new(config.auth.learning_deck_token_signer()?);
    let integration_token_cipher = config
        .integration_token_encryption_key
        .as_ref()
        .map(IntegrationTokenCipher::new)
        .transpose()?;
    let x_oauth = match (
        config.x_client_id.clone(),
        config.x_oauth_redirect_uri.clone(),
    ) {
        (Some(client_id), Some(redirect_uri)) => {
            let token_cipher = integration_token_cipher.clone().ok_or_else(|| {
                anyhow::anyhow!("X_TOKEN_ENCRYPTION_KEY is required when X OAuth is configured")
            })?;
            Some(XOAuthServices {
                gateway: Arc::new(XOAuthGateway::new(
                    &client_id,
                    config.x_client_secret.clone(),
                    &redirect_uri,
                    config.x_oauth_authorize_url.clone(),
                    config.x_oauth_token_url.clone(),
                    config.x_api_base_url.clone(),
                )?),
                token_cipher,
            })
        }
        _ => None,
    };
    let application_sha = config.application_sha.clone();
    let gateway = gateway::Gateway::new(
        OwnershipRepository::new(database.pool().clone()),
        config.replica_id,
        config.application_sha,
    )?;
    let state = AppState {
        database: database.clone(),
        service_name: config.service_name.into(),
        readiness_timeout: config.readiness_timeout,
        checkout_timeout: config.checkout_timeout,
        debug_auth_enabled: config.debug,
        secure_admin_cookie: config.environment.requires_secure_cookies(),
        auth: config.auth,
        audio_storage,
        learning_deck_artifact_store: Arc::new(LearningDeckArtifactStore::from_content_store(
            &content_body_store,
        )),
        content_body_store,
        content_misc,
        briefing_dig,
        scrape,
        feed_validator,
        onboarding,
        transcription,
        integration_token_cipher,
        learning_deck_token_signer,
        x_oauth,
        gateway,
    };
    let listener = tokio::net::TcpListener::bind(config.bind_address).await?;
    let transition_monitor = tokio::spawn(state.gateway.clone().monitor_transitions());
    tracing::info!(
        bind_address = %config.bind_address,
        environment = %config.environment,
        version = env!("CARGO_PKG_VERSION"),
        revision = application_sha.as_str(),
        "Newsly Rust API listening"
    );

    let server_result = axum::serve(
        listener,
        build_router(state).into_make_service_with_connect_info::<std::net::SocketAddr>(),
    )
    .with_graceful_shutdown(shutdown_signal())
    .await;
    transition_monitor.abort();
    server_result?;

    tracing::info!("Newsly Rust API stopping");
    database.close().await;
    Ok(())
}

fn request_id_from_headers(headers: &HeaderMap) -> String {
    headers
        .get(&REQUEST_ID_HEADER)
        .and_then(|value| value.to_str().ok())
        .filter(|value| !value.is_empty())
        .map_or_else(|| uuid::Uuid::new_v4().to_string(), str::to_owned)
}

async fn shutdown_signal() {
    let interrupt = async {
        tokio::signal::ctrl_c()
            .await
            .expect("failed to install Ctrl+C handler");
    };

    #[cfg(unix)]
    let terminate = async {
        tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
            .expect("failed to install SIGTERM handler")
            .recv()
            .await;
    };

    #[cfg(not(unix))]
    let terminate = std::future::pending::<()>();

    tokio::select! {
        () = interrupt => {},
        () = terminate => {},
    }
}
