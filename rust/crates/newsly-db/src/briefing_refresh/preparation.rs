//! Transactional source seeding and immutable Briefing work-plan construction.

use super::{
    AssertSqlSafe, BTreeMap, BTreeSet, BriefingAppendBatch, BriefingCompactionBatch,
    BriefingDonorIdentity, BriefingLensAssignmentSnapshot, BriefingPendingIdentity,
    BriefingRefreshClaimFence, BriefingRefreshConfig, BriefingRefreshLens, BriefingRefreshMode,
    BriefingRefreshRepositoryError, BriefingRefreshSource, BriefingSemanticLens,
    BriefingUnassignedSource, DEFAULT_MASTHEAD_DECK, FIXED_LENSES, HashMap, HashSet, LensRow,
    NaiveDateTime, PendingContentRow, PendingNewsRow, Postgres, PrepareBriefingRefreshOutcome,
    PreparedBriefingRefreshSeed, SegmentRow, SemanticLensRow, Transaction, UnassignedNewsRow, Utc,
    Uuid,
    lens_assignment::{assign_pending_ids, next_news_position},
    sources::{
        event_group_count, json_strings, load_eligible_sources_for_keys, news_topic,
        source_from_content, source_from_news, visible_news_sql,
    },
};

pub async fn prepare_briefing_refresh(
    transaction: &mut Transaction<'_, Postgres>,
    task_id: i64,
    user_id: i64,
    mode: BriefingRefreshMode,
    config: &BriefingRefreshConfig,
) -> Result<PrepareBriefingRefreshOutcome, BriefingRefreshRepositoryError> {
    config.validate()?;
    let claim_fence = load_briefing_refresh_claim_fence(transaction, task_id, user_id)
        .await?
        .ok_or(BriefingRefreshRepositoryError::ClaimOwnershipLost)?;
    ensure_state(transaction, user_id, config).await?;
    let starting_version = state_version(transaction, user_id).await?;
    if !active_user_exists(transaction, user_id).await? {
        return Ok(PrepareBriefingRefreshOutcome::Disabled {
            version: starting_version,
        });
    }
    ensure_fixed_lenses(transaction, user_id).await?;
    if mode == BriefingRefreshMode::Full {
        sqlx::query("DELETE FROM briefing_pending_sources WHERE user_id::bigint = $1")
            .bind(user_id)
            .execute(&mut **transaction)
            .await?;
    }
    let full = mode == BriefingRefreshMode::Full;
    let pending_added = seed_content_pending(transaction, user_id, full).await?
        + seed_news_pending(transaction, user_id, full).await?;
    prune_ineligible_pending(transaction, user_id).await?;
    let assigned = assign_nonsemantic_pending_lenses(transaction, user_id).await?;
    let lens_assignment = load_lens_assignment_snapshot(transaction, user_id).await?;
    Ok(PrepareBriefingRefreshOutcome::Ready(
        PreparedBriefingRefreshSeed {
            task_id,
            user_id,
            mode,
            starting_version,
            pending_added,
            prepared_state_changed: assigned > 0,
            lens_assignment,
            claim_fence,
        },
    ))
}

pub(super) async fn load_briefing_refresh_claim_fence(
    transaction: &mut Transaction<'_, Postgres>,
    task_id: i64,
    user_id: i64,
) -> Result<Option<BriefingRefreshClaimFence>, sqlx::Error> {
    Ok(
        sqlx::query_as::<_, (String, Uuid, i32, String, i64, String)>(
            r#"
        SELECT locked_by, lease_token, retry_count, executor_runtime,
               executor_version, executor_namespace
        FROM processing_tasks
        WHERE id::bigint = $1 AND task_type = 'briefing_refresh'
          AND owner_user_id::bigint = $2 AND status = 'processing'
          AND locked_by IS NOT NULL AND lease_token IS NOT NULL
          AND lease_expires_at > timezone('UTC', clock_timestamp())
        FOR SHARE
        "#,
        )
        .bind(task_id)
        .bind(user_id)
        .fetch_optional(&mut **transaction)
        .await?
        .map(
            |(
                locked_by,
                lease_token,
                retry_count,
                executor_runtime,
                executor_version,
                executor_namespace,
            )| BriefingRefreshClaimFence {
                locked_by,
                lease_token,
                retry_count,
                executor_runtime,
                executor_version,
                executor_namespace,
            },
        ),
    )
}

