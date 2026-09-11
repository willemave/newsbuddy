//! Vectors for the exact text consumed by personalized news lens assignment.
use crate::BriefingRefreshSource;
use chrono::NaiveDateTime;
use serde_json::Value;
use sha2::{Digest, Sha256};
use sqlx::{FromRow, PgConnection};

pub const ENCODER_VERSION: i32 = 1;

pub fn input_hash(text: &str) -> String {
    use std::fmt::Write;
    let mut output = String::with_capacity(64);
    for byte in Sha256::digest(text.as_bytes()) {
        write!(output, "{byte:02x}").expect("writing to String cannot fail");
    }
    output
}

#[derive(Debug, Clone)]
pub struct PreparedNewsEmbedding {
    pub news_item_id: i64,
    pub model: String,
    pub input_hash: String,
    pub vector: Vec<f64>,
}

pub async fn load(
    connection: &mut PgConnection,
    id: i64,
    model: &str,
    text: &str,
) -> Result<Option<Vec<f64>>, sqlx::Error> {
    let value: Option<Value> = sqlx::query_scalar("SELECT vector FROM news_lens_embeddings WHERE news_item_id::bigint = $1 AND model = $2 AND input_hash = $3 AND encoder_version = $4 AND dimensions = jsonb_array_length(vector)")
        .bind(id).bind(model).bind(input_hash(text)).bind(ENCODER_VERSION).fetch_optional(connection).await?;
    Ok(value
        .and_then(|v| serde_json::from_value::<Vec<f64>>(v).ok())
        .filter(|v| {
            !v.is_empty() && v.iter().all(|n| n.is_finite()) && v.iter().any(|n| *n != 0.0)
        }))
}

pub async fn save(
    connection: &mut PgConnection,
    embedding: &PreparedNewsEmbedding,
) -> Result<bool, sqlx::Error> {
    let Some(source) = source(connection, embedding.news_item_id).await? else {
        return Ok(false);
    };
    if input_hash(&source.embedding_text()) != embedding.input_hash {
        return Ok(false);
    }
    if embedding.vector.is_empty()
        || embedding.vector.iter().any(|v| !v.is_finite())
        || !embedding.vector.iter().any(|v| *v != 0.0)
    {
        return Ok(false);
    }
    let dimensions =
        i32::try_from(embedding.vector.len()).map_err(|e| sqlx::Error::Decode(Box::new(e)))?;
    // Refresh the observation without rewriting a matching (potentially TOASTed) vector.
    let touched = sqlx::query("UPDATE news_lens_embeddings SET checked_at=now() WHERE news_item_id::bigint=$1 AND model=$2 AND input_hash=$3 AND encoder_version=$4 AND dimensions=$5")
        .bind(embedding.news_item_id).bind(&embedding.model).bind(&embedding.input_hash)
        .bind(ENCODER_VERSION).bind(dimensions).execute(&mut *connection).await?.rows_affected();
    if touched > 0 {
        return Ok(true);
    }
    sqlx::query("INSERT INTO news_lens_embedding_models(model,dimensions) VALUES ($1,$2) ON CONFLICT DO NOTHING")
        .bind(&embedding.model).bind(dimensions).execute(&mut *connection).await?;
    sqlx::query("INSERT INTO news_lens_embeddings (news_item_id, model, input_hash, encoder_version, dimensions, vector) VALUES ($1::bigint::integer,$2,$3,$4,$5,$6) ON CONFLICT (news_item_id,model) DO UPDATE SET input_hash=EXCLUDED.input_hash, encoder_version=EXCLUDED.encoder_version, dimensions=EXCLUDED.dimensions, vector=EXCLUDED.vector, checked_at=now()")
        .bind(embedding.news_item_id).bind(&embedding.model).bind(&embedding.input_hash).bind(ENCODER_VERSION).bind(dimensions).bind(serde_json::json!(embedding.vector)).execute(connection).await?;
    Ok(true)
}

