use super::{NewsCategoryCandidate, NewsCategoryPublicationLens, NewsCategorySnapshot};
use crate::briefing_refresh::{BriefingRefreshRepositoryError, lens_assignment::valid_lens_key};
use serde_json::json;
use sqlx::{PgPool, Postgres, Transaction};
use std::collections::{HashMap, HashSet};

pub async fn load_candidate(
    pool: &PgPool,
    user_id: i64,
) -> Result<Option<NewsCategoryCandidate>, sqlx::Error> {
    sqlx::query_as(
        "SELECT input_hash,candidate,naming_result FROM news_category_candidates WHERE user_id::bigint=$1",
    ).bind(user_id).fetch_optional(pool).await
}

pub async fn save_candidate(
    tx: &mut Transaction<'_, Postgres>,
    user_id: i64,
    candidate: &NewsCategoryCandidate,
) -> Result<(), sqlx::Error> {
    sqlx::query(r#"INSERT INTO news_category_candidates(user_id,input_hash,candidate,naming_result,updated_at)
      VALUES($1,$2,$3,$4,timezone('UTC',clock_timestamp()))
      ON CONFLICT(user_id) DO UPDATE SET input_hash=EXCLUDED.input_hash,candidate=EXCLUDED.candidate,
        naming_result=EXCLUDED.naming_result,updated_at=EXCLUDED.updated_at"#)
        .bind(user_id).bind(&candidate.input_hash).bind(&candidate.candidate)
        .bind(&candidate.naming_result).execute(&mut **tx).await?;
    Ok(())
}

/// Caller must first pass snapshot and schedule fences in the same transaction.
/// Composed segments, read identities and narration snapshots are never rewritten.
pub async fn apply_partition(
    tx: &mut Transaction<'_, Postgres>,
    snapshot: &NewsCategorySnapshot,
    lenses: &[NewsCategoryPublicationLens],
) -> Result<(), BriefingRefreshRepositoryError> {
    if lenses.is_empty() {
        return Ok(());
    }
    validate_partition(snapshot, lenses)?;
    let user_id = snapshot.user_id;
    sqlx::query("UPDATE briefing_lenses SET accepts_news=false WHERE user_id::bigint=$1 AND tier='news' AND key<>'misc' AND accepts_news")
        .bind(user_id).execute(&mut **tx).await?;
    let mut next_position: i32 = sqlx::query_scalar(
        "SELECT coalesce(max(position),1)+1 FROM briefing_lenses WHERE user_id::bigint=$1",
    )
    .bind(user_id)
    .fetch_one(&mut **tx)
    .await?;
    let centroid_model = format!("openrouter:{}", snapshot.model);
    let mut assignments = HashMap::new();
    for lens in lenses {
        let weight = i32::try_from(lens.story_ids.len())
            .unwrap_or(32)
            .clamp(1, 32);
        sqlx::query(r#"INSERT INTO briefing_lenses(user_id,key,tier,title,deck,position,status,
            centroid,centroid_weight,centroid_model,routing_rule,accepts_news)
            VALUES($1,$2,'news',$3,$4,$5,'active',$6,$7,$8,$9,true)
            ON CONFLICT(user_id,key) DO UPDATE SET title=EXCLUDED.title,deck=EXCLUDED.deck,
            centroid=EXCLUDED.centroid,centroid_weight=EXCLUDED.centroid_weight,
            centroid_model=EXCLUDED.centroid_model,routing_rule=EXCLUDED.routing_rule,
            accepts_news=true,status='active',retired_at=NULL,updated_at=timezone('UTC',clock_timestamp())"#)
            .bind(user_id).bind(&lens.key).bind(&lens.title).bind(&lens.deck).bind(next_position)
            .bind(json!(lens.centroid)).bind(weight).bind(&centroid_model).bind(&lens.routing_rule)
            .execute(&mut **tx).await?;
        next_position = next_position.saturating_add(1);
        for id in &lens.story_ids {
            assignments.insert(*id, lens.key.as_str());
        }
    }
    // Mixed coverage is a utility lens, separate from the bounded semantic topics.
    sqlx::query(r#"INSERT INTO briefing_lenses(user_id,key,tier,title,deck,position,status,centroid_weight,accepts_news)
      VALUES($1,'misc','news','More news','Stories outside your current recurring topics.',$2,'active',0,true)
      ON CONFLICT(user_id,key) DO UPDATE SET accepts_news=true,status='active',retired_at=NULL"#)
        .bind(user_id).bind(next_position).execute(&mut **tx).await?;
    let fitting_ids: HashSet<i64> = snapshot.stories.iter().map(|s| s.source.id).collect();
    let (pending_ids, pending_lens_keys): (Vec<i64>, Vec<String>) = snapshot
        .pending
        .iter()
        .filter(|pending| fitting_ids.contains(&pending.source_id))
        .map(|pending| {
            let key = assignments
                .get(&pending.source_id)
                .copied()
                .unwrap_or("misc");
            (pending.id, key.to_owned())
        })
        .unzip();
    sqlx::query(
        r#"UPDATE briefing_pending_sources AS pending
           SET lens_key=assignment.lens_key
           FROM UNNEST($2::bigint[],$3::text[]) AS assignment(id,lens_key)
           WHERE pending.id::bigint=assignment.id AND pending.user_id::bigint=$1
             AND pending.source_kind='news'"#,
    )
    .bind(user_id)
    .bind(&pending_ids)
    .bind(&pending_lens_keys)
    .execute(&mut **tx)
    .await?;
    sqlx::query(r#"UPDATE briefing_lenses AS lens
        SET status='retired', retired_at=timezone('UTC',clock_timestamp()),
            updated_at=timezone('UTC',clock_timestamp())
        WHERE lens.user_id::bigint=$1 AND lens.status='active' AND NOT lens.accepts_news
          AND lens.tier='news' AND lens.key<>'misc'
          AND NOT EXISTS (SELECT 1 FROM briefing_segments segment WHERE segment.lens_id=lens.id AND segment.status IN ('active','degraded'))
          AND NOT EXISTS (SELECT 1 FROM briefing_pending_sources pending WHERE pending.user_id=lens.user_id AND pending.lens_key=lens.key)"#)
        .bind(user_id).execute(&mut **tx).await?;
    sqlx::query("UPDATE briefing_states SET version=version+1 WHERE user_id::bigint=$1")
        .bind(user_id)
        .execute(&mut **tx)
        .await?;
    Ok(())
}

fn validate_partition(
    snapshot: &NewsCategorySnapshot,
    lenses: &[NewsCategoryPublicationLens],
) -> Result<(), BriefingRefreshRepositoryError> {
    let fail = || {
        BriefingRefreshRepositoryError::InvalidLensAssignmentPlan(
            "invalid nightly category partition".into(),
        )
    };
    let Some(first) = snapshot.stories.first() else {
        return Err(fail());
    };
    if lenses.len() > 10 {
        return Err(fail());
    }
    let valid_ids: HashSet<i64> = snapshot.stories.iter().map(|s| s.source.id).collect();
    let reusable: HashSet<&str> = snapshot.lenses.iter().map(|l| l.key.as_str()).collect();
    let mut keys = HashSet::new();
    let mut ids = HashSet::new();
    for lens in lenses {
        if lens.key == "misc"
            || !valid_lens_key(&lens.key)
            || !keys.insert(&lens.key)
            || (snapshot.all_keys.contains(&lens.key) && !reusable.contains(lens.key.as_str()))
            || !(2..=40).contains(&lens.title.chars().count())
            || !(8..=180).contains(&lens.deck.chars().count())
            || lens.routing_rule.chars().count() > 400
            || lens.story_ids.len() < 3
            || lens.centroid.len() != first.vector.len()
            || lens.centroid.iter().any(|v| !v.is_finite())
            || !lens.centroid.iter().any(|v| *v != 0.0)
            || lens
                .story_ids
                .iter()
                .any(|id| !valid_ids.contains(id) || !ids.insert(*id))
        {
            return Err(fail());
        }
    }
    Ok(())
}
