use std::collections::BTreeMap;
use std::future::Future;
use std::sync::Arc;

use chrono::{Duration, Utc};
use newsly_db::{PrepareXSyncOutcome, PreparedXSync, prepare_x_sync};
use newsly_providers::{
    IntegrationTokenCipher, IntegrationTokenCipherError, XSyncGateway, XSyncGatewayError,
};
use newsly_queue::{OwnedWorkPlan, QueueKernel, TaskResult, TaskType};
use serde_json::Value;
use sqlx::PgPool;
use uuid::Uuid;

use crate::{HandlerExecution, HandlerFuture, LeaseHealth, TaskHandler};

use super::finalizer::XSyncFinalizer;
use super::model::{
    BookmarkFetchOutcome, DurableXCredentials, XRequestUsage, XSyncFinalizationPlan, XSyncMutation,
};

const TOKEN_EXPIRY_SKEW_SECONDS: i64 = 60;
const BOOKMARK_SYNC_MAX_PAGES: usize = 10;
const BOOKMARK_SYNC_PAGE_SIZE: u8 = 5;
const UNRECOVERABLE_REFRESH_MARKERS: [&str; 4] = [
    "X API 400: invalid_request",
    "X API 400: invalid_grant",
    "X API 400: invalid_client",
    "X API 400: unauthorized_client",
];

#[derive(Debug, Clone)]
pub struct XSyncWorkerServices {
    pool: PgPool,
    queue: QueueKernel,
    sync_enabled: bool,
    gateway: Option<XSyncGateway>,
    token_cipher: Option<IntegrationTokenCipher>,
    sync_min_interval_minutes: i64,
    bookmark_min_interval_minutes: i64,
    posts_read_cost_usd: Option<f64>,
    users_read_cost_usd: Option<f64>,
}

impl XSyncWorkerServices {
    #[allow(clippy::too_many_arguments)]
    pub const fn new(
        pool: PgPool,
        queue: QueueKernel,
        sync_enabled: bool,
        gateway: Option<XSyncGateway>,
        token_cipher: Option<IntegrationTokenCipher>,
        sync_min_interval_minutes: i64,
        bookmark_min_interval_minutes: i64,
        posts_read_cost_usd: Option<f64>,
        users_read_cost_usd: Option<f64>,
    ) -> Self {
        Self {
            pool,
            queue,
            sync_enabled,
            gateway,
            token_cipher,
            sync_min_interval_minutes,
            bookmark_min_interval_minutes,
            posts_read_cost_usd,
            users_read_cost_usd,
        }
    }
}

#[derive(Debug, Clone)]
pub struct XSyncIntegrationHandler {
    services: Arc<XSyncWorkerServices>,
}

impl XSyncIntegrationHandler {
    pub fn new(services: Arc<XSyncWorkerServices>) -> Self {
        Self { services }
    }
}

impl TaskHandler for XSyncIntegrationHandler {
    fn task_type(&self) -> TaskType {
        TaskType::SyncIntegration
    }

    fn execute(&self, plan: Arc<OwnedWorkPlan>, lease: LeaseHealth) -> HandlerFuture<'_> {
        let services = Arc::clone(&self.services);
        Box::pin(async move { execute_x_sync(&services, &plan, lease).await })
    }
}

