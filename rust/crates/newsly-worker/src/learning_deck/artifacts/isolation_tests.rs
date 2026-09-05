use super::*;

#[sqlx::test]
async fn retry_cleanup_cannot_remove_replacement_bundle(pool: sqlx::PgPool) {
    newsly_db::run_migrations(&pool).await.unwrap();
    let root = tempfile::tempdir().unwrap();
    let store = LearningDeckArtifactStore {
        pool,
        backend: ArtifactBackend::Local {
            root: root.path().to_owned(),
        },
        prefix: String::new(),
        limits: LearningDeckArtifactLimits {
            index_html_bytes: 10_000,
            source_notes_bytes: 10_000,
            asset_count: 10,
            asset_bytes: 10_000,
        },
    };
    let html = r#"<html><head><meta name="newsly-deck-layout" content="responsive-v2"><style>section { color: black; }</style></head><body><div class="reveal"><section>Evidence</section></div></body></html>"#;
    let first = store
        .store_bundle(1, 2, 3, html, "# Sources\nFirst", &[])
        .await
        .unwrap();
    let second = store
        .store_bundle(1, 2, 3, html, "# Sources\nSecond", &[])
        .await
        .unwrap();
    assert_ne!(first.storage_prefix, second.storage_prefix);
    store
        .delete_many(&first.artifact_object_keys)
        .await
        .unwrap();
    assert_eq!(
        store
            .get_text(&second.source_notes_object_key)
            .await
            .unwrap()
            .as_deref(),
        Some("# Sources\nSecond")
    );
    assert!(store.exists(&second.deck_object_key).await.unwrap());
    let first_log = store
        .store_agent_log(1, 2, 3, &[serde_json::json!({"attempt":1})])
        .await
        .unwrap()
        .unwrap();
    let second_log = store
        .store_agent_log(1, 2, 3, &[serde_json::json!({"attempt":2})])
        .await
        .unwrap()
        .unwrap();
    assert_ne!(first_log, second_log);
}
