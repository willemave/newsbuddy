use std::{collections::HashMap, sync::Arc, time::Duration};

use axum::{
    Json, Router,
    extract::{
        Query, State,
        rejection::{JsonRejection, QueryRejection},
    },
    http::{HeaderMap, StatusCode},
    routing::{get, post},
};
use futures_util::{StreamExt, stream::FuturesUnordered};
use newsly_contracts::{
    NewsFilterNewsItem, NewsFilterNewsResponse, NewsFilterPreviewRequest, NewsFilterPreviewResponse,
};
use newsly_db::{
    NewsItemProjection,
    news_filter::{self as repository, NewsFilterCandidate},
};
use newsly_providers::{
    NewsFilterAttempt, NewsFilterChoice, NewsFilterDecision, NewsFilterGateway,
    NewsFilterGatewayError, NewsFilterInput,
};
use serde::{Deserialize, Serialize};

use crate::{
    AppState,
    auth::AuthenticatedUser,
    content_read::presentation::present_news_summary,
    error::ApiError,
    gateway::RouteOwnershipStamp,
    pagination_cursor::{self, InvalidCursor},
    request_id_from_headers,
    write_support::{begin_fenced, commit, decode_json, internal_error},
};

const PREVIEW_LIMIT: usize = 20;
const PREVIEW_DEADLINE: Duration = Duration::from_secs(15);

pub(super) fn router() -> Router<AppState> {
    Router::new()
        .route("/api/briefing/filter/news", get(list))
        .route("/api/briefing/filter/preview", post(preview))
}

#[derive(Debug, Deserialize)]
pub(super) struct ListQuery {
    #[serde(default)]
    filtered: bool,
    cursor: Option<String>,
    #[serde(default = "default_limit")]
    limit: usize,
}

fn default_limit() -> usize {
    30
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Cursor {
    route: String,
    user_id: i64,
    filtered: bool,
    before_id: i64,
}

fn decode_cursor(value: &str, user_id: i64, filtered: bool) -> Result<i64, InvalidCursor> {
    if value.len() > 512 {
        return Err(InvalidCursor);
    }
    let cursor: Cursor = pagination_cursor::decode(value)?;
    if cursor.route != "news_filter_v1"
        || cursor.user_id != user_id
        || cursor.filtered != filtered
        || cursor.before_id <= 0
    {
        return Err(InvalidCursor);
    }
    Ok(cursor.before_id)
}

fn encode_cursor(user_id: i64, filtered: bool, before_id: i64) -> Result<String, InvalidCursor> {
    pagination_cursor::encode(&Cursor {
        route: "news_filter_v1".to_owned(),
        user_id,
        filtered,
        before_id,
    })
}

#[utoipa::path(get, path = "/api/briefing/filter/news", operation_id = "listFilterNews", tag = "briefing", security(("HTTPBearer" = [])), params(("filtered" = Option<bool>, Query, description = "Only filtered-out items when true; all News otherwise"), ("cursor" = Option<String>, Query, description = "Opaque pagination cursor"), ("limit" = Option<usize>, Query, description = "Page size, 1 through 50", minimum = 1, maximum = 50)), responses((status = 200, description = "News with filter status", body = NewsFilterNewsResponse), (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope), (status = 422, description = "Validation error", body = newsly_contracts::ErrorEnvelope), (status = 500, description = "Internal error", body = newsly_contracts::ErrorEnvelope)))]
pub(super) async fn list(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: AuthenticatedUser,
    query: Result<Query<ListQuery>, QueryRejection>,
) -> Result<Json<NewsFilterNewsResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let Query(query) =
        query.map_err(|_| ApiError::validation("Invalid News list query", &request_id))?;
    if !(1..=50).contains(&query.limit) {
        return Err(ApiError::validation(
            "limit must be between 1 and 50",
            &request_id,
        ));
    }
    let before_id = query
        .cursor
        .as_deref()
        .map(|value| decode_cursor(value, user.id, query.filtered))
        .transpose()
        .map_err(|_| ApiError::validation("Invalid News list cursor", &request_id))?;
    let mut rows = repository::list_news_ids(
        state.database.pool(),
        user.id,
        query.filtered,
        before_id,
        query.limit + 1,
    )
    .await
    .map_err(|error| internal_error(error, &request_id))?;
    let has_more = rows.len() > query.limit;
    rows.truncate(query.limit);
    let next_cursor = if has_more {
        rows.last()
            .map(|row| encode_cursor(user.id, query.filtered, row.news_item_id))
            .transpose()
            .map_err(|error| internal_error(error, &request_id))?
    } else {
        None
    };
    let ids = rows.iter().map(|row| row.news_item_id).collect::<Vec<_>>();
    let news = newsly_db::find_visible_news_items_by_ids(state.database.pool(), user.id, &ids)
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    let statuses = rows
        .into_iter()
        .map(|row| (row.news_item_id, row.filter_status))
        .collect();
    Ok(Json(NewsFilterNewsResponse {
        items: present_items(&news, &statuses),
        next_cursor,
    }))
}

