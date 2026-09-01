use std::collections::{HashMap, HashSet};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use axum::body::Body;
use axum::extract::{Request, State};
use axum::http::{HeaderMap, HeaderName, HeaderValue, Method, Response, StatusCode};
use axum::middleware::Next;
use axum::response::IntoResponse;
use newsly_db::{OwnershipRepository, OwnershipRepositoryError};
use newsly_domain::{
    ApplicationSha, OwnershipRecord, ReplicaId, ResourceKey, ResourceKind, RuntimeOwner,
    TransitionState,
};
use tokio::sync::RwLock;

use crate::error::ApiError;
use crate::route_manifest::RouteManifest;
use crate::{AppState, request_id_from_headers};

const OWNERSHIP_VERSION_HEADER: HeaderName = HeaderName::from_static("x-newsly-ownership-version");
const OWNERSHIP_RESOURCE_HEADER: HeaderName =
    HeaderName::from_static("x-newsly-ownership-resource");
const OWNERSHIP_OWNER_HEADER: HeaderName = HeaderName::from_static("x-newsly-ownership-owner");
const CLIENT_HEADER: HeaderName = HeaderName::from_static("x-newsly-client");
const CLIENT_VERSION_HEADER: HeaderName = HeaderName::from_static("x-newsly-client-version");
const CLIENT_BUILD_HEADER: HeaderName = HeaderName::from_static("x-newsly-client-build");
const DEFAULT_READ_CACHE_TTL: Duration = Duration::from_secs(1);
const MAX_CLIENT_TELEMETRY_CHARS: usize = 64;

#[derive(Debug, Clone)]
pub(crate) struct Gateway {
    manifest: Arc<RouteManifest>,
    ownership: OwnershipRepository,
    read_cache: Arc<RwLock<HashMap<String, CachedOwnership>>>,
    read_cache_ttl: Duration,
    replica_id: ReplicaId,
    application_sha: ApplicationSha,
    barriers: Arc<WriteBarriers>,
}

impl Gateway {
    /// Creates the owner-aware runtime gate.
    ///
    /// # Errors
    ///
    /// Returns an error when the checked-in route manifest is invalid.
    pub(crate) fn new(
        ownership: OwnershipRepository,
        replica_id: ReplicaId,
        application_sha: ApplicationSha,
    ) -> Result<Self, GatewayBuildError> {
        Ok(Self {
            manifest: Arc::new(
                RouteManifest::embedded()
                    .map_err(|error| GatewayBuildError::Manifest(error.to_string()))?,
            ),
            ownership,
            read_cache: Arc::new(RwLock::new(HashMap::new())),
            read_cache_ttl: DEFAULT_READ_CACHE_TTL,
            replica_id,
            application_sha,
            barriers: Arc::new(WriteBarriers::default()),
        })
    }

    async fn resolve(
        &self,
        operation_id: &str,
        is_write: bool,
    ) -> Result<OwnershipRecord, OwnershipResolveError> {
        if !is_write {
            let cache = self.read_cache.read().await;
            if let Some(cached) = cache.get(operation_id)
                && cached.checked_at.elapsed() <= self.read_cache_ttl
            {
                return Ok(cached.record.clone());
            }
        }

        let resource_key = ResourceKey::new(operation_id)?;
        match self
            .ownership
            .get(ResourceKind::RouteGroup, &resource_key)
            .await
        {
            Ok(record) => {
                self.read_cache.write().await.insert(
                    operation_id.to_owned(),
                    CachedOwnership {
                        record: record.clone(),
                        checked_at: Instant::now(),
                    },
                );
                Ok(record)
            }
            Err(error) if !is_write => {
                let cache = self.read_cache.read().await;
                if let Some(cached) = cache.get(operation_id) {
                    tracing::warn!(
                        error = %error,
                        operation_id,
                        cached_version = cached.record.active_version.get(),
                        "route ownership refresh failed; serving read from last verified owner"
                    );
                    return Ok(cached.record.clone());
                }
                Err(error.into())
            }
            Err(error) => Err(error.into()),
        }
    }

    async fn stop_for_prepared_transition(&self, operation_id: &str) {
        self.barriers.block_and_drain(operation_id).await;
    }

    fn resume_after_active_transition(&self, operation_id: &str) {
        self.barriers.resume(operation_id);
    }

