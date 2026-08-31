//! Final Briefing publication, compaction, retirement, and usage persistence.

use super::{
    BTreeMap, BTreeSet, BriefingRefreshApplyOutcome, BriefingRefreshConfig, BriefingRefreshMode,
    BriefingRefreshPublication, BriefingRefreshRepositoryError, BriefingSegmentUsage,
    ComposedBriefingAppend, ComposedBriefingCompaction, ComposedBriefingSegment,
    DEFAULT_MASTHEAD_DECK, HashSet, NaiveDateTime, Postgres, PreparedBriefingRefresh, Transaction,
    Utc, Value, json,
    preparation::{ensure_state, lock_state},
    sources::{json_strings, load_eligible_sources_for_keys, load_read_source_keys, u64_to_i32},
};

pub async fn apply_briefing_refresh(
    transaction: &mut Transaction<'static, Postgres>,
    publication: &BriefingRefreshPublication,
    config: &BriefingRefreshConfig,
) -> Result<BriefingRefreshApplyOutcome, BriefingRefreshRepositoryError> {
    let prepared = &publication.prepared;
    ensure_state(transaction, prepared.user_id, config).await?;
    let current_version = lock_state(transaction, prepared.user_id).await?;
    let version_stale = current_version != prepared.starting_version;
    persist_composition_usage(transaction, publication).await?;
    let append_current = if version_stale {
        false
    } else {
        append_publication_is_current(transaction, prepared.user_id, &publication.append_segments)
            .await?
    };

    let mut appended = 0_usize;
    let mut compacted = 0_usize;
    if prepared.mode == BriefingRefreshMode::Full && append_current {
        compacted += sqlx::query(
            r#"
            UPDATE briefing_segments
            SET status = 'compacted', updated_at = timezone('UTC', clock_timestamp())
            WHERE user_id::bigint = $1 AND status IN ('active', 'degraded')
            "#,
        )
        .bind(prepared.user_id)
        .execute(&mut **transaction)
        .await?
        .rows_affected() as usize;
    }
    if append_current {
        for composed in &publication.append_segments {
            persist_segment(transaction, prepared, &composed.segment).await?;
            let ids = composed
                .pending_rows
                .iter()
                .map(|row| row.id)
                .collect::<Vec<_>>();
            let deleted = sqlx::query(
                "DELETE FROM briefing_pending_sources WHERE user_id::bigint = $1 AND id::bigint = ANY($2::bigint[])",
            )
            .bind(prepared.user_id)
            .bind(&ids)
            .execute(&mut **transaction)
            .await?
            .rows_affected() as usize;
            if deleted != ids.len() {
                return Err(BriefingRefreshRepositoryError::PendingOwnershipLost);
            }
            appended += 1;
        }
    }
    if !version_stale {
        for plan in &publication.compactions {
            if compaction_is_current(transaction, prepared.user_id, plan).await? {
                for segment in &plan.segments {
                    persist_segment(transaction, prepared, segment).await?;
                }
                let donor_ids = plan
                    .donors
                    .iter()
                    .map(|donor| donor.segment_id)
                    .collect::<Vec<_>>();
                compacted += sqlx::query(
                    r#"
                    UPDATE briefing_segments
                    SET status = 'compacted', updated_at = timezone('UTC', clock_timestamp())
                    WHERE user_id::bigint = $1 AND id::bigint = ANY($2::bigint[])
                    "#,
                )
                .bind(prepared.user_id)
                .bind(&donor_ids)
                .execute(&mut **transaction)
                .await?
                .rows_affected() as usize;
            }
        }
    }
    persist_embedding_usage(transaction, publication).await?;
    let retired = retire_read_segments(transaction, prepared.user_id).await?;
    let idle_retired = retire_idle_lenses(transaction, prepared.user_id, config).await?;
    let visible_mutation = appended > 0
        || compacted > 0
        || retired > 0
        || idle_retired > 0
        || prepared.prepared_state_changed;
    let version = if visible_mutation {
        current_version.saturating_add(1)
    } else {
        current_version
    };
    let last_append = (appended > 0).then_some(publication.finalized_at.naive_utc());
    let masthead_deck = if appended > 0 {
        Some(masthead_deck(transaction, prepared.user_id).await?)
    } else {
        None
    };
    sqlx::query(
        r#"
        UPDATE briefing_states
        SET version = $2,
            last_sweep_at = $3,
            last_append_at = COALESCE($4, last_append_at),
            masthead_deck = COALESCE($5, masthead_deck)
        WHERE user_id::bigint = $1
        "#,
    )
    .bind(prepared.user_id)
    .bind(version)
    .bind(publication.finalized_at.naive_utc())
    .bind(last_append)
    .bind(masthead_deck)
    .execute(&mut **transaction)
    .await?;
    if prepared.mode == BriefingRefreshMode::Sweep {
        sqlx::query(
            "UPDATE processing_tasks SET dedupe_key = NULL WHERE id::bigint = $1 AND task_type = 'briefing_refresh' AND status = 'processing'",
        )
        .bind(prepared.task_id)
        .execute(&mut **transaction)
        .await?;
    }
    let delay = next_sweep_delay(transaction, prepared.user_id, config).await?;
    Ok(BriefingRefreshApplyOutcome {
        version,
        appended_segments: appended,
        compacted_segments: compacted,
        retired_segments: retired + idle_retired,
        stale: version_stale || !append_current,
        next_sweep_delay_seconds: delay,
    })
}

