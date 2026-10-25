use super::*;

async fn user(pool: &PgPool) -> i64 {
    sqlx::query_scalar("INSERT INTO users(apple_id,email,is_active,is_admin) VALUES ('direct-feed-test','direct-feed@test',true,false) RETURNING id::bigint")
        .fetch_one(pool).await.unwrap()
}

async fn legacy_config(pool: &PgPool, user_id: i64, active: bool) -> i64 {
    sqlx::query_scalar("INSERT INTO user_scraper_configs(user_id,scraper_type,feed_url,config,is_active) VALUES ($1::bigint::integer,'substack','https://STRATECHERY.com:443/feed','{}',$2) RETURNING id::bigint")
        .bind(user_id).bind(active).fetch_one(pool).await.unwrap()
}

#[sqlx::test(migrations = false)]
async fn url_subscription_reuses_legacy_type_and_prefers_an_active_match(pool: PgPool) {
    crate::run_migrations(&pool).await.unwrap();
    let user_id = user(&pool).await;
    let id = legacy_config(&pool, user_id, false).await;
    let mut tx = pool.begin().await.unwrap();
    let reactivated = apply_validated_feed_subscription(
        &mut tx,
        user_id,
        "atom",
        "atom",
        Some("Stratechery"),
        "https://stratechery.com/feed",
        None,
    )
    .await
    .unwrap();
    assert_eq!(reactivated.config.id, id);
    assert_eq!(reactivated.config.scraper_type, "substack");
    assert_eq!(
        reactivated.config.feed_url.as_deref(),
        Some("https://STRATECHERY.com:443/feed")
    );
    assert_eq!(reactivated.mutation, FeedSubscriptionMutation::Reactivated);
    assert_eq!(reactivated.config.config["feed_format"], "atom");
    tx.commit().await.unwrap();
    // A historical inactive row of the requested type must not displace an active match.
    sqlx::query("INSERT INTO user_scraper_configs(user_id,scraper_type,feed_url,config,is_active) VALUES ($1::bigint::integer,'atom','https://stratechery.com/feed','{}',false)")
        .bind(user_id).execute(&pool).await.unwrap();
    let mut tx = pool.begin().await.unwrap();
    let already = apply_validated_feed_subscription(
        &mut tx,
        user_id,
        "atom",
        "rss",
        None,
        "https://stratechery.com/feed",
        None,
    )
    .await
    .unwrap();
    assert_eq!(already.config.id, id);
    assert_eq!(
        already.mutation,
        FeedSubscriptionMutation::AlreadySubscribed
    );
    tx.commit().await.unwrap();
    let count: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM user_scraper_configs WHERE user_id::bigint=$1 AND is_active",
    )
    .bind(user_id)
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(count, 1);
    let mut tx = pool.begin().await.unwrap();
    let distinct_path = apply_validated_feed_subscription(
        &mut tx,
        user_id,
        "atom",
        "rss",
        None,
        "https://stratechery.com/Feed",
        None,
    )
    .await
    .unwrap();
    assert_eq!(distinct_path.mutation, FeedSubscriptionMutation::Created);
    assert_ne!(distinct_path.config.id, id);
    tx.rollback().await.unwrap();
}

#[sqlx::test(migrations = false)]
async fn exact_reference_survives_redirect_but_rejects_a_changed_original_url(pool: PgPool) {
    crate::run_migrations(&pool).await.unwrap();
    let user_id = user(&pool).await;
    let id = legacy_config(&pool, user_id, false).await;
    let reference = FeedSubscriptionReference {
        id,
        feed_url: "https://STRATECHERY.com:443/feed".into(),
    };
    let mut tx = pool.begin().await.unwrap();
    let redirected = apply_validated_feed_subscription(
        &mut tx,
        user_id,
        "atom",
        "rss",
        None,
        "https://stratechery.com/new-feed",
        Some(&reference),
    )
    .await
    .unwrap();
    assert_eq!(redirected.config.id, id);
    assert_eq!(redirected.config.scraper_type, "substack");
    assert_eq!(
        redirected.config.feed_url.as_deref(),
        Some("https://stratechery.com/new-feed")
    );
    tx.commit().await.unwrap();
    let mut tx = pool.begin().await.unwrap();
    assert!(matches!(
        apply_validated_feed_subscription(
            &mut tx,
            user_id,
            "atom",
            "rss",
            None,
            "https://stratechery.com/feed",
            Some(&reference)
        )
        .await,
        Err(ScraperConfigRepositoryError::ChangedDuringPrepare)
    ));
    tx.rollback().await.unwrap();
}

