//! Summary-only preference filtering inside the existing leased refresh task.

use std::{collections::HashMap, sync::Arc, time::Duration};

use futures_util::{StreamExt, stream::FuturesUnordered};
use newsly_db::news_filter::{
    self as repository, NewsFilterCandidate, NewsFilterPass, NewsFilterPreparation,
    NewsFilterStoredDecision, NewsFilterUsage,
};
use newsly_providers::{
    NewsFilterAttempt, NewsFilterChoice, NewsFilterDecision, NewsFilterGateway,
    NewsFilterGatewayError, NewsFilterInput,
};
use thiserror::Error;

use crate::LeaseHealth;

use super::BriefingRefreshWorkerServices;

const FILTER_DEADLINE: Duration = Duration::from_secs(20);
const FILTER_CONCURRENCY: usize = 4;

pub(super) async fn filter_news(
    services: &BriefingRefreshWorkerServices,
    task_id: i64,
    user_id: i64,
    lease: &mut LeaseHealth,
) -> Result<NewsFilterPass, FilterPhaseError> {
    if lease.ownership_lost() {
        return Err(FilterPhaseError::LeaseLost);
    }
    let preparation = {
        let mut transaction = services.pool.begin().await?;
        let preparation = repository::prepare_refresh(
            &mut transaction,
            task_id,
            user_id,
            lease.claim().lease_token.get(),
        )
        .await?;
        transaction.commit().await?;
        preparation
    };
    let Some(preparation) = preparation else {
        return Ok(NewsFilterPass::default());
    };
    let outcome = execute_candidates(&services.filter_gateway, &preparation, lease).await;
    persist_accounting(&services.pool, task_id, user_id, &preparation, &outcome).await?;
    if lease.ownership_lost() || outcome.lease_lost {
        return Err(FilterPhaseError::LeaseLost);
    }
    {
        let mut transaction = services.pool.begin().await?;
        if !repository::store_decisions(
            &mut transaction,
            task_id,
            user_id,
            &preparation,
            &outcome.decisions,
        )
        .await?
        {
            return Err(FilterPhaseError::LeaseLost);
        }
        transaction.commit().await?;
    }
    tracing::info!(
        task_id,
        user_id,
        revision = preparation.preference.revision,
        decisions = outcome.decisions.len(),
        unavailable = outcome.unavailable.len(),
        more_work = outcome.more_work,
        "News preference filtering completed behind the refresh lease"
    );
    Ok(NewsFilterPass {
        revision: preparation.preference.revision,
        unavailable: outcome.unavailable,
        examined: preparation.examined,
        more_work: outcome.more_work,
    })
}

// Accounting survives loss of product ownership. Refund only requests never dispatched.
async fn persist_accounting(
    pool: &sqlx::PgPool,
    task_id: i64,
    user_id: i64,
    preparation: &NewsFilterPreparation,
    outcome: &FilterOutcomes,
) -> Result<(), sqlx::Error> {
    let mut transaction = pool.begin().await?;
    for usage in &outcome.usage {
        repository::record_usage(&mut transaction, user_id, Some(task_id), usage).await?;
    }
    repository::refund_background(
        &mut transaction,
        user_id,
        preparation.usage_day,
        preparation
            .candidates
            .len()
            .saturating_sub(outcome.usage.len()),
    )
    .await?;
    transaction.commit().await
}

#[derive(Debug, Default)]
struct FilterOutcomes {
    decisions: Vec<NewsFilterStoredDecision>,
    unavailable: Vec<(i64, String)>,
    usage: Vec<NewsFilterUsage>,
    more_work: bool,
    lease_lost: bool,
}

