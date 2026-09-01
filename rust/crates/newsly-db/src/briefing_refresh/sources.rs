//! Eligible source loading and conversion into provider-independent Briefing projections.

use super::{
    AssertSqlSafe, BTreeSet, BriefingRefreshSource, HashMap, HashSet, Map, NaiveDateTime, Postgres,
    SourceContentRow, SourceNewsRow, Transaction, Value,
};

pub(crate) async fn load_eligible_sources_for_keys(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    keys: &[String],
) -> Result<HashMap<String, BriefingRefreshSource>, sqlx::Error> {
    let (content_ids, news_ids) = parse_source_ids(keys);
    let mut sources = HashMap::new();
    if !content_ids.is_empty() {
        let rows = sqlx::query_as::<_, SourceContentRow>(
            r#"
            SELECT content.id::bigint AS id, content.content_type, content.url,
                   content.source_url, content.title, content.source,
                   content.content_metadata::jsonb AS metadata, content.created_at,
                   content.publication_date
            FROM contents AS content
            JOIN content_status AS membership ON membership.content_id = content.id
            WHERE content.id::bigint = ANY($2::bigint[])
              AND membership.user_id::bigint = $1 AND membership.status = 'inbox'
              AND content.status = 'completed'
              AND content.content_type IN ('article', 'podcast')
              AND (content.classification IS NULL OR content.classification <> 'skip')
              AND NOT EXISTS (
                  SELECT 1 FROM content_read_status AS read_status
                  WHERE read_status.user_id::bigint = $1
                    AND read_status.content_id = content.id
              )
            "#,
        )
        .bind(user_id)
        .bind(&content_ids)
        .fetch_all(&mut **transaction)
        .await?;
        for row in rows {
            let source = source_from_content(
                row.id,
                &row.content_type,
                &row.url,
                row.source_url.as_deref(),
                row.title.as_deref(),
                row.source.as_deref(),
                &row.metadata,
                row.created_at,
                row.publication_date,
            );
            sources.insert(source.source_key.clone(), source);
        }
    }
    if !news_ids.is_empty() {
        let rows = sqlx::query_as::<_, SourceNewsRow>(AssertSqlSafe(format!(
            r#"
            WITH visible_news AS ({visible_news})
            SELECT news.id::bigint AS id, news.summary_text,
                   news.summary_key_points::jsonb AS summary_key_points,
                   news.raw_metadata::jsonb AS raw_metadata, news.article_url,
                   news.canonical_story_url, news.canonical_item_url,
                   news.published_at, news.processed_at, news.ingested_at, news.created_at
            FROM visible_news AS news
            WHERE news.id::bigint = ANY($2::bigint[])
              AND NOT EXISTS (
                  SELECT 1 FROM news_items AS member
                  JOIN news_item_read_status AS read_status ON read_status.news_item_id = member.id
                  WHERE read_status.user_id::bigint = $1
                    AND coalesce(member.representative_news_item_id, member.id) = news.id
              )
            "#,
            visible_news = visible_news_sql()
        )))
        .bind(user_id)
        .bind(&news_ids)
        .fetch_all(&mut **transaction)
        .await?;
        for row in rows {
            let source = source_from_news(
                row.id,
                row.summary_text.as_deref(),
                &row.summary_key_points,
                &row.raw_metadata,
                row.article_url.as_deref(),
                row.canonical_story_url.as_deref(),
                row.canonical_item_url.as_deref(),
                row.published_at,
                row.processed_at,
                row.ingested_at,
                row.created_at,
            );
            sources.insert(source.source_key.clone(), source);
        }
    }
    Ok(sources)
}