#[sqlx::test(migrations = false)]
async fn concurrent_different_types_create_one_subscription_and_one_backfill_owner(pool: PgPool) {
    crate::run_migrations(&pool).await.unwrap();
    let user_id = user(&pool).await;
    let barrier = tokio::sync::Barrier::new(2);
    let pool_ref = &pool;
    let barrier_ref = &barrier;
    let subscribe = |scraper_type, feed_url| async move {
        let mut tx = pool_ref.begin().await.unwrap();
        // Both transactions already hold the shared user fence before competing for identity.
        lock_active_user(&mut tx, user_id).await.unwrap();
        barrier_ref.wait().await;
        let applied = apply_validated_feed_subscription(
            &mut tx,
            user_id,
            scraper_type,
            "rss",
            None,
            feed_url,
            None,
        )
        .await
        .unwrap();
        tx.commit().await.unwrap();
        applied
    };
    let (first, second) = tokio::join!(
        subscribe("atom", "https://EXAMPLE.test:443/Feed"),
        subscribe("podcast_rss", "https://example.test/Feed")
    );
    assert_eq!(first.config.id, second.config.id);
    assert_eq!(
        usize::from(first.mutation.needs_backfill())
            + usize::from(second.mutation.needs_backfill()),
        1
    );
    let count: i64 =
        sqlx::query_scalar("SELECT count(*) FROM user_scraper_configs WHERE user_id::bigint=$1")
            .bind(user_id)
            .fetch_one(&pool)
            .await
            .unwrap();
    assert_eq!(count, 1);
}

#[sqlx::test(migrations = false)]
async fn referenced_redirect_reuses_an_existing_target_of_either_direct_type(pool: PgPool) {
    crate::run_migrations(&pool).await.unwrap();
    let user_id = user(&pool).await;
    let id = legacy_config(&pool, user_id, false).await;
    let reference = FeedSubscriptionReference {
        id,
        feed_url: "https://STRATECHERY.com:443/feed".into(),
    };
    for (index, scraper_type) in ["substack", "atom"].into_iter().enumerate() {
        let target_url = format!("https://stratechery.com/target-{index}");
        let target:i64=sqlx::query_scalar("INSERT INTO user_scraper_configs(user_id,scraper_type,feed_url,config,is_active) VALUES ($1::bigint::integer,$2,$3,'{}',true) RETURNING id::bigint")
            .bind(user_id).bind(scraper_type).bind(&target_url).fetch_one(&pool).await.unwrap();
        let mut tx = pool.begin().await.unwrap();
        let result = apply_validated_feed_subscription(
            &mut tx,
            user_id,
            "atom",
            "rss",
            None,
            &target_url,
            Some(&reference),
        )
        .await
        .unwrap();
        assert_eq!(result.config.id, target);
        assert_eq!(result.mutation, FeedSubscriptionMutation::AlreadySubscribed);
        tx.commit().await.unwrap();
        let source_url: Option<String> =
            sqlx::query_scalar("SELECT feed_url FROM user_scraper_configs WHERE id::bigint=$1")
                .bind(id)
                .fetch_one(&pool)
                .await
                .unwrap();
        assert_eq!(source_url.as_deref(), Some(reference.feed_url.as_str()));
    }
}