#[allow(clippy::too_many_lines)]
async fn execute_x_sync(
    services: &XSyncWorkerServices,
    plan: &OwnedWorkPlan,
    mut lease: LeaseHealth,
) -> HandlerExecution {
    let Some(user_id) = plan.payload.get("user_id").and_then(Value::as_i64) else {
        return plain_failure("Missing user_id in sync_integration payload", false);
    };
    let provider = payload_string(&plan.payload, "provider")
        .unwrap_or("x")
        .to_ascii_lowercase();
    if provider != "x" {
        return plain_failure(
            format!("Unsupported integration provider: {provider}"),
            false,
        );
    }
    let trigger = payload_string(&plan.payload, "trigger")
        .unwrap_or("cron")
        .to_ascii_lowercase();
    if !services.sync_enabled {
        return HandlerExecution::from_result(TaskResult::ok());
    }
    let (Some(gateway), Some(token_cipher)) = (&services.gateway, &services.token_cipher) else {
        return plain_failure("X sync worker provider configuration is incomplete", false);
    };

    // This is the only database work before provider calls. Commit the owned plan, then drop the
    // transaction so token refresh, identity lookup, and pagination cannot pin PostgreSQL state.
    let prepared = {
        let mut transaction = match services.pool.begin().await {
            Ok(transaction) => transaction,
            Err(error) => return plain_failure(error.to_string(), true),
        };
        let outcome = match prepare_x_sync(
            &mut transaction,
            user_id,
            trigger != "cron",
            Utc::now(),
            services.sync_min_interval_minutes,
            services.bookmark_min_interval_minutes,
        )
        .await
        {
            Ok(outcome) => outcome,
            Err(error) => return plain_failure(error.to_string(), true),
        };
        if let Err(error) = transaction.commit().await {
            return plain_failure(error.to_string(), true);
        }
        match outcome {
            PrepareXSyncOutcome::Prepared(prepared) => *prepared,
            PrepareXSyncOutcome::UserMissing => {
                return plain_failure(format!("User {user_id} not found"), false);
            }
            PrepareXSyncOutcome::NotConnected | PrepareXSyncOutcome::SkippedRecently => {
                return HandlerExecution::from_result(TaskResult::ok());
            }
        }
    };

    let Some(encrypted_access_token) = prepared.access_token_encrypted.as_deref() else {
        return finalized_failure(
            services,
            plan,
            prepared,
            None,
            Vec::new(),
            "Missing stored X access token".to_owned(),
            false,
        );
    };
    let mut credentials = DurableXCredentials {
        provider_user_id: prepared.provider_user_id.clone(),
        provider_username: prepared.provider_username.clone(),
        access_token_encrypted: encrypted_access_token.to_owned(),
        refresh_token_encrypted: prepared.refresh_token_encrypted.clone(),
        token_expires_at: prepared.token_expires_at,
        scopes: prepared.scopes.clone(),
    };
    let mut usage = Vec::new();

    let now = Utc::now();
    let access_token = if prepared
        .token_expires_at
        .is_some_and(|expires_at| expires_at <= now + Duration::seconds(TOKEN_EXPIRY_SKEW_SECONDS))
    {
        let Some(encrypted_refresh_token) = prepared.refresh_token_encrypted.as_deref() else {
            return finalized_failure(
                services,
                plan,
                prepared,
                None,
                usage,
                "X access token expired and no refresh token is available".to_owned(),
                false,
            );
        };
        let refresh_token = match token_cipher.decrypt(encrypted_refresh_token) {
            Ok(refresh_token) => refresh_token,
            Err(error) => {
                return local_crypto_failure(services, plan, prepared, usage, error);
            }
        };
        let refreshed =
            match provider_call(&mut lease, gateway.refresh_oauth_token(&refresh_token)).await {
                Ok(Ok(refreshed)) => refreshed,
                Ok(Err(error)) if unrecoverable_refresh_error(&error) => {
                    let reason = error.to_string();
                    return HandlerExecution::with_finalizer(
                        TaskResult::ok(),
                        XSyncFinalizer::new(
                            services.queue.clone(),
                            XSyncFinalizationPlan {
                                task_id: plan.task_id,
                                prepared,
                                mutation: XSyncMutation::ReauthRequired {
                                    reason,
                                    recorded_at: Utc::now(),
                                },
                            },
                        ),
                    );
                }
                Ok(Err(error)) => {
                    return finalized_failure(
                        services,
                        plan,
                        prepared,
                        None,
                        usage,
                        error.to_string(),
                        true,
                    );
                }
                Err(LeaseLost) => return lease_lost_failure(),
            };
        let access_token_encrypted = match token_cipher.encrypt(&refreshed.access_token) {
            Ok(encrypted) => encrypted,
            Err(error) => {
                return local_crypto_failure(services, plan, prepared, usage, error);
            }
        };
        let refresh_token_encrypted = match refreshed.refresh_token.as_deref() {
            Some(refresh_token) => match token_cipher.encrypt(refresh_token) {
                Ok(encrypted) => Some(encrypted),
                Err(error) => {
                    return local_crypto_failure(services, plan, prepared, usage, error);
                }
            },
            None => credentials.refresh_token_encrypted.clone(),
        };
        let token_expires_at = refreshed.expires_in.and_then(|expires_in| {
            (expires_in > 0)
                .then(|| expires_in.saturating_sub(TOKEN_EXPIRY_SKEW_SECONDS).max(0))
                .and_then(|seconds| Utc::now().checked_add_signed(Duration::seconds(seconds)))
        });
        credentials.access_token_encrypted = access_token_encrypted;
        credentials.refresh_token_encrypted = refresh_token_encrypted;
        credentials.token_expires_at = token_expires_at;
        if !refreshed.scopes.is_empty() {
            credentials.scopes = refreshed.scopes;
        }
        refreshed.access_token
    } else {
        match token_cipher.decrypt(encrypted_access_token) {
            Ok(access_token) => access_token,
            Err(error) => {
                return local_crypto_failure(services, plan, prepared, usage, error);
            }
        }
    };

    let provider_user_id = if let Some(provider_user_id) = credentials
        .provider_user_id
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(ToOwned::to_owned)
    {
        provider_user_id
    } else {
        let authenticated_user =
            match provider_call(&mut lease, gateway.authenticated_user(&access_token)).await {
                Ok(Ok(user)) => user,
                Ok(Err(error)) => {
                    return finalized_failure(
                        services,
                        plan,
                        prepared,
                        Some(credentials),
                        usage,
                        error.to_string(),
                        true,
                    );
                }
                Err(LeaseLost) => return lease_lost_failure(),
            };
        usage.push(XRequestUsage {
            model: "users.read",
            feature: "x_sync",
            operation: "x_sync.ensure_provider_user",
            request_id: Uuid::new_v4().to_string(),
            request_count: 1,
            resource_ids: vec![authenticated_user.id.clone()],
            unit_cost_usd: services.users_read_cost_usd,
            channel: None,
        });
        credentials.provider_user_id = Some(authenticated_user.id.clone());
        credentials.provider_username = authenticated_user.username;
        authenticated_user.id
    };

    let bookmarks = if prepared.skip_bookmarks {
        BookmarkFetchOutcome {
            status: "skipped_recently",
            fetched: 0,
            tweets: Vec::new(),
            included_tweets: BTreeMap::new(),
            newest_item_id: None,
        }
    } else {
        match fetch_bookmarks(
            services,
            gateway,
            &mut lease,
            &access_token,
            &provider_user_id,
            prepared.last_synced_item_id.as_deref(),
            &mut usage,
        )
        .await
        {
            Ok(bookmarks) => bookmarks,
            Err(FetchBookmarksError::LeaseLost) => return lease_lost_failure(),
            Err(FetchBookmarksError::Provider(error)) => {
                return finalized_failure(
                    services,
                    plan,
                    prepared,
                    Some(credentials),
                    usage,
                    error.to_string(),
                    true,
                );
            }
        }
    };

    HandlerExecution::with_finalizer(
        TaskResult::ok(),
        XSyncFinalizer::new(
            services.queue.clone(),
            XSyncFinalizationPlan {
                task_id: plan.task_id,
                prepared,
                mutation: XSyncMutation::Complete {
                    credentials,
                    bookmarks,
                    usage,
                    completed_at: Utc::now(),
                },
            },
        ),
    )
}

