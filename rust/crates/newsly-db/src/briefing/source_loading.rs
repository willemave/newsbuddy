//! Load authorized Briefing sources and their presentation metadata.
use super::{
    BriefingDiscussionProjection, BriefingRepositoryError, BriefingSourceProjection,
    ContentBriefingSourceProjection, ContentSourceRow, NewsBriefingSourceProjection, NewsSourceRow,
    parse_source_ids,
};
use crate::news_visibility::{SUBSCRIBED_AGGREGATORS_CTE, visible_news_predicate};
use sqlx::PgPool;
use std::collections::HashMap;

pub(super) async fn load_sources(
    pool: &PgPool,
    user_id: i64,
    source_keys: &[String],
) -> Result<HashMap<String, BriefingSourceProjection>, BriefingRepositoryError> {
    let (content_ids, news_ids) = parse_source_ids(source_keys);
    let mut found = HashMap::new();
    if !content_ids.is_empty() {
        let rows = sqlx::query_as::<_, ContentSourceRow>(
            r#"
            SELECT content.id::bigint AS id, content.content_type, content.url,
                   content.source_url, content.title, content.source,
                   content.content_metadata::jsonb AS metadata,
                   (SELECT body.char_count FROM content_bodies AS body
                    WHERE body.content_id = content.id AND body.variant = 'source')
                       AS source_char_count,
                   content.created_at, content.publication_date
            FROM contents AS content
            WHERE content.id::bigint = ANY($2::bigint[])
              AND EXISTS (
                  SELECT 1 FROM content_status AS user_status
                  WHERE user_status.user_id::bigint = $1
                    AND user_status.content_id = content.id
              )
            "#,
        )
        .bind(user_id)
        .bind(&content_ids)
        .fetch_all(pool)
        .await?;
        for row in rows {
            let id = row.id;
            found.insert(
                format!("content:{id}"),
                BriefingSourceProjection::Content(ContentBriefingSourceProjection {
                    id,
                    content_type: row.content_type,
                    url: row.url,
                    source_url: row.source_url,
                    title: row.title,
                    source: row.source,
                    metadata: row.metadata,
                    source_char_count: row.source_char_count,
                    created_at: row.created_at.and_utc(),
                    publication_date: row.publication_date.map(|value| value.and_utc()),
                }),
            );
        }
    }
    if !news_ids.is_empty() {
        let rows = sqlx::query_as::<_, NewsSourceRow>(sqlx::AssertSqlSafe(format!(
            r#"
            {SUBSCRIBED_AGGREGATORS_CTE}
            SELECT news.id::bigint AS id, news.platform, news.summary_text,
                   news.summary_key_points::jsonb AS summary_key_points,
                   news.raw_metadata::jsonb AS raw_metadata, news.article_url,
                   news.canonical_story_url, news.canonical_item_url,
                   news.published_at, news.processed_at, news.ingested_at,
                   news.created_at, discussion.platform AS discussion_platform,
                   discussion.comment_count AS discussion_comment_count,
                   discussion.discussion_url,
                   discussion.summary::jsonb AS discussion_summary,
                   discussion.summary_status AS discussion_summary_status,
                   discussion.last_refresh_status AS discussion_last_refresh_status,
                   discussion.summary_generated_at AS discussion_summary_generated_at,
                   discussion.last_comments_fetched_at AS discussion_last_comments_fetched_at,
                   discussion.last_count_checked_at AS discussion_last_count_checked_at
            FROM news_items AS news
            LEFT JOIN news_item_discussions AS discussion
              ON discussion.news_item_id = news.id
            WHERE news.id::bigint = ANY($2::bigint[])
              AND {visibility}
            "#,
            visibility = visible_news_predicate("news")
        )))
        .bind(user_id)
        .bind(&news_ids)
        .fetch_all(pool)
        .await?;
        for row in rows {
            let id = row.id;
            let discussion = row
                .discussion_platform
                .map(|platform| BriefingDiscussionProjection {
                    platform,
                    comment_count: row.discussion_comment_count,
                    discussion_url: row.discussion_url,
                    summary: row.discussion_summary,
                    summary_status: row
                        .discussion_summary_status
                        .unwrap_or_else(|| "not_ready".to_owned()),
                    last_refresh_status: row
                        .discussion_last_refresh_status
                        .unwrap_or_else(|| "pending".to_owned()),
                    summary_generated_at: row
                        .discussion_summary_generated_at
                        .map(|value| value.and_utc()),
                    last_comments_fetched_at: row
                        .discussion_last_comments_fetched_at
                        .map(|value| value.and_utc()),
                    last_count_checked_at: row
                        .discussion_last_count_checked_at
                        .map(|value| value.and_utc()),
                });
            found.insert(
                format!("news:{id}"),
                BriefingSourceProjection::News(NewsBriefingSourceProjection {
                    id,
                    platform: row.platform,
                    summary_text: row.summary_text,
                    summary_key_points: row.summary_key_points,
                    raw_metadata: row.raw_metadata,
                    article_url: row.article_url,
                    canonical_story_url: row.canonical_story_url,
                    canonical_item_url: row.canonical_item_url,
                    published_at: row.published_at.map(|value| value.and_utc()),
                    processed_at: row.processed_at.map(|value| value.and_utc()),
                    ingested_at: row.ingested_at.and_utc(),
                    created_at: row.created_at.and_utc(),
                    discussion,
                }),
            );
        }
    }
    Ok(found)
}