async fn evaluate_one(
    gateway: &NewsFilterGateway,
    text: &str,
    candidate: NewsFilterCandidate,
    attempt: Arc<NewsFilterAttempt>,
) -> (
    i64,
    String,
    Result<NewsFilterDecision, NewsFilterGatewayError>,
) {
    let input = NewsFilterInput {
        filter: text.to_owned(),
        title: candidate.title,
        summary: candidate.summary,
        key_points: candidate.key_points,
    };
    (
        candidate.news_item_id,
        candidate.input_hash,
        gateway.decide_with_attempt(&input, &attempt).await,
    )
}

async fn execute_candidates(
    gateway: &NewsFilterGateway,
    preparation: &NewsFilterPreparation,
    lease: &mut LeaseHealth,
) -> FilterOutcomes {
    if lease.ownership_lost() {
        return FilterOutcomes {
            lease_lost: true,
            ..FilterOutcomes::default()
        };
    }
    let mut candidates = preparation.candidates.iter().cloned();
    let mut calls = FuturesUnordered::new();
    let mut started = HashMap::new();
    for candidate in candidates.by_ref().take(FILTER_CONCURRENCY) {
        let attempt = Arc::new(NewsFilterAttempt::default());
        started.insert(
            candidate.news_item_id,
            (candidate.input_hash.clone(), Arc::clone(&attempt)),
        );
        calls.push(evaluate_one(
            gateway,
            &preparation.preference.text,
            candidate,
            attempt,
        ));
    }
    let deadline = tokio::time::Instant::now() + FILTER_DEADLINE;
    let mut outcome = FilterOutcomes {
        more_work: preparation.more_work,
        ..FilterOutcomes::default()
    };
    loop {
        let completed = tokio::select! {
            completed = tokio::time::timeout_at(deadline, calls.next()) => completed,
            () = lease.wait_for_ownership_loss() => { outcome.lease_lost = true; break; },
        };
        let Ok(Some((id, input_hash, result))) = completed else {
            break;
        };
        let (_, attempt) = started.remove(&id).expect("queued attempt identity");
        match result {
            Ok(decision) => {
                outcome.usage.push(NewsFilterUsage {
                    news_item_id: id,
                    generation_id: Some(decision.generation_id),
                    input_tokens: decision.usage.input_tokens,
                    output_tokens: decision.usage.output_tokens,
                    cost_usd: Some(decision.usage.cost_usd),
                    outcome: "complete".to_owned(),
                });
                outcome.decisions.push(NewsFilterStoredDecision {
                    news_item_id: id,
                    input_hash,
                    choice: match decision.choice {
                        NewsFilterChoice::Match => "match",
                        NewsFilterChoice::NoMatch => "no_match",
                        NewsFilterChoice::InsufficientEvidence => "insufficient_evidence",
                    }
                    .to_owned(),
                    no_match_probability: decision.probabilities.no_match,
                    probabilities: serde_json::to_value(decision.probabilities)
                        .expect("validated finite probabilities"),
                });
            }
            Err(error) => {
                tracing::warn!(
                    news_item_id = id,
                    error_code = error.code(),
                    "News preference filter unavailable"
                );
                if attempt.http_started() {
                    let metered = error.metered_usage();
                    outcome.usage.push(NewsFilterUsage {
                        news_item_id: id,
                        generation_id: error.generation_id().map(str::to_owned),
                        input_tokens: metered.map_or(0, |usage| usage.input_tokens),
                        output_tokens: metered.map_or(0, |usage| usage.output_tokens),
                        cost_usd: metered.map(|usage| usage.cost_usd),
                        outcome: "unavailable".to_owned(),
                    });
                }
                outcome.unavailable.push((id, input_hash));
            }
        }
        if lease.ownership_lost() {
            outcome.lease_lost = true;
            break;
        }
        if let Some(candidate) = candidates.next() {
            let attempt = Arc::new(NewsFilterAttempt::default());
            started.insert(
                candidate.news_item_id,
                (candidate.input_hash.clone(), Arc::clone(&attempt)),
            );
            calls.push(evaluate_one(
                gateway,
                &preparation.preference.text,
                candidate,
                attempt,
            ));
        }
    }
    drop(calls);
    for (id, (input_hash, attempt)) in started {
        if !attempt.http_started() {
            outcome.more_work = true;
            continue;
        }
        outcome.unavailable.push((id, input_hash));
        outcome.usage.push(NewsFilterUsage {
            news_item_id: id,
            generation_id: None,
            input_tokens: 0,
            output_tokens: 0,
            cost_usd: None,
            outcome: "unavailable".to_owned(),
        });
    }
    outcome.more_work |= candidates.len() > 0;
    outcome
}

