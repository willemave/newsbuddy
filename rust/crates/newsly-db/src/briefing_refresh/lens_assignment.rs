//! Lease-fenced semantic lens assignment and centroid persistence.

use super::{
    ApplyBriefingLensAssignmentOutcome, BTreeMap, BTreeSet, BriefingLensAssignmentPlan,
    BriefingLensAssignmentSnapshot, BriefingLensAssignmentUsage, BriefingRefreshConfig,
    BriefingRefreshMode, BriefingRefreshRepositoryError, HashMap, HashSet, Postgres,
    PreparedBriefingRefresh, PreparedBriefingRefreshSeed, Transaction, json,
    preparation::{
        load_append_batches, load_compaction_batches, lock_state, semantic_lens_from_row,
        semantic_lens_rows,
    },
    publication::bump_first_edition_revision,
    sources::{load_eligible_sources_for_keys, u64_to_i32},
};

pub async fn apply_briefing_lens_assignment(
    transaction: &mut Transaction<'_, Postgres>,
    seed: PreparedBriefingRefreshSeed,
    plan: &BriefingLensAssignmentPlan,
    config: &BriefingRefreshConfig,
) -> Result<ApplyBriefingLensAssignmentOutcome, BriefingRefreshRepositoryError> {
    config.validate()?;
    validate_lens_assignment_plan(&seed, plan, config)?;
    if !live_refresh_task_is_fenced(transaction, &seed).await? {
        return Ok(ApplyBriefingLensAssignmentOutcome::Stale);
    }
    let current_version = lock_state(transaction, seed.user_id).await?;
    if current_version != seed.starting_version
        || !lens_snapshot_is_current(transaction, seed.user_id, &seed.lens_assignment).await?
        || !pending_snapshot_is_current(transaction, seed.user_id, &seed.lens_assignment).await?
        || !source_snapshot_is_current(transaction, seed.user_id, &seed.lens_assignment).await?
    {
        return Ok(ApplyBriefingLensAssignmentOutcome::Stale);
    }

    for lens in &plan.new_lenses {
        sqlx::query(
            r#"
            INSERT INTO briefing_lenses (
                user_id, key, tier, title, deck, position, status, centroid,
                centroid_weight, centroid_model, created_at, updated_at
            )
            VALUES (
                $1::bigint::integer, $2, 'news', $3, $4, $5, 'active', $6,
                $7, $8, timezone('UTC', clock_timestamp()), timezone('UTC', clock_timestamp())
            )
            ON CONFLICT (user_id, key) DO UPDATE
            SET status = 'active', retired_at = NULL,
                updated_at = timezone('UTC', clock_timestamp())
            "#,
        )
        .bind(seed.user_id)
        .bind(&lens.key)
        .bind(&lens.title)
        .bind(&lens.deck)
        .bind(lens.position)
        .bind(lens.centroid.as_ref().map(|centroid| json!(centroid)))
        .bind(lens.centroid_weight)
        .bind(&lens.centroid_model)
        .execute(&mut **transaction)
        .await?;
    }
    for mutation in &plan.centroid_mutations {
        let updated = sqlx::query(
            r#"
            UPDATE briefing_lenses
            SET centroid = $4, centroid_weight = $5, centroid_model = $6,
                updated_at = timezone('UTC', clock_timestamp())
            WHERE user_id::bigint = $1 AND id::bigint = $2 AND key = $3
              AND tier = 'news' AND status = 'active'
            "#,
        )
        .bind(seed.user_id)
        .bind(mutation.lens_id)
        .bind(&mutation.lens_key)
        .bind(json!(mutation.centroid))
        .bind(mutation.centroid_weight)
        .bind(&mutation.centroid_model)
        .execute(&mut **transaction)
        .await?
        .rows_affected();
        if updated != 1 {
            return Ok(ApplyBriefingLensAssignmentOutcome::Stale);
        }
    }
    let mut changed = 0_usize;
    let mut assignments_by_lens: BTreeMap<&str, Vec<i64>> = BTreeMap::new();
    for assignment in &plan.assignments {
        assignments_by_lens
            .entry(&assignment.lens_key)
            .or_default()
            .push(assignment.pending_id);
    }
    for (lens_key, pending_ids) in assignments_by_lens {
        changed += assign_pending_ids(transaction, seed.user_id, &pending_ids, lens_key).await?;
    }
    if changed != plan.assignments.len() {
        return Ok(ApplyBriefingLensAssignmentOutcome::Stale);
    }
    persist_lens_assignment_usage(transaction, &seed, &plan.usage).await?;

    let append_batches = load_append_batches(transaction, seed.user_id, seed.mode, config).await?;
    let compaction_batches = if seed.mode == BriefingRefreshMode::Full {
        Vec::new()
    } else {
        load_compaction_batches(transaction, seed.user_id).await?
    };
    if seed.mode != BriefingRefreshMode::Full
        && (seed.pending_added > 0
            || seed.prepared_state_changed
            || changed > 0
            || !append_batches.is_empty())
    {
        bump_first_edition_revision(transaction, seed.user_id).await?;
    }
    Ok(ApplyBriefingLensAssignmentOutcome::Ready(
        PreparedBriefingRefresh {
            task_id: seed.task_id,
            user_id: seed.user_id,
            mode: seed.mode,
            starting_version: seed.starting_version,
            prepared_state_changed: seed.prepared_state_changed || changed > 0,
            append_batches,
            compaction_batches,
        },
    ))
}

