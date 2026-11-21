use serde_json::{Value, json};
use sqlx::PgPool;

const STRIP_FIGURES_MIGRATION: &str =
    include_str!("../../migrations/20261120000000_briefing_source_cards_strip_figures.sql");

fn passage(text: &str) -> Value {
    json!({
        "type": "passage",
        "weight": "feature",
        "paragraphs": [{"runs": [{"kind": "text", "text": text, "source_key": null, "bold": false}]}],
        "source_key": null,
        "image_url": null,
        "thumbnail_url": null,
        "caption": null,
        "placement": null,
        "alignment": null,
        "text": null,
    })
}

fn figure() -> Value {
    json!({
        "type": "figure", "weight": null, "paragraphs": null, "source_key": "content:1",
        "image_url": "/images/1.png", "thumbnail_url": null, "caption": "Caption",
        "placement": "inset", "alignment": "right", "text": null,
    })
}

fn pullquote() -> Value {
    json!({
        "type": "pullquote", "weight": null, "paragraphs": null, "source_key": null,
        "image_url": null, "thumbnail_url": null, "caption": null, "placement": null,
        "alignment": null, "text": "An editorial callout.",
    })
}

async fn user(pool: &PgPool, name: &str, version: i32) -> i64 {
    let id: i64 = sqlx::query_scalar(
        "INSERT INTO users(apple_id,email,is_active,is_admin) VALUES ($1,$1 || '@example.test',true,false) RETURNING id::bigint",
    )
    .bind(name)
    .fetch_one(pool)
    .await
    .unwrap();
    sqlx::query(
        "INSERT INTO briefing_states(user_id,version,masthead_title,masthead_deck) VALUES ($1::bigint::integer,$2,'Test','Test')",
    )
    .bind(id)
    .bind(version)
    .execute(pool)
    .await
    .unwrap();
    id
}

async fn segment(pool: &PgPool, user_id: i64, status: &str, blocks: Value) -> i64 {
    let lens: i64 = sqlx::query_scalar(
        "INSERT INTO briefing_lenses(user_id,key,tier,title,deck,position,status,centroid_weight)
         VALUES ($1::bigint::integer,'articles','longform','Articles','Test',1,'active',0)
         ON CONFLICT (user_id,key) DO UPDATE SET title = EXCLUDED.title
         RETURNING id::bigint",
    )
    .bind(user_id)
    .fetch_one(pool)
    .await
    .unwrap();
    sqlx::query_scalar(
        "INSERT INTO briefing_segments(lens_id,user_id,blocks,markdown_raw,narration_text,source_keys,status,model,prompt_version,warnings)
         VALUES ($1::bigint::integer,$2::bigint::integer,$3,'Stored','Stored','[\"content:1\"]',$4,'test','test','[]')
         RETURNING id::bigint",
    )
    .bind(lens)
    .bind(user_id)
    .bind(blocks)
    .bind(status)
    .fetch_one(pool)
    .await
    .unwrap()
}

async fn blocks(pool: &PgPool, segment_id: i64) -> Value {
    sqlx::query_scalar("SELECT blocks::jsonb FROM briefing_segments WHERE id = $1::bigint")
        .bind(segment_id)
        .fetch_one(pool)
        .await
        .unwrap()
}

async fn version(pool: &PgPool, user_id: i64) -> i32 {
    sqlx::query_scalar("SELECT version FROM briefing_states WHERE user_id = $1::bigint")
        .bind(user_id)
        .fetch_one(pool)
        .await
        .unwrap()
}

#[sqlx::test(migrations = false)]
async fn strip_removes_figures_and_pullquotes_and_bumps_affected_versions_once(pool: PgPool) {
    crate::run_migrations(&pool).await.unwrap();
    // Two changed active segments for one user still bump the version only once.
    let reader = user(&pool, "source-cards-reader", 7).await;
    let mixed = segment(
        &pool,
        reader,
        "active",
        json!([
            passage("First"),
            figure(),
            passage("Second"),
            pullquote(),
            passage("Third")
        ]),
    )
    .await;
    let figure_only_tail =
        segment(&pool, reader, "active", json!([passage("Lead"), figure()])).await;
    // Hidden segments are stripped too, but do not change the visible Briefing.
    let retired_owner = user(&pool, "source-cards-retired", 3).await;
    let retired = segment(
        &pool,
        retired_owner,
        "retired",
        json!([figure(), passage("Old")]),
    )
    .await;
    let degraded_owner = user(&pool, "source-cards-degraded", 11).await;
    let degraded = segment(
        &pool,
        degraded_owner,
        "degraded",
        json!([pullquote(), passage("Degraded")]),
    )
    .await;
    let untouched_owner = user(&pool, "source-cards-untouched", 5).await;
    let untouched_blocks = json!([passage("Only"), passage("Passages")]);
    let untouched = segment(&pool, untouched_owner, "active", untouched_blocks.clone()).await;
    let untouched_updated_at = sqlx::query_scalar::<_, chrono::NaiveDateTime>(
        "SELECT updated_at FROM briefing_segments WHERE id = $1::bigint",
    )
    .bind(untouched)
    .fetch_one(&pool)
    .await
    .unwrap();

    sqlx::raw_sql(STRIP_FIGURES_MIGRATION)
        .execute(&pool)
        .await
        .unwrap();

    assert_eq!(
        blocks(&pool, mixed).await,
        json!([passage("First"), passage("Second"), passage("Third")])
    );
    assert_eq!(
        blocks(&pool, figure_only_tail).await,
        json!([passage("Lead")])
    );
    assert_eq!(blocks(&pool, retired).await, json!([passage("Old")]));
    assert_eq!(blocks(&pool, degraded).await, json!([passage("Degraded")]));
    assert_eq!(blocks(&pool, untouched).await, untouched_blocks);
    assert_eq!(version(&pool, reader).await, 8);
    assert_eq!(version(&pool, degraded_owner).await, 12);
    assert_eq!(version(&pool, retired_owner).await, 3);
    assert_eq!(version(&pool, untouched_owner).await, 5);
    assert_eq!(
        sqlx::query_scalar::<_, chrono::NaiveDateTime>(
            "SELECT updated_at FROM briefing_segments WHERE id = $1::bigint",
        )
        .bind(untouched)
        .fetch_one(&pool)
        .await
        .unwrap(),
        untouched_updated_at
    );

    // A rerun finds nothing to strip and changes no version.
    sqlx::raw_sql(STRIP_FIGURES_MIGRATION)
        .execute(&pool)
        .await
        .unwrap();
    assert_eq!(version(&pool, reader).await, 8);
    assert_eq!(version(&pool, degraded_owner).await, 12);
    let remaining: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM briefing_segments
         WHERE jsonb_path_exists(blocks, '$[*] ? (@.type == \"figure\" || @.type == \"pullquote\")')",
    )
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(remaining, 0);
}