#[utoipa::path(post, path = "/api/briefing/filter/preview", operation_id = "previewNewsFilter", tag = "briefing", security(("HTTPBearer" = [])), request_body = NewsFilterPreviewRequest, responses((status = 200, description = "Bounded preview without changing saved preferences", body = NewsFilterPreviewResponse), (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope), (status = 409, description = "Ownership changed", body = newsly_contracts::ErrorEnvelope), (status = 422, description = "Validation error", body = newsly_contracts::ErrorEnvelope), (status = 429, description = "Preview busy or daily budget exhausted", body = newsly_contracts::ErrorEnvelope), (status = 500, description = "Internal error", body = newsly_contracts::ErrorEnvelope)))]
pub(super) async fn preview(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: AuthenticatedUser,
    stamp: RouteOwnershipStamp,
    payload: Result<Json<NewsFilterPreviewRequest>, JsonRejection>,
) -> Result<Json<NewsFilterPreviewResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let Json(payload) = decode_json(payload, &request_id)?;
    let enabled = newsly_db::news_filter_preferences::validate(&payload.text, true)
        .map_err(|error| ApiError::validation(error.to_string(), &request_id))?;
    let candidates =
        repository::load_preview_candidates(state.database.pool(), user.id, PREVIEW_LIMIT)
            .await
            .map_err(|error| internal_error(error, &request_id))?;
    let ids = candidates
        .iter()
        .map(|candidate| candidate.news_item_id)
        .collect::<Vec<_>>();
    if !enabled || candidates.is_empty() {
        let news = newsly_db::find_visible_news_items_by_ids(state.database.pool(), user.id, &ids)
            .await
            .map_err(|error| internal_error(error, &request_id))?;
        let statuses = ids.into_iter().map(|id| (id, "off".to_owned())).collect();
        return Ok(Json(NewsFilterPreviewResponse {
            items: present_items(&news, &statuses),
        }));
    }
    let mut transaction = begin_fenced(state.database.pool(), &stamp, &request_id).await?;
    let token = repository::reserve_preview(&mut transaction, user.id, candidates.len())
        .await
        .map_err(|error| internal_error(error, &request_id))?
        .ok_or_else(|| {
            ApiError::new(
                StatusCode::TOO_MANY_REQUESTS,
                "news_filter_preview_limited",
                "Preview is busy or its daily budget is exhausted",
                &request_id,
            )
        })?;
    commit(transaction, &request_id).await?;

    // Detach only the bounded provider/accounting phase: disconnects cannot discard billing.
    let pool = state.database.pool().clone();
    let gateway = Arc::clone(&state.news_filter);
    let text = payload.text;
    let user_id = user.id;
    let evidence = candidates
        .iter()
        .map(|candidate| (candidate.news_item_id, candidate.input_hash.clone()))
        .collect::<Vec<_>>();
    let work = tokio::spawn(complete_preview(
        gateway, pool, user_id, token, text, candidates,
    ));
    let statuses = work
        .await
        .map_err(|error| internal_error(error, &request_id))?
        .map_err(|error| internal_error(error, &request_id))?;
    // Response ownership checks cannot discard provider accounting.
    let mut transaction = begin_fenced(state.database.pool(), &stamp, &request_id).await?;
    if !repository::lock_active_user(&mut transaction, user.id)
        .await
        .map_err(|error| internal_error(error, &request_id))?
    {
        return Err(ApiError::new(
            StatusCode::UNAUTHORIZED,
            "invalid_credentials",
            "Account is no longer active",
            &request_id,
        ));
    }
    commit(transaction, &request_id).await?;
    let items = load_preview_items(state.database.pool(), user.id, statuses, &evidence)
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    Ok(Json(NewsFilterPreviewResponse { items }))
}