#[derive(Debug, Error)]
pub(super) enum FilterPhaseError {
    #[error("queue lease was lost during News preference filtering")]
    LeaseLost,
    #[error(transparent)]
    Database(#[from] sqlx::Error),
}

#[cfg(test)]
mod tests {
    use super::*;

    fn lease(lost: bool) -> (LeaseHealth, [tokio::sync::watch::Sender<bool>; 2]) {
        let (ownership_tx, ownership_lost_rx) = tokio::sync::watch::channel(lost);
        let (cancel_tx, cancellation_rx) = tokio::sync::watch::channel(false);
        let now = chrono::Utc::now();
        let claim = newsly_queue::ClaimedTask {
            id: 1,
            owner_user_id: Some(1),
            task_type: newsly_queue::TaskType::BriefingRefresh,
            content_id: None,
            payload: serde_json::Map::new(),
            retry_count: 0,
            status: newsly_queue::TaskStatus::Processing,
            queue_name: newsly_queue::TaskQueue::Llm,
            executor_runtime: newsly_domain::RuntimeOwner::Rust,
            executor_version: 1,
            executor_namespace: "rust".to_owned(),
            created_at: Some(now),
            available_at: now,
            started_at: now,
            locked_at: now,
            locked_by: "fixture".to_owned(),
            lease_token: newsly_domain::LeaseToken::new(),
            lease_expires_at: now + chrono::Duration::minutes(1),
        };
        (
            LeaseHealth {
                ownership_lost_rx,
                cancellation_rx,
                claim,
            },
            [ownership_tx, cancel_tx],
        )
    }

    fn preparation() -> NewsFilterPreparation {
        NewsFilterPreparation {
            preference: newsly_db::news_filter_preferences::NewsFilterPreferenceRecord {
                revision: 3,
                text: "AI".to_owned(),
                enabled: true,
            },
            candidates: (1..=8)
                .map(|id| NewsFilterCandidate {
                    news_item_id: id,
                    input_hash: format!("hash-{id}"),
                    title: "Title".to_owned(),
                    summary: "Summary".to_owned(),
                    key_points: vec![],
                })
                .collect(),
            lease_token: uuid::Uuid::nil(),
            usage_day: chrono::Utc::now().date_naive(),
            examined: true,
            more_work: true,
        }
    }

    #[tokio::test]
    async fn unavailable_filter_admits_only_attempted_exact_inputs_without_fake_billing() {
        let gateway = NewsFilterGateway::new(None).unwrap();
        let preparation = preparation();
        let (mut lease, _senders) = lease(false);
        let outcome = execute_candidates(&gateway, &preparation, &mut lease).await;
        assert!(outcome.decisions.is_empty());
        assert!(outcome.usage.is_empty());
        assert_eq!(outcome.unavailable.len(), 8);
        for candidate in preparation.candidates {
            assert!(
                outcome
                    .unavailable
                    .contains(&(candidate.news_item_id, candidate.input_hash))
            );
        }
        assert!(outcome.more_work);
    }

    #[tokio::test]
    async fn lease_loss_prevents_filter_work_and_publication() {
        let gateway = NewsFilterGateway::new(None).unwrap();
        let (mut lease, _senders) = lease(true);
        let outcome = execute_candidates(&gateway, &preparation(), &mut lease).await;
        assert!(outcome.lease_lost);
        assert!(outcome.usage.is_empty());
    }

