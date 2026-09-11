use std::collections::BTreeSet;

use serde::Serialize;
use sqlx::types::Json;

use super::{
    ChatContentHit, ChatToolRepositoryError, ContentHitRow, content_select, validate_search,
};
use sqlx::{AssertSqlSafe, FromRow, PgPool};

#[derive(Debug, Default)]
pub struct ChatContentFilters {
    pub query: String,
    pub source: Option<String>,
    pub unread_only: bool,
    pub saved_only: bool,
    pub limit: i64,
    pub offset: i64,
}

#[derive(Debug, Serialize)]
pub struct ChatContentPage {
    pub items: Vec<ChatContentHit>,
    pub total_count: i64,
    pub scope_total_count: i64,
    pub query: String,
    pub returned_count: usize,
    pub has_more: bool,
    pub next_offset: Option<i64>,
    pub source_resolution: SourceResolution,
}

#[derive(Debug, Serialize)]
#[serde(tag = "status", rename_all = "snake_case")]
pub enum SourceResolution {
    Unfiltered,
    Resolved { name: String },
    Unknown,
    Ambiguous { candidates: Vec<String> },
}

#[derive(Debug, FromRow)]
struct Source {
    name: String,
    configured_name: Option<String>,
    feed_url: Option<String>,
}

impl Source {
    fn names(&self) -> impl Iterator<Item = &str> {
        std::iter::once(self.name.as_str()).chain(self.configured_name.as_deref())
    }
}

fn source_matches<'a>(sources: &'a [Source], query: &str) -> Vec<&'a Source> {
    let exact: Vec<_> = sources
        .iter()
        .filter(|s| {
            s.names()
                .any(|name| name.eq_ignore_ascii_case(query.trim()))
        })
        .collect();
    if !exact.is_empty() {
        return exact;
    }
    let tokens = |text: &str| -> BTreeSet<String> {
        text.to_lowercase()
            .split(|c: char| !c.is_alphanumeric())
            .filter(|token| !token.is_empty())
            .map(str::to_owned)
            .collect()
    };
    let query_tokens = tokens(query);
    sources
        .iter()
        .filter(|s| {
            s.names()
                .any(|name| !query_tokens.is_empty() && query_tokens.is_subset(&tokens(name)))
        })
        .collect()
}

#[derive(FromRow)]
struct PageRow {
    total_count: i64,
    scope_total_count: i64,
    items: Json<Vec<ContentHitRow>>,
}