async fn complete_preview(
    gateway: Arc<NewsFilterGateway>,
    pool: sqlx::PgPool,
    user_id: i64,
    token: uuid::Uuid,
    text: String,
    candidates: Vec<NewsFilterCandidate>,
) -> Result<HashMap<i64, String>, sqlx::Error> {
    let reserved = candidates.len();
    // All external work owns immutable inputs and no checked-out database connection.
    let (statuses, usages) = evaluate_preview(&gateway, &text, candidates).await;
    let accounting = async {
        let mut transaction = pool.begin().await?;
        for usage in &usages {
            repository::record_usage(&mut transaction, user_id, None, usage).await?;
        }
        transaction.commit().await
    }
    .await;
    // Cleanup still runs after a failed accounting write.
    let release =
        repository::release_preview(&pool, user_id, token, reserved.saturating_sub(usages.len()))
            .await;
    accounting?;
    release?;
    Ok(statuses)
}

async fn evaluate_one(
    gateway: &NewsFilterGateway,
    text: &str,
    candidate: NewsFilterCandidate,
    attempt: Arc<NewsFilterAttempt>,
) -> (i64, Result<NewsFilterDecision, NewsFilterGatewayError>) {
    let input = NewsFilterInput {
        filter: text.to_owned(),
        title: candidate.title,
        summary: candidate.summary,
        key_points: candidate.key_points,
    };
    (
        candidate.news_item_id,
        gateway.decide_with_attempt(&input, &attempt).await,
    )
}

async fn evaluate_preview(
    gateway: &NewsFilterGateway,
    text: &str,
    candidates: Vec<NewsFilterCandidate>,
) -> (HashMap<i64, String>, Vec<repository::NewsFilterUsage>) {
    let mut statuses = candidates
        .iter()
        .map(|candidate| (candidate.news_item_id, "pending".to_owned()))
        .collect::<HashMap<_, _>>();
    let mut candidates = candidates.into_iter();
    let mut calls = FuturesUnordered::new();
    let mut started = HashMap::new();
    for candidate in candidates.by_ref().take(2) {
        let attempt = Arc::new(NewsFilterAttempt::default());
        started.insert(candidate.news_item_id, Arc::clone(&attempt));
        calls.push(evaluate_one(gateway, text, candidate, attempt));
    }
    let deadline = tokio::time::Instant::now() + PREVIEW_DEADLINE;
    let mut usages = Vec::new();
    while let Ok(Some((id, result))) = tokio::time::timeout_at(deadline, calls.next()).await {
        statuses.insert(
            id,
            result
                .as_ref()
                .map_or("unavailable", |decision| decision_status(decision))
                .to_owned(),
        );
        let (metered, generation_id) = match &result {
            Ok(decision) => (Some(&decision.usage), Some(decision.generation_id.clone())),
            Err(error) => (
                error.metered_usage(),
                error.generation_id().map(str::to_owned),
            ),
        };
        let attempt = started
            .remove(&id)
            .expect("queued preview attempt identity");
        if let Err(error) = &result {
            tracing::warn!(
                news_item_id = id,
                error_code = error.code(),
                "News filter preview unavailable"
            );
        }
        if attempt.http_started() {
            usages.push(repository::NewsFilterUsage {
                news_item_id: id,
                generation_id,
                input_tokens: metered.map_or(0, |usage| usage.input_tokens),
                output_tokens: metered.map_or(0, |usage| usage.output_tokens),
                cost_usd: metered.map(|usage| usage.cost_usd),
                outcome: statuses[&id].clone(),
            });
        }
        if let Some(candidate) = candidates.next() {
            let attempt = Arc::new(NewsFilterAttempt::default());
            started.insert(candidate.news_item_id, Arc::clone(&attempt));
            calls.push(evaluate_one(gateway, text, candidate, attempt));
        }
    }
    drop(calls);
    for (id, attempt) in started {
        if !attempt.http_started() {
            continue;
        }
        statuses.insert(id, "unavailable".to_owned());
        usages.push(repository::NewsFilterUsage {
            news_item_id: id,
            generation_id: None,
            input_tokens: 0,
            output_tokens: 0,
            cost_usd: None,
            outcome: "unavailable".to_owned(),
        });
    }
    (statuses, usages)
}

fn decision_status(decision: &NewsFilterDecision) -> &'static str {
    if decision.should_hide() {
        "filtered_out"
    } else if decision.choice == NewsFilterChoice::Match {
        "matched"
    } else {
        "uncertain"
    }
}