async fn fetch_bookmarks(
    services: &XSyncWorkerServices,
    gateway: &XSyncGateway,
    lease: &mut LeaseHealth,
    access_token: &str,
    provider_user_id: &str,
    checkpoint: Option<&str>,
    usage: &mut Vec<XRequestUsage>,
) -> Result<BookmarkFetchOutcome, FetchBookmarksError> {
    let mut newest_item_id = None;
    let mut fetched = 0_usize;
    let mut tweets = Vec::new();
    let mut included_tweets = BTreeMap::new();
    let mut pagination_token = None;

    for _ in 0..BOOKMARK_SYNC_MAX_PAGES {
        let page = provider_call(
            lease,
            gateway.fetch_bookmarks(
                access_token,
                provider_user_id,
                pagination_token.as_deref(),
                BOOKMARK_SYNC_PAGE_SIZE,
            ),
        )
        .await
        .map_err(|LeaseLost| FetchBookmarksError::LeaseLost)?
        .map_err(FetchBookmarksError::Provider)?;
        usage.push(XRequestUsage {
            model: "posts.read",
            feature: "x_sync",
            operation: "x_sync.bookmarks.read",
            request_id: Uuid::new_v4().to_string(),
            request_count: 1,
            resource_ids: page.tweets.iter().map(|tweet| tweet.id.clone()).collect(),
            unit_cost_usd: services.posts_read_cost_usd,
            channel: Some("bookmarks"),
        });
        included_tweets.extend(page.included_tweets);
        if newest_item_id.is_none() {
            newest_item_id = page.tweets.first().map(|tweet| tweet.id.clone());
        }
        fetched = fetched.saturating_add(page.tweets.len());
        if page.tweets.is_empty() {
            break;
        }

        let mut reached_checkpoint = false;
        for tweet in page.tweets {
            if checkpoint.is_some_and(|checkpoint| tweet.id == checkpoint) {
                reached_checkpoint = true;
                break;
            }
            tweets.push(tweet);
        }
        if reached_checkpoint || page.next_token.is_none() {
            break;
        }
        pagination_token = page.next_token;
    }

    Ok(BookmarkFetchOutcome {
        status: "success",
        fetched,
        tweets,
        included_tweets,
        newest_item_id,
    })
}