#[derive(FromRow)]
struct NewsRow {
    id: i64,
    summary_text: Option<String>,
    summary_key_points: Value,
    raw_metadata: Value,
    article_url: Option<String>,
    canonical_story_url: Option<String>,
    canonical_item_url: Option<String>,
    published_at: Option<NaiveDateTime>,
    processed_at: Option<NaiveDateTime>,
    ingested_at: NaiveDateTime,
    created_at: NaiveDateTime,
}

pub async fn source(
    connection: &mut PgConnection,
    id: i64,
) -> Result<Option<BriefingRefreshSource>, sqlx::Error> {
    let row = sqlx::query_as::<_, NewsRow>("SELECT id::bigint, summary_text, summary_key_points::jsonb, raw_metadata::jsonb, article_url, canonical_story_url, canonical_item_url, published_at, processed_at, ingested_at, created_at FROM news_items WHERE id::bigint = $1 AND status = 'ready' AND representative_news_item_id IS NULL")
        .bind(id).fetch_optional(connection).await?;
    Ok(row.map(|r| {
        crate::briefing_refresh::sources::source_from_news(
            r.id,
            r.summary_text.as_deref(),
            &r.summary_key_points,
            &r.raw_metadata,
            r.article_url.as_deref(),
            r.canonical_story_url.as_deref(),
            r.canonical_item_url.as_deref(),
            r.published_at,
            r.processed_at,
            r.ingested_at,
            r.created_at,
        )
    }))
}

pub async fn record_usage(
    connection: &mut PgConnection,
    task_id: i64,
    usage: &crate::BriefingLensAssignmentUsage,
) -> Result<(), sqlx::Error> {
    fn tokens(n: u64) -> i32 {
        i32::try_from(n).unwrap_or(i32::MAX)
    }
    let total_tokens = usage
        .usage
        .input_tokens
        .saturating_add(usage.usage.output_tokens);
    sqlx::query(
        r#"
            INSERT INTO vendor_usage_records (
                provider, model, feature, operation, source, request_id, task_id, user_id,
                request_count, input_tokens, cache_read_tokens, cache_write_tokens,
                output_tokens, total_tokens, currency, pricing_version, metadata, created_at
            )
            VALUES (
                $1, $2, $3, $4, 'queue', $5, $6::bigint::integer, $7::bigint::integer,
                $8, $9, $10, $11, $12, $13, 'USD', '2026-08-02', '{}'::jsonb,
                timezone('UTC', clock_timestamp())
            )
            "#,
    )
    .bind(&usage.provider)
    .bind(&usage.model)
    .bind(&usage.feature)
    .bind(&usage.operation)
    .bind(&usage.provider_response_id)
    .bind(task_id)
    .bind(Option::<i64>::None)
    .bind(tokens(usage.usage.request_count))
    .bind(tokens(usage.usage.input_tokens))
    .bind(tokens(usage.usage.cached_input_tokens))
    .bind(tokens(usage.usage.cache_write_tokens))
    .bind(tokens(usage.usage.output_tokens))
    .bind(tokens(total_tokens))
    .execute(&mut *connection)
    .await?;
    Ok(())
}

/// A terminal preparation failure for unchanged source state is not automatically replayed.
pub async fn preparation_failed(
    connection: &mut PgConnection,
    id: i64,
    model: &str,
    text: &str,
) -> Result<bool, sqlx::Error> {
    sqlx::query_scalar(
        r"SELECT EXISTS(SELECT 1 FROM processing_tasks t
        WHERE t.task_type='prepare_news_lens' AND t.payload->>'news_item_id'=$1::bigint::text
          AND t.status='failed' AND t.completed_at >= timezone('UTC',now())-interval '6 hours'
          AND t.payload->>'embedding_model'=$2 AND t.payload->>'input_hash'=$3
          AND (t.payload->>'encoder_version')::integer=$4)",
    )
    .bind(id)
    .bind(model)
    .bind(input_hash(text))
    .bind(ENCODER_VERSION)
    .fetch_one(connection)
    .await
}
