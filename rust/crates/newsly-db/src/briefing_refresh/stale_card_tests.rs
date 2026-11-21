//! Recomposition of live cards written by an older card prompt.

use sqlx::PgPool;

use super::tests::{
    article_publication, insert_eligible_article, prepare_article, prepare_refresh, test_config,
};
use super::{
    BriefingCompactionKind, BriefingRefreshPublication, BriefingRefreshSource,
    ComposedBriefingCompaction, ComposedBriefingSegment, Utc, apply_briefing_refresh, json,
};

#[sqlx::test]
async fn stale_card_recomposes_in_place_and_keeps_its_position(pool: PgPool) {
    let user_id = insert_eligible_article(&pool).await;
    let publication = article_publication(prepare_article(&pool, user_id).await);
    let mut transaction = pool.begin().await.expect("publication transaction");
    apply_briefing_refresh(&mut transaction, &publication, &test_config())
        .await
        .expect("initial publication");
    transaction.commit().await.expect("publication commit");

    let current = prepare_refresh(&pool, user_id).await;
    assert!(
        current.compaction_batches.is_empty(),
        "a card from the current prompt is left alone"
    );

    let (segment_id, created_at): (i64, chrono::NaiveDateTime) = sqlx::query_as(
        "UPDATE briefing_segments SET prompt_version = 'briefing-v8-density', created_at = TIMESTAMP '2026-01-02 03:04:05' WHERE user_id = $1::bigint AND status = 'active' RETURNING id::bigint, created_at",
    )
    .bind(user_id)
    .fetch_one(&pool)
    .await
    .expect("card ages to an older prompt");
    let mut transaction = pool.begin().await.expect("inventory transaction");
    let inventory = super::stale_card_inventory(&mut transaction, &[user_id], 10)
        .await
        .expect("stale card inventory");
    transaction.rollback().await.expect("inventory rollback");
    assert_eq!(
        inventory,
        vec![super::StaleCardInventory {
            user_id,
            stale_cards: 1
        }]
    );
    let prepared = prepare_refresh(&pool, user_id).await;
    assert_eq!(prepared.compaction_batches.len(), 1);
    let batch = prepared.compaction_batches[0].clone();
    assert_eq!(batch.kind, BriefingCompactionKind::StaleCard { created_at });
    assert!(batch.repair_required);
    assert_eq!(batch.donors.len(), 1);
    assert_eq!(batch.donors[0].segment_id, segment_id);
    assert_eq!(batch.sources.len(), 1);

    let publication = BriefingRefreshPublication {
        append_segments: Vec::new(),
        compactions: vec![ComposedBriefingCompaction {
            donors: batch.donors.clone(),
            planned_source_keys: batch.planned_source_keys.clone(),
            segments: vec![ComposedBriefingSegment {
                lens: batch.lens.clone(),
                blocks: json!([{"type":"passage"}]),
                markdown_raw: "Card summary".to_owned(),
                narration_text: "Card summary".to_owned(),
                source_keys: batch.planned_source_keys.clone(),
                event_groups: vec![batch.planned_source_keys.clone()],
                model: "test".to_owned(),
                prompt_version: crate::BRIEFING_CARD_PROMPT_VERSION.to_owned(),
            }],
            kind: batch.kind,
        }],
        embedding_usage: Vec::new(),
        finalized_at: Utc::now(),
        prepared,
    };
    let mut transaction = pool.begin().await.expect("recompose transaction");
    let outcome = apply_briefing_refresh(&mut transaction, &publication, &test_config())
        .await
        .expect("recompose publication");
    transaction.commit().await.expect("recompose commit");

    assert!(!outcome.stale);
    assert_eq!(outcome.compacted_segments, 1);
    let donor_status: String =
        sqlx::query_scalar("SELECT status FROM briefing_segments WHERE id = $1::bigint")
            .bind(segment_id)
            .fetch_one(&pool)
            .await
            .expect("donor status");
    assert_eq!(donor_status, "compacted");
    let (active_created_at, active_version): (chrono::NaiveDateTime, String) = sqlx::query_as(
        "SELECT created_at, prompt_version FROM briefing_segments WHERE user_id = $1::bigint AND status = 'active'",
    )
    .bind(user_id)
    .fetch_one(&pool)
    .await
    .expect("exactly one active replacement");
    assert_eq!(active_created_at, created_at, "the card keeps its place");
    assert_eq!(active_version, crate::BRIEFING_CARD_PROMPT_VERSION);
    assert!(
        prepare_refresh(&pool, user_id)
            .await
            .compaction_batches
            .is_empty(),
        "a recomposed card is current"
    );
    let mut transaction = pool.begin().await.expect("inventory transaction");
    assert!(
        super::stale_card_inventory(&mut transaction, &[], 10)
            .await
            .expect("stale card inventory")
            .is_empty()
    );
}