    /// Watches durable prepared transitions, drains this replica, and records a truthful ready
    /// acknowledgement. Registry failures retain existing barriers and therefore fail closed.
    pub(crate) async fn monitor_transitions(self) {
        let mut interval = tokio::time::interval(Duration::from_secs(1));
        interval.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
        loop {
            interval.tick().await;
            let transitions = match self.ownership.prepared_route_transitions().await {
                Ok(transitions) => transitions,
                Err(error) => {
                    tracing::warn!(error = %error, "route transition watcher could not refresh ownership");
                    continue;
                }
            };
            let prepared = transitions
                .iter()
                .map(|transition| transition.resource_key.to_string())
                .collect::<HashSet<_>>();
            for operation_id in self.barriers.blocked_operations() {
                if !prepared.contains(&operation_id) {
                    self.barriers.resume(&operation_id);
                }
            }
            for transition in transitions {
                let operation_id = transition.resource_key.to_string();
                self.barriers.block_and_drain(&operation_id).await;
                if let Err(error) = self
                    .ownership
                    .acknowledge(
                        ResourceKind::RouteGroup,
                        &transition.resource_key,
                        transition.desired_version,
                        &self.replica_id,
                        newsly_domain::ReadinessState::Ready,
                        &self.application_sha,
                    )
                    .await
                {
                    tracing::warn!(
                        error = %error,
                        operation_id,
                        desired_version = transition.desired_version.get(),
                        replica_id = self.replica_id.as_str(),
                        "route transition watcher could not acknowledge drained replica"
                    );
                }
            }
        }
    }
}

#[derive(Debug, Clone)]
struct CachedOwnership {
    record: OwnershipRecord,
    checked_at: Instant,
}

#[derive(Debug, Default)]
struct WriteBarriers {
    entries: Mutex<HashMap<String, BarrierEntry>>,
    changed: tokio::sync::Notify,
}

impl WriteBarriers {
    fn enter(self: &Arc<Self>, operation_id: &str) -> Option<WritePermit> {
        let mut entries = self.entries.lock().expect("write barrier lock poisoned");
        let entry = entries.entry(operation_id.to_owned()).or_default();
        if entry.blocked {
            return None;
        }
        entry.in_flight += 1;
        Some(WritePermit {
            operation_id: operation_id.to_owned(),
            barriers: Arc::clone(self),
        })
    }

    async fn block_and_drain(&self, operation_id: &str) {
        loop {
            let notified = self.changed.notified();
            let is_drained = {
                let mut entries = self.entries.lock().expect("write barrier lock poisoned");
                let entry = entries.entry(operation_id.to_owned()).or_default();
                entry.blocked = true;
                entry.in_flight == 0
            };
            if is_drained {
                return;
            }
            notified.await;
        }
    }

    fn resume(&self, operation_id: &str) {
        let mut entries = self.entries.lock().expect("write barrier lock poisoned");
        if let Some(entry) = entries.get_mut(operation_id) {
            entry.blocked = false;
        }
        self.changed.notify_waiters();
    }

    fn leave(&self, operation_id: &str) {
        let mut entries = self.entries.lock().expect("write barrier lock poisoned");
        let entry = entries
            .get_mut(operation_id)
            .expect("write permit has a barrier entry");
        entry.in_flight = entry
            .in_flight
            .checked_sub(1)
            .expect("write barrier in-flight count underflow");
        self.changed.notify_waiters();
    }

    fn blocked_operations(&self) -> Vec<String> {
        self.entries
            .lock()
            .expect("write barrier lock poisoned")
            .iter()
            .filter(|(_operation_id, entry)| entry.blocked)
            .map(|(operation_id, _entry)| operation_id.clone())
            .collect()
    }
}

#[derive(Debug, Default)]
struct BarrierEntry {
    blocked: bool,
    in_flight: usize,
}

#[derive(Debug)]
struct WritePermit {
    operation_id: String,
    barriers: Arc<WriteBarriers>,
}

#[derive(Debug, Clone)]
pub(crate) struct RouteOwnershipStamp {
    pub operation_id: String,
    pub owner: RuntimeOwner,
    pub version: i64,
}

impl Drop for WritePermit {
    fn drop(&mut self) {
        self.barriers.leave(&self.operation_id);
    }
}