pub(super) fn validate_lens_assignment_plan(
    seed: &PreparedBriefingRefreshSeed,
    plan: &BriefingLensAssignmentPlan,
    config: &BriefingRefreshConfig,
) -> Result<(), BriefingRefreshRepositoryError> {
    if plan.task_id != seed.task_id
        || plan.user_id != seed.user_id
        || plan.starting_version != seed.starting_version
    {
        return Err(BriefingRefreshRepositoryError::InvalidLensAssignmentPlan(
            "plan does not belong to the prepared refresh".to_owned(),
        ));
    }
    let pending = seed
        .lens_assignment
        .pending_sources
        .iter()
        .map(|source| {
            (
                source.pending_id,
                (source.source_kind.as_str(), source.source_id),
            )
        })
        .collect::<HashMap<_, _>>();
    let mut assigned_ids = HashSet::new();
    let new_keys = plan
        .new_lenses
        .iter()
        .map(|lens| lens.key.as_str())
        .collect::<HashSet<_>>();
    let active_keys = seed
        .lens_assignment
        .active_news_lens_keys
        .iter()
        .map(String::as_str)
        .collect::<HashSet<_>>();
    if new_keys.len() != plan.new_lenses.len()
        || seed
            .lens_assignment
            .active_news_lens_keys
            .len()
            .saturating_add(plan.new_lenses.len())
            > config.max_news_lenses
    {
        return Err(BriefingRefreshRepositoryError::InvalidLensAssignmentPlan(
            "planned lenses are duplicated or exceed the configured cap".to_owned(),
        ));
    }
    for lens in &plan.new_lenses {
        if (seed.lens_assignment.all_lens_keys.contains(&lens.key) && lens.key != "misc")
            || !valid_lens_key(&lens.key)
            || !(2..=40).contains(&lens.title.chars().count())
            || !(8..=180).contains(&lens.deck.chars().count())
            || lens.centroid_weight < 0
            || !valid_optional_vector(lens.centroid.as_deref())
        {
            return Err(BriefingRefreshRepositoryError::InvalidLensAssignmentPlan(
                "planned lens fields are invalid".to_owned(),
            ));
        }
    }
    for assignment in &plan.assignments {
        if !assigned_ids.insert(assignment.pending_id)
            || pending.get(&assignment.pending_id)
                != Some(&(assignment.source_kind.as_str(), assignment.source_id))
            || (!active_keys.contains(assignment.lens_key.as_str())
                && !new_keys.contains(assignment.lens_key.as_str()))
        {
            return Err(BriefingRefreshRepositoryError::InvalidLensAssignmentPlan(
                "pending assignment is duplicated or does not match the snapshot".to_owned(),
            ));
        }
    }
    let semantic_lenses = seed
        .lens_assignment
        .active_lenses
        .iter()
        .map(|lens| ((lens.id, lens.key.as_str()), lens))
        .collect::<HashMap<_, _>>();
    let mut mutation_ids = HashSet::new();
    for mutation in &plan.centroid_mutations {
        if !mutation_ids.insert(mutation.lens_id)
            || !semantic_lenses.contains_key(&(mutation.lens_id, mutation.lens_key.as_str()))
            || mutation.centroid_weight <= 0
            || mutation.centroid_weight > config.centroid_max_weight
            || mutation.centroid_model.trim().is_empty()
            || !valid_vector(&mutation.centroid)
        {
            return Err(BriefingRefreshRepositoryError::InvalidLensAssignmentPlan(
                "centroid mutation is invalid or stale".to_owned(),
            ));
        }
    }
    Ok(())
}