pub(super) async fn append_publication_is_current(
    transaction: &mut Transaction<'static, Postgres>,
    user_id: i64,
    composed: &[ComposedBriefingAppend],
) -> Result<bool, BriefingRefreshRepositoryError> {
    let expected = composed
        .iter()
        .flat_map(|window| window.pending_rows.iter())
        .map(|row| {
            (
                row.id,
                (row.source_kind.clone(), row.source_id, row.lens_key.clone()),
            )
        })
        .collect::<BTreeMap<_, _>>();
    if expected.is_empty() {
        return Ok(true);
    }
    let ids = expected.keys().copied().collect::<Vec<_>>();
    let actual = sqlx::query_as::<_, (i64, String, i64, String)>(
        r#"
        SELECT id::bigint, source_kind, source_id::bigint, lens_key
        FROM briefing_pending_sources
        WHERE user_id::bigint = $1 AND id::bigint = ANY($2::bigint[])
        FOR UPDATE
        "#,
    )
    .bind(user_id)
    .bind(&ids)
    .fetch_all(&mut **transaction)
    .await?
    .into_iter()
    .map(|(id, kind, source_id, lens)| (id, (kind, source_id, lens)))
    .collect::<BTreeMap<_, _>>();
    if actual != expected {
        return Ok(false);
    }
    let source_keys = composed
        .iter()
        .flat_map(|window| window.segment.source_keys.iter().cloned())
        .collect::<Vec<_>>();
    let eligible = load_eligible_sources_for_keys(transaction, user_id, &source_keys).await?;
    Ok(eligible.len() == source_keys.iter().collect::<HashSet<_>>().len())
}

pub(super) async fn compaction_is_current(
    transaction: &mut Transaction<'static, Postgres>,
    user_id: i64,
    plan: &ComposedBriefingCompaction,
) -> Result<bool, BriefingRefreshRepositoryError> {
    let expected = plan
        .donors
        .iter()
        .map(|donor| (donor.segment_id, donor.source_keys.clone()))
        .collect::<BTreeMap<_, _>>();
    let ids = expected.keys().copied().collect::<Vec<_>>();
    let actual = sqlx::query_as::<_, (i64, Value)>(
        r#"
        SELECT id::bigint, source_keys::jsonb
        FROM briefing_segments
        WHERE user_id::bigint = $1 AND id::bigint = ANY($2::bigint[])
          AND status IN ('active', 'degraded')
        FOR UPDATE
        "#,
    )
    .bind(user_id)
    .bind(&ids)
    .fetch_all(&mut **transaction)
    .await?
    .into_iter()
    .map(|(id, keys)| (id, json_strings(&keys)))
    .collect::<BTreeMap<_, _>>();
    if actual != expected {
        return Ok(false);
    }
    let all = expected
        .values()
        .flat_map(|keys| keys.iter().cloned())
        .collect::<Vec<_>>();
    let eligible = load_eligible_sources_for_keys(transaction, user_id, &all).await?;
    let current = all
        .into_iter()
        .filter(|key| eligible.contains_key(key))
        .collect::<BTreeSet<_>>();
    Ok(current
        == plan
            .planned_source_keys
            .iter()
            .cloned()
            .collect::<BTreeSet<_>>())
}