pub(super) async fn ensure_state(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    config: &BriefingRefreshConfig,
) -> Result<(), sqlx::Error> {
    sqlx::query(
        r#"
        INSERT INTO briefing_states (user_id, version, masthead_title, masthead_deck)
        VALUES ($1::bigint::integer, 0, $2, $3)
        ON CONFLICT (user_id) DO NOTHING
        "#,
    )
    .bind(user_id)
    .bind(&config.masthead_title)
    .bind(DEFAULT_MASTHEAD_DECK)
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

pub(super) async fn state_version(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
) -> Result<i32, sqlx::Error> {
    sqlx::query_scalar("SELECT version FROM briefing_states WHERE user_id::bigint = $1")
        .bind(user_id)
        .fetch_one(&mut **transaction)
        .await
}

pub(super) async fn lock_state(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
) -> Result<i32, sqlx::Error> {
    sqlx::query_scalar("SELECT version FROM briefing_states WHERE user_id::bigint = $1 FOR UPDATE")
        .bind(user_id)
        .fetch_one(&mut **transaction)
        .await
}

pub(super) async fn active_user_exists(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
) -> Result<bool, sqlx::Error> {
    sqlx::query_scalar(
        "SELECT EXISTS(SELECT 1 FROM users WHERE id::bigint = $1 AND is_active IS TRUE)",
    )
    .bind(user_id)
    .fetch_one(&mut **transaction)
    .await
}

pub(super) async fn ensure_fixed_lenses(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
) -> Result<(), sqlx::Error> {
    for (key, tier, title, deck, position) in FIXED_LENSES {
        upsert_lens(transaction, user_id, key, tier, title, deck, position).await?;
    }
    Ok(())
}

#[allow(clippy::too_many_arguments)]
pub(super) async fn upsert_lens(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    key: &str,
    tier: &str,
    title: &str,
    deck: &str,
    position: i32,
) -> Result<i64, sqlx::Error> {
    sqlx::query_scalar(
        r#"
        INSERT INTO briefing_lenses (
            user_id, key, tier, title, deck, position, status, centroid_weight,
            created_at, updated_at
        )
        VALUES (
            $1::bigint::integer, $2, $3, $4, $5, $6, 'active', 0,
            timezone('UTC', clock_timestamp()), timezone('UTC', clock_timestamp())
        )
        ON CONFLICT (user_id, key) DO UPDATE
        SET status = 'active', retired_at = NULL, updated_at = timezone('UTC', clock_timestamp())
        RETURNING id::bigint
        "#,
    )
    .bind(user_id)
    .bind(key)
    .bind(tier)
    .bind(title)
    .bind(deck)
    .bind(position)
    .fetch_one(&mut **transaction)
    .await
}

pub(super) async fn seed_content_pending(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    full: bool,
) -> Result<usize, sqlx::Error> {
    Ok(sqlx::query(
        r#"
        INSERT INTO briefing_pending_sources (
            user_id, lens_key, source_kind, source_id, enqueued_at
        )
        SELECT
            $1::bigint::integer,
            CASE WHEN content.content_type = 'podcast' THEN 'podcasts' ELSE 'articles' END,
            'content', content.id, timezone('UTC', clock_timestamp())
        FROM contents AS content
        JOIN content_status AS membership
          ON membership.content_id = content.id
         AND membership.user_id::bigint = $1
         AND membership.status = 'inbox'
        WHERE content.status = 'completed'
          AND content.content_type IN ('article', 'podcast')
          AND (content.classification IS NULL OR content.classification <> 'skip')
          AND NOT EXISTS (
              SELECT 1 FROM content_read_status AS read_status
              WHERE read_status.user_id::bigint = $1
                AND read_status.content_id = content.id
          )
          AND (
              $2::boolean IS TRUE OR NOT EXISTS (
                  SELECT 1
                  FROM briefing_segments AS segment,
                       jsonb_array_elements_text(segment.source_keys::jsonb) AS source_key(value)
                  WHERE segment.user_id::bigint = $1
                    AND segment.status IN ('active', 'degraded')
                    AND source_key.value = 'content:' || content.id::text
              )
          )
        ON CONFLICT (user_id, source_kind, source_id) DO NOTHING
        "#,
    )
    .bind(user_id)
    .bind(full)
    .execute(&mut **transaction)
    .await?
    .rows_affected() as usize)
}

pub(super) async fn seed_news_pending(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    full: bool,
) -> Result<usize, sqlx::Error> {
    Ok(sqlx::query(AssertSqlSafe(format!(
        r#"
        WITH visible_news AS ({visible_news})
        INSERT INTO briefing_pending_sources (
            user_id, lens_key, source_kind, source_id, enqueued_at
        )
        SELECT $1::bigint::integer, NULL, 'news', news.id,
               timezone('UTC', clock_timestamp())
        FROM visible_news AS news
        WHERE NOT EXISTS (
            SELECT 1
            FROM news_items AS member
            JOIN news_item_read_status AS read_status ON read_status.news_item_id = member.id
            WHERE read_status.user_id::bigint = $1
              AND coalesce(member.representative_news_item_id, member.id) = news.id
        )
          AND (
              $2::boolean IS TRUE OR NOT EXISTS (
                  SELECT 1
                  FROM briefing_segments AS segment,
                       jsonb_array_elements_text(segment.source_keys::jsonb) AS source_key(value)
                  WHERE segment.user_id::bigint = $1
                    AND segment.status IN ('active', 'degraded')
                    AND source_key.value = 'news:' || news.id::text
              )
          )
        ON CONFLICT (user_id, source_kind, source_id) DO NOTHING
        "#,
        visible_news = visible_news_sql()
    )))
    .bind(user_id)
    .bind(full)
    .execute(&mut **transaction)
    .await?
    .rows_affected() as usize)
}

pub(super) async fn prune_ineligible_pending(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
) -> Result<usize, sqlx::Error> {
    let content = sqlx::query(
        r#"
        DELETE FROM briefing_pending_sources AS pending
        WHERE pending.user_id::bigint = $1 AND pending.source_kind = 'content'
          AND NOT EXISTS (
              SELECT 1 FROM contents AS content
              JOIN content_status AS membership ON membership.content_id = content.id
              WHERE content.id = pending.source_id
                AND membership.user_id::bigint = $1 AND membership.status = 'inbox'
                AND content.status = 'completed'
                AND content.content_type IN ('article', 'podcast')
                AND (content.classification IS NULL OR content.classification <> 'skip')
                AND NOT EXISTS (
                    SELECT 1 FROM content_read_status AS read_status
                    WHERE read_status.user_id::bigint = $1
                      AND read_status.content_id = content.id
                )
          )
        "#,
    )
    .bind(user_id)
    .execute(&mut **transaction)
    .await?
    .rows_affected() as usize;
    let news = sqlx::query(AssertSqlSafe(format!(
        r#"
        WITH visible_news AS ({visible_news})
        DELETE FROM briefing_pending_sources AS pending
        WHERE pending.user_id::bigint = $1 AND pending.source_kind = 'news'
          AND NOT EXISTS (
              SELECT 1 FROM visible_news AS news
              WHERE news.id = pending.source_id
                AND NOT EXISTS (
                    SELECT 1
                    FROM news_items AS member
                    JOIN news_item_read_status AS read_status
                      ON read_status.news_item_id = member.id
                    WHERE read_status.user_id::bigint = $1
                      AND coalesce(member.representative_news_item_id, member.id) = news.id
                )
          )
        "#,
        visible_news = visible_news_sql()
    )))
    .bind(user_id)
    .execute(&mut **transaction)
    .await?
    .rows_affected() as usize;
    let unknown = sqlx::query(
        r#"
        DELETE FROM briefing_pending_sources
        WHERE user_id::bigint = $1 AND source_kind NOT IN ('content', 'news')
        "#,
    )
    .bind(user_id)
    .execute(&mut **transaction)
    .await?
    .rows_affected() as usize;
    Ok(content + news + unknown)
}

pub(super) async fn assign_nonsemantic_pending_lenses(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
) -> Result<usize, sqlx::Error> {
    let mut changed = sqlx::query(
        r#"
        UPDATE briefing_pending_sources AS pending
        SET lens_key = CASE WHEN content.content_type = 'podcast' THEN 'podcasts' ELSE 'articles' END
        FROM contents AS content
        WHERE pending.user_id::bigint = $1 AND pending.source_kind = 'content'
          AND pending.source_id = content.id AND pending.lens_key IS NULL
        "#,
    )
    .bind(user_id)
    .execute(&mut **transaction)
    .await?
    .rows_affected() as usize;
    let unassigned = unassigned_news_rows(transaction, user_id).await?;
    if unassigned.is_empty() {
        return Ok(changed);
    }
    let active_lens_keys = sqlx::query_scalar::<_, String>(
        "SELECT key FROM briefing_lenses WHERE user_id::bigint = $1 AND tier = 'news' AND status = 'active'",
    )
    .bind(user_id)
    .fetch_all(&mut **transaction)
    .await?
    .into_iter()
    .collect::<HashSet<_>>();
    for row in &unassigned {
        let Some(key) = news_topic(&row.raw_metadata)
            .0
            .map(|slug| format!("news-{slug}"))
            .filter(|key| active_lens_keys.contains(key))
        else {
            continue;
        };
        changed += assign_pending_ids(transaction, user_id, &[row.pending_id], &key).await?;
    }
    Ok(changed)
}

pub(super) async fn load_lens_assignment_snapshot(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
) -> Result<BriefingLensAssignmentSnapshot, BriefingRefreshRepositoryError> {
    let pending_sources = unassigned_news_rows(transaction, user_id)
        .await?
        .into_iter()
        .map(|row| BriefingUnassignedSource {
            pending_id: row.pending_id,
            source_kind: "news".to_owned(),
            source_id: row.id,
            enqueued_at: row.enqueued_at.and_utc(),
            source: source_from_news(
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
            ),
        })
        .collect();
    let active_lenses = semantic_lens_rows(transaction, user_id)
        .await?
        .into_iter()
        .map(semantic_lens_from_row)
        .collect::<Result<Vec<_>, _>>()?;
    let all_lens_keys = sqlx::query_scalar::<_, String>(
        "SELECT key FROM briefing_lenses WHERE user_id::bigint = $1 ORDER BY key",
    )
    .bind(user_id)
    .fetch_all(&mut **transaction)
    .await?;
    let active_news_lens_keys = sqlx::query_scalar::<_, String>(
        "SELECT key FROM briefing_lenses WHERE user_id::bigint = $1 AND tier = 'news' AND status = 'active' ORDER BY position, id",
    )
    .bind(user_id)
    .fetch_all(&mut **transaction)
    .await?;
    let next_news_position = next_news_position(transaction, user_id).await?;
    Ok(BriefingLensAssignmentSnapshot {
        pending_sources,
        active_lenses,
        active_news_lens_keys,
        all_lens_keys,
        next_news_position,
    })
}

pub(super) async fn unassigned_news_rows(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
) -> Result<Vec<UnassignedNewsRow>, sqlx::Error> {
    sqlx::query_as(
        r#"
        SELECT pending.id::bigint AS pending_id, pending.enqueued_at,
               news.id::bigint AS id, news.summary_text,
               news.summary_key_points::jsonb AS summary_key_points,
               news.raw_metadata::jsonb AS raw_metadata, news.article_url,
               news.canonical_story_url, news.canonical_item_url,
               news.published_at, news.processed_at, news.ingested_at, news.created_at
        FROM briefing_pending_sources AS pending
        JOIN news_items AS news ON news.id = pending.source_id
        WHERE pending.user_id::bigint = $1 AND pending.source_kind = 'news'
          AND pending.lens_key IS NULL
        ORDER BY pending.enqueued_at, pending.id
        "#,
    )
    .bind(user_id)
    .fetch_all(&mut **transaction)
    .await
}

pub(super) async fn semantic_lens_rows(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
) -> Result<Vec<SemanticLensRow>, sqlx::Error> {
    sqlx::query_as(
        r#"
        SELECT id::bigint AS id, key, title, deck, position, centroid::jsonb AS centroid,
               centroid_weight, centroid_model, routing_rule, updated_at
        FROM briefing_lenses
        WHERE user_id::bigint = $1 AND tier = 'news' AND status = 'active' AND key <> 'misc'
        ORDER BY position, id
        FOR SHARE
        "#,
    )
    .bind(user_id)
    .fetch_all(&mut **transaction)
    .await
}

pub(super) fn semantic_lens_from_row(
    row: SemanticLensRow,
) -> Result<BriefingSemanticLens, BriefingRefreshRepositoryError> {
    let centroid = row
        .centroid
        .map(|value| {
            value
                .as_array()
                .ok_or(BriefingRefreshRepositoryError::InvalidStoredCentroid)?
                .iter()
                .map(|item| {
                    item.as_f64()
                        .filter(|value| value.is_finite())
                        .ok_or(BriefingRefreshRepositoryError::InvalidStoredCentroid)
                })
                .collect::<Result<Vec<_>, _>>()
        })
        .transpose()?;
    Ok(BriefingSemanticLens {
        id: row.id,
        key: row.key,
        title: row.title,
        deck: row.deck,
        position: row.position,
        centroid,
        centroid_weight: row.centroid_weight,
        centroid_model: row.centroid_model,
        routing_rule: row.routing_rule,
        updated_at: row.updated_at.and_utc(),
    })
}

pub(super) async fn load_append_batches(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    mode: BriefingRefreshMode,
    config: &BriefingRefreshConfig,
) -> Result<Vec<BriefingAppendBatch>, BriefingRefreshRepositoryError> {
    let lenses = active_lenses(transaction, user_id).await?;
    let content_rows = pending_content_rows(transaction, user_id).await?;
    let news_rows = pending_news_rows(transaction, user_id).await?;
    let mut rows_by_lens: HashMap<
        String,
        Vec<(
            BriefingPendingIdentity,
            BriefingRefreshSource,
            NaiveDateTime,
        )>,
    > = HashMap::new();
    for row in content_rows {
        rows_by_lens.entry(row.lens_key.clone()).or_default().push((
            BriefingPendingIdentity {
                id: row.pending_id,
                source_kind: "content".to_owned(),
                source_id: row.id,
                lens_key: row.lens_key.clone(),
            },
            source_from_content(
                row.id,
                &row.content_type,
                &row.url,
                row.source_url.as_deref(),
                row.title.as_deref(),
                row.source.as_deref(),
                &row.metadata,
                row.created_at,
                row.publication_date,
            ),
            row.enqueued_at,
        ));
    }
    for row in news_rows {
        rows_by_lens.entry(row.lens_key.clone()).or_default().push((
            BriefingPendingIdentity {
                id: row.pending_id,
                source_kind: "news".to_owned(),
                source_id: row.id,
                lens_key: row.lens_key.clone(),
            },
            source_from_news(
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
            ),
            row.enqueued_at,
        ));
    }
    let now = Utc::now().naive_utc();
    let mut batches = Vec::new();
    for lens in lenses {
        let Some(rows) = rows_by_lens.remove(&lens.key) else {
            continue;
        };
        let ready = lens.tier != "news"
            || mode == BriefingRefreshMode::Full
            || rows.len() >= config.window_min
            || rows
                .first()
                .is_some_and(|row| (now - row.2).num_seconds() >= config.pending_max_age_seconds);
        if !ready {
            continue;
        }
        let (pending_rows, sources): (Vec<_>, Vec<_>) = rows
            .into_iter()
            .map(|(pending, source, _)| (pending, source))
            .unzip();
        batches.push(BriefingAppendBatch {
            lens,
            pending_rows,
            sources,
        });
    }
    Ok(batches)
}

pub(super) async fn active_lenses(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
) -> Result<Vec<BriefingRefreshLens>, sqlx::Error> {
    Ok(sqlx::query_as::<_, LensRow>(
        r#"
        SELECT id::bigint AS id, key, tier, title, deck, position
        FROM briefing_lenses
        WHERE user_id::bigint = $1 AND status = 'active'
        ORDER BY position, id
        "#,
    )
    .bind(user_id)
    .fetch_all(&mut **transaction)
    .await?
    .into_iter()
    .map(BriefingRefreshLens::from)
    .collect())
}

pub(super) async fn pending_content_rows(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
) -> Result<Vec<PendingContentRow>, sqlx::Error> {
    sqlx::query_as(
        r#"
        SELECT pending.id::bigint AS pending_id, pending.lens_key, pending.enqueued_at,
               content.id::bigint AS id, content.content_type, content.url,
               content.source_url, content.title, content.source,
               content.content_metadata::jsonb AS metadata, content.created_at,
               content.publication_date
        FROM briefing_pending_sources AS pending
        JOIN contents AS content ON content.id = pending.source_id
        WHERE pending.user_id::bigint = $1 AND pending.source_kind = 'content'
          AND pending.lens_key IS NOT NULL
        ORDER BY pending.lens_key, pending.enqueued_at, pending.id
        "#,
    )
    .bind(user_id)
    .fetch_all(&mut **transaction)
    .await
}

pub(super) async fn pending_news_rows(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
) -> Result<Vec<PendingNewsRow>, sqlx::Error> {
    sqlx::query_as(
        r#"
        SELECT pending.id::bigint AS pending_id, pending.lens_key, pending.enqueued_at,
               news.id::bigint AS id, news.summary_text,
               news.summary_key_points::jsonb AS summary_key_points,
               news.raw_metadata::jsonb AS raw_metadata, news.article_url,
               news.canonical_story_url, news.canonical_item_url,
               news.published_at, news.processed_at, news.ingested_at, news.created_at
        FROM briefing_pending_sources AS pending
        JOIN news_items AS news ON news.id = pending.source_id
        WHERE pending.user_id::bigint = $1 AND pending.source_kind = 'news'
          AND pending.lens_key IS NOT NULL
        ORDER BY pending.lens_key, pending.enqueued_at, pending.id
        "#,
    )
    .bind(user_id)
    .fetch_all(&mut **transaction)
    .await
}

pub(super) async fn load_compaction_batches(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
) -> Result<Vec<BriefingCompactionBatch>, BriefingRefreshRepositoryError> {
    let segments = sqlx::query_as::<_, SegmentRow>(
        r#"
        SELECT segment.id::bigint AS id, segment.lens_id::bigint AS lens_id,
               lens.key AS lens_key, lens.tier AS lens_tier, lens.title AS lens_title,
               lens.deck AS lens_deck, lens.position AS lens_position,
               segment.source_keys::jsonb AS source_keys,
               segment.event_groups::jsonb AS event_groups
        FROM briefing_segments AS segment
        JOIN briefing_lenses AS lens ON lens.id = segment.lens_id
        WHERE segment.user_id::bigint = $1 AND segment.status IN ('active', 'degraded')
          AND lens.status = 'active'
        ORDER BY lens.position, lens.id, segment.created_at DESC, segment.id DESC
        "#,
    )
    .bind(user_id)
    .fetch_all(&mut **transaction)
    .await?;
    if segments.is_empty() {
        return Ok(Vec::new());
    }
    let all_keys = segments
        .iter()
        .flat_map(|segment| json_strings(&segment.source_keys))
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect::<Vec<_>>();
    let sources = load_eligible_sources_for_keys(transaction, user_id, &all_keys).await?;
    let mut by_lens: BTreeMap<i64, Vec<SegmentRow>> = BTreeMap::new();
    for segment in segments {
        by_lens.entry(segment.lens_id).or_default().push(segment);
    }
    let mut batches = Vec::new();
    for (_lens_id, lens_segments) in by_lens {
        let mut repair_ids = HashSet::new();
        let mut regular_ids = Vec::new();
        for segment in &lens_segments {
            let keys = json_strings(&segment.source_keys);
            if keys.iter().any(|key| !sources.contains_key(key)) {
                repair_ids.insert(segment.id);
            }
            let event_count = segment
                .event_groups
                .as_ref()
                .map_or(keys.len(), event_group_count);
            if (segment.lens_tier == "news" && (1..=2).contains(&event_count))
                || (segment.lens_tier != "news" && keys.len() > 1)
            {
                regular_ids.push(segment.id);
            }
        }
        if lens_segments[0].lens_tier == "news" && regular_ids.len() < 2 {
            regular_ids.clear();
        }
        let repair_required = !repair_ids.is_empty();
        repair_ids.extend(regular_ids);
        if repair_ids.is_empty() {
            continue;
        }
        let donors = lens_segments
            .iter()
            .filter(|segment| repair_ids.contains(&segment.id))
            .map(|segment| BriefingDonorIdentity {
                segment_id: segment.id,
                source_keys: json_strings(&segment.source_keys),
            })
            .collect::<Vec<_>>();
        let mut replacement_keys = Vec::new();
        let mut seen = HashSet::new();
        for donor in &donors {
            for key in &donor.source_keys {
                if sources.contains_key(key) && seen.insert(key.clone()) {
                    replacement_keys.push(key.clone());
                }
            }
        }
        let replacement_sources = replacement_keys
            .iter()
            .filter_map(|key| sources.get(key).cloned())
            .collect::<Vec<_>>();
        let first = &lens_segments[0];
        batches.push(BriefingCompactionBatch {
            lens: BriefingRefreshLens {
                id: first.lens_id,
                key: first.lens_key.clone(),
                tier: first.lens_tier.clone(),
                title: first.lens_title.clone(),
                deck: first.lens_deck.clone(),
                position: first.lens_position,
            },
            donors,
            planned_source_keys: replacement_keys,
            sources: replacement_sources,
            repair_required,
        });
    }
    Ok(batches)
}
