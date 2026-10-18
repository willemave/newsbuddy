//! Private exact-text artifacts. Source identity remains the cache authorization boundary.
use newsly_domain::{
    EmbeddingVector, EmbeddingVectorStore, NewsRelationDocument, RelationEmbeddingText,
    prepare_relation_embedding_texts,
};
use newsly_providers::RelevantLink;
use sqlx::PgConnection;

const ENCODER_VERSION: i32 = 1;
const PROVIDER: &str = "openrouter";

pub(super) fn text_sources(
    item: &NewsRelationDocument,
    candidates: &[NewsRelationDocument],
    texts: &[RelationEmbeddingText],
) -> Vec<i64> {
    let mut owners = std::collections::HashMap::new();
    for document in std::iter::once(item).chain(candidates) {
        for text in prepare_relation_embedding_texts(document, std::slice::from_ref(document)) {
            owners.entry(text.text_sha256).or_insert(document.id);
        }
    }
    texts
        .iter()
        .map(|text| {
            *owners
                .get(&text.text_sha256)
                .expect("canonical text originates in a document")
        })
        .collect()
}

/// Only current authorized source identities may supply a shared exact-text vector.
/// This avoids first-document ownership churn when candidate order or membership changes.
pub(super) async fn load_vectors(
    connection: &mut PgConnection,
    texts: &[RelationEmbeddingText],
    authorized_sources: &[i64],
    model: &str,
) -> Result<Vec<Option<Vec<f64>>>, sqlx::Error> {
    if texts.is_empty() {
        return Ok(Vec::new());
    }
    let hashes = texts
        .iter()
        .map(|text| text.text_sha256.clone())
        .collect::<Vec<_>>();
    let values: Vec<Option<serde_json::Value>> = sqlx::query_scalar(
        "SELECT cached.vector FROM unnest($1::text[]) WITH ORDINALITY requested(text_hash,position)
         LEFT JOIN LATERAL (
             SELECT vector FROM news_relation_embeddings
             WHERE news_item_id::bigint=ANY($2) AND text_hash=requested.text_hash
               AND provider=$3 AND model=$4 AND encoder_version=$5
               AND dimensions=jsonb_array_length(vector)
             ORDER BY news_item_id LIMIT 1
         ) cached ON true
         ORDER BY requested.position",
    )
    .bind(hashes)
    .bind(authorized_sources)
    .bind(PROVIDER)
    .bind(model)
    .bind(ENCODER_VERSION)
    .fetch_all(connection)
    .await?;
    Ok(values
        .into_iter()
        .map(|value| value.and_then(|value| serde_json::from_value(value).ok()))
        .collect())
}

pub(super) fn vector_store(
    texts: &[RelationEmbeddingText],
    vectors: Vec<Vec<f64>>,
) -> Result<EmbeddingVectorStore, newsly_domain::InvalidEmbeddingBundle> {
    if texts.len() != vectors.len() {
        return Err(newsly_domain::InvalidEmbeddingBundle::ZeroDimensions);
    }
    let dimensions = vectors.first().map_or(1, Vec::len);
    EmbeddingVectorStore::new(
        dimensions,
        texts
            .iter()
            .zip(vectors)
            .map(|(text, vector)| EmbeddingVector {
                id: text.id.clone(),
                text_sha256: text.text_sha256.clone(),
                vector,
            }),
    )
}

pub(super) async fn save_vectors(
    connection: &mut PgConnection,
    texts: &[RelationEmbeddingText],
    owners: &[i64],
    model: &str,
    vectors: &[Vec<f64>],
) -> Result<(), sqlx::Error> {
    if texts.len() != owners.len() || texts.len() != vectors.len() {
        return Err(sqlx::Error::Protocol(
            "relation preparation count mismatch".into(),
        ));
    }
    vector_store(texts, vectors.to_vec())
        .map_err(|error| sqlx::Error::Protocol(error.to_string()))?;
    let hashes = texts
        .iter()
        .map(|text| text.text_sha256.clone())
        .collect::<Vec<_>>();
    sqlx::query(
        "INSERT INTO news_relation_embeddings(news_item_id,provider,model,encoder_version,text_hash,dimensions,vector)
         SELECT source.id,$3,$4,$5,requested.text_hash,jsonb_array_length(prepared.vector),prepared.vector
         FROM unnest($1::bigint[],$2::text[]) WITH ORDINALITY requested(source_id,text_hash,position)
         JOIN news_items source ON source.id::bigint=requested.source_id
         JOIN jsonb_array_elements($6::jsonb) WITH ORDINALITY prepared(vector,position) ON prepared.position=requested.position
         ON CONFLICT DO NOTHING")
        .bind(owners).bind(hashes).bind(PROVIDER).bind(model).bind(ENCODER_VERSION).bind(serde_json::json!(vectors))
        .execute(&mut *connection).await?;
    // Keep storage finite per item/model; current batches are far below this bound.
    sqlx::query("DELETE FROM news_relation_embeddings WHERE ctid IN (SELECT ctid FROM (SELECT ctid,row_number() OVER (PARTITION BY news_item_id ORDER BY created_at DESC,text_hash) AS n FROM news_relation_embeddings WHERE news_item_id::bigint=ANY($1)) old WHERE n>1024)")
        .bind(owners).execute(connection).await?;
    Ok(())
}

pub(super) async fn load_links(
    connection: &mut PgConnection,
    source_id: i64,
    hash: &str,
    model: &str,
) -> Result<Option<Option<Vec<RelevantLink>>>, sqlx::Error> {
    let value: Option<serde_json::Value> = sqlx::query_scalar("SELECT result FROM news_relation_link_preparations WHERE news_item_id::bigint=$1 AND input_hash=$2 AND model=$3")
        .bind(source_id).bind(hash).bind(model).fetch_optional(connection).await?;
    value
        .map(|value| {
            serde_json::from_value(value).map_err(|error| sqlx::Error::Decode(Box::new(error)))
        })
        .transpose()
}

pub(super) async fn save_links(
    connection: &mut PgConnection,
    source_id: i64,
    hash: &str,
    model: &str,
    links: Option<&[RelevantLink]>,
) -> Result<(), sqlx::Error> {
    sqlx::query("INSERT INTO news_relation_link_preparations(news_item_id,input_hash,model,result) SELECT id,$2,$3,$4 FROM news_items WHERE id::bigint=$1 ON CONFLICT DO NOTHING")
        .bind(source_id).bind(hash).bind(model).bind(serde_json::json!(links)).execute(&mut *connection).await?;
    sqlx::query("DELETE FROM news_relation_link_preparations WHERE ctid IN (SELECT ctid FROM news_relation_link_preparations WHERE news_item_id::bigint=$1 ORDER BY created_at DESC,input_hash OFFSET 8)")
        .bind(source_id).execute(connection).await?;
    Ok(())
}