pub(super) async fn persist_segment(
    transaction: &mut Transaction<'static, Postgres>,
    prepared: &PreparedBriefingRefresh,
    segment: &ComposedBriefingSegment,
) -> Result<(), BriefingRefreshRepositoryError> {
    sqlx::query(
        r#"
        INSERT INTO briefing_segments (
            lens_id, user_id, blocks, markdown_raw, narration_text, source_keys,
            event_groups, status, model, prompt_version, input_tokens, output_tokens,
            generation_ms, warnings, created_at, updated_at
        )
        VALUES (
            $1::bigint::integer, $2::bigint::integer, $3, $4, $5, $6, $7,
            'active', $8, $9, $10, $11, $12, $13,
            timezone('UTC', clock_timestamp()), timezone('UTC', clock_timestamp())
        )
        "#,
    )
    .bind(segment.lens.id)
    .bind(prepared.user_id)
    .bind(&segment.blocks)
    .bind(&segment.markdown_raw)
    .bind(&segment.narration_text)
    .bind(json!(segment.source_keys))
    .bind(json!(segment.event_groups))
    .bind(&segment.model)
    .bind(&segment.prompt_version)
    .bind(segment.input_tokens)
    .bind(segment.output_tokens)
    .bind(segment.generation_ms)
    .bind(json!(segment.warnings))
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

pub(super) async fn persist_composition_usage(
    transaction: &mut Transaction<'static, Postgres>,
    publication: &BriefingRefreshPublication,
) -> Result<(), sqlx::Error> {
    for segment in publication
        .append_segments
        .iter()
        .map(|window| &window.segment)
        .chain(
            publication
                .compactions
                .iter()
                .flat_map(|plan| plan.segments.iter()),
        )
    {
        persist_usage(transaction, &publication.prepared, &segment.usage).await?;
    }
    Ok(())
}

pub(super) async fn persist_usage(
    transaction: &mut Transaction<'static, Postgres>,
    prepared: &PreparedBriefingRefresh,
    usage: &BriefingSegmentUsage,
) -> Result<(), sqlx::Error> {
    let total_tokens = usage
        .usage
        .input_tokens
        .saturating_add(usage.usage.output_tokens);
    sqlx::query(
        r#"
        INSERT INTO vendor_usage_records (
            provider, model, feature, operation, source, request_id, task_id, user_id,
            request_count, input_tokens, cache_read_tokens, cache_write_tokens,
            output_tokens, total_tokens,
            currency, pricing_version, metadata, created_at
        )
        VALUES (
            $1, $2, 'briefing_compose', $3, 'queue', $4,
            $5::bigint::integer, $6::bigint::integer, $7, $8, $9, $10, $11, $12,
            'USD', '2026-08-02', $13, timezone('UTC', clock_timestamp())
        )
        "#,
    )
    .bind(&usage.provider)
    .bind(&usage.model)
    .bind(&usage.operation)
    .bind(&usage.provider_response_id)
    .bind(prepared.task_id)
    .bind(prepared.user_id)
    .bind(u64_to_i32(usage.usage.request_count))
    .bind(u64_to_i32(usage.usage.input_tokens))
    .bind(u64_to_i32(usage.usage.cached_input_tokens))
    .bind(u64_to_i32(usage.usage.cache_write_tokens))
    .bind(u64_to_i32(usage.usage.output_tokens))
    .bind(u64_to_i32(total_tokens))
    .bind(json!({
        "prompt_version": "briefing-v6",
        "reasoning_tokens": usage.usage.reasoning_tokens,
        "input_audio_tokens": usage.usage.input_audio_tokens,
        "output_audio_tokens": usage.usage.output_audio_tokens,
    }))
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

pub(super) async fn persist_embedding_usage(
    transaction: &mut Transaction<'static, Postgres>,
    publication: &BriefingRefreshPublication,
) -> Result<(), sqlx::Error> {
    for usage in &publication.embedding_usage {
        let total = usage
            .usage
            .input_tokens
            .saturating_add(usage.usage.output_tokens);
        sqlx::query(
            r#"
            INSERT INTO vendor_usage_records (
                provider, model, feature, operation, source, request_id, task_id, user_id,
                request_count, input_tokens, output_tokens, total_tokens,
                currency, pricing_version, metadata, created_at
            )
            VALUES (
                $1, $2, 'briefing_event_grouping', 'briefing.embed_events', 'queue', $3,
                $4::bigint::integer, $5::bigint::integer, $6, $7, $8, $9,
                'USD', '2026-08-02', '{}'::jsonb, timezone('UTC', clock_timestamp())
            )
            "#,
        )
        .bind(&usage.provider)
        .bind(&usage.model)
        .bind(&usage.provider_response_id)
        .bind(publication.prepared.task_id)
        .bind(publication.prepared.user_id)
        .bind(u64_to_i32(usage.usage.request_count))
        .bind(u64_to_i32(usage.usage.input_tokens))
        .bind(u64_to_i32(usage.usage.output_tokens))
        .bind(u64_to_i32(total))
        .execute(&mut **transaction)
        .await?;
    }
    Ok(())
}

pub(super) async fn retire_read_segments(
    transaction: &mut Transaction<'static, Postgres>,
    user_id: i64,
) -> Result<usize, BriefingRefreshRepositoryError> {
    let segments = sqlx::query_as::<_, (i64, Value)>(
        "SELECT id::bigint, source_keys::jsonb FROM briefing_segments WHERE user_id::bigint = $1 AND status IN ('active', 'degraded') FOR UPDATE",
    )
    .bind(user_id)
    .fetch_all(&mut **transaction)
    .await?;
    let all_keys = segments
        .iter()
        .flat_map(|(_, keys)| json_strings(keys))
        .collect::<Vec<_>>();
    let read = load_read_source_keys(transaction, user_id, &all_keys).await?;
    let ids = segments
        .into_iter()
        .filter_map(|(id, keys)| {
            let keys = json_strings(&keys);
            (!keys.is_empty() && keys.iter().all(|key| read.contains(key))).then_some(id)
        })
        .collect::<Vec<_>>();
    if ids.is_empty() {
        return Ok(0);
    }
    Ok(sqlx::query(
        "UPDATE briefing_segments SET status = 'retired', updated_at = timezone('UTC', clock_timestamp()) WHERE id::bigint = ANY($1::bigint[])",
    )
    .bind(&ids)
    .execute(&mut **transaction)
    .await?
    .rows_affected() as usize)
}

pub(super) async fn retire_idle_lenses(
    transaction: &mut Transaction<'static, Postgres>,
    user_id: i64,
    config: &BriefingRefreshConfig,
) -> Result<usize, sqlx::Error> {
    Ok(sqlx::query(
        r#"
        UPDATE briefing_lenses AS lens
        SET status = 'retired', retired_at = timezone('UTC', clock_timestamp()),
            updated_at = timezone('UTC', clock_timestamp())
        WHERE lens.user_id::bigint = $1 AND lens.status = 'active'
          AND lens.key NOT IN ('podcasts', 'articles')
          AND lens.updated_at < timezone('UTC', clock_timestamp()) - ($2::text || ' days')::interval
          AND NOT EXISTS (
              SELECT 1 FROM briefing_segments AS segment
              WHERE segment.lens_id = lens.id AND segment.status IN ('active', 'degraded')
          )
          AND NOT EXISTS (
              SELECT 1 FROM briefing_pending_sources AS pending
              WHERE pending.user_id = lens.user_id AND pending.lens_key = lens.key
          )
        "#,
    )
    .bind(user_id)
    .bind(i32::try_from(config.lens_idle_days).unwrap_or(i32::MAX))
    .execute(&mut **transaction)
    .await?
    .rows_affected() as usize)
}

pub(super) async fn masthead_deck(
    transaction: &mut Transaction<'static, Postgres>,
    user_id: i64,
) -> Result<String, sqlx::Error> {
    let titles = sqlx::query_scalar::<_, String>(
        "SELECT title FROM briefing_lenses WHERE user_id::bigint = $1 AND status = 'active' ORDER BY position LIMIT 4",
    )
    .bind(user_id)
    .fetch_all(&mut **transaction)
    .await?;
    Ok(if titles.is_empty() {
        DEFAULT_MASTHEAD_DECK.to_owned()
    } else {
        format!(
            "New unread segments are ready across {}.",
            titles.join(", ")
        )
    })
}

pub(super) async fn next_sweep_delay(
    transaction: &mut Transaction<'static, Postgres>,
    user_id: i64,
    config: &BriefingRefreshConfig,
) -> Result<i64, sqlx::Error> {
    let deadline = sqlx::query_scalar::<_, Option<NaiveDateTime>>(
        r#"
        WITH pending_by_lens AS (
            SELECT coalesce(pending.lens_key, '__unassigned__') AS lens_key,
                   count(*)::bigint AS source_count,
                   min(pending.enqueued_at) AS oldest_at
            FROM briefing_pending_sources AS pending
            LEFT JOIN briefing_lenses AS lens
              ON lens.user_id = pending.user_id AND lens.key = pending.lens_key
            WHERE pending.user_id::bigint = $1 AND pending.source_kind = 'news'
              AND (
                  pending.lens_key IS NULL
                  OR (lens.status = 'active' AND lens.tier = 'news')
              )
            GROUP BY coalesce(pending.lens_key, '__unassigned__')
        )
        SELECT min(
            CASE WHEN source_count >= $2
                 THEN timezone('UTC', clock_timestamp())
                 ELSE oldest_at + ($3::text || ' seconds')::interval
            END
        )
        FROM pending_by_lens
        "#,
    )
    .bind(user_id)
    .bind(i64::try_from(config.window_min).unwrap_or(i64::MAX))
    .bind(config.pending_max_age_seconds)
    .fetch_one(&mut **transaction)
    .await?;
    let pending_delay =
        deadline.map(|deadline| (deadline - Utc::now().naive_utc()).num_seconds().max(0));
    Ok(pending_delay.map_or(config.sweep_seconds, |delay| {
        config.sweep_seconds.min(delay)
    }))
}

pub(super) async fn bump_first_edition_revision(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
) -> Result<(), sqlx::Error> {
    sqlx::query(
        r#"
        UPDATE onboarding_first_edition_runs
        SET revision = revision + 1
        WHERE user_id::bigint = $1 AND status = 'active'
        "#,
    )
    .bind(user_id)
    .execute(&mut **transaction)
    .await?;
    Ok(())
}
