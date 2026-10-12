use serde_json::{Value, json};
use sqlx::PgPool;

use super::super::{PrepareFeedDiscoveryTaskOutcome, prepare_feed_discovery_task};

async fn user(pool: &PgPool, name: &str) -> i64 {
    sqlx::query_scalar(
        "INSERT INTO users (apple_id, email, is_active, is_admin) VALUES ($1, $1, TRUE, FALSE) RETURNING id::bigint",
    )
    .bind(name)
    .fetch_one(pool)
    .await
    .unwrap()
}

async fn content(pool: &PgPool, key: &str, title: Option<&str>, metadata: Value) -> i64 {
    sqlx::query_scalar(
        "INSERT INTO contents (content_type, url, title, source, is_aggregate, content_metadata) VALUES ('article', $1, $2, 'Example', FALSE, $3) RETURNING id::bigint",
    )
    .bind(format!("https://example.com/{key}"))
    .bind(title)
    .bind(sqlx::types::Json(metadata))
    .fetch_one(pool)
    .await
    .unwrap()
}

async fn save(pool: &PgPool, user_id: i64, content_id: i64) {
    sqlx::query(
        "INSERT INTO content_knowledge_saves (user_id, content_id, saved_at) VALUES ($1::bigint::integer, $2::bigint::integer, '2026-01-01'::timestamptz)",
    )
    .bind(user_id)
    .bind(content_id)
    .execute(pool)
    .await
    .unwrap();
}

#[sqlx::test(migrations = false)]
async fn feed_discovery_reads_saved_content_summaries_from_current_metadata_schema(pool: PgPool) {
    crate::run_migrations(&pool).await.unwrap();
    let owner = user(&pool, "feed-owner").await;
    let other = user(&pool, "feed-other").await;
    let oldest = content(&pool, "oldest", Some("Oldest"), json!({})).await;
    save(&pool, owner, oldest).await;
    let cases = [
        (json!({"summary": "Text summary"}), Some("Text summary")),
        (
            json!({"summary": {"one_line": "One line", "overview": "Longer overview"}}),
            Some("One line"),
        ),
        (
            json!({"summary": {"overview": "Overview"}}),
            Some("Overview"),
        ),
        (
            json!({"summary": {"artifact": {"payload": {"overview": "Artifact overview"}}}}),
            Some("Artifact overview"),
        ),
        (json!({}), None),
    ];
    let mut expected = Vec::new();
    for (index, (metadata, summary)) in cases.into_iter().enumerate() {
        let id = content(&pool, &format!("saved-{index}"), None, metadata).await;
        save(&pool, owner, id).await;
        expected.push((id, summary));
    }
    let unsaved = content(&pool, "unsaved", Some("Unsaved"), json!({})).await;
    let other_saved = content(&pool, "other", Some("Other user"), json!({})).await;
    save(&pool, other, other_saved).await;

    let PrepareFeedDiscoveryTaskOutcome::Ready(snapshot) =
        prepare_feed_discovery_task(&pool, 1_811_590, 0, owner, "weekly", 5)
            .await
            .expect("feed discovery should prepare against the migrated contents schema")
    else {
        panic!("an active user without a completed run should get a snapshot");
    };
    assert_eq!(snapshot.user_id, owner);
    assert_eq!(snapshot.trigger, "weekly");
    assert_eq!(snapshot.favorites.len(), 5);
    for (favorite, (id, summary)) in snapshot.favorites.iter().zip(expected.into_iter().rev()) {
        assert_eq!(favorite.id, id);
        assert_eq!(favorite.summary.as_deref(), summary);
        assert_eq!(favorite.title, "Untitled");
        assert_eq!(favorite.source.as_deref(), Some("Example"));
        assert_eq!(favorite.content_type, "article");
        assert!(favorite.url.starts_with("https://example.com/saved-"));
        assert!(![oldest, unsaved, other_saved].contains(&favorite.id));
    }
    let claim: (i64, i32) = sqlx::query_as(
        "SELECT discovery_task_id::bigint, discovery_retry_count FROM feed_discovery_runs WHERE id::bigint = $1",
    )
    .bind(snapshot.run_id)
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(claim, (1_811_590, 0));
}

#[sqlx::test(migrations = false)]
async fn feed_discovery_without_saves_resumes_its_claim_and_reuses_completed_run(pool: PgPool) {
    crate::run_migrations(&pool).await.unwrap();
    let owner = user(&pool, "feed-empty").await;
    let PrepareFeedDiscoveryTaskOutcome::Ready(first) =
        prepare_feed_discovery_task(&pool, 1_811_591, 0, owner, "weekly", 5)
            .await
            .expect("empty favorites should not cause a SQL error")
    else {
        panic!("an active user should get an empty snapshot");
    };
    assert!(first.favorites.is_empty());
    let PrepareFeedDiscoveryTaskOutcome::Ready(retry) =
        prepare_feed_discovery_task(&pool, 1_811_591, 1, owner, "weekly", 5)
            .await
            .unwrap()
    else {
        panic!("a retry should resume its existing run");
    };
    assert_eq!(retry.run_id, first.run_id);
    sqlx::query("UPDATE feed_discovery_runs SET status = 'completed' WHERE id::bigint = $1")
        .bind(first.run_id)
        .execute(&pool)
        .await
        .unwrap();
    assert_eq!(
        prepare_feed_discovery_task(&pool, 1_811_592, 0, owner, "weekly", 5)
            .await
            .unwrap(),
        PrepareFeedDiscoveryTaskOutcome::ReuseCompleted {
            run_id: first.run_id
        }
    );
}
