use std::collections::BTreeMap;

use chrono::{Duration, NaiveDateTime, Utc};
use serde_json::{Value, json};
use sqlx::{FromRow, Postgres, Transaction};
use thiserror::Error;
use url::Url;
use uuid::Uuid;

use super::model::{
    DiscussionApplyOutcome, DiscussionFinalizationPlan, DiscussionMutation, DiscussionPreparation,
    DiscussionSnapshot, DiscussionSummaryMode, FetchedDiscussionArtifact, SummaryPublication,
};

const REFRESH_TTL: Duration = Duration::hours(1);
const ROW_CLAIM_TTL: Duration = Duration::minutes(15);

#[derive(Debug, FromRow)]
struct NewsItemSource {
    id: i64,
    owner_user_id: Option<i64>,
    platform: Option<String>,
    source_external_id: Option<String>,
    canonical_item_url: Option<String>,
    discussion_url: Option<String>,
    raw_metadata: Value,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct DiscussionIdentity {
    platform: String,
    external_id: String,
    discussion_url: String,
    title: Option<String>,
    author: Option<String>,
    score: Option<i32>,
    comment_count: Option<i32>,
}

#[derive(Debug, FromRow)]
struct PreparedDiscussionRow {
    id: i64,
    title: Option<String>,
    author: Option<String>,
    score: Option<i32>,
    comment_count: Option<i32>,
    raw_comments_ref: Option<Value>,
    raw_comments_sha256: Option<String>,
    summary: Option<Value>,
    summary_status: String,
    summary_input_sha256: Option<String>,
    summary_comment_fingerprints: Option<Value>,
    summary_seen_input_sha256: Option<String>,
    summary_incremental_update_count: i32,
    summary_generated_at: Option<NaiveDateTime>,
    last_refresh_status: String,
    next_refresh_after: Option<NaiveDateTime>,
}

/// Claims the per-discussion generation and returns an immutable external-work snapshot.
pub(super) async fn prepare_discussion(
    transaction: &mut Transaction<'_, Postgres>,
    news_item_id: i64,
) -> Result<DiscussionPreparation, DiscussionRepositoryError> {
    let Some(news_item) = sqlx::query_as::<_, NewsItemSource>(
        r"
        SELECT
            id::bigint AS id,
            owner_user_id::bigint AS owner_user_id,
            platform,
            source_external_id,
            canonical_item_url,
            discussion_url,
            COALESCE(raw_metadata, '{}'::json) AS raw_metadata
        FROM news_items
        WHERE id::bigint = $1
        FOR UPDATE
        ",
    )
    .bind(news_item_id)
    .fetch_optional(&mut **transaction)
    .await?
    else {
        return Ok(DiscussionPreparation::NotFound);
    };
    let Some(identity) = resolve_identity(&news_item) else {
        return Ok(DiscussionPreparation::Unsupported);
    };
    let row = upsert_and_lock_discussion(transaction, news_item_id, &identity).await?;
    let now = Utc::now().naive_utc();
    if matches!(row.last_refresh_status.as_str(), "gone" | "unsupported") {
        return Ok(DiscussionPreparation::Terminal);
    }
    let next_in_future = row
        .next_refresh_after
        .is_some_and(|next_refresh| next_refresh > now);
    if next_in_future {
        match row.last_refresh_status.as_str() {
            "processing" | "failed" => {
                return Ok(DiscussionPreparation::Deferred(retry_after_seconds(
                    row.next_refresh_after,
                    now,
                )));
            }
            "completed" if row.raw_comments_ref.is_some() => {
                return Ok(DiscussionPreparation::Fresh);
            }
            _ => {}
        }
    }

    let claim_token = Uuid::new_v4();
    let claim_expires_at = now + ROW_CLAIM_TTL;
    let claimed = sqlx::query(
        r"
        UPDATE news_item_discussions
        SET
            last_refresh_status = 'processing',
            last_refresh_error = NULL,
            next_refresh_after = $3,
            refresh_claim_token = $4,
            updated_at = timezone('UTC', clock_timestamp())
        WHERE id::bigint = $1
          AND news_item_id::bigint = $2
        ",
    )
    .bind(row.id)
    .bind(news_item_id)
    .bind(claim_expires_at)
    .bind(claim_token)
    .execute(&mut **transaction)
    .await?
    .rows_affected();
    if claimed != 1 {
        return Err(DiscussionRepositoryError::ClaimRejected);
    }
    Ok(DiscussionPreparation::Ready(DiscussionSnapshot {
        discussion_id: row.id,
        news_item_id: news_item.id,
        owner_user_id: news_item.owner_user_id,
        platform: identity.platform,
        external_id: identity.external_id,
        discussion_url: identity.discussion_url,
        title: row.title,
        author: row.author,
        score: row.score,
        comment_count: row.comment_count,
        raw_comments_sha256: row.raw_comments_sha256,
        summary: row.summary,
        summary_status: row.summary_status,
        summary_input_sha256: row.summary_input_sha256,
        summary_comment_fingerprints: normalize_fingerprints(
            row.summary_comment_fingerprints.as_ref(),
        ),
        summary_seen_input_sha256: row.summary_seen_input_sha256,
        summary_incremental_update_count: row.summary_incremental_update_count,
        summary_generated_at: row.summary_generated_at,
        claim_token,
    }))
}

async fn upsert_and_lock_discussion(
    transaction: &mut Transaction<'_, Postgres>,
    news_item_id: i64,
    identity: &DiscussionIdentity,
) -> Result<PreparedDiscussionRow, sqlx::Error> {
    sqlx::query_as::<_, PreparedDiscussionRow>(
        r"
        INSERT INTO news_item_discussions (
            news_item_id,
            platform,
            external_id,
            discussion_url,
            title,
            author,
            score,
            comment_count,
            last_count_checked_at,
            summary_status,
            summary_incremental_update_count,
            last_refresh_status,
            created_at,
            updated_at
        )
        VALUES (
            $1::bigint::integer, $2, $3, $4, $5, $6, $7, $8,
            timezone('UTC', clock_timestamp()), 'not_ready', 0, 'pending',
            timezone('UTC', clock_timestamp()), timezone('UTC', clock_timestamp())
        )
        ON CONFLICT (news_item_id) DO UPDATE SET
            platform = EXCLUDED.platform,
            external_id = EXCLUDED.external_id,
            discussion_url = EXCLUDED.discussion_url,
            title = COALESCE(EXCLUDED.title, news_item_discussions.title),
            author = COALESCE(EXCLUDED.author, news_item_discussions.author),
            score = COALESCE(EXCLUDED.score, news_item_discussions.score),
            comment_count = COALESCE(EXCLUDED.comment_count, news_item_discussions.comment_count),
            last_count_checked_at = EXCLUDED.last_count_checked_at,
            raw_comments_ref = CASE WHEN
                news_item_discussions.platform IS DISTINCT FROM EXCLUDED.platform OR
                news_item_discussions.external_id IS DISTINCT FROM EXCLUDED.external_id OR
                news_item_discussions.discussion_url IS DISTINCT FROM EXCLUDED.discussion_url
                THEN NULL ELSE news_item_discussions.raw_comments_ref END,
            raw_comments_sha256 = CASE WHEN
                news_item_discussions.platform IS DISTINCT FROM EXCLUDED.platform OR
                news_item_discussions.external_id IS DISTINCT FROM EXCLUDED.external_id OR
                news_item_discussions.discussion_url IS DISTINCT FROM EXCLUDED.discussion_url
                THEN NULL ELSE news_item_discussions.raw_comments_sha256 END,
            summary = CASE WHEN
                news_item_discussions.platform IS DISTINCT FROM EXCLUDED.platform OR
                news_item_discussions.external_id IS DISTINCT FROM EXCLUDED.external_id OR
                news_item_discussions.discussion_url IS DISTINCT FROM EXCLUDED.discussion_url
                THEN NULL ELSE news_item_discussions.summary END,
            summary_status = CASE WHEN
                news_item_discussions.platform IS DISTINCT FROM EXCLUDED.platform OR
                news_item_discussions.external_id IS DISTINCT FROM EXCLUDED.external_id OR
                news_item_discussions.discussion_url IS DISTINCT FROM EXCLUDED.discussion_url
                THEN 'not_ready' ELSE news_item_discussions.summary_status END,
            summary_input_sha256 = CASE WHEN
                news_item_discussions.platform IS DISTINCT FROM EXCLUDED.platform OR
                news_item_discussions.external_id IS DISTINCT FROM EXCLUDED.external_id OR
                news_item_discussions.discussion_url IS DISTINCT FROM EXCLUDED.discussion_url
                THEN NULL ELSE news_item_discussions.summary_input_sha256 END,
            summary_comment_count = CASE WHEN
                news_item_discussions.platform IS DISTINCT FROM EXCLUDED.platform OR
                news_item_discussions.external_id IS DISTINCT FROM EXCLUDED.external_id OR
                news_item_discussions.discussion_url IS DISTINCT FROM EXCLUDED.discussion_url
                THEN NULL ELSE news_item_discussions.summary_comment_count END,
            summary_comment_fingerprints = CASE WHEN
                news_item_discussions.platform IS DISTINCT FROM EXCLUDED.platform OR
                news_item_discussions.external_id IS DISTINCT FROM EXCLUDED.external_id OR
                news_item_discussions.discussion_url IS DISTINCT FROM EXCLUDED.discussion_url
                THEN NULL ELSE news_item_discussions.summary_comment_fingerprints END,
            summary_seen_input_sha256 = CASE WHEN
                news_item_discussions.platform IS DISTINCT FROM EXCLUDED.platform OR
                news_item_discussions.external_id IS DISTINCT FROM EXCLUDED.external_id OR
                news_item_discussions.discussion_url IS DISTINCT FROM EXCLUDED.discussion_url
                THEN NULL ELSE news_item_discussions.summary_seen_input_sha256 END,
            summary_seen_comment_count = CASE WHEN
                news_item_discussions.platform IS DISTINCT FROM EXCLUDED.platform OR
                news_item_discussions.external_id IS DISTINCT FROM EXCLUDED.external_id OR
                news_item_discussions.discussion_url IS DISTINCT FROM EXCLUDED.discussion_url
                THEN NULL ELSE news_item_discussions.summary_seen_comment_count END,
            summary_seen_comment_fingerprints = CASE WHEN
                news_item_discussions.platform IS DISTINCT FROM EXCLUDED.platform OR
                news_item_discussions.external_id IS DISTINCT FROM EXCLUDED.external_id OR
                news_item_discussions.discussion_url IS DISTINCT FROM EXCLUDED.discussion_url
                THEN NULL ELSE news_item_discussions.summary_seen_comment_fingerprints END,
            summary_incremental_update_count = CASE WHEN
                news_item_discussions.platform IS DISTINCT FROM EXCLUDED.platform OR
                news_item_discussions.external_id IS DISTINCT FROM EXCLUDED.external_id OR
                news_item_discussions.discussion_url IS DISTINCT FROM EXCLUDED.discussion_url
                THEN 0 ELSE news_item_discussions.summary_incremental_update_count END,
            summary_generated_at = CASE WHEN
                news_item_discussions.platform IS DISTINCT FROM EXCLUDED.platform OR
                news_item_discussions.external_id IS DISTINCT FROM EXCLUDED.external_id OR
                news_item_discussions.discussion_url IS DISTINCT FROM EXCLUDED.discussion_url
                THEN NULL ELSE news_item_discussions.summary_generated_at END,
            next_refresh_after = CASE WHEN
                news_item_discussions.platform IS DISTINCT FROM EXCLUDED.platform OR
                news_item_discussions.external_id IS DISTINCT FROM EXCLUDED.external_id OR
                news_item_discussions.discussion_url IS DISTINCT FROM EXCLUDED.discussion_url
                THEN NULL ELSE news_item_discussions.next_refresh_after END,
            last_refresh_status = CASE WHEN
                news_item_discussions.platform IS DISTINCT FROM EXCLUDED.platform OR
                news_item_discussions.external_id IS DISTINCT FROM EXCLUDED.external_id OR
                news_item_discussions.discussion_url IS DISTINCT FROM EXCLUDED.discussion_url
                THEN 'pending' ELSE news_item_discussions.last_refresh_status END,
            last_refresh_error = CASE WHEN
                news_item_discussions.platform IS DISTINCT FROM EXCLUDED.platform OR
                news_item_discussions.external_id IS DISTINCT FROM EXCLUDED.external_id OR
                news_item_discussions.discussion_url IS DISTINCT FROM EXCLUDED.discussion_url
                THEN NULL ELSE news_item_discussions.last_refresh_error END,
            updated_at = timezone('UTC', clock_timestamp())
        RETURNING
            id::bigint AS id,
            title,
            author,
            score,
            comment_count,
            raw_comments_ref,
            raw_comments_sha256,
            summary,
            summary_status,
            summary_input_sha256,
            summary_comment_fingerprints,
            summary_seen_input_sha256,
            summary_incremental_update_count,
            summary_generated_at,
            last_refresh_status,
            next_refresh_after
        ",
    )
    .bind(news_item_id)
    .bind(&identity.platform)
    .bind(&identity.external_id)
    .bind(&identity.discussion_url)
    .bind(&identity.title)
    .bind(&identity.author)
    .bind(identity.score)
    .bind(identity.comment_count)
    .fetch_one(&mut **transaction)
    .await
}

#[derive(Debug, FromRow)]
struct LockedClaim {
    platform: String,
    external_id: Option<String>,
    discussion_url: Option<String>,
    last_refresh_status: String,
    next_refresh_after: Option<NaiveDateTime>,
    refresh_claim_token: Option<Uuid>,
}

/// Publishes the product mutation only while both the queue lease and per-row generation are live.
pub(super) async fn apply_discussion_mutation(
    transaction: &mut Transaction<'static, Postgres>,
    plan: &DiscussionFinalizationPlan,
) -> Result<DiscussionApplyOutcome, DiscussionRepositoryError> {
    let news_exists = sqlx::query_scalar::<_, i64>(
        "SELECT id::bigint FROM news_items WHERE id::bigint = $1 FOR KEY SHARE",
    )
    .bind(plan.snapshot.news_item_id)
    .fetch_optional(&mut **transaction)
    .await?
    .is_some();
    if !news_exists {
        return Ok(DiscussionApplyOutcome::NewsItemMissing);
    }
    let Some(claim) = sqlx::query_as::<_, LockedClaim>(
        r"
        SELECT
            platform,
            external_id,
            discussion_url,
            last_refresh_status,
            next_refresh_after,
            refresh_claim_token
        FROM news_item_discussions
        WHERE id::bigint = $1 AND news_item_id::bigint = $2
        FOR UPDATE
        ",
    )
    .bind(plan.snapshot.discussion_id)
    .bind(plan.snapshot.news_item_id)
    .fetch_optional(&mut **transaction)
    .await?
    else {
        return Ok(DiscussionApplyOutcome::ClaimLost {
            retry_after_seconds: 1,
        });
    };
    if claim.last_refresh_status != "processing"
        || claim.refresh_claim_token != Some(plan.snapshot.claim_token)
    {
        return Ok(DiscussionApplyOutcome::ClaimLost {
            retry_after_seconds: retry_after_seconds(
                claim.next_refresh_after,
                Utc::now().naive_utc(),
            ),
        });
    }
    if claim.platform != plan.snapshot.platform
        || claim.external_id.as_deref() != Some(plan.snapshot.external_id.as_str())
        || claim.discussion_url.as_deref() != Some(plan.snapshot.discussion_url.as_str())
    {
        release_changed_identity(transaction, plan).await?;
        return Ok(DiscussionApplyOutcome::IdentityChanged);
    }

    match &plan.mutation {
        DiscussionMutation::Completed { fetched, summary } => {
            apply_fetched_state(transaction, plan, fetched).await?;
            apply_summary_publication(transaction, plan, summary).await?;
            complete_refresh(transaction, plan, fetched).await?;
        }
        DiscussionMutation::Failed { reason, fetched } => {
            if let Some(fetched) = fetched {
                apply_fetched_state(transaction, plan, fetched).await?;
            }
            fail_refresh(transaction, plan, reason, fetched.is_some()).await?;
        }
        DiscussionMutation::Terminal { status, reason } => {
            terminal_refresh(transaction, plan, status, reason).await?;
        }
    }
    Ok(DiscussionApplyOutcome::Applied)
}

async fn apply_fetched_state(
    transaction: &mut Transaction<'static, Postgres>,
    plan: &DiscussionFinalizationPlan,
    fetched: &FetchedDiscussionArtifact,
) -> Result<(), sqlx::Error> {
    sqlx::query(
        r"
        UPDATE news_item_discussions
        SET
            title = COALESCE($3, title),
            author = COALESCE($4, author),
            score = COALESCE($5, score),
            comment_count = COALESCE($6, comment_count),
            fetched_comment_count = $7,
            last_count_checked_at = $8,
            last_comments_fetched_at = $8,
            raw_comments_ref = $9,
            raw_comments_sha256 = $10,
            updated_at = timezone('UTC', clock_timestamp())
        WHERE id::bigint = $1
          AND news_item_id::bigint = $2
          AND refresh_claim_token = $11
          AND last_refresh_status = 'processing'
        ",
    )
    .bind(plan.snapshot.discussion_id)
    .bind(plan.snapshot.news_item_id)
    .bind(&fetched.title)
    .bind(&fetched.author)
    .bind(fetched.score)
    .bind(fetched.declared_comment_count)
    .bind(fetched.fetched_comment_count)
    .bind(fetched.fetched_at.naive_utc())
    .bind(fetched.pointer.to_json())
    .bind(&fetched.pointer.sha256)
    .bind(plan.snapshot.claim_token)
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

async fn apply_summary_publication(
    transaction: &mut Transaction<'static, Postgres>,
    plan: &DiscussionFinalizationPlan,
    publication: &SummaryPublication,
) -> Result<(), DiscussionRepositoryError> {
    match publication {
        SummaryPublication::Preserve => {}
        SummaryPublication::NotReady => {
            sqlx::query(
                r"
                UPDATE news_item_discussions
                SET summary_status = CASE WHEN summary IS NULL THEN 'not_ready' ELSE summary_status END
                WHERE id::bigint = $1 AND refresh_claim_token = $2
                ",
            )
            .bind(plan.snapshot.discussion_id)
            .bind(plan.snapshot.claim_token)
            .execute(&mut **transaction)
            .await?;
        }
        SummaryPublication::TrackSummarized { input } => {
            store_summarized_tracking(
                transaction,
                plan,
                input,
                plan.snapshot.summary_incremental_update_count,
            )
            .await?;
            store_seen_tracking(transaction, plan, input).await?;
        }
        SummaryPublication::TrackSeen { input } => {
            store_seen_tracking(transaction, plan, input).await?;
        }
        SummaryPublication::Generated {
            input,
            summary,
            model,
            mode,
            usage,
        } => {
            let incremental_updates = if *mode == DiscussionSummaryMode::Merge {
                plan.snapshot
                    .summary_incremental_update_count
                    .saturating_add(1)
            } else {
                0
            };
            sqlx::query(
                r"
                UPDATE news_item_discussions
                SET
                    summary = $3,
                    summary_status = 'completed',
                    summary_version = 1,
                    summary_model = $4,
                    summary_generated_at = $5
                WHERE id::bigint = $1
                  AND refresh_claim_token = $2
                  AND last_refresh_status = 'processing'
                ",
            )
            .bind(plan.snapshot.discussion_id)
            .bind(plan.snapshot.claim_token)
            .bind(summary)
            .bind(truncate_chars(model, 100))
            .bind(plan.finalized_at.naive_utc())
            .execute(&mut **transaction)
            .await?;
            store_summarized_tracking(transaction, plan, input, incremental_updates).await?;
            store_seen_tracking(transaction, plan, input).await?;
            persist_usage(transaction, plan, usage).await?;
            bump_briefing_versions(transaction, plan.snapshot.news_item_id).await?;
        }
    }
    Ok(())
}

async fn store_summarized_tracking(
    transaction: &mut Transaction<'static, Postgres>,
    plan: &DiscussionFinalizationPlan,
    input: &super::model::DiscussionSummaryInput,
    incremental_update_count: i32,
) -> Result<(), sqlx::Error> {
    sqlx::query(
        r"
        UPDATE news_item_discussions
        SET
            summary_input_sha256 = $3,
            summary_comment_count = $4,
            summary_comment_fingerprints = $5,
            summary_incremental_update_count = $6
        WHERE id::bigint = $1 AND refresh_claim_token = $2
        ",
    )
    .bind(plan.snapshot.discussion_id)
    .bind(plan.snapshot.claim_token)
    .bind(&input.input_sha256)
    .bind(input.comment_count)
    .bind(serde_json::to_value(&input.comment_fingerprints).unwrap_or_else(|_| json!({})))
    .bind(incremental_update_count)
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

async fn store_seen_tracking(
    transaction: &mut Transaction<'static, Postgres>,
    plan: &DiscussionFinalizationPlan,
    input: &super::model::DiscussionSummaryInput,
) -> Result<(), sqlx::Error> {
    sqlx::query(
        r"
        UPDATE news_item_discussions
        SET
            summary_seen_input_sha256 = $3,
            summary_seen_comment_count = $4,
            summary_seen_comment_fingerprints = $5
        WHERE id::bigint = $1 AND refresh_claim_token = $2
        ",
    )
    .bind(plan.snapshot.discussion_id)
    .bind(plan.snapshot.claim_token)
    .bind(&input.input_sha256)
    .bind(input.comment_count)
    .bind(serde_json::to_value(&input.comment_fingerprints).unwrap_or_else(|_| json!({})))
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

async fn complete_refresh(
    transaction: &mut Transaction<'static, Postgres>,
    plan: &DiscussionFinalizationPlan,
    fetched: &FetchedDiscussionArtifact,
) -> Result<(), sqlx::Error> {
    sqlx::query(
        r"
        UPDATE news_item_discussions
        SET
            last_refresh_status = 'completed',
            last_refresh_error = NULL,
            next_refresh_after = $3,
            refresh_claim_token = NULL,
            updated_at = timezone('UTC', clock_timestamp())
        WHERE id::bigint = $1 AND refresh_claim_token = $2
        ",
    )
    .bind(plan.snapshot.discussion_id)
    .bind(plan.snapshot.claim_token)
    .bind((fetched.fetched_at + REFRESH_TTL).naive_utc())
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

async fn fail_refresh(
    transaction: &mut Transaction<'static, Postgres>,
    plan: &DiscussionFinalizationPlan,
    reason: &str,
    summary_failed: bool,
) -> Result<(), sqlx::Error> {
    sqlx::query(
        r"
        UPDATE news_item_discussions
        SET
            summary_status = CASE
                WHEN $3 THEN 'failed'
                WHEN summary IS NULL THEN 'failed'
                ELSE summary_status
            END,
            last_refresh_status = 'failed',
            last_refresh_error = $4,
            next_refresh_after = $5,
            refresh_claim_token = NULL,
            updated_at = timezone('UTC', clock_timestamp())
        WHERE id::bigint = $1 AND refresh_claim_token = $2
        ",
    )
    .bind(plan.snapshot.discussion_id)
    .bind(plan.snapshot.claim_token)
    .bind(summary_failed)
    .bind(truncate_chars(reason, 2_000))
    .bind((plan.finalized_at + REFRESH_TTL).naive_utc())
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

async fn terminal_refresh(
    transaction: &mut Transaction<'static, Postgres>,
    plan: &DiscussionFinalizationPlan,
    status: &str,
    reason: &str,
) -> Result<(), sqlx::Error> {
    sqlx::query(
        r"
        UPDATE news_item_discussions
        SET
            summary_status = CASE WHEN summary IS NULL THEN $3 ELSE summary_status END,
            last_refresh_status = $3,
            last_refresh_error = $4,
            next_refresh_after = NULL,
            refresh_claim_token = NULL,
            updated_at = timezone('UTC', clock_timestamp())
        WHERE id::bigint = $1 AND refresh_claim_token = $2
        ",
    )
    .bind(plan.snapshot.discussion_id)
    .bind(plan.snapshot.claim_token)
    .bind(status)
    .bind(truncate_chars(reason, 2_000))
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

async fn release_changed_identity(
    transaction: &mut Transaction<'static, Postgres>,
    plan: &DiscussionFinalizationPlan,
) -> Result<(), sqlx::Error> {
    sqlx::query(
        r"
        UPDATE news_item_discussions
        SET
            last_refresh_status = 'pending',
            last_refresh_error = 'discussion identity changed during refresh',
            next_refresh_after = timezone('UTC', clock_timestamp()),
            refresh_claim_token = NULL,
            updated_at = timezone('UTC', clock_timestamp())
        WHERE id::bigint = $1 AND refresh_claim_token = $2
        ",
    )
    .bind(plan.snapshot.discussion_id)
    .bind(plan.snapshot.claim_token)
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

async fn persist_usage(
    transaction: &mut Transaction<'static, Postgres>,
    plan: &DiscussionFinalizationPlan,
    usage: &super::model::DiscussionUsage,
) -> Result<(), sqlx::Error> {
    let total_tokens = usage
        .usage
        .input_tokens
        .saturating_add(usage.usage.output_tokens);
    let operation = if usage.summary_mode == DiscussionSummaryMode::Merge {
        "news_discussions.merge_summary"
    } else {
        "news_discussions.summarize"
    };
    sqlx::query(
        r"
        INSERT INTO vendor_usage_records (
            provider,
            model,
            feature,
            operation,
            source,
            request_id,
            task_id,
            user_id,
            input_tokens,
            cache_read_tokens,
            cache_write_tokens,
            output_tokens,
            total_tokens,
            request_count,
            currency,
            metadata,
            created_at
        )
        VALUES (
            $1, $2, 'news_discussions', $3, 'discussion_scraper', $4, $5,
            (SELECT id FROM users WHERE id::bigint = $6 AND is_active IS TRUE),
            $7, $8, $9, $10, $11, $12, 'USD', $13,
            timezone('UTC', clock_timestamp())
        )
        ",
    )
    .bind(&usage.provider)
    .bind(&usage.model)
    .bind(operation)
    .bind(&usage.provider_response_id)
    .bind(plan.task_id)
    .bind(plan.snapshot.owner_user_id)
    .bind(saturating_i32(usage.usage.input_tokens))
    .bind(saturating_i32(usage.usage.cached_input_tokens))
    .bind(saturating_i32(usage.usage.cache_write_tokens))
    .bind(saturating_i32(usage.usage.output_tokens))
    .bind(saturating_i32(total_tokens))
    .bind(saturating_i32(usage.usage.request_count))
    .bind(json!({
        "news_item_id": plan.snapshot.news_item_id,
        "news_item_discussion_id": plan.snapshot.discussion_id,
        "platform": plan.snapshot.platform,
        "summary_mode": usage.summary_mode.usage_label(),
        "summary_input_sha256": usage.summary_input_sha256,
        "summary_comment_count": usage.summary_comment_count,
        "changed_comment_count": usage.changed_comment_count,
        "reasoning_tokens": usage.usage.reasoning_tokens,
    }))
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

async fn bump_briefing_versions(
    transaction: &mut Transaction<'static, Postgres>,
    news_item_id: i64,
) -> Result<(), sqlx::Error> {
    sqlx::query(
        r"
        UPDATE briefing_states AS state
        SET version = state.version + 1
        WHERE EXISTS (
            SELECT 1
            FROM briefing_segments AS segment
            JOIN users ON users.id = segment.user_id AND users.is_active IS TRUE
            WHERE segment.user_id = state.user_id
              AND segment.status IN ('active', 'degraded')
              AND segment.source_keys::jsonb @>
                  jsonb_build_array('news:' || $1::bigint::text)
        )
        ",
    )
    .bind(news_item_id)
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

fn resolve_identity(news_item: &NewsItemSource) -> Option<DiscussionIdentity> {
    let metadata = news_item.raw_metadata.as_object();
    let mut platform = clean_string(news_item.platform.as_deref())
        .or_else(|| metadata.and_then(|value| clean_value(value.get("platform"))))
        .unwrap_or_default()
        .to_ascii_lowercase();
    if platform == "hn" || platform == "hacker_news" {
        "hackernews".clone_into(&mut platform);
    }
    let mut discussion_url = news_item
        .discussion_url
        .as_deref()
        .and_then(normalize_http_url)
        .or_else(|| {
            news_item
                .canonical_item_url
                .as_deref()
                .and_then(normalize_http_url)
        });
    if platform == "reddit" {
        discussion_url = discussion_url.and_then(|value| normalize_reddit_url(&value));
    }
    if !matches!(platform.as_str(), "hackernews" | "reddit") {
        platform = infer_platform(discussion_url.as_deref())?;
        if platform == "reddit" {
            discussion_url = discussion_url.and_then(|value| normalize_reddit_url(&value));
        }
    }
    let discussion_url = discussion_url?;
    let aggregator = metadata
        .and_then(|value| value.get("aggregator"))
        .and_then(Value::as_object);
    let external_id = clean_string(news_item.source_external_id.as_deref())
        .or_else(|| aggregator.and_then(|value| clean_value(value.get("external_id"))))
        .or_else(|| metadata.and_then(|value| clean_value(value.get("source_external_id"))))
        .or_else(|| match platform.as_str() {
            "hackernews" => hacker_news_id(&discussion_url),
            "reddit" => reddit_submission_id(&discussion_url),
            _ => None,
        })?;
    let title = metadata
        .and_then(|value| nested_clean(value, &["summary", "title"]))
        .or_else(|| metadata.and_then(|value| nested_clean(value, &["article", "title"])))
        .or_else(|| aggregator.and_then(|value| clean_value(value.get("title"))));
    let author = aggregator.and_then(|value| clean_value(value.get("author")));
    let score = metadata
        .and_then(|value| nonnegative_i32(value.get("score")))
        .or_else(|| aggregator.and_then(|value| nonnegative_i32(value.get("score"))));
    let comment_count = metadata
        .and_then(|value| nonnegative_i32(value.get("comment_count")))
        .or_else(|| metadata.and_then(|value| nonnegative_i32(value.get("comments_count"))))
        .or_else(|| {
            aggregator
                .and_then(|value| value.get("metadata"))
                .and_then(Value::as_object)
                .and_then(|value| {
                    nonnegative_i32(value.get("comments_count"))
                        .or_else(|| nonnegative_i32(value.get("comment_count")))
                })
        });
    Some(DiscussionIdentity {
        platform,
        external_id,
        discussion_url,
        title,
        author,
        score,
        comment_count,
    })
}

fn normalize_http_url(value: &str) -> Option<String> {
    let value = value.trim();
    if value.is_empty() || value.starts_with(['/', '?', '#']) {
        return None;
    }
    let candidate = if value.starts_with("//") {
        format!("https:{value}")
    } else if value.contains("://") {
        value.to_owned()
    } else {
        format!("https://{value}")
    };
    let mut url = Url::parse(&candidate).ok()?;
    if !matches!(url.scheme(), "http" | "https") || url.host_str().is_none() {
        return None;
    }
    url.set_scheme("https").ok()?;
    Some(url.to_string())
}

fn normalize_reddit_url(value: &str) -> Option<String> {
    let mut url = Url::parse(value).ok()?;
    let host = url.host_str()?.to_ascii_lowercase();
    if matches!(
        host.as_str(),
        "reddit.com" | "old.reddit.com" | "www.reddit.com"
    ) {
        url.set_host(Some("www.reddit.com")).ok()?;
    }
    Some(url.to_string())
}

fn infer_platform(url: Option<&str>) -> Option<String> {
    let host = Url::parse(url?).ok()?.host_str()?.to_ascii_lowercase();
    if domain_matches(&host, "news.ycombinator.com") {
        Some("hackernews".to_owned())
    } else if domain_matches(&host, "reddit.com") || domain_matches(&host, "redd.it") {
        Some("reddit".to_owned())
    } else {
        None
    }
}

fn hacker_news_id(value: &str) -> Option<String> {
    let url = Url::parse(value).ok()?;
    url.query_pairs()
        .find(|(name, _)| name == "id")
        .map(|(_, value)| value.into_owned())
        .filter(|value| !value.is_empty())
        .or_else(|| {
            let parts = url.path_segments()?.collect::<Vec<_>>();
            let value = parts.last()?.strip_suffix(".json")?;
            value
                .chars()
                .all(|character| character.is_ascii_digit())
                .then(|| value.to_owned())
        })
}

fn reddit_submission_id(value: &str) -> Option<String> {
    let url = Url::parse(value).ok()?;
    let parts = url.path_segments()?.collect::<Vec<_>>();
    parts
        .windows(2)
        .find(|window| window[0].eq_ignore_ascii_case("comments"))
        .map(|window| window[1].to_ascii_lowercase())
        .filter(|value| !value.is_empty())
}

fn domain_matches(host: &str, domain: &str) -> bool {
    host == domain || host.ends_with(&format!(".{domain}"))
}

fn normalize_fingerprints(value: Option<&Value>) -> Option<BTreeMap<String, String>> {
    let object = value?.as_object()?;
    Some(
        object
            .iter()
            .filter_map(|(key, value)| {
                clean_value(Some(value)).map(|fingerprint| (key.clone(), fingerprint))
            })
            .collect(),
    )
}

fn nested_clean(object: &serde_json::Map<String, Value>, path: &[&str]) -> Option<String> {
    let mut current = Value::Object(object.clone());
    for key in path {
        current = current.get(*key)?.clone();
    }
    clean_value(Some(&current))
}

fn clean_value(value: Option<&Value>) -> Option<String> {
    clean_string(value?.as_str())
}

fn clean_string(value: Option<&str>) -> Option<String> {
    let cleaned = value?.split_whitespace().collect::<Vec<_>>().join(" ");
    (!cleaned.is_empty()).then_some(cleaned)
}

fn nonnegative_i32(value: Option<&Value>) -> Option<i32> {
    let value = value?;
    let parsed = value
        .as_i64()
        .or_else(|| value.as_str()?.trim().replace(',', "").parse::<i64>().ok())?;
    i32::try_from(parsed).ok().filter(|value| *value >= 0)
}

fn retry_after_seconds(next_refresh: Option<NaiveDateTime>, now: NaiveDateTime) -> i64 {
    next_refresh
        .map_or(1, |next| (next - now).num_seconds().saturating_add(1))
        .clamp(1, REFRESH_TTL.num_seconds())
}

fn truncate_chars(value: &str, limit: usize) -> String {
    value.chars().take(limit).collect()
}

fn saturating_i32(value: u64) -> i32 {
    i32::try_from(value).unwrap_or(i32::MAX)
}

#[derive(Debug, Error)]
pub(super) enum DiscussionRepositoryError {
    #[error("PostgreSQL discussion operation failed")]
    Sqlx(#[from] sqlx::Error),
    #[error("the prepared discussion row could not be claimed")]
    ClaimRejected,
}

#[cfg(test)]
mod tests {
    use super::*;

    fn source(
        platform: Option<&str>,
        url: Option<&str>,
        external_id: Option<&str>,
    ) -> NewsItemSource {
        NewsItemSource {
            id: 1,
            owner_user_id: None,
            platform: platform.map(str::to_owned),
            source_external_id: external_id.map(str::to_owned),
            canonical_item_url: None,
            discussion_url: url.map(str::to_owned),
            raw_metadata: json!({}),
        }
    }

    #[test]
    fn normalizes_hn_alias_and_extracts_identity() {
        let identity = resolve_identity(&source(
            Some("HN"),
            Some("http://news.ycombinator.com/item?id=123"),
            None,
        ))
        .unwrap();
        assert_eq!(identity.platform, "hackernews");
        assert_eq!(identity.external_id, "123");
        assert!(identity.discussion_url.starts_with("https://"));
    }

    #[test]
    fn infers_and_canonicalizes_reddit() {
        let identity = resolve_identity(&source(
            None,
            Some("https://old.reddit.com/r/rust/comments/AbC123/example"),
            None,
        ))
        .unwrap();
        assert_eq!(identity.platform, "reddit");
        assert_eq!(identity.external_id, "abc123");
        assert_eq!(
            Url::parse(&identity.discussion_url).unwrap().host_str(),
            Some("www.reddit.com")
        );
    }

    #[test]
    fn rejects_unrelated_discussion_source() {
        assert!(
            resolve_identity(&source(None, Some("https://example.com/thread"), None)).is_none()
        );
    }
}