    #[sqlx::test(migrations = false)]
    async fn accounting_refunds_unused_slots_and_preserves_billing_after_lease_loss(
        pool: sqlx::PgPool,
    ) {
        newsly_db::run_migrations(&pool).await.unwrap();
        let user_id: i64 = sqlx::query_scalar("INSERT INTO users(apple_id,email,is_active,is_admin) VALUES('filter-accounting','filter-accounting@example.test',true,false) RETURNING id::bigint").fetch_one(&pool).await.unwrap();
        let task_id: i64 = sqlx::query_scalar("INSERT INTO processing_tasks(task_type,payload,status,queue_name,owner_user_id,started_at,locked_at,locked_by,lease_token,lease_expires_at,executor_runtime,executor_version,executor_namespace) VALUES('briefing_refresh','{}'::json,'processing','llm',$1::bigint::integer,timezone('UTC',now()),timezone('UTC',now()),'replacement-worker',$2,timezone('UTC',now())+interval '5 minutes','rust',1,'briefing_refresh') RETURNING id::bigint").bind(user_id).bind(uuid::Uuid::new_v4()).fetch_one(&pool).await.unwrap();
        let preparation = preparation();
        sqlx::query("INSERT INTO news_filter_daily_usage(user_id,day,background_count) VALUES($1::bigint::integer,$2,8)").bind(user_id).bind(preparation.usage_day).execute(&pool).await.unwrap();
        // Missing credentials/invalid inputs never dispatch and refund every reservation.
        persist_accounting(
            &pool,
            task_id,
            user_id,
            &preparation,
            &FilterOutcomes::default(),
        )
        .await
        .unwrap();
        let count: i32 = sqlx::query_scalar("SELECT background_count FROM news_filter_daily_usage WHERE user_id=$1::bigint AND day=$2").bind(user_id).bind(preparation.usage_day).fetch_one(&pool).await.unwrap();
        assert_eq!(count, 0);
        sqlx::query(
            "UPDATE news_filter_daily_usage SET background_count=8 WHERE user_id=$1::bigint",
        )
        .bind(user_id)
        .execute(&pool)
        .await
        .unwrap();
        sqlx::query("UPDATE users SET is_active=false WHERE id=$1::bigint")
            .bind(user_id)
            .execute(&pool)
            .await
            .unwrap();
        let outcome = FilterOutcomes {
            lease_lost: true,
            usage: vec![
                NewsFilterUsage {
                    news_item_id: 1,
                    generation_id: Some("paid-before-loss".to_owned()),
                    input_tokens: 100,
                    output_tokens: 0,
                    cost_usd: Some(0.000_004_2),
                    outcome: "complete".to_owned(),
                },
                NewsFilterUsage {
                    news_item_id: 2,
                    generation_id: None,
                    input_tokens: 0,
                    output_tokens: 0,
                    cost_usd: None,
                    outcome: "unavailable".to_owned(),
                },
            ],
            ..FilterOutcomes::default()
        };
        persist_accounting(&pool, task_id, user_id, &preparation, &outcome)
            .await
            .unwrap();
        let count: i32 = sqlx::query_scalar("SELECT background_count FROM news_filter_daily_usage WHERE user_id=$1::bigint AND day=$2").bind(user_id).bind(preparation.usage_day).fetch_one(&pool).await.unwrap();
        assert_eq!(count, 2);
        let billing: (i64, i64) = sqlx::query_as("SELECT count(*)::bigint,count(cost_usd)::bigint FROM vendor_usage_records WHERE task_id=$1::bigint").bind(task_id).fetch_one(&pool).await.unwrap();
        assert_eq!(billing, (2, 1));
        let products: i64 = sqlx::query_scalar(
            "SELECT count(*)::bigint FROM news_filter_decisions WHERE user_id=$1::bigint",
        )
        .bind(user_id)
        .fetch_one(&pool)
        .await
        .unwrap();
        assert_eq!(products, 0);
    }
}