pub(super) fn valid_lens_key(key: &str) -> bool {
    key == "misc"
        || (key.starts_with("news-")
            && key.len() <= 64
            && key
                .bytes()
                .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'-'))
}

pub(super) fn valid_vector(vector: &[f64]) -> bool {
    !vector.is_empty() && vector.iter().all(|value| value.is_finite())
}

pub(super) fn valid_optional_vector(vector: Option<&[f64]>) -> bool {
    vector.is_none_or(valid_vector)
}

pub(super) async fn live_refresh_task_is_fenced(
    transaction: &mut Transaction<'_, Postgres>,
    seed: &PreparedBriefingRefreshSeed,
) -> Result<bool, sqlx::Error> {
    Ok(sqlx::query_scalar::<_, i64>(
        r#"
        SELECT id::bigint FROM processing_tasks
        WHERE id::bigint = $1 AND task_type = 'briefing_refresh'
          AND owner_user_id::bigint = $2 AND status = 'processing'
          AND locked_by = $3 AND lease_token = $4
          AND retry_count = $5 AND executor_runtime = $6
          AND executor_version = $7 AND executor_namespace = $8
          AND lease_expires_at > timezone('UTC', clock_timestamp())
        FOR SHARE
        "#,
    )
    .bind(seed.task_id)
    .bind(seed.user_id)
    .bind(&seed.claim_fence.locked_by)
    .bind(seed.claim_fence.lease_token)
    .bind(seed.claim_fence.retry_count)
    .bind(&seed.claim_fence.executor_runtime)
    .bind(seed.claim_fence.executor_version)
    .bind(&seed.claim_fence.executor_namespace)
    .fetch_optional(&mut **transaction)
    .await?
    .is_some())
}

pub(super) async fn pending_snapshot_is_current(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    snapshot: &BriefingLensAssignmentSnapshot,
) -> Result<bool, sqlx::Error> {
    let ids = snapshot
        .pending_sources
        .iter()
        .map(|source| source.pending_id)
        .collect::<Vec<_>>();
    if ids.is_empty() {
        return Ok(true);
    }
    let rows = sqlx::query_as::<_, (i64, String, i64, Option<String>)>(
        r#"
        SELECT id::bigint, source_kind, source_id::bigint, lens_key
        FROM briefing_pending_sources
        WHERE user_id::bigint = $1 AND id::bigint = ANY($2::bigint[])
        ORDER BY id
        FOR UPDATE
        "#,
    )
    .bind(user_id)
    .bind(&ids)
    .fetch_all(&mut **transaction)
    .await?;
    let expected = snapshot
        .pending_sources
        .iter()
        .map(|source| {
            (
                source.pending_id,
                source.source_kind.as_str(),
                source.source_id,
            )
        })
        .collect::<BTreeSet<_>>();
    let actual = rows
        .iter()
        .filter(|(_, _, _, lens_key)| lens_key.is_none())
        .map(|(id, kind, source_id, _)| (*id, kind.as_str(), *source_id))
        .collect::<BTreeSet<_>>();
    Ok(rows.len() == expected.len() && actual == expected)
}

