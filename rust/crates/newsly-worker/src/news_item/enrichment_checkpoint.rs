//! Private successful extraction artifacts; they never grant publication authority.
use super::model::{BodyPointer, EnrichmentMutation, NewsSnapshot};
use super::storage::{NewsArticleBodyStore, NewsBodyStoreError, StagedNewsBody};
use serde::{Deserialize, Serialize};

const POLICY_VERSION: i32 = 1;

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct PreparedArticle {
    article_url: String,
    final_url: String,
    title: Option<String>,
    article_domain: Option<String>,
    extraction_method: String,
    storage_key: String,
    storage_bucket: Option<String>,
    sha256: String,
    byte_size: i32,
    char_count: i32,
}

pub(super) async fn load(
    pool: &sqlx::PgPool,
    snapshot: &NewsSnapshot,
    store: &NewsArticleBodyStore,
) -> Result<Option<EnrichmentMutation>, String> {
    let prepared: Option<serde_json::Value> = sqlx::query_scalar("SELECT prepared FROM news_enrichment_preparations WHERE news_item_id::bigint=$1 AND source_fingerprint=$2 AND policy_version=$3")
        .bind(snapshot.id).bind(&snapshot.fingerprint).bind(POLICY_VERSION).fetch_optional(pool).await.map_err(|error| error.to_string())?;
    let Some(prepared) = prepared else {
        return Ok(None);
    };
    let prepared: PreparedArticle =
        serde_json::from_value(prepared).map_err(|error| error.to_string())?;
    // No connection is retained during file IO. Removed or damaged staged bodies cannot be reused.
    let body = match store
        .read(&BodyPointer {
            storage_provider: "local".into(),
            storage_key: prepared.storage_key.clone(),
        })
        .await
    {
        Ok(body) => body,
        Err(NewsBodyStoreError::InvalidUtf8(_) | NewsBodyStoreError::BodyTooLarge(_)) => {
            return Ok(None);
        }
        Err(error) => return Err(error.to_string()),
    };
    if body.is_none_or(|body| newsly_domain::sha256_hex(body.as_bytes()) != prepared.sha256) {
        return Ok(None);
    }
    Ok(Some(EnrichmentMutation::Storage {
        article_url: prepared.article_url,
        final_url: prepared.final_url,
        title: prepared.title,
        article_domain: prepared.article_domain,
        extraction_method: prepared.extraction_method,
        body: StagedNewsBody {
            storage_provider: "local",
            content_format: "text",
            storage_bucket: prepared.storage_bucket,
            storage_key: prepared.storage_key,
            sha256: prepared.sha256,
            byte_size: prepared.byte_size,
            char_count: prepared.char_count,
        },
    }))
}

pub(super) async fn save(
    pool: &sqlx::PgPool,
    snapshot: &NewsSnapshot,
    mutation: &EnrichmentMutation,
) -> Result<(), String> {
    let EnrichmentMutation::Storage {
        article_url,
        final_url,
        title,
        article_domain,
        extraction_method,
        body,
    } = mutation
    else {
        return Ok(());
    };
    let prepared = PreparedArticle {
        article_url: article_url.clone(),
        final_url: final_url.clone(),
        title: title.clone(),
        article_domain: article_domain.clone(),
        extraction_method: extraction_method.clone(),
        storage_key: body.storage_key.clone(),
        storage_bucket: body.storage_bucket.clone(),
        sha256: body.sha256.clone(),
        byte_size: body.byte_size,
        char_count: body.char_count,
    };
    let mut tx = pool.begin().await.map_err(|error| error.to_string())?;
    sqlx::query("INSERT INTO news_enrichment_preparations(news_item_id,source_fingerprint,policy_version,prepared) SELECT id,$2,$3,$4 FROM news_items WHERE id::bigint=$1 ON CONFLICT(news_item_id,source_fingerprint,policy_version) DO UPDATE SET prepared=EXCLUDED.prepared,created_at=now()")
        .bind(snapshot.id).bind(&snapshot.fingerprint).bind(POLICY_VERSION).bind(serde_json::json!(prepared)).execute(&mut *tx).await.map_err(|error| error.to_string())?;
    sqlx::query("DELETE FROM news_enrichment_preparations WHERE ctid IN (SELECT ctid FROM news_enrichment_preparations WHERE news_item_id::bigint=$1 ORDER BY created_at DESC,source_fingerprint OFFSET 4)")
        .bind(snapshot.id).execute(&mut *tx).await.map_err(|error| error.to_string())?;
    tx.commit().await.map_err(|error| error.to_string())?;
    Ok(())
}