/// Resolves every public operation through the durable owner/version registry before dispatch.
///
/// Reads may use the last verified owner during a transient registry failure. Writes always prove
/// current ownership and stop during the prepared transition barrier; they are never replayed to a
/// second runtime. Operations owned by another runtime fail closed because this process cannot
/// dispatch them elsewhere; the registry keeps authority transitions and rollback windows
/// explicit.
pub(crate) async fn ownership_gateway(
    State(state): State<AppState>,
    mut request: Request,
    next: Next,
) -> Response<Body> {
    strip_incoming_ownership_headers(request.headers_mut());
    let Some(route) = state
        .gateway
        .manifest
        .find(request.method(), request.uri().path())
    else {
        return next.run(request).await;
    };
    let is_write = is_write_method(request.method()) || route.write_semantics();
    let ownership = match state.gateway.resolve(route.operation_id(), is_write).await {
        Ok(ownership) => ownership,
        Err(error) => {
            tracing::error!(
                error = %error,
                operation_id = route.operation_id(),
                is_write,
                "route ownership could not be proven"
            );
            return unavailable_response(request.headers(), is_write).into_response();
        }
    };

    if is_write && ownership.transition_state == TransitionState::Preparing {
        state
            .gateway
            .stop_for_prepared_transition(route.operation_id())
            .await;
        tracing::info!(
            operation_id = route.operation_id(),
            active_owner = %ownership.active_owner,
            active_version = ownership.active_version.get(),
            desired_owner = ?ownership.desired_owner,
            desired_version = ?ownership.desired_version.map(newsly_domain::OwnershipVersion::get),
            "write stopped at ownership transition barrier"
        );
        return ApiError::new(
            StatusCode::SERVICE_UNAVAILABLE,
            "ownership_transition",
            "This operation is briefly unavailable during a runtime transition",
            request_id_from_headers(request.headers()),
        )
        .with_retryable(true)
        .into_response();
    }

    if ownership.transition_state == TransitionState::Active {
        state
            .gateway
            .resume_after_active_transition(route.operation_id());
    }

    let _write_permit = if is_write {
        match state.gateway.barriers.enter(route.operation_id()) {
            Some(permit) => Some(permit),
            None => {
                return ApiError::new(
                    StatusCode::SERVICE_UNAVAILABLE,
                    "ownership_transition",
                    "This operation is briefly unavailable during a runtime transition",
                    request_id_from_headers(request.headers()),
                )
                .with_retryable(true)
                .into_response();
            }
        }
    } else {
        None
    };

    if let Err(error) = add_ownership_headers(request.headers_mut(), &ownership) {
        tracing::error!(error = %error, "could not encode route ownership headers");
        return unavailable_response(request.headers(), is_write).into_response();
    }
    request.extensions_mut().insert(RouteOwnershipStamp {
        operation_id: route.operation_id().to_owned(),
        owner: ownership.active_owner,
        version: ownership.active_version.get(),
    });

    if ownership.active_owner == RuntimeOwner::Python {
        tracing::warn!(
            operation_id = route.operation_id(),
            active_version = ownership.active_version.get(),
            "request reached the Rust-only runtime for an operation owned by another runtime"
        );
        return ApiError::new(
            StatusCode::SERVICE_UNAVAILABLE,
            "ownership_not_promoted",
            "This operation is not currently owned by the Rust runtime",
            request_id_from_headers(request.headers()),
        )
        .with_retryable(true)
        .into_response();
    }

    let telemetry = RouteAccessTelemetry::from_request(route.operation_id(), &request);
    let response = next.run(request).await;
    telemetry.record(response.status(), ownership.active_version.get());
    response
}

#[derive(Debug)]
struct RouteAccessTelemetry {
    operation_id: String,
    method: Method,
    client: Option<String>,
    client_version: Option<String>,
    client_build: Option<String>,
    started_at: Instant,
}

impl RouteAccessTelemetry {
    fn from_request(operation_id: &str, request: &Request) -> Self {
        Self {
            operation_id: operation_id.to_owned(),
            method: request.method().clone(),
            client: bounded_header(request.headers(), &CLIENT_HEADER),
            client_version: bounded_header(request.headers(), &CLIENT_VERSION_HEADER),
            client_build: bounded_header(request.headers(), &CLIENT_BUILD_HEADER),
            started_at: Instant::now(),
        }
    }

