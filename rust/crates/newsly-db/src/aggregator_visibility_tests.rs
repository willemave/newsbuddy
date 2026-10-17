use std::collections::BTreeSet;

use serde_json::{Value, json};
use sqlx::PgPool;

use crate::chat_tooling::list_unread_chat_news;
use crate::content_read::{NewsReadFilter, list_visible_news_items};
use crate::learning_decks::find_visible_news_item_for_learning_deck;
use crate::news_actions::mark_visible_news_items_read;
use crate::stats::get_unread_counts;

const VISIBILITY_MIGRATION: &str =
    include_str!("../migrations/20261002000000_aggregator_visibility.sql");

async fn user(pool: &PgPool, apple_id: &str) -> i64 {
    sqlx::query_scalar(
        "INSERT INTO users(apple_id,email,is_active,is_admin) VALUES ($1,$1 || '@example.com',true,false) RETURNING id::bigint",
    )
    .bind(apple_id)
    .fetch_one(pool)
    .await
    .unwrap()
}

async fn subscribe(pool: &PgPool, user_id: i64, config: Value) -> i64 {
    let key = config["key"].as_str().unwrap_or_default().to_owned();
    sqlx::query_scalar(
        "INSERT INTO user_scraper_configs(user_id,scraper_type,feed_url,config,is_active) VALUES ($1::bigint::integer,'aggregator',$2,$3,true) RETURNING id::bigint",
    )
    .bind(user_id)
    .bind(format!("aggregator://{key}"))
    .bind(config)
    .fetch_one(pool)
    .await
    .unwrap()
}

async fn global_news(pool: &PgPool, platform: &str, external_id: &str, topic: Option<&str>) -> i64 {
    let mut aggregator = json!({"key": platform});
    if let Some(topic) = topic {
        aggregator["topic"] = json!(topic);
    }
    sqlx::query_scalar(
        "INSERT INTO news_items(ingest_key,visibility_scope,platform,status,summary_text,raw_metadata,ingested_at,created_at)
         VALUES ($1,'global',$2,'ready','Paper summary',$3,now(),now()) RETURNING id::bigint",
    )
    .bind(format!("{platform}:{external_id}"))
    .bind(platform)
    .bind(json!({"aggregator": aggregator, "summary": {"title": external_id}}))
    .fetch_one(pool)
    .await
    .unwrap()
}

/// Visible ids as seen by every read path that applies aggregator visibility; asserts they agree.
async fn visible_ids(pool: &PgPool, user_id: i64, candidates: &[i64]) -> BTreeSet<i64> {
    let feed = list_visible_news_items(pool, user_id, NewsReadFilter::All, None, 50)
        .await
        .unwrap()
        .items
        .into_iter()
        .map(|item| item.id)
        .collect::<BTreeSet<_>>();

    let unread = get_unread_counts(pool, user_id).await.unwrap().news;
    assert_eq!(usize::try_from(unread).unwrap(), feed.len(), "unread count");

    let chat = list_unread_chat_news(pool, user_id, 50).await.unwrap();
    assert_eq!(
        usize::try_from(chat.total_count).unwrap(),
        feed.len(),
        "chat count"
    );
    let chat_ids = chat
        .items
        .into_iter()
        .map(|item| item.news_item_id)
        .collect::<BTreeSet<_>>();
    assert_eq!(chat_ids, feed, "chat search");

    let mut transaction = pool.begin().await.unwrap();
    let mut deck = BTreeSet::new();
    for &id in candidates {
        if find_visible_news_item_for_learning_deck(&mut transaction, user_id, id)
            .await
            .unwrap()
            .is_some()
        {
            deck.insert(id);
        }
    }
    assert_eq!(deck, feed, "learning deck lookup");
    let marked = mark_visible_news_items_read(&mut transaction, user_id, candidates)
        .await
        .unwrap();
    let failed = marked.failed_ids.into_iter().collect::<BTreeSet<_>>();
    let readable = candidates
        .iter()
        .copied()
        .filter(|id| !failed.contains(id))
        .collect::<BTreeSet<_>>();
    assert_eq!(readable, feed, "bulk mark read");
    transaction.rollback().await.unwrap();

    feed
}

