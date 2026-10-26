use super::*;

fn source(id: i64, title: &str) -> BriefingRefreshSource {
    BriefingRefreshSource {
        source_key: format!("news:{id}"),
        kind: "news".to_owned(),
        id,
        title: title.to_owned(),
        original_title: title.to_owned(),
        source_name: None,
        summary: None,
        key_points: Vec::new(),
        url: None,
        image_url: None,
        thumbnail_url: None,
        published_at: None,
        briefing_context: None,
    }
}

fn embedding_batch(vectors: Vec<Vec<f64>>) -> BriefingEmbeddingBatch {
    BriefingEmbeddingBatch {
        vectors,
        model: "test-model".to_owned(),
        usage: newsly_agent_runtime::ProviderUsage {
            request_count: 1,
            ..Default::default()
        },
        provider_response_id: Some("fresh-request".to_owned()),
    }
}

#[tokio::test]
async fn warm_event_vectors_skip_provider_work_and_keep_same_event_together() {
    let sources = vec![
        source(1, "Orbit launch succeeds"),
        source(2, "Orbit launch reaches space"),
        source(3, "Markets rally sharply"),
    ];
    let calls = std::sync::atomic::AtomicUsize::new(0);
    let planned = plan_windows_with_embeddings(
        &sources,
        "news",
        4,
        0.78,
        32,
        vec![
            Some(vec![1.0, 0.0]),
            Some(vec![0.99, 0.01]),
            Some(vec![0.0, 1.0]),
        ],
        |_| {
            calls.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
            async { Err::<BriefingEmbeddingBatch, _>("unexpected provider call") }
        },
    )
    .await;
    assert_eq!(calls.load(std::sync::atomic::Ordering::SeqCst), 0);
    assert!(
        planned.embedding_batches.is_empty(),
        "reused vectors have no fresh provider usage"
    );
    assert_eq!(
        planned.windows[0].event_groups,
        vec![
            vec!["news:1".to_owned(), "news:2".to_owned()],
            vec!["news:3".to_owned()]
        ]
    );
    assert_eq!(planned.windows[0].sources, sources);
}

#[tokio::test]
async fn mixed_event_vectors_embed_only_missing_inputs_in_source_order() {
    let sources = vec![
        source(1, "Orbit launch succeeds"),
        source(2, "Orbit launch reaches space"),
        source(3, "Markets rally sharply"),
        source(4, "Different finding"),
    ];
    let inputs = std::sync::Mutex::new(Vec::new());
    let planned = plan_windows_with_embeddings(
        &sources,
        "news",
        4,
        0.78,
        1,
        vec![Some(vec![1.0, 0.0]), None, Some(vec![0.0, 1.0]), None],
        |texts| {
            inputs.lock().unwrap().push(texts.clone());
            async move {
                Ok::<_, &str>(embedding_batch(vec![if texts[0].starts_with("Orbit") {
                    vec![0.99, 0.01]
                } else {
                    vec![0.0, 1.0]
                }]))
            }
        },
    )
    .await;
    assert_eq!(
        *inputs.lock().unwrap(),
        vec![
            vec![sources[1].embedding_text()],
            vec![sources[3].embedding_text()]
        ]
    );
    assert_eq!(planned.embedding_batches.len(), 2);
    assert_eq!(planned.windows[0].event_groups[0], vec!["news:1", "news:2"]);
    assert_eq!(planned.windows[0].sources, sources);
}

#[tokio::test]
async fn failed_missing_embedding_preserves_arrival_order_and_prior_usage() {
    let sources = vec![
        source(1, "Orbit launch succeeds"),
        source(2, "Orbit launch reaches space"),
        source(3, "Orbit launch continues"),
    ];
    let planned = plan_windows_with_embeddings(
        &sources,
        "news",
        4,
        0.78,
        1,
        vec![Some(vec![1.0, 0.0]), None, None],
        |texts| async move {
            if texts[0].contains("continues") {
                Err("provider unavailable")
            } else {
                Ok(embedding_batch(vec![vec![1.0, 0.0]]))
            }
        },
    )
    .await;
    assert_eq!(planned.windows[0].sources, sources);
    assert_eq!(
        planned.windows[0].event_groups,
        vec![vec!["news:1"], vec!["news:2"], vec!["news:3"]]
    );
    assert_eq!(
        planned.embedding_batches.len(),
        1,
        "the successful missing-input call remains accounted"
    );
}

#[tokio::test]
async fn malformed_embedding_count_cannot_shift_source_identity() {
    let sources = vec![
        source(1, "Orbit launch succeeds"),
        source(2, "Orbit launch reaches space"),
    ];
    let planned =
        plan_windows_with_embeddings(&sources, "news", 4, 0.78, 32, vec![None, None], |_| async {
            Ok::<_, &str>(embedding_batch(vec![vec![1.0, 0.0]]))
        })
        .await;
    assert_eq!(
        planned.windows[0].event_groups,
        vec![vec!["news:1"], vec!["news:2"]]
    );
    assert_eq!(planned.embedding_batches.len(), 1);
}