/// Ownership and state predicates apply before the page window. Counts and items share a snapshot.
pub async fn search_accessible_content(
    pool: &PgPool,
    user_id: i64,
    filters: &ChatContentFilters,
) -> Result<ChatContentPage, ChatToolRepositoryError> {
    validate_search(user_id, &filters.query, filters.limit)?;
    if filters.limit > 25
        || filters.offset < 0
        || filters.offset > 100_000
        || filters
            .source
            .as_ref()
            .is_some_and(|s| s.trim().is_empty() || s.chars().count() > 2_000)
    {
        return Err(ChatToolRepositoryError::InvalidInput);
    }
    let mut page = ChatContentPage {
        items: Vec::new(),
        total_count: 0,
        scope_total_count: 0,
        query: filters.query.trim().to_owned(),
        returned_count: 0,
        has_more: false,
        next_offset: None,
        source_resolution: SourceResolution::Unfiltered,
    };
    let mut names = Vec::new();
    let mut feed_url = None;
    if let Some(query) = &filters.source {
        let sources = sqlx::query_as::<_, Source>(r#"
            SELECT COALESCE(NULLIF(BTRIM(display_name), ''), NULLIF(BTRIM(config->>'name'), ''), feed_url, 'Unnamed source') AS name,
                   NULLIF(BTRIM(config->>'name'), '') AS configured_name, feed_url
            FROM user_scraper_configs
            WHERE user_id::bigint = $1 AND is_active = TRUE
              AND EXISTS (SELECT 1 FROM users WHERE id::bigint = $1 AND is_active = TRUE)
            ORDER BY id
        "#).bind(user_id).fetch_all(pool).await?;
        let matches = source_matches(&sources, query);
        match matches.as_slice() {
            [] => {
                page.source_resolution = SourceResolution::Unknown;
                return Ok(page);
            }
            [source] => {
                names = source.names().map(str::to_owned).collect();
                feed_url = source.feed_url.clone();
                page.source_resolution = SourceResolution::Resolved {
                    name: source.name.clone(),
                };
            }
            _ => {
                page.source_resolution = SourceResolution::Ambiguous {
                    candidates: matches.iter().map(|s| s.name.clone()).collect(),
                };
                return Ok(page);
            }
        }
    }
    // Only fixed SQL fragments are interpolated; all model/user values are bound.
    let statement = format!(
        r#"
        WITH scoped AS (
            {select}
            WHERE (saved.id IS NOT NULL OR EXISTS (
                SELECT 1 FROM content_status inbox
                WHERE inbox.content_id = content.id AND inbox.user_id::bigint = $1 AND inbox.status = 'inbox'
            ))
            AND content.status = 'completed'
            AND (content.classification IS NULL OR content.classification <> 'skip')
            AND (NOT $5 OR read.id IS NULL)
            AND (NOT $6 OR saved.id IS NOT NULL)
            AND (cardinality($7::text[]) = 0 OR (
                EXISTS (SELECT 1 FROM unnest($7::text[]) name WHERE LOWER(BTRIM(content.source)) = LOWER(name))
                OR ($8::text IS NOT NULL AND RTRIM(LOWER(BTRIM(content.content_metadata->>'feed_url')), '/') = RTRIM(LOWER(BTRIM($8)), '/'))
            ))
        ), matches AS (
            SELECT scoped.* FROM scoped JOIN contents content ON content.id::bigint = scoped.content_id
            WHERE (BTRIM($2) = '' OR (
                to_tsvector('english', COALESCE(content.content_metadata->'summary'->>'title', '') || ' ' || COALESCE(content.title, '') || ' ' || COALESCE(content.source, '') || ' ' || COALESCE(content.search_text, '')) @@ websearch_to_tsquery('english', $2)
                OR COALESCE(content.title, '') OPERATOR(public.%>>) $2
                OR COALESCE(content.content_metadata->'summary'->>'title', '') OPERATOR(public.%>>) $2
                OR COALESCE(content.source, '') OPERATOR(public.%>>) $2
            ))
        ), page AS (
            SELECT * FROM matches ORDER BY published_at DESC NULLS LAST, content_id DESC LIMIT $3 OFFSET $4
        )
        SELECT (SELECT COUNT(*) FROM matches)::bigint AS total_count,
               (SELECT COUNT(*) FROM scoped)::bigint AS scope_total_count,
               COALESCE((SELECT jsonb_agg(page ORDER BY published_at DESC NULLS LAST, content_id DESC) FROM page), '[]'::jsonb) AS items
    "#,
        select = content_select()
    );
    let row = sqlx::query_as::<_, PageRow>(AssertSqlSafe(statement))
        .bind(user_id)
        .bind(filters.query.trim())
        .bind(filters.limit)
        .bind(filters.offset)
        .bind(filters.unread_only)
        .bind(filters.saved_only)
        .bind(names)
        .bind(feed_url)
        .fetch_one(pool)
        .await?;
    page.items = row.items.0.into_iter().map(Into::into).collect();
    page.total_count = row.total_count;
    page.scope_total_count = row.scope_total_count;
    page.returned_count = page.items.len();
    let next = filters.offset + i64::try_from(page.returned_count).unwrap_or(25);
    page.has_more = next < page.total_count;
    page.next_offset = page.has_more.then_some(next);
    Ok(page)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[sqlx::test]
    async fn ownership_filters_paging_and_access_are_applied_before_limit(pool: PgPool) {
        sqlx::raw_sql(r#"
            INSERT INTO users (id, apple_id, email, is_admin, is_active) VALUES
              (9001, 'chat-search-one', 'one@chat.test', FALSE, TRUE),
              (9002, 'chat-search-two', 'two@chat.test', FALSE, TRUE);
            INSERT INTO user_scraper_configs (user_id, scraper_type, display_name, feed_url, config, is_active) VALUES
              (9001, 'podcast_rss', 'Example Best', 'https://best.test/feed', '{}', TRUE),
              (9001, 'podcast_rss', 'Example Rest', 'https://rest.test/feed', '{}', TRUE);
            INSERT INTO contents (id, content_type, url, title, source, status, is_aggregate, content_metadata, search_text, created_at) VALUES
              (9101, 'podcast', 'https://best.test/1', 'Older inference', 'Example Best', 'completed', FALSE, '{}', 'inference costs', '2026-08-01'),
              (9102, 'podcast', 'https://best.test/2', 'Newer episode', 'Old feed name', 'completed', FALSE, '{"feed_url":"https://best.test/feed"}', 'new topic', '2026-08-02'),
              (9103, 'podcast', 'https://rest.test/3', 'Mentions Example Best', 'Example Rest', 'completed', FALSE, '{}', 'Example Best', '2026-08-03'),
              (9104, 'podcast', 'https://best.test/private', 'Private', 'Example Best', 'completed', FALSE, '{}', 'private', '2026-08-04');
            INSERT INTO content_status (user_id, content_id, status) VALUES
              (9001,9101,'inbox'), (9001,9102,'inbox'), (9001,9103,'inbox'), (9002,9104,'inbox');
            INSERT INTO content_read_status (user_id,content_id) VALUES (9001,9102);
            INSERT INTO content_knowledge_saves (user_id,content_id) VALUES (9001,9101);
        "#).execute(&pool).await.unwrap();
        let mut filters = ChatContentFilters {
            source: Some("Example Best".into()),
            limit: 1,
            ..Default::default()
        };
        let first = search_accessible_content(&pool, 9001, &filters)
            .await
            .unwrap();
        assert_eq!(first.total_count, 2);
        assert_eq!(first.items[0].content_id, 9102);
        assert_eq!(first.next_offset, Some(1));
        filters.offset = 1;
        let second = search_accessible_content(&pool, 9001, &filters)
            .await
            .unwrap();
        assert_eq!(second.total_count, 2);
        assert_eq!(second.items[0].content_id, 9101);
        assert!(!second.has_more);
        filters.offset = 20;
        let empty = search_accessible_content(&pool, 9001, &filters)
            .await
            .unwrap();
        assert_eq!(empty.total_count, 2);
        assert!(empty.items.is_empty());
        assert!(!empty.has_more);
        filters.offset = 0;
        filters.unread_only = true;
        filters.saved_only = true;
        let saved = search_accessible_content(&pool, 9001, &filters)
            .await
            .unwrap();
        assert_eq!(saved.total_count, 1);
        assert_eq!(saved.items[0].content_id, 9101);
        filters.source = None;
        let listing = search_accessible_content(&pool, 9001, &filters)
            .await
            .unwrap();
        assert_eq!(listing.items[0].content_id, 9101);
        filters.query = "medieval pottery".into();
        let no_match = search_accessible_content(&pool, 9001, &filters)
            .await
            .unwrap();
        assert_eq!(no_match.total_count, 0);
        assert_eq!(no_match.scope_total_count, 1);
        assert_eq!(no_match.query, "medieval pottery");
        assert!(
            search_accessible_content(&pool, 9001, &filters)
                .await
                .unwrap()
                .items
                .is_empty()
        );
        filters.query.clear();
        filters.source = Some("Example".into());
        let ambiguous = search_accessible_content(&pool, 9001, &filters)
            .await
            .unwrap();
        assert!(matches!(
            ambiguous.source_resolution,
            SourceResolution::Ambiguous { .. }
        ));
        assert!(ambiguous.items.is_empty());
        filters.source = Some("Unknown".into());
        assert!(matches!(
            search_accessible_content(&pool, 9001, &filters)
                .await
                .unwrap()
                .source_resolution,
            SourceResolution::Unknown
        ));
        filters.source = None;
        filters.saved_only = false;
        filters.unread_only = false;
        filters.limit = 25;
        assert_eq!(
            search_accessible_content(&pool, 9001, &filters)
                .await
                .unwrap()
                .total_count,
            3
        );
        assert_eq!(
            search_accessible_content(&pool, 9002, &filters)
                .await
                .unwrap()
                .total_count,
            1
        );
        sqlx::query("UPDATE users SET is_active = FALSE WHERE id = 9002")
            .execute(&pool)
            .await
            .unwrap();
        assert_eq!(
            search_accessible_content(&pool, 9002, &filters)
                .await
                .unwrap()
                .total_count,
            0
        );
        // Saved items remain accessible when no longer in the inbox.
        sqlx::query("UPDATE content_status SET status = 'archived' WHERE content_id = 9101")
            .execute(&pool)
            .await
            .unwrap();
        filters.saved_only = true;
        assert_eq!(
            search_accessible_content(&pool, 9001, &filters)
                .await
                .unwrap()
                .total_count,
            1
        );
    }
}