#[sqlx::test(migrations = false)]
async fn read_paths_agree_on_aggregator_subscriptions_and_topics(pool: PgPool) {
    crate::run_migrations(&pool).await.unwrap();
    let language = global_news(&pool, "arxiv", "2609.00001", Some("cs.CL")).await;
    let vision = global_news(&pool, "arxiv", "2609.00002", Some("cs.CV")).await;
    let trending = global_news(&pool, "hfpapers", "2609.00003", None).await;
    let unowned = global_news(&pool, "reddit", "t3_abc", None).await;
    let candidates = [language, vision, trending, unowned];

    let cases = [
        (
            "arxiv-language",
            json!({"key": "arxiv", "topics": ["cs.CL"]}),
            vec![language],
        ),
        ("arxiv-all", json!({"key": "arxiv"}), vec![language, vision]),
        (
            "arxiv-null-topics",
            json!({"key": "arxiv", "topics": null}),
            vec![language, vision],
        ),
        (
            "arxiv-empty-topics",
            json!({"key": "arxiv", "topics": []}),
            vec![language, vision],
        ),
        ("hf-only", json!({"key": "hfpapers"}), vec![trending]),
        // Older API binaries accepted topics on any aggregator; untopiced items stay visible.
        (
            "hf-legacy-topics",
            json!({"key": "hfpapers", "topics": ["cs.CL"]}),
            vec![trending],
        ),
    ];
    for (name, config, expected) in cases {
        let user_id = user(&pool, name).await;
        subscribe(&pool, user_id, config).await;
        assert_eq!(
            visible_ids(&pool, user_id, &candidates).await,
            expected.into_iter().collect::<BTreeSet<_>>(),
            "{name}"
        );
    }

    // Global items no aggregator owns, and aggregators nobody selected, stay out of chat too.
    let unsubscribed = user(&pool, "no-aggregators").await;
    assert!(
        visible_ids(&pool, unsubscribed, &candidates)
            .await
            .is_empty()
    );
}

#[sqlx::test(migrations = false)]
async fn visibility_function_is_inlined_into_callers(pool: PgPool) {
    crate::run_migrations(&pool).await.unwrap();
    let plan = sqlx::query_scalar::<_, String>(
        "EXPLAIN (VERBOSE) SELECT count(*) FROM news_items AS news
         WHERE EXISTS (
             SELECT 1 FROM user_scraper_configs AS config
             WHERE config.user_id = 1::bigint
               AND aggregator_config_admits(config.config::jsonb, news.platform, news.raw_metadata)
         )",
    )
    .fetch_all(&pool)
    .await
    .unwrap()
    .join("\n");
    assert!(
        !plan.contains("aggregator_config_admits"),
        "the visibility function must stay inlinable:\n{plan}"
    );
}

#[sqlx::test(migrations = false)]
async fn visibility_migration_normalizes_aggregator_configs(pool: PgPool) {
    crate::run_migrations(&pool).await.unwrap();
    let spaced = subscribe(
        &pool,
        user(&pool, "legacy-spaced").await,
        json!({"key": " ArXiv ", "topics": ["CS.cl", "physics.optics"]}),
    )
    .await;
    let alias = subscribe(
        &pool,
        user(&pool, "legacy-alias").await,
        json!({"key": "hn"}),
    )
    .await;
    let stray = subscribe(
        &pool,
        user(&pool, "legacy-stray").await,
        json!({"key": "techmeme", "topics": ["science"]}),
    )
    .await;
    let invalid = subscribe(
        &pool,
        user(&pool, "legacy-invalid").await,
        json!({"key": "brutalist", "topics": ["weather"]}),
    )
    .await;
    let malformed = subscribe(
        &pool,
        user(&pool, "legacy-malformed").await,
        json!({"key": "brutalist", "topics": "science"}),
    )
    .await;
    let canonical = subscribe(
        &pool,
        user(&pool, "legacy-canonical").await,
        json!({"key": "brutalist", "topics": ["science"]}),
    )
    .await;
    let untouched_at = sqlx::query_scalar::<_, Option<chrono::NaiveDateTime>>(
        "SELECT updated_at FROM user_scraper_configs WHERE id::bigint = $1",
    )
    .bind(canonical)
    .fetch_one(&pool)
    .await
    .unwrap();

    sqlx::raw_sql(VISIBILITY_MIGRATION)
        .execute(&pool)
        .await
        .unwrap();

    let state = |id: i64| {
        let pool = pool.clone();
        async move {
            sqlx::query_as::<_, (Value, bool, Option<chrono::NaiveDateTime>)>(
                "SELECT config::jsonb, is_active, updated_at FROM user_scraper_configs WHERE id::bigint = $1",
            )
            .bind(id)
            .fetch_one(&pool)
            .await
            .unwrap()
        }
    };
    assert_eq!(
        state(spaced).await.0,
        json!({"key": "arxiv", "topics": ["cs.CL"]})
    );
    assert!(!state(alias).await.1, "non-catalog keys are deactivated");
    assert_eq!(state(stray).await.0, json!({"key": "techmeme"}));
    assert_eq!(state(invalid).await.0, json!({"key": "brutalist"}));
    assert_eq!(state(malformed).await.0, json!({"key": "brutalist"}));
    let (config, active, updated_at) = state(canonical).await;
    assert_eq!(config, json!({"key": "brutalist", "topics": ["science"]}));
    assert!(active);
    assert_eq!(
        updated_at, untouched_at,
        "canonical configs are not rewritten"
    );
}