pub(super) async fn load_read_source_keys(
    transaction: &mut Transaction<'static, Postgres>,
    user_id: i64,
    keys: &[String],
) -> Result<HashSet<String>, sqlx::Error> {
    let (content_ids, news_ids) = parse_source_ids(keys);
    let mut read = HashSet::new();
    if !content_ids.is_empty() {
        for id in sqlx::query_scalar::<_, i64>(
            "SELECT DISTINCT content_id::bigint FROM content_read_status WHERE user_id::bigint = $1 AND content_id::bigint = ANY($2::bigint[])",
        )
        .bind(user_id)
        .bind(&content_ids)
        .fetch_all(&mut **transaction)
        .await?
        {
            read.insert(format!("content:{id}"));
        }
    }
    if !news_ids.is_empty() {
        for id in sqlx::query_scalar::<_, i64>(
            r#"
            SELECT requested.id::bigint
            FROM unnest($2::bigint[]) AS requested(id)
            JOIN news_items AS requested_news ON requested_news.id::bigint = requested.id
            WHERE EXISTS (
                SELECT 1 FROM news_items AS member
                JOIN news_item_read_status AS read_status ON read_status.news_item_id = member.id
                WHERE read_status.user_id::bigint = $1
                  AND coalesce(member.representative_news_item_id, member.id) =
                      coalesce(requested_news.representative_news_item_id, requested_news.id)
            )
            "#,
        )
        .bind(user_id)
        .bind(&news_ids)
        .fetch_all(&mut **transaction)
        .await?
        {
            read.insert(format!("news:{id}"));
        }
    }
    Ok(read)
}

pub(super) fn visible_news_sql() -> &'static str {
    r#"
    WITH valid_aggregators AS (
        SELECT lower(btrim(config::jsonb ->> 'key')) AS source_key,
               CASE WHEN jsonb_typeof(config::jsonb -> 'topics') = 'array'
                    THEN config::jsonb -> 'topics' ELSE '[]'::jsonb END AS topics
        FROM user_scraper_configs
        WHERE user_id::bigint = $1 AND scraper_type = 'aggregator'
          AND is_active IS TRUE
          AND lower(btrim(config::jsonb ->> 'key')) = ANY(ARRAY[
              'brutalist', 'finurls', 'hackernews', 'mediagazer',
              'memeorandum', 'sciurls', 'techmeme'
          ])
    )
    SELECT news.*
    FROM news_items AS news
    WHERE news.status = 'ready' AND news.representative_news_item_id IS NULL
      AND (
          (news.visibility_scope = 'user' AND news.owner_user_id::bigint = $1)
          OR (
              news.visibility_scope = 'global'
              AND EXISTS (
                  SELECT 1 FROM valid_aggregators AS selected
                  WHERE selected.source_key = lower(btrim(coalesce(news.platform, '')))
                    AND (
                        selected.source_key <> 'brutalist'
                        OR jsonb_array_length(selected.topics) = 0
                        OR lower(btrim(coalesce(
                            news.raw_metadata::jsonb #>> '{aggregator,topic}', ''
                        ))) IN (
                            SELECT lower(btrim(value))
                            FROM jsonb_array_elements_text(selected.topics) AS value
                        )
                    )
              )
          )
      )
    "#
}

#[allow(clippy::too_many_arguments)]
pub(super) fn source_from_content(
    id: i64,
    _content_type: &str,
    url: &str,
    source_url: Option<&str>,
    title: Option<&str>,
    source_name: Option<&str>,
    metadata: &Value,
    created_at: NaiveDateTime,
    publication_date: Option<NaiveDateTime>,
) -> BriefingRefreshSource {
    let map = metadata.as_object().cloned().unwrap_or_default();
    let summary_value = map.get("summary");
    let summary = summary_value
        .and_then(short_summary)
        .or_else(|| clean_value(map.get("excerpt")));
    let key_points = metadata_key_points(&map);
    let version = image_version(&map);
    BriefingRefreshSource {
        source_key: format!("content:{id}"),
        kind: "content".to_owned(),
        id,
        title: clean_text(title).unwrap_or_else(|| format!("Content {id}")),
        source_name: clean_text(source_name).or_else(|| clean_value(map.get("source"))),
        summary,
        key_points,
        url: clean_text(source_url).or_else(|| clean_text(Some(url))),
        image_url: Some(versioned_url(
            format!("/static/images/content/{id}.png"),
            version.as_deref(),
        )),
        thumbnail_url: Some(versioned_url(
            format!("/static/images/thumbnails/{id}.png"),
            version.as_deref(),
        )),
        published_at: publication_date
            .or(Some(created_at))
            .map(|value| value.and_utc()),
        briefing_context: briefing_context(&map),
    }
}