async fn provider_call<T>(
    lease: &mut LeaseHealth,
    future: impl Future<Output = T>,
) -> Result<T, LeaseLost> {
    tokio::pin!(future);
    tokio::select! {
        result = &mut future => Ok(result),
        () = lease.wait_for_ownership_loss() => Err(LeaseLost),
    }
}

fn finalized_failure(
    services: &XSyncWorkerServices,
    plan: &OwnedWorkPlan,
    prepared: PreparedXSync,
    credentials: Option<DurableXCredentials>,
    usage: Vec<XRequestUsage>,
    error: String,
    retryable: bool,
) -> HandlerExecution {
    HandlerExecution::with_finalizer(
        TaskResult::fail(Some(error.clone()), retryable),
        XSyncFinalizer::new(
            services.queue.clone(),
            XSyncFinalizationPlan {
                task_id: plan.task_id,
                prepared,
                mutation: XSyncMutation::Failed {
                    credentials,
                    usage,
                    error,
                },
            },
        ),
    )
}

fn local_crypto_failure(
    services: &XSyncWorkerServices,
    plan: &OwnedWorkPlan,
    prepared: PreparedXSync,
    usage: Vec<XRequestUsage>,
    error: IntegrationTokenCipherError,
) -> HandlerExecution {
    finalized_failure(
        services,
        plan,
        prepared,
        None,
        usage,
        error.to_string(),
        false,
    )
}

fn unrecoverable_refresh_error(error: &XSyncGatewayError) -> bool {
    let message = error.to_string();
    UNRECOVERABLE_REFRESH_MARKERS
        .iter()
        .any(|marker| message.contains(marker))
}

fn payload_string<'a>(payload: &'a serde_json::Map<String, Value>, key: &str) -> Option<&'a str> {
    payload
        .get(key)
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
}

fn plain_failure(message: impl Into<String>, retryable: bool) -> HandlerExecution {
    HandlerExecution::from_result(TaskResult::fail(Some(message.into()), retryable))
}

fn lease_lost_failure() -> HandlerExecution {
    plain_failure("lease ownership was lost during X synchronization", true)
}

#[derive(Debug, Clone, Copy)]
struct LeaseLost;

#[derive(Debug)]
enum FetchBookmarksError {
    LeaseLost,
    Provider(XSyncGatewayError),
}