#[sqlx::test(migrations = false)]
async fn aggregator_subscription_creates_reactivates_and_narrows_topics(pool: PgPool) {
    use crate::scraper_configs::{FeedSubscriptionMutation, apply_aggregator_subscription};
    use newsly_domain::AggregatorKey;

    crate::run_migrations(&pool).await.unwrap();
    let language = global_news(&pool, "arxiv", "2609.00011", Some("cs.CL")).await;
    let vision = global_news(&pool, "arxiv", "2609.00012", Some("cs.CV")).await;
    let candidates = [language, vision];
    let user_id = user(&pool, "chat-aggregator").await;
    let apply = |topics: Option<&'static [&'static str]>| {
        let pool = pool.clone();
        async move {
            let mut transaction = pool.begin().await.unwrap();
            let applied = apply_aggregator_subscription(
                &mut transaction,
                user_id,
                AggregatorKey::Arxiv,
                topics,
            )
            .await
            .unwrap();
            transaction.commit().await.unwrap();
            applied
        }
    };

    let created = apply(None).await;
    assert_eq!(created.mutation, FeedSubscriptionMutation::Created);
    assert_eq!(
        created.config.feed_url.as_deref(),
        Some("aggregator://arxiv")
    );
    assert_eq!(created.config.display_name.as_deref(), Some("arXiv"));
    assert_eq!(created.config.config, json!({"key": "arxiv", "limit": 1}));
    assert_eq!(
        visible_ids(&pool, user_id, &candidates).await,
        BTreeSet::from([language, vision])
    );

    let narrowed = apply(Some(&["cs.CL"])).await;
    assert_eq!(
        narrowed.mutation,
        FeedSubscriptionMutation::AlreadySubscribed
    );
    assert_eq!(narrowed.config.id, created.config.id);
    assert_eq!(
        visible_ids(&pool, user_id, &candidates).await,
        BTreeSet::from([language])
    );

    sqlx::query("UPDATE user_scraper_configs SET is_active = FALSE WHERE id = $1")
        .bind(i32::try_from(created.config.id).unwrap())
        .execute(&pool)
        .await
        .unwrap();
    let reactivated = apply(None).await;
    assert_eq!(reactivated.mutation, FeedSubscriptionMutation::Reactivated);
    assert_eq!(reactivated.config.config["topics"], json!(["cs.CL"]));

    let widened = apply(Some(&[])).await;
    assert!(widened.config.config.get("topics").is_none());
    assert_eq!(
        visible_ids(&pool, user_id, &candidates).await,
        BTreeSet::from([language, vision])
    );
}