pub(super) async fn lens_snapshot_is_current(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    snapshot: &BriefingLensAssignmentSnapshot,
) -> Result<bool, BriefingRefreshRepositoryError> {
    let current_lenses = semantic_lens_rows(transaction, user_id)
        .await?
        .into_iter()
        .map(semantic_lens_from_row)
        .collect::<Result<Vec<_>, _>>()?;
    let all_keys = sqlx::query_scalar::<_, String>(
        "SELECT key FROM briefing_lenses WHERE user_id::bigint = $1 ORDER BY key FOR SHARE",
    )
    .bind(user_id)
    .fetch_all(&mut **transaction)
    .await?;
    let active_keys = sqlx::query_scalar::<_, String>(
        "SELECT key FROM briefing_lenses WHERE user_id::bigint = $1 AND tier = 'news' AND status = 'active' ORDER BY position, id FOR SHARE",
    )
    .bind(user_id)
    .fetch_all(&mut **transaction)
    .await?;
    Ok(current_lenses == snapshot.active_lenses
        && all_keys == snapshot.all_lens_keys
        && active_keys == snapshot.active_news_lens_keys)
}

pub(super) async fn source_snapshot_is_current(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    snapshot: &BriefingLensAssignmentSnapshot,
) -> Result<bool, BriefingRefreshRepositoryError> {
    let keys = snapshot
        .pending_sources
        .iter()
        .map(|pending| pending.source.source_key.clone())
        .collect::<Vec<_>>();
    if keys.is_empty() {
        return Ok(true);
    }
    let current = load_eligible_sources_for_keys(transaction, user_id, &keys).await?;
    Ok(current.len() == snapshot.pending_sources.len()
        && snapshot
            .pending_sources
            .iter()
            .all(|pending| current.get(&pending.source.source_key) == Some(&pending.source)))
}

pub(super) async fn persist_lens_assignment_usage(
    transaction: &mut Transaction<'_, Postgres>,
    seed: &PreparedBriefingRefreshSeed,
    usages: &[BriefingLensAssignmentUsage],
) -> Result<(), sqlx::Error> {
    for usage in usages {
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
        .bind(seed.task_id)
        .bind(seed.user_id)
        .bind(u64_to_i32(usage.usage.request_count))
        .bind(u64_to_i32(usage.usage.input_tokens))
        .bind(u64_to_i32(usage.usage.cached_input_tokens))
        .bind(u64_to_i32(usage.usage.cache_write_tokens))
        .bind(u64_to_i32(usage.usage.output_tokens))
        .bind(u64_to_i32(total_tokens))
        .execute(&mut **transaction)
        .await?;
    }
    Ok(())
}

pub(super) async fn next_news_position(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
) -> Result<i32, sqlx::Error> {
    sqlx::query_scalar::<_, i32>(
        "SELECT COALESCE(max(position), 1) + 1 FROM briefing_lenses WHERE user_id::bigint = $1",
    )
    .bind(user_id)
    .fetch_one(&mut **transaction)
    .await
}

pub(super) async fn assign_pending_ids(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    ids: &[i64],
    lens_key: &str,
) -> Result<usize, sqlx::Error> {
    let changed = sqlx::query(
        "UPDATE briefing_pending_sources SET lens_key = $3 WHERE user_id::bigint = $1 AND id::bigint = ANY($2::bigint[]) AND lens_key IS NULL",
    )
    .bind(user_id)
    .bind(ids)
    .bind(lens_key)
    .execute(&mut **transaction)
    .await?
    .rows_affected() as usize;
    if changed > 0 {
        sqlx::query(
            "UPDATE briefing_lenses SET updated_at = timezone('UTC', clock_timestamp()) WHERE user_id::bigint = $1 AND key = $2",
        )
        .bind(user_id)
        .bind(lens_key)
        .execute(&mut **transaction)
        .await?;
    }
    Ok(changed)
}