#[tokio::test]
async fn incompatible_fresh_vectors_preserve_all_original_source_identities() {
    let sources = vec![
        source(1, "Orbit launch succeeds"),
        source(2, "Orbit launch reaches space"),
    ];
    for fresh in [vec![1.0, 0.0, 0.0], vec![f64::NAN, 0.0]] {
        let planned = plan_windows_with_embeddings(
            &sources,
            "news",
            4,
            0.78,
            32,
            vec![Some(vec![1.0, 0.0]), None],
            |_| {
                let fresh = fresh.clone();
                async move { Ok::<_, &str>(embedding_batch(vec![fresh])) }
            },
        )
        .await;
        assert_eq!(planned.windows[0].sources, sources);
        assert_eq!(
            planned.windows[0].event_groups,
            vec![vec!["news:1"], vec!["news:2"]]
        );
        assert_eq!(planned.embedding_batches.len(), 1);
    }
}

#[tokio::test]
async fn article_planning_does_not_need_database_or_provider() {
    let pool = PgPool::connect_lazy("postgresql://unused@127.0.0.1:1/unavailable").unwrap();
    let gateway = BriefingCompositionGateway::from_env().unwrap();
    let sources = vec![source(1, "First article"), source(2, "Second article")];
    let planned = plan_windows(&pool, &gateway, &sources, "articles", 4, 0.78, 32).await;
    assert_eq!(planned.windows.len(), 2);
    assert!(planned.embedding_batches.is_empty());
}

#[sqlx::test(migrations = false)]
async fn event_cache_requires_exact_model_text_and_encoder_before_alignment(pool: PgPool) {
    newsly_db::run_migrations(&pool).await.unwrap();
    sqlx::query("INSERT INTO news_lens_embedding_models(model,dimensions) VALUES('test-model',2),('other-model',2)").execute(&pool).await.unwrap();
    let mut sources = Vec::new();
    for index in 0..4 {
        let id:i64=sqlx::query_scalar("INSERT INTO news_items(ingest_key,visibility_scope,platform,status,raw_metadata,ingested_at,created_at) VALUES($1,'global','hackernews','ready','{}',timezone('UTC',now()),timezone('UTC',now())) RETURNING id::bigint").bind(format!("cache-{index}")).fetch_one(&pool).await.unwrap();
        let current = source(id, &format!("Current title {index}"));
        let text = if index == 2 {
            "Old title".to_owned()
        } else {
            current.embedding_text()
        };
        sqlx::query("INSERT INTO news_lens_embeddings(news_item_id,model,input_hash,encoder_version,dimensions,vector) VALUES($1::bigint::integer,$2,$3,$4,2,'[1.0,0.0]')")
            .bind(id).bind(if index==1 {"other-model"} else {"test-model"}).bind(news_lens_embeddings::input_hash(&text)).bind(if index==3 {99} else {news_lens_embeddings::ENCODER_VERSION}).execute(&pool).await.unwrap();
        sources.push(current);
    }
    let cached = cached_news_vectors(&pool, &sources, "test-model")
        .await
        .unwrap();
    assert_eq!(cached, vec![Some(vec![1.0, 0.0]), None, None, None]);
    let seen = std::sync::Mutex::new(Vec::new());
    let planned = plan_windows_with_embeddings(&sources, "news", 4, 0.78, 32, cached, |texts| {
        *seen.lock().unwrap() = texts.clone();
        async move { Ok::<_, &str>(embedding_batch(vec![vec![0.0, 1.0]; texts.len()])) }
    })
    .await;
    assert_eq!(
        *seen.lock().unwrap(),
        sources[1..]
            .iter()
            .map(BriefingRefreshSource::embedding_text)
            .collect::<Vec<_>>()
    );
    assert_eq!(planned.windows[0].sources, sources);
    assert_eq!(planned.embedding_batches.len(), 1);
    for source in &sources {
        sqlx::query("UPDATE news_lens_embeddings SET model='test-model',input_hash=$2,encoder_version=$3 WHERE news_item_id::bigint=$1")
            .bind(source.id).bind(news_lens_embeddings::input_hash(&source.embedding_text())).bind(news_lens_embeddings::ENCODER_VERSION).execute(&pool).await.unwrap();
    }
    let warm = cached_news_vectors(&pool, &sources, "test-model")
        .await
        .unwrap();
    assert!(warm.iter().all(Option::is_some));
    let calls = std::sync::atomic::AtomicUsize::new(0);
    let planned = plan_windows_with_embeddings(&sources, "news", 4, 0.78, 32, warm, |_| {
        calls.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        async { Err::<BriefingEmbeddingBatch, &str>("unexpected request") }
    })
    .await;
    assert_eq!(calls.load(std::sync::atomic::Ordering::SeqCst), 0);
    assert!(planned.embedding_batches.is_empty());
}

#[test]
fn event_grouping_requires_similarity_and_a_distinctive_title_token() {
    let sources = vec![
        source(1, "Orbit launch succeeds"),
        source(2, "Orbit launch reaches space"),
        source(3, "Markets rally sharply"),
    ];
    let vectors = vec![vec![1.0, 0.0], vec![0.99, 0.01], vec![1.0, 0.0]];
    assert_eq!(
        group_news_sources(&sources, &vectors, 0.78),
        vec![vec![0, 1], vec![2]]
    );
}

#[test]
fn event_windows_are_balanced_without_splitting_events() {
    let events = (0..9).map(|index| vec![index]).collect::<Vec<_>>();
    let windows = balance_event_windows(&events, 4);
    assert_eq!(windows.iter().map(Vec::len).collect::<Vec<_>>(), [3, 3, 3]);
}

#[test]
fn match_tokens_follow_the_persisted_stemming_contract() {
    assert_eq!(
        match_tokens("Launching rockets after tests"),
        BTreeSet::from(["launch".to_owned(), "rocket".to_owned(), "test".to_owned()])
    );
}