#[sqlx::test(migrations = false)]
async fn read_paths_agree_on_owned_news_and_inactive_subscriptions(pool: PgPool) {
    crate::run_migrations(&pool).await.unwrap();
    let owner = user(&pool, "visibility-owner").await;
    let other = user(&pool, "visibility-other").await;
    let subscribed = global_news(&pool, "hfpapers", "owned-active", None).await;
    let inactive = global_news(&pool, "arxiv", "owned-inactive", None).await;
    subscribe(&pool, owner, json!({"key": "hfpapers"})).await;
    let inactive_config = subscribe(&pool, owner, json!({"key": "arxiv"})).await;
    sqlx::query("UPDATE user_scraper_configs SET is_active = false WHERE id::bigint = $1")
        .bind(inactive_config)
        .execute(&pool)
        .await
        .unwrap();
    let mut owned = Vec::new();
    for (account, key) in [(owner, "owned-self"), (other, "owned-other")] {
        owned.push(sqlx::query_scalar::<_, i64>(
            "INSERT INTO news_items(ingest_key,visibility_scope,owner_user_id,platform,status,summary_text,raw_metadata,ingested_at,created_at)
             VALUES ($1,'user',$2::bigint::integer,'reddit','ready','Owned summary','{}',now(),now()) RETURNING id::bigint"
        ).bind(key).bind(account).fetch_one(&pool).await.unwrap());
    }
    assert_eq!(
        visible_ids(&pool, owner, &[subscribed, inactive, owned[0], owned[1]]).await,
        BTreeSet::from([subscribed, owned[0]])
    );
}

#[sqlx::test(migrations = false)]
async fn cross_listed_topics_preserve_legacy_and_research_visibility(pool: PgPool) {
    crate::run_migrations(&pool).await.unwrap();
    let cases = [
        (
            json!({"key":"brutalist","topics":["politics"]}),
            "brutalist",
            json!({"aggregator":{"topic":"science","topics":["science","politics"]}}),
            true,
        ),
        (
            json!({"key":"brutalist","topics":["sports"]}),
            "brutalist",
            json!({"aggregator":{"topic":"science","topics":["science","politics"]}}),
            false,
        ),
        (
            json!({"key":"brutalist","topics":["science"]}),
            "brutalist",
            json!({"aggregator":{"topic":"science"}}),
            true,
        ),
        (
            json!({"key":"brutalist","topics":["politics"]}),
            "brutalist",
            json!({"aggregator":{"topic":"science"}}),
            false,
        ),
        (
            json!({"key":"brutalist","topics":["science"]}),
            "brutalist",
            json!({}),
            true,
        ),
        (
            json!({"key":"brutalist"}),
            "brutalist",
            json!({"aggregator":{"topic":"science"}}),
            true,
        ),
        (
            json!({"key":"brutalist","topics":[]}),
            "brutalist",
            json!({"aggregator":{"topic":"science"}}),
            true,
        ),
        (
            json!({"key":"brutalist","topics":["politics"]}),
            "brutalist",
            json!({"aggregator":{"topic":"science","topics":"politics"}}),
            false,
        ),
        (
            json!({"key":"arxiv","topics":["cs.CL"]}),
            "arxiv",
            json!({"aggregator":{"topic":"cs.CL"}}),
            true,
        ),
        (
            json!({"key":"arxiv","topics":["cs.CL"]}),
            "arxiv",
            json!({"aggregator":{"topic":"cs.CV"}}),
            false,
        ),
        (
            json!({"key":"hfpapers","topics":["cs.CL"]}),
            "hfpapers",
            json!({"aggregator":{}}),
            true,
        ),
        (
            json!({"key":"arxiv","topics":["science"]}),
            "brutalist",
            json!({"aggregator":{"topic":"science","topics":["science"]}}),
            false,
        ),
    ];
    for (config, platform, metadata, expected) in cases {
        let actual: bool = sqlx::query_scalar("SELECT aggregator_config_admits($1, $2, $3::json)")
            .bind(&config)
            .bind(platform)
            .bind(&metadata)
            .fetch_one(&pool)
            .await
            .unwrap();
        assert_eq!(
            actual, expected,
            "config={config}, platform={platform}, metadata={metadata}"
        );
    }
    // Cross-list overlap must reach every existing visible-news read path.
    let item_id = global_news(&pool, "brutalist", "cross-listed", Some("science")).await;
    sqlx::query("UPDATE news_items SET raw_metadata = jsonb_set(raw_metadata::jsonb, '{aggregator,topics}', '[\"science\",\"politics\"]')::json WHERE id::bigint=$1")
        .bind(item_id).execute(&pool).await.unwrap();
    let subscriber = user(&pool, "cross-list-politics").await;
    subscribe(
        &pool,
        subscriber,
        json!({"key":"brutalist","topics":["politics"]}),
    )
    .await;
    assert_eq!(
        visible_ids(&pool, subscriber, &[item_id]).await,
        BTreeSet::from([item_id])
    );
}