#[sqlx::test(migrations = false)]
async fn redirects_preserve_search_history_and_put_carries_only_server_aliases(pool: PgPool) {
    crate::run_migrations(&pool).await.unwrap();
    let user_id = user(&pool).await;
    let id = legacy_config(&pool, user_id, false).await;
    sqlx::query(
        "UPDATE user_scraper_configs SET display_name='Historical Feed' WHERE id::bigint=$1",
    )
    .bind(id)
    .execute(&pool)
    .await
    .unwrap();
    let reference = FeedSubscriptionReference {
        id,
        feed_url: "https://STRATECHERY.com:443/feed".into(),
    };
    let mut tx = pool.begin().await.unwrap();
    let redirected = apply_validated_feed_subscription(
        &mut tx,
        user_id,
        "atom",
        "rss",
        None,
        "https://stratechery.com/new-feed",
        Some(&reference),
    )
    .await
    .unwrap();
    assert_eq!(
        feed_url_aliases(&redirected.config.config).collect::<Vec<_>>(),
        vec![reference.feed_url.as_str()]
    );
    tx.commit().await.unwrap();
    sqlx::raw_sql(r#"
        INSERT INTO contents(id,content_type,url,title,source,status,is_aggregate,content_metadata) VALUES
          (9601,'article','https://example.test/history-1','Old','Historical Feed','completed',false,'{"feed_url":"https://STRATECHERY.com:443/feed"}'),
          (9602,'article','https://example.test/history-2','New','Historical Feed','completed',false,'{"feed_url":"https://stratechery.com/new-feed"}'),
          (9603,'article','https://example.test/history-3','Other','Historical Feed','completed',false,'{"feed_url":"https://stratechery.com/NEW-feed"}');
    "#).execute(&pool).await.unwrap();
    sqlx::query("INSERT INTO content_status(user_id,content_id,status) VALUES ($1::bigint::integer,9601,'inbox'),($1::bigint::integer,9602,'inbox'),($1::bigint::integer,9603,'inbox')")
        .bind(user_id).execute(&pool).await.unwrap();
    for (source_url, source) in [
        (Some("https://stratechery.com/feed"), None),
        (Some("https://stratechery.com/new-feed"), None),
        (None, Some("Historical Feed")),
    ] {
        let filters = crate::ChatContentFilters {
            source_url: source_url.map(str::to_owned),
            source: source.map(str::to_owned),
            limit: 25,
            ..Default::default()
        };
        let page = crate::search_accessible_content(&pool, user_id, &filters)
            .await
            .unwrap();
        assert_eq!(page.total_count, 2);
        assert!(page.items.iter().all(|item| item.content_id != 9603));
    }
    let malicious = serde_json::json!({"limit":5,"feed_url_aliases":["https://evil.test/feed"]});
    let mut tx = pool.begin().await.unwrap();
    let updated = update_scraper_config(
        &mut tx,
        user_id,
        id,
        "substack",
        &ScraperConfigPatch {
            config: Some(&malicious),
            ..Default::default()
        },
    )
    .await
    .unwrap();
    assert_eq!(
        feed_url_aliases(&updated.config).collect::<Vec<_>>(),
        vec![reference.feed_url.as_str()]
    );
    tx.commit().await.unwrap();
    let mut tx = pool.begin().await.unwrap();
    let updated = update_scraper_config(
        &mut tx,
        user_id,
        id,
        "substack",
        &ScraperConfigPatch {
            feed_url: Some("https://stratechery.com/third-feed"),
            config: Some(&malicious),
            ..Default::default()
        },
    )
    .await
    .unwrap();
    assert_eq!(
        feed_url_aliases(&updated.config).collect::<Vec<_>>(),
        vec![
            reference.feed_url.as_str(),
            "https://stratechery.com/new-feed"
        ]
    );
    tx.commit().await.unwrap();
    // History is attribution only: a repurposed old URL must form its own subscription.
    let mut tx = pool.begin().await.unwrap();
    let repurposed = apply_validated_feed_subscription(
        &mut tx,
        user_id,
        "atom",
        "rss",
        None,
        "https://stratechery.com/feed",
        None,
    )
    .await
    .unwrap();
    assert_eq!(repurposed.mutation, FeedSubscriptionMutation::Created);
    assert_ne!(repurposed.config.id, id);
    tx.rollback().await.unwrap();
}

#[sqlx::test(migrations = false)]
async fn generic_create_and_chat_subscribe_serialize_one_direct_feed_identity(pool: PgPool) {
    crate::run_migrations(&pool).await.unwrap();
    let user_id = user(&pool).await;
    let barrier = tokio::sync::Barrier::new(2);
    let create = async {
        let mut tx = pool.begin().await.unwrap();
        lock_active_user(&mut tx, user_id).await.unwrap();
        barrier.wait().await;
        let config = serde_json::json!({"feed_url_aliases":["https://evil.test/feed"]});
        let result = create_scraper_config(
            &mut tx,
            &NewScraperConfig {
                user_id,
                scraper_type: "substack",
                feed_url: "https://EXAMPLE.test:443/Feed",
                display_name: None,
                config: &config,
                is_active: true,
            },
        )
        .await;
        match result {
            Ok(config) => {
                assert!(feed_url_aliases(&config.config).next().is_none());
            }
            Err(ScraperConfigRepositoryError::AlreadyExists) => {}
            Err(error) => panic!("unexpected create error: {error}"),
        }
        tx.commit().await.unwrap();
    };
    let chat = async {
        let mut tx = pool.begin().await.unwrap();
        lock_active_user(&mut tx, user_id).await.unwrap();
        barrier.wait().await;
        apply_validated_feed_subscription(
            &mut tx,
            user_id,
            "atom",
            "rss",
            None,
            "https://example.test/Feed",
            None,
        )
        .await
        .unwrap();
        tx.commit().await.unwrap();
    };
    tokio::join!(create, chat);
    let count: i64 =
        sqlx::query_scalar("SELECT count(*) FROM user_scraper_configs WHERE user_id::bigint=$1")
            .bind(user_id)
            .fetch_one(&pool)
            .await
            .unwrap();
    assert_eq!(count, 1);
    assert!(
        scraper_config_identity_exists(&pool, user_id, "podcast_rss", "https://example.test/Feed")
            .await
            .unwrap()
    );
}