    fn record(self, status: StatusCode, ownership_version: i64) {
        let elapsed_ms = self.started_at.elapsed().as_millis();
        tracing::info!(
            target: "newsly::api_access",
            operation_id = self.operation_id,
            method = %self.method,
            status = status.as_u16(),
            elapsed_ms,
            ownership_version,
            client = self.client.as_deref().unwrap_or("unknown"),
            client_version = self.client_version.as_deref().unwrap_or("unknown"),
            client_build = self.client_build.as_deref().unwrap_or("unknown"),
            "API operation completed"
        );
    }
}

fn bounded_header(headers: &HeaderMap, name: &HeaderName) -> Option<String> {
    headers
        .get(name)
        .and_then(|value| value.to_str().ok())
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(|value| value.chars().take(MAX_CLIENT_TELEMETRY_CHARS).collect())
}

/// Returns the typed not-found envelope for routes that are not explicitly implemented in Rust.
pub(crate) async fn not_found_fallback(mut request: Request) -> Response<Body> {
    strip_incoming_ownership_headers(request.headers_mut());
    ApiError::new(
        StatusCode::NOT_FOUND,
        "not_found",
        "Route not found",
        request_id_from_headers(request.headers()),
    )
    .into_response()
}

fn unavailable_response(headers: &HeaderMap, is_write: bool) -> ApiError {
    ApiError::new(
        StatusCode::SERVICE_UNAVAILABLE,
        "ownership_unavailable",
        if is_write {
            "The operation could not prove its active write owner"
        } else {
            "The operation owner is unavailable"
        },
        request_id_from_headers(headers),
    )
    .with_retryable(true)
}

fn add_ownership_headers(
    headers: &mut HeaderMap,
    ownership: &OwnershipRecord,
) -> Result<(), axum::http::header::InvalidHeaderValue> {
    headers.insert(
        OWNERSHIP_VERSION_HEADER,
        HeaderValue::from_str(&ownership.active_version.get().to_string())?,
    );
    headers.insert(
        OWNERSHIP_RESOURCE_HEADER,
        HeaderValue::from_str(ownership.resource_key.as_str())?,
    );
    headers.insert(
        OWNERSHIP_OWNER_HEADER,
        HeaderValue::from_static(ownership.active_owner.as_str()),
    );
    Ok(())
}

fn strip_incoming_ownership_headers(headers: &mut HeaderMap) {
    headers.remove(&OWNERSHIP_VERSION_HEADER);
    headers.remove(&OWNERSHIP_RESOURCE_HEADER);
    headers.remove(&OWNERSHIP_OWNER_HEADER);
}

fn is_write_method(method: &Method) -> bool {
    !matches!(
        *method,
        Method::GET | Method::HEAD | Method::OPTIONS | Method::TRACE
    )
}

#[derive(Debug, thiserror::Error)]
pub enum GatewayBuildError {
    #[error("route ownership manifest is invalid: {0}")]
    Manifest(String),
}

#[derive(Debug, thiserror::Error)]
enum OwnershipResolveError {
    #[error(transparent)]
    InvalidResource(#[from] newsly_domain::InvalidOwnershipValue),
    #[error(transparent)]
    Repository(#[from] OwnershipRepositoryError),
}

#[cfg(test)]
mod tests {
    use axum::http::{HeaderMap, HeaderValue};

    use super::{CLIENT_HEADER, MAX_CLIENT_TELEMETRY_CHARS, bounded_header};

    #[test]
    fn client_telemetry_is_trimmed_and_bounded() {
        let mut headers = HeaderMap::new();
        let value = format!("  {}  ", "x".repeat(MAX_CLIENT_TELEMETRY_CHARS + 20));
        headers.insert(
            CLIENT_HEADER.clone(),
            HeaderValue::from_str(&value).expect("valid fixture header"),
        );

        let client = bounded_header(&headers, &CLIENT_HEADER).expect("client header");
        assert_eq!(client.len(), MAX_CLIENT_TELEMETRY_CHARS);
        assert!(client.bytes().all(|byte| byte == b'x'));
    }

    #[test]
    fn empty_client_telemetry_is_ignored() {
        let mut headers = HeaderMap::new();
        headers.insert(CLIENT_HEADER.clone(), HeaderValue::from_static("   "));

        assert_eq!(bounded_header(&headers, &CLIENT_HEADER), None);
    }
}
