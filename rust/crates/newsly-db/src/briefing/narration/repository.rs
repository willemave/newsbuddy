//! Builds immutable audio chapters from the selected Briefing sources.

use std::collections::HashMap;

use serde_json::json;
use sqlx::{Postgres, Transaction};

use super::{episode_group_id, program::NarrationProgram, source_snapshot, stable_hash};
use crate::briefing::{
    AudioEpisodeRow, BriefingLensProjection, BriefingNarrationSelection, BriefingRepositoryError,
    BriefingSegmentProjection, PrepareNarrationOutcome, SegmentWithLensRow, dedupe_source_keys,
    segment_from_parts,
};
use crate::briefing_refresh::load_eligible_sources_for_keys;
use newsly_domain::BriefingNarrationStyle;

pub async fn prepare_briefing_narration(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    selection: &BriefingNarrationSelection,
    chaptered: bool,
) -> Result<PrepareNarrationOutcome, BriefingRepositoryError> {
    let (lens_key, tier) = match selection {
        BriefingNarrationSelection::Lens(key) | BriefingNarrationSelection::AdaptedLens(key) => {
            (Some(key.as_str()), None)
        }
        BriefingNarrationSelection::ArticleTier => (None, Some("longform")),
        BriefingNarrationSelection::PodcastTier => (None, Some("audio")),
        BriefingNarrationSelection::NewsProgram => (None, Some("news")),
    };
    let lenses = sqlx::query_as::<_, BriefingLensProjection>(
        r#"
        SELECT id::bigint AS id, key, tier, title, deck, position
        FROM briefing_lenses
        WHERE user_id::bigint = $1 AND status = 'active'
          AND ($2::text IS NULL OR key = $2)
          AND ($3::text IS NULL OR tier = $3)
        ORDER BY position, id
        "#,
    )
    .bind(user_id)
    .bind(lens_key)
    .bind(tier)
    .fetch_all(&mut **transaction)
    .await?;
    if lenses.is_empty() && lens_key.is_some() {
        return Ok(PrepareNarrationOutcome::LensNotFound);
    }
    if lenses.is_empty() {
        return Ok(PrepareNarrationOutcome::Empty);
    }
    let lens_ids = lenses.iter().map(|lens| lens.id).collect::<Vec<_>>();
    let segment_rows = sqlx::query_as::<_, SegmentWithLensRow>(
        r#"
        SELECT id::bigint AS id, lens_id::bigint AS lens_id, created_at, status,
               narration_text, blocks::jsonb AS blocks, source_keys::jsonb AS source_keys
        FROM briefing_segments
        WHERE lens_id::bigint = ANY($1::bigint[]) AND status IN ('active', 'degraded')
        ORDER BY lens_id, created_at DESC, id DESC
        "#,
    )
    .bind(&lens_ids)
    .fetch_all(&mut **transaction)
    .await?;
    let mut segments_by_lens = HashMap::<i64, Vec<BriefingSegmentProjection>>::new();
    for row in segment_rows {
        segments_by_lens
            .entry(row.lens_id)
            .or_default()
            .push(segment_from_parts(
                row.id,
                row.created_at,
                row.status,
                row.narration_text,
                row.blocks,
                &row.source_keys,
            ));
    }
    let ordered_segments = lenses
        .iter()
        .flat_map(|lens| segments_by_lens.remove(&lens.id).unwrap_or_default())
        .collect::<Vec<_>>();
    let source_keys = dedupe_source_keys(
        ordered_segments
            .iter()
            .flat_map(|segment| &segment.source_keys),
    );
    let sources = load_eligible_sources_for_keys(transaction, user_id, &source_keys).await?;
    let program = NarrationProgram::resolve(selection, chaptered, &lenses[0])?;
    let plans = program.chapters(ordered_segments, &sources);
    if plans.is_empty() {
        return Ok(PrepareNarrationOutcome::Empty);
    }
    let chaptered = program.chaptered();
    let prompt_version = program.prompt_version;
    let episode_group_id = chaptered.then(|| episode_group_id(&program, &plans, &sources));
    let chapter_count = plans.len();
    let mut episodes = Vec::with_capacity(chapter_count);
    for plan in plans {
        let snapshot = source_snapshot(
            &program,
            episode_group_id.as_deref(),
            chapter_count,
            &plan,
            &sources,
        );
        let input_hash = if let Some(group_id) = &episode_group_id {
            stable_hash(&json!({
                "prompt_version": prompt_version,
                "episode_group_id": group_id,
                "chapter_index": plan.index,
                "source_snapshot": snapshot,
            }))
        } else {
            stable_hash(&json!({
                "prompt_version": prompt_version,
                "source_snapshot": snapshot,
            }))
        };
        let title = program.chapter_title(&plan, &sources);
        let estimated_duration = if chaptered {
            plan.duration_seconds
        } else {
            i32::max(
                30,
                i32::try_from(plan.narration_text.len() / 14).unwrap_or(i32::MAX),
            )
        };
        let script = (program.style == BriefingNarrationStyle::Preauthored).then(|| {
            json!({
                "title": title,
                "estimated_duration_seconds": estimated_duration,
                "turns": [{"speaker": "host", "text": plan.narration_text}],
            })
        });
        let script_text = (program.style == BriefingNarrationStyle::Preauthored)
            .then_some(plan.narration_text.as_str());
        let model =
            (program.style == BriefingNarrationStyle::Preauthored).then_some("deterministic");
        let row = sqlx::query_as::<_, AudioEpisodeRow>(
            r#"
            INSERT INTO audio_episodes (
                user_id, kind, status, title, input_hash, episode_group_id,
                chapter_index, source_item_ids, source_snapshot, script,
                script_text, prompt_version, model, audio_content_type,
                duration_seconds, share_enabled, created_at, updated_at
            ) VALUES (
                $1::bigint::integer, 'briefing_narration', 'pending', $2, $3,
                $4, $5, '[]'::jsonb, $6::jsonb, $7::jsonb, $8, $9,
                $10, 'audio/mpeg', $11, FALSE,
                timezone('UTC', now()), timezone('UTC', now())
            )
            ON CONFLICT (user_id, kind, input_hash) DO UPDATE SET
                title = EXCLUDED.title,
                episode_group_id = EXCLUDED.episode_group_id,
                chapter_index = EXCLUDED.chapter_index,
                source_item_ids = EXCLUDED.source_item_ids,
                source_snapshot = EXCLUDED.source_snapshot,
                script = CASE WHEN audio_episodes.status = 'completed' THEN audio_episodes.script
                              ELSE EXCLUDED.script END,
                script_text = CASE WHEN audio_episodes.status = 'completed' THEN audio_episodes.script_text
                                   ELSE EXCLUDED.script_text END,
                prompt_version = EXCLUDED.prompt_version,
                model = CASE WHEN audio_episodes.status = 'completed' THEN audio_episodes.model
                             ELSE EXCLUDED.model END,
                status = CASE WHEN audio_episodes.status = 'failed' THEN 'pending'
                              ELSE audio_episodes.status END,
                error_message = CASE WHEN audio_episodes.status = 'failed' THEN NULL
                                     ELSE audio_episodes.error_message END,
                audio_storage_path = CASE WHEN audio_episodes.status = 'failed' THEN NULL
                                          ELSE audio_episodes.audio_storage_path END,
                started_at = CASE WHEN audio_episodes.status = 'failed' THEN NULL
                                  ELSE audio_episodes.started_at END,
                completed_at = CASE WHEN audio_episodes.status = 'failed' THEN NULL
                                    ELSE audio_episodes.completed_at END,
                duration_seconds = CASE
                    WHEN audio_episodes.status = 'completed' THEN audio_episodes.duration_seconds
                    ELSE EXCLUDED.duration_seconds
                END,
                updated_at = timezone('UTC', now())
            RETURNING
                id::bigint AS id, kind, status, title,
                source_content_id::bigint AS source_content_id,
                source_item_ids::jsonb AS source_item_ids,
                source_snapshot::jsonb AS source_snapshot, script_text,
                audio_storage_path, duration_seconds, error_message,
                episode_group_id, chapter_index, created_at, updated_at
            "#,
        )
        .bind(user_id)
        .bind(&title)
        .bind(input_hash)
        .bind(episode_group_id.as_deref())
        .bind(chaptered.then_some(plan.index))
        .bind(snapshot)
        .bind(script)
        .bind(script_text)
        .bind(prompt_version)
        .bind(model)
        .bind(chaptered.then_some(plan.duration_seconds))
        .fetch_one(&mut **transaction)
        .await?;
        episodes.push(row.into());
    }
    Ok(PrepareNarrationOutcome::Ready(episodes))
}

/// Restarts only failed chapters, preserving the edition, script checkpoint, and source window.
pub async fn retry_briefing_narration(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    group_id: &str,
) -> Result<Vec<crate::briefing::AudioEpisodeProjection>, BriefingRepositoryError> {
    sqlx::query(
        r#"
        UPDATE audio_episodes
        SET status = 'pending', error_message = NULL, started_at = NULL,
            completed_at = NULL, audio_storage_path = NULL, updated_at = timezone('UTC', now())
        WHERE user_id::bigint = $1 AND kind = 'briefing_narration'
          AND episode_group_id = $2 AND status = 'failed'
        "#,
    )
    .bind(user_id)
    .bind(group_id)
    .execute(&mut **transaction)
    .await?;
    crate::briefing::load_briefing_narration(&mut **transaction, user_id, group_id).await
}