fn present_items(
    news: &[NewsItemProjection],
    statuses: &HashMap<i64, String>,
) -> Vec<NewsFilterNewsItem> {
    news.iter()
        .filter_map(|item| {
            statuses.get(&item.id).map(|status| NewsFilterNewsItem {
                news: present_news_summary(item),
                filter_status: status.clone(),
            })
        })
        .collect()
}

async fn load_preview_items(
    pool: &sqlx::PgPool,
    user_id: i64,
    mut statuses: HashMap<i64, String>,
    evidence: &[(i64, String)],
) -> Result<Vec<NewsFilterNewsItem>, newsly_db::ContentReadRepositoryError> {
    let matching =
        newsly_db::find_visible_news_items_for_filter_preview(pool, user_id, evidence).await?;
    let mut items = present_items(&matching, &statuses);
    for item in &matching {
        statuses.remove(&item.id);
    }
    // Changed evidence has no applicable decision; reloading it cannot validate an old outcome.
    let remaining = statuses.keys().copied().collect::<Vec<_>>();
    for status in statuses.values_mut() {
        "pending".clone_into(status);
    }
    let changed = newsly_db::find_visible_news_items_by_ids(pool, user_id, &remaining).await?;
    items.extend(present_items(&changed, &statuses));
    items.sort_unstable_by_key(|item| std::cmp::Reverse(item.news.id));
    Ok(items)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[sqlx::test(migrations = false)]
    async fn preview_rejects_changed_evidence_and_rechecks_visibility(pool: sqlx::PgPool) {
        newsly_db::run_migrations(&pool).await.unwrap();
        let user_id: i64 = sqlx::query_scalar("INSERT INTO users(apple_id,email,is_active,is_admin) VALUES('snapshot-preview','snapshot-preview@example.test',true,false) RETURNING id::bigint").fetch_one(&pool).await.unwrap();
        let id: i64 = sqlx::query_scalar(r#"INSERT INTO news_items(ingest_key,visibility_scope,owner_user_id,platform,status,summary_text,summary_key_points,raw_metadata,ingested_at,created_at) VALUES('snapshot-preview','user',$1::bigint::integer,'reddit','ready','Original summary','["Original point"]','{"summary":{"title":"Original title"}}',now(),now()) RETURNING id::bigint"#).bind(user_id).fetch_one(&pool).await.unwrap();
        let evidence = repository::load_preview_candidates(&pool, user_id, PREVIEW_LIMIT)
            .await
            .unwrap();
        assert_eq!(evidence.len(), 1);
        let evidence = evidence
            .into_iter()
            .map(|candidate| (candidate.news_item_id, candidate.input_hash))
            .collect::<Vec<_>>();
        let statuses = HashMap::from([(id, "filtered_out".to_owned())]);
        let unchanged = load_preview_items(&pool, user_id, statuses.clone(), &evidence)
            .await
            .unwrap();
        assert_eq!(unchanged[0].filter_status, "filtered_out");
        assert_eq!(unchanged[0].news.title, "Original title");
        sqlx::query(r#"UPDATE news_items SET summary_text='Rewritten summary',summary_key_points='["Rewritten point"]',raw_metadata='{"summary":{"title":"Rewritten title"}}' WHERE id::bigint=$1"#).bind(id).execute(&pool).await.unwrap();
        let items = load_preview_items(&pool, user_id, statuses.clone(), &evidence)
            .await
            .unwrap();
        assert_eq!(items.len(), 1);
        let item = &items[0];
        assert_eq!(item.filter_status, "pending");
        assert_eq!(item.news.title, "Rewritten title");
        assert_eq!(
            item.news.short_summary.as_deref(),
            Some("Rewritten summary")
        );
        assert_eq!(item.news.news_summary.as_deref(), Some("Rewritten summary"));
        assert_eq!(
            item.news.news_key_points.as_ref().unwrap(),
            &["Rewritten point"]
        );

        sqlx::query("UPDATE news_items SET status='new' WHERE id::bigint=$1")
            .bind(id)
            .execute(&pool)
            .await
            .unwrap();
        let hidden = load_preview_items(&pool, user_id, statuses, &evidence)
            .await
            .unwrap();
        assert!(hidden.is_empty());
    }

    #[test]
    fn cursors_bind_user_collection_and_positive_id() {
        let cursor = encode_cursor(7, false, 123).unwrap();
        assert_eq!(decode_cursor(&cursor, 7, false).unwrap(), 123);
        assert!(decode_cursor(&cursor, 8, false).is_err());
        assert!(decode_cursor(&cursor, 7, true).is_err());
        assert!(decode_cursor(&encode_cursor(7, false, 0).unwrap(), 7, false).is_err());
        assert!(decode_cursor("invalid", 7, false).is_err());
        assert!(decode_cursor(&"x".repeat(513), 7, false).is_err());
    }

    #[test]
    fn list_defaults_to_all_news_and_preview_status_uses_conservative_threshold() {
        let query: ListQuery = serde_json::from_value(serde_json::json!({})).unwrap();
        assert!(!query.filtered);
        assert_eq!(query.limit, 30);
        assert!(query.cursor.is_none());
        let mut decision = NewsFilterDecision {
            choice: NewsFilterChoice::NoMatch,
            probabilities: newsly_providers::NewsFilterProbabilities {
                match_probability: 0.05,
                no_match: 0.9,
                insufficient_evidence: 0.05,
            },
            usage: newsly_providers::NewsFilterUsage {
                input_tokens: 10,
                output_tokens: 0,
                cost_usd: 0.0,
            },
            generation_id: "fixture".to_owned(),
        };
        assert_eq!(decision_status(&decision), "filtered_out");
        decision.probabilities.no_match = 0.89;
        assert_eq!(decision_status(&decision), "uncertain");
        decision.choice = NewsFilterChoice::Match;
        assert_eq!(decision_status(&decision), "matched");
        decision.choice = NewsFilterChoice::InsufficientEvidence;
        assert_eq!(decision_status(&decision), "uncertain");
    }

    #[tokio::test]
    async fn unavailable_provider_keeps_every_preview_item_without_fake_usage() {
        let gateway = NewsFilterGateway::new(None).unwrap();
        let candidates = (1..=20)
            .map(|id| NewsFilterCandidate {
                news_item_id: id,
                input_hash: "hash".to_owned(),
                title: "Title".to_owned(),
                summary: "Summary".to_owned(),
                key_points: vec![],
            })
            .collect();
        let (statuses, usage) = evaluate_preview(&gateway, "AI", candidates).await;
        assert_eq!(statuses.len(), 20);
        assert!(statuses.values().all(|status| status == "unavailable"));
        assert!(usage.is_empty());
    }

    #[sqlx::test(migrations = false)]
    async fn dropped_preview_join_handle_still_finishes_accounting_and_refund(pool: sqlx::PgPool) {
        newsly_db::run_migrations(&pool).await.unwrap();
        let user_id: i64 = sqlx::query_scalar("INSERT INTO users(apple_id,email,is_active,is_admin) VALUES('detached-preview','detached-preview@example.test',true,false) RETURNING id::bigint").fetch_one(&pool).await.unwrap();
        let mut transaction = pool.begin().await.unwrap();
        let token = repository::reserve_preview(&mut transaction, user_id, 1)
            .await
            .unwrap()
            .unwrap();
        transaction.commit().await.unwrap();
        let gateway = Arc::new(NewsFilterGateway::new(None).unwrap());
        let background_pool = pool.clone();
        let (done_tx, done_rx) = tokio::sync::oneshot::channel();
        let work = tokio::spawn(async move {
            let result = complete_preview(
                gateway,
                background_pool,
                user_id,
                token,
                "AI".to_owned(),
                vec![NewsFilterCandidate {
                    news_item_id: 1,
                    input_hash: "hash".to_owned(),
                    title: "Title".to_owned(),
                    summary: "Summary".to_owned(),
                    key_points: vec![],
                }],
            )
            .await;
            done_tx.send(result).unwrap();
        });
        drop(work); // A disconnected HTTP caller drops its awaited join handle.
        let statuses = tokio::time::timeout(Duration::from_secs(15), done_rx)
            .await
            .unwrap()
            .unwrap()
            .unwrap();
        assert_eq!(statuses[&1], "unavailable");
        let reservation: (i32, Option<uuid::Uuid>) = sqlx::query_as("SELECT preview_count,preview_token FROM news_filter_daily_usage WHERE user_id=$1::bigint").bind(user_id).fetch_one(&pool).await.unwrap();
        assert_eq!(reservation, (0, None));
    }
}