#[test]
fn stale_card_selection_is_newest_first_and_card_only() {
    use super::preparation::{is_stale_card, stale_card_batches};
    use std::collections::HashMap;

    let base = chrono::NaiveDate::from_ymd_opt(2026, 1, 1)
        .unwrap()
        .and_hms_opt(0, 0, 0)
        .unwrap();
    let segment = |id: i64, tier: &str, keys: &[&str], version: &str| super::SegmentRow {
        id,
        lens_id: 1,
        lens_key: tier.to_owned(),
        lens_tier: tier.to_owned(),
        lens_title: tier.to_owned(),
        lens_deck: String::new(),
        lens_position: 0,
        source_keys: json!(keys),
        event_groups: None,
        prompt_version: version.to_owned(),
        created_at: base + chrono::Duration::minutes(id),
    };
    let source = |key: &str| BriefingRefreshSource {
        source_key: key.to_owned(),
        kind: "content".to_owned(),
        id: 1,
        title: "Title".to_owned(),
        original_title: "Title".to_owned(),
        source_name: None,
        summary: None,
        key_points: Vec::new(),
        url: None,
        image_url: None,
        thumbnail_url: None,
        published_at: None,
        briefing_context: None,
    };
    let keys = (1..=20)
        .map(|id| format!("content:{id}"))
        .collect::<Vec<_>>();
    let sources = keys
        .iter()
        .map(|key| (key.clone(), source(key)))
        .collect::<HashMap<_, _>>();

    let old = "briefing-v8-density";
    assert!(is_stale_card(
        &segment(1, "longform", &["content:1"], old),
        &sources
    ));
    assert!(is_stale_card(
        &segment(2, "audio", &["content:2"], old),
        &sources
    ));
    assert!(!is_stale_card(
        &segment(
            3,
            "longform",
            &["content:3"],
            crate::BRIEFING_CARD_PROMPT_VERSION
        ),
        &sources
    ));
    assert!(!is_stale_card(
        &segment(4, "news", &["content:4"], old),
        &sources
    ));
    assert!(!is_stale_card(
        &segment(5, "longform", &["content:5", "content:6"], old),
        &sources
    ));
    assert!(!is_stale_card(
        &segment(6, "longform", &["content:99"], old),
        &sources
    ));

    let stale = (1..=20)
        .map(|id| {
            segment(
                id,
                "longform",
                &[keys[usize::try_from(id - 1).unwrap()].as_str()],
                old,
            )
        })
        .collect::<Vec<_>>();
    let batches = stale_card_batches(stale, &sources);
    let donor_ids = batches
        .iter()
        .map(|batch| batch.donors[0].segment_id)
        .collect::<Vec<_>>();
    assert_eq!(
        donor_ids,
        (1..=20).rev().collect::<Vec<_>>(),
        "every candidate, newest first; the worker applies the per-refresh cap"
    );
    assert!(batches.iter().all(|batch| batch.repair_required
        && matches!(batch.kind, BriefingCompactionKind::StaleCard { .. })));
}