#[allow(clippy::too_many_arguments)]
pub(super) fn source_from_news(
    id: i64,
    summary_text: Option<&str>,
    summary_key_points: &Value,
    raw_metadata: &Value,
    article_url: Option<&str>,
    canonical_story_url: Option<&str>,
    canonical_item_url: Option<&str>,
    published_at: Option<NaiveDateTime>,
    processed_at: Option<NaiveDateTime>,
    ingested_at: NaiveDateTime,
    created_at: NaiveDateTime,
) -> BriefingRefreshSource {
    let map = raw_metadata.as_object().cloned().unwrap_or_default();
    let version = image_version(&map);
    let has_image = map.get("image_generated_at").is_some_and(value_truthy);
    let title = news_title(&map, summary_text, id);
    BriefingRefreshSource {
        source_key: format!("news:{id}"),
        kind: "news".to_owned(),
        id,
        title,
        source_name: None,
        summary: clean_text(summary_text),
        key_points: json_strings(summary_key_points),
        url: clean_text(article_url)
            .or_else(|| clean_text(canonical_story_url))
            .or_else(|| clean_text(canonical_item_url)),
        image_url: None,
        thumbnail_url: has_image.then(|| {
            versioned_url(
                format!("/static/images/news_thumbnails/{id}.png"),
                version.as_deref(),
            )
        }),
        published_at: published_at
            .or(processed_at)
            .or(Some(ingested_at))
            .or(Some(created_at))
            .map(|value| value.and_utc()),
        briefing_context: None,
    }
}

pub(super) fn news_title(
    metadata: &Map<String, Value>,
    summary_text: Option<&str>,
    id: i64,
) -> String {
    for path in [
        ["summary", "title"],
        ["cluster", "canonical_title"],
        ["article", "title"],
    ] {
        if let Some(value) = metadata
            .get(path[0])
            .and_then(Value::as_object)
            .and_then(|object| object.get(path[1]))
            .and_then(Value::as_str)
            .and_then(|value| clean_text(Some(value)))
        {
            return value;
        }
    }
    clean_text(summary_text).map_or_else(
        || format!("News item {id}"),
        |value| value.chars().take(120).collect(),
    )
}

pub(super) fn briefing_context(metadata: &Map<String, Value>) -> Option<String> {
    let mut parts = Vec::new();
    if let Some(summary) = metadata.get("summary") {
        if let Some(object) = summary.as_object() {
            for (label, key) in [
                ("Overview", "overview"),
                ("Summary", "summary"),
                ("One line", "one_line"),
                ("Takeaway", "takeaway"),
            ] {
                if let Some(value) = clean_value(object.get(key)) {
                    parts.push(format!("{label}: {value}"));
                }
            }
        } else if let Some(value) = clean_value(Some(summary)) {
            parts.push(format!("Summary: {value}"));
        }
    }
    if let Some(value) = clean_value(metadata.get("excerpt")) {
        parts.push(format!("Excerpt: {value}"));
    }
    let value = parts.join("\n\n");
    (!value.is_empty()).then(|| value.chars().take(2_400).collect())
}

pub(super) fn metadata_key_points(metadata: &Map<String, Value>) -> Vec<String> {
    metadata
        .get("key_points")
        .or_else(|| {
            metadata
                .get("summary")
                .and_then(Value::as_object)
                .and_then(|summary| summary.get("key_points").or_else(|| summary.get("points")))
        })
        .map_or_else(Vec::new, json_strings)
        .into_iter()
        .take(6)
        .collect()
}

pub(super) fn short_summary(value: &Value) -> Option<String> {
    value
        .as_str()
        .and_then(|value| clean_text(Some(value)))
        .or_else(|| {
            value.as_object().and_then(|object| {
                ["one_line", "summary", "overview", "hook"]
                    .into_iter()
                    .find_map(|key| clean_value(object.get(key)))
                    .or_else(|| {
                        object
                            .get("feed_preview")
                            .and_then(Value::as_object)
                            .and_then(|preview| clean_value(preview.get("one_line")))
                    })
            })
        })
}

pub(super) fn clean_value(value: Option<&Value>) -> Option<String> {
    value
        .and_then(Value::as_str)
        .and_then(|value| clean_text(Some(value)))
}

