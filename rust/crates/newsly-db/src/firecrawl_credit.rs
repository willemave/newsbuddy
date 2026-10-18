//! Shared credit pause and one leased recovery call across worker processes.
use chrono::{DateTime, Utc};
use sqlx::PgPool;
use uuid::Uuid;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum FirecrawlAdmission {
    Allowed { probe_token: Option<Uuid> },
    Paused { resume_at: DateTime<Utc> },
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum FirecrawlCreditTransition {
    Unchanged,
    Paused { resume_at: DateTime<Utc> },
    Recovered,
}

#[derive(sqlx::FromRow)]
struct CreditState {
    retry_after: Option<DateTime<Utc>>,
    probe_token: Option<Uuid>,
    probe_expires_at: Option<DateTime<Utc>>,
    observed_at: DateTime<Utc>,
}

const LOCK_STATE: &str = "SELECT retry_after, probe_token, probe_expires_at,
    clock_timestamp() AS observed_at FROM firecrawl_credit_state WHERE singleton FOR UPDATE";

/// Reserve admission immediately before dispatch. Commit releases all DB handles before HTTP.
/// A paused account admits exactly one ordinary task after cooldown; abandoned probes expire.
pub async fn acquire_firecrawl_admission(pool: &PgPool) -> Result<FirecrawlAdmission, sqlx::Error> {
    let mut transaction = pool.begin().await?;
    let state: CreditState = sqlx::query_as(LOCK_STATE)
        .fetch_one(&mut *transaction)
        .await?;
    let admission = if let Some(retry_after) = state.retry_after {
        let resume_at = state
            .probe_expires_at
            .map_or(retry_after, |expires| expires.max(retry_after));
        if resume_at > state.observed_at {
            FirecrawlAdmission::Paused { resume_at }
        } else {
            let token = Uuid::new_v4();
            sqlx::query(
                "UPDATE firecrawl_credit_state SET probe_token = $1,
                probe_expires_at = clock_timestamp() + interval '6 minutes',
                updated_at = clock_timestamp() WHERE singleton",
            )
            .bind(token)
            .execute(&mut *transaction)
            .await?;
            FirecrawlAdmission::Allowed {
                probe_token: Some(token),
            }
        }
    } else {
        FirecrawlAdmission::Allowed { probe_token: None }
    };
    transaction.commit().await?;
    Ok(admission)
}

/// Observe each completed call once. Status zero represents a transport/local failure.
/// Only a matching, unexpired probe's 2xx response can recover the gate. Replacing or
/// invalidating its random token fences late results without a process-local generation.
pub async fn observe_firecrawl_response(
    pool: &PgPool,
    probe_token: Option<Uuid>,
    status: u16,
) -> Result<FirecrawlCreditTransition, sqlx::Error> {
    let mut transaction = pool.begin().await?;
    let state: CreditState = sqlx::query_as(LOCK_STATE)
        .fetch_one(&mut *transaction)
        .await?;
    let matching_probe = probe_token.is_some()
        && probe_token == state.probe_token
        && state
            .probe_expires_at
            .is_some_and(|expiry| expiry > state.observed_at);
    if probe_token.is_some() && !matching_probe {
        transaction.commit().await?;
        return Ok(FirecrawlCreditTransition::Unchanged);
    }
    let transition = if status == 402 || matching_probe && !(200..300).contains(&status) {
        // In-flight ordinary 402s invalidate a recovery probe, but do not continually
        // extend an already closed cooldown. Signal changes share this transaction.
        let resume_at: DateTime<Utc> = sqlx::query_scalar(
            "UPDATE firecrawl_credit_state
            SET retry_after = CASE WHEN retry_after IS NULL OR probe_token IS NOT NULL
                THEN clock_timestamp() + interval '15 minutes' ELSE retry_after END,
                probe_token = NULL, probe_expires_at = NULL, updated_at = clock_timestamp()
            WHERE singleton RETURNING retry_after",
        )
        .fetch_one(&mut *transaction)
        .await?;
        if state.retry_after.is_none() {
            crate::pipeline_monitoring::set_signal(&mut transaction, "firecrawl_credits_exhausted", true,
                "Firecrawl reported HTTP 402: shared credit pause active; queued work will probe recovery after cooldown")
                .await?;
        }
        FirecrawlCreditTransition::Paused { resume_at }
    } else if matching_probe {
        sqlx::query(
            "UPDATE firecrawl_credit_state SET retry_after = NULL,
            probe_token = NULL, probe_expires_at = NULL, updated_at = clock_timestamp()
            WHERE singleton",
        )
        .execute(&mut *transaction)
        .await?;
        crate::pipeline_monitoring::set_signal(
            &mut transaction,
            "firecrawl_credits_exhausted",
            false,
            "Firecrawl recovery probe succeeded: shared credit pause cleared",
        )
        .await?;
        FirecrawlCreditTransition::Recovered
    } else {
        FirecrawlCreditTransition::Unchanged
    };
    transaction.commit().await?;
    Ok(transition)
}

#[cfg(test)]
mod tests;
