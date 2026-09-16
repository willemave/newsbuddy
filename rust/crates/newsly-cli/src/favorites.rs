use std::collections::HashSet;
use std::fmt::Write as _;
use std::time::Duration;

use serde::{Deserialize, Deserializer, Serialize};
use serde_json::{Map, Value};

use crate::client::{ApiError, Client};

const PAGE_SIZE: usize = 100;

#[derive(Debug, Serialize)]
pub(crate) struct FavoritesResult {
    articles: Vec<FavoriteArticle>,
    count: usize,
    requested_limit: usize,
}

impl FavoritesResult {
    pub(crate) fn render_text(&self) -> String {
        let mut text = format!("articles: {}\n", self.count);
        for (index, article) in self.articles.iter().enumerate() {
            let title = article.title.as_deref().unwrap_or("(untitled)");
            writeln!(
                text,
                "{}. {title}\n   id: {}\n   url: {}\n   knowledge_saved_at: {}",
                index + 1,
                article.id,
                article.url,
                article.knowledge_saved_at,
            )
            .expect("writing to a String cannot fail");
        }
        text
    }
}

#[derive(Debug, Deserialize, Serialize)]
struct FavoriteArticle {
    id: i64,
    #[serde(deserialize_with = "required_nullable_title")]
    title: Option<String>,
    url: String,
    knowledge_saved_at: String,
    #[serde(flatten)]
    extra: Map<String, Value>,
}

// A title may be explicitly null, but the wire field must still be present.
fn required_nullable_title<'de, D: Deserializer<'de>>(
    deserializer: D,
) -> Result<Option<String>, D::Error> {
    Option::<String>::deserialize(deserializer)
        .map_err(|error| serde::de::Error::custom(format!("invalid article title: {error}")))
}

#[derive(Deserialize)]
struct KnowledgePage {
    contents: Vec<Value>,
    meta: KnowledgePagination,
}

#[derive(Deserialize)]
struct KnowledgePagination {
    has_more: bool,
    next_cursor: Option<String>,
}

pub(crate) async fn collect_favorite_articles(
    client: &Client,
    limit: usize,
    timeout: Duration,
) -> Result<FavoritesResult, ApiError> {
    tokio::time::timeout(timeout, collect_pages(client, limit))
        .await
        .map_err(|_| ApiError::local(format!("content favorites timed out after {timeout:?}")))?
}

async fn collect_pages(client: &Client, limit: usize) -> Result<FavoritesResult, ApiError> {
    let mut articles = Vec::with_capacity(limit);
    let mut cursor: Option<String> = None;
    let mut seen_cursors = HashSet::new();

    while articles.len() < limit {
        let mut query = vec![("limit".to_owned(), PAGE_SIZE.to_string())];
        if let Some(cursor) = cursor.as_ref() {
            query.push(("cursor".to_owned(), cursor.clone()));
        }
        let response = client.list_knowledge_content(&query).await?;
        let page: KnowledgePage = serde_json::from_value(response).map_err(malformed)?;
        if page.contents.len() > PAGE_SIZE {
            return Err(malformed(format!(
                "page returned {} contents for requested limit {PAGE_SIZE}",
                page.contents.len()
            )));
        }

        for content in page.contents {
            let content_type = content
                .get("content_type")
                .and_then(Value::as_str)
                .ok_or_else(|| malformed("content is missing string field \"content_type\""))?;
            if content_type == "article" {
                articles.push(serde_json::from_value(content).map_err(malformed)?);
                if articles.len() == limit {
                    break;
                }
            }
        }

        let next_cursor = page.meta.next_cursor;
        if !page.meta.has_more {
            if next_cursor.is_some() {
                return Err(malformed(
                    "meta.next_cursor must be null when meta.has_more is false",
                ));
            }
            break;
        }
        let next_cursor = next_cursor
            .filter(|value| !value.is_empty())
            .ok_or_else(|| {
                malformed("meta.next_cursor must be a non-empty string when meta.has_more is true")
            })?;
        if !seen_cursors.insert(next_cursor.clone()) {
            return Err(malformed("meta.next_cursor repeated a previous cursor"));
        }
        cursor = Some(next_cursor);
    }

    Ok(FavoritesResult {
        count: articles.len(),
        articles,
        requested_limit: limit,
    })
}

fn malformed(message: impl std::fmt::Display) -> ApiError {
    ApiError::local(format!("invalid Knowledge list response: {message}"))
}