pub(super) fn clean_text(value: Option<&str>) -> Option<String> {
    let compact = value?.split_whitespace().collect::<Vec<_>>().join(" ");
    (!compact.is_empty()).then_some(compact)
}

pub(super) fn json_strings(value: &Value) -> Vec<String> {
    value
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|item| {
            item.as_str()
                .or_else(|| item.get("text").and_then(Value::as_str))
                .and_then(|value| clean_text(Some(value)))
        })
        .collect()
}

pub(super) fn event_group_count(value: &Value) -> usize {
    value.as_array().map_or(0, Vec::len)
}

pub(super) fn image_version(metadata: &Map<String, Value>) -> Option<String> {
    metadata
        .get("image_version")
        .filter(|value| value_truthy(value))
        .or_else(|| {
            metadata
                .get("thumbnail_version")
                .filter(|value| value_truthy(value))
        })
        .and_then(|value| match value {
            Value::String(value) => clean_text(Some(value)),
            Value::Number(value) => Some(value.to_string()),
            Value::Bool(true) => Some("true".to_owned()),
            _ => None,
        })
}

pub(super) fn value_truthy(value: &Value) -> bool {
    match value {
        Value::Null | Value::Bool(false) => false,
        Value::String(value) => !value.trim().is_empty(),
        Value::Number(_) | Value::Bool(true) => true,
        Value::Array(value) => !value.is_empty(),
        Value::Object(value) => !value.is_empty(),
    }
}

pub(super) fn versioned_url(base: String, version: Option<&str>) -> String {
    match version {
        Some(version) => format!("{base}?v={}", percent_encode(version)),
        None => base,
    }
}

pub(super) fn percent_encode(value: &str) -> String {
    value
        .bytes()
        .flat_map(|byte| {
            if byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.' | b'~') {
                vec![char::from(byte)]
            } else {
                format!("%{byte:02X}").chars().collect()
            }
        })
        .collect()
}

pub(super) fn news_topic(metadata: &Value) -> (Option<String>, Option<String>) {
    let aggregator = metadata.get("aggregator").and_then(Value::as_object);
    let topic = aggregator
        .and_then(|value| value.get("topic"))
        .and_then(Value::as_str)
        .and_then(|value| clean_text(Some(value)));
    let slug = topic
        .as_deref()
        .map(slugify)
        .filter(|value| !value.is_empty());
    let title = aggregator
        .and_then(|value| value.get("topic_title"))
        .and_then(Value::as_str)
        .and_then(|value| clean_text(Some(value)))
        .or_else(|| slug.as_deref().map(title_case_slug));
    (slug, title)
}

pub(super) fn slugify(value: &str) -> String {
    let mut output = String::new();
    let mut dash = false;
    for character in value.trim().to_ascii_lowercase().chars() {
        if character.is_ascii_alphanumeric() {
            output.push(character);
            dash = false;
        } else if !dash && !output.is_empty() {
            output.push('-');
            dash = true;
        }
    }
    output.trim_matches('-').chars().take(48).collect()
}

pub(super) fn title_case_slug(value: &str) -> String {
    value
        .split('-')
        .filter(|word| !word.is_empty())
        .map(|word| {
            let mut chars = word.chars();
            chars.next().map_or_else(String::new, |first| {
                first.to_uppercase().collect::<String>() + chars.as_str()
            })
        })
        .collect::<Vec<_>>()
        .join(" ")
}

pub(super) fn parse_source_ids(keys: &[String]) -> (Vec<i64>, Vec<i64>) {
    let mut content = BTreeSet::new();
    let mut news = BTreeSet::new();
    for key in keys {
        let Some((kind, raw_id)) = key.split_once(':') else {
            continue;
        };
        let Some(id) = raw_id.parse::<i64>().ok().filter(|id| *id > 0) else {
            continue;
        };
        match kind {
            "content" => {
                content.insert(id);
            }
            "news" => {
                news.insert(id);
            }
            _ => {}
        }
    }
    (content.into_iter().collect(), news.into_iter().collect())
}

pub(super) fn u64_to_i32(value: u64) -> i32 {
    i32::try_from(value).unwrap_or(i32::MAX)
}
