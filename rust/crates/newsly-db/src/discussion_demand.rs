//! Canonical item-level demand for discussion summarization.

use sqlx::PgConnection;

// Candidate identities come from product demand, never the entire historical news archive.
// Keep keys as text so a malformed persisted citation cannot cause a numeric cast failure.
macro_rules! candidate_query {
    ($query:literal) => {
        concat!(
            r#"
WITH discussion_subscriptions AS MATERIALIZED (
    SELECT config.user_id, config.config::jsonb AS config
    FROM user_scraper_configs config
    JOIN users owner ON owner.id = config.user_id AND owner.is_active
    WHERE config.is_active AND config.scraper_type = 'aggregator'
), discussion_opened AS MATERIALIZED (
    SELECT opened.user_id, COALESCE(member.representative_news_item_id, member.id) AS item_id
    FROM news_item_read_status opened
    JOIN users owner ON owner.id = opened.user_id AND owner.is_active
    JOIN news_items member ON member.id = opened.news_item_id
), discussion_consumers AS MATERIALIZED (
    SELECT pending.user_id, pending.source_id::text AS id, TRUE AS briefing
    FROM briefing_pending_sources pending
    JOIN users owner ON owner.id = pending.user_id AND owner.is_active
    WHERE pending.source_kind = 'news'
    UNION ALL
    SELECT segment.user_id, substr(source.key, 6), TRUE
    FROM briefing_segments segment
    JOIN users owner ON owner.id = segment.user_id AND owner.is_active
    CROSS JOIN LATERAL jsonb_array_elements_text(
        CASE WHEN jsonb_typeof(segment.source_keys::jsonb) = 'array'
             THEN segment.source_keys::jsonb ELSE '[]'::jsonb END
    ) source(key)
    WHERE segment.status IN ('active', 'degraded') AND source.key LIKE 'news:%'
    UNION ALL
    SELECT user_id, item_id::text, FALSE FROM discussion_opened
), discussion_work_items AS MATERIALIZED (
    SELECT item.id, item.platform, item.source_external_id, item.discussion_url,
           item.canonical_item_url, item.raw_metadata, item.status,
           item.representative_news_item_id, item.published_at, item.processed_at,
           item.ingested_at, item.created_at, item.visibility_scope, item.owner_user_id
    FROM news_items item
    LEFT JOIN news_item_discussions discussion ON discussion.news_item_id = item.id
    WHERE item.status = 'ready' AND item.representative_news_item_id IS NULL
      AND item.platform IN ('hackernews', 'hn', 'reddit')
      AND (discussion.id IS NULL OR (
          discussion.platform IN ('hackernews', 'reddit')
          AND discussion.external_id IS NOT NULL AND discussion.discussion_url IS NOT NULL
          AND discussion.last_refresh_status NOT IN ('gone', 'unsupported')
          AND (discussion.next_refresh_after IS NULL
               OR discussion.next_refresh_after <= timezone('UTC', now()))
          AND (discussion.summary IS NULL OR discussion.summary_status <> 'completed'
               OR COALESCE(item.published_at, item.processed_at, item.ingested_at, item.created_at)
                    > timezone('UTC', now()) - interval '24 hours')
      ))
), discussion_eligible_demand AS MATERIALIZED (
    SELECT item.id,
           bool_or(consumer.briefing AND opened.item_id IS NULL) AS unread_briefing_demand
    FROM discussion_work_items item
    JOIN discussion_consumers consumer ON consumer.id = item.id::text
    LEFT JOIN discussion_opened opened
      ON opened.user_id = consumer.user_id AND opened.item_id = item.id
    LEFT JOIN discussion_subscriptions subscription
      ON subscription.user_id = consumer.user_id
      AND aggregator_config_admits(subscription.config, item.platform, item.raw_metadata)
    WHERE (
          (item.visibility_scope = 'user' AND item.owner_user_id = consumer.user_id)
          OR (item.visibility_scope = 'global' AND subscription.user_id IS NOT NULL)
      )
    GROUP BY item.id
), discussion_candidate_items AS MATERIALIZED (
    SELECT item.*, demand.unread_briefing_demand
    FROM discussion_work_items item
    JOIN discussion_eligible_demand demand ON demand.id = item.id
)
"#,
            $query
        )
    };
}
pub(crate) use candidate_query;

/// Checks active, eligible consumers and the live merge window immediately before paid work.
pub async fn discussion_summary_is_demanded(
    connection: &mut PgConnection,
    news_item_id: i64,
    initial_summary: bool,
) -> Result<bool, sqlx::Error> {
    sqlx::query_scalar("SELECT news_item_discussion_is_demanded($1, $2)")
        .bind(news_item_id)
        .bind(initial_summary)
        .fetch_one(connection)
        .await
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use sqlx::PgPool;

    async fn demanded(pool: &PgPool, item_id: i64, initial: bool) -> bool {
        discussion_summary_is_demanded(&mut pool.acquire().await.unwrap(), item_id, initial)
            .await
            .unwrap()
    }

    #[sqlx::test(migrations = false)]
    async fn demand_tracks_open_unread_consumers_visibility_and_deadline(pool: PgPool) {
        crate::run_migrations(&pool).await.unwrap();
        let user_id: i64 = sqlx::query_scalar(
            "INSERT INTO users (apple_id, email, is_active, is_admin) VALUES ('discussion-demand', 'discussion-demand@example.com', true, false) RETURNING id::bigint",
        ).fetch_one(&pool).await.unwrap();
        let item_id: i64 = sqlx::query_scalar(
            "INSERT INTO news_items (ingest_key, visibility_scope, owner_user_id, platform, raw_metadata, ingested_at, created_at) VALUES ('discussion-demand', 'user', $1, 'reddit', '{}', timezone('UTC', now()), timezone('UTC', now())) RETURNING id::bigint",
        ).bind(user_id).fetch_one(&pool).await.unwrap();
        assert!(!demanded(&pool, item_id, true).await);
        assert!(!demanded(&pool, item_id, false).await);
        sqlx::query("INSERT INTO news_item_read_status (user_id, news_item_id, read_at, created_at) VALUES ($1, $2, now(), now())")
            .bind(user_id).bind(item_id).execute(&pool).await.unwrap();
        assert!(demanded(&pool, item_id, true).await);
        assert!(!demanded(&pool, item_id, false).await);
        sqlx::query("INSERT INTO briefing_pending_sources (user_id, source_kind, source_id) VALUES ($1, 'news', $2)")
            .bind(user_id).bind(item_id).execute(&pool).await.unwrap();
        assert!(!demanded(&pool, item_id, false).await);
        sqlx::query("DELETE FROM news_item_read_status WHERE news_item_id::bigint = $1")
            .bind(item_id)
            .execute(&pool)
            .await
            .unwrap();
        assert!(demanded(&pool, item_id, false).await);
        sqlx::query("UPDATE news_items SET published_at = timezone('UTC', now()) - interval '24 hours' WHERE id::bigint = $1")
            .bind(item_id).execute(&pool).await.unwrap();
        assert!(!demanded(&pool, item_id, false).await);
        assert!(demanded(&pool, item_id, true).await);
        sqlx::query("UPDATE users SET is_active = false WHERE id::bigint = $1")
            .bind(user_id)
            .execute(&pool)
            .await
            .unwrap();
        assert!(!demanded(&pool, item_id, true).await);
        sqlx::query("UPDATE users SET is_active = true WHERE id::bigint = $1")
            .bind(user_id)
            .execute(&pool)
            .await
            .unwrap();
        sqlx::query("UPDATE news_items SET visibility_scope = 'global', owner_user_id = NULL, platform = 'brutalist', raw_metadata = $2, published_at = timezone('UTC', now()) WHERE id::bigint = $1")
            .bind(item_id).bind(json!({"aggregator":{"topic":"science"}})).execute(&pool).await.unwrap();
        sqlx::query("INSERT INTO user_scraper_configs (user_id, scraper_type, is_active, config) VALUES ($1, 'aggregator', true, $2)")
            .bind(user_id).bind(json!({"key":"brutalist","topics":["politics"]})).execute(&pool).await.unwrap();
        assert!(!demanded(&pool, item_id, true).await);
        sqlx::query("UPDATE user_scraper_configs SET config = $2 WHERE user_id::bigint = $1")
            .bind(user_id)
            .bind(json!({"key":"brutalist","topics":["science"]}))
            .execute(&pool)
            .await
            .unwrap();
        assert!(demanded(&pool, item_id, false).await);
        let lens_id: i64 = sqlx::query_scalar(
            "INSERT INTO briefing_lenses (user_id, key, tier, title, deck, position, status, centroid_weight) VALUES ($1, 'discussion-demand', 'news', 'Demand', '', 0, 'active', 0) RETURNING id::bigint",
        ).bind(user_id).fetch_one(&pool).await.unwrap();
        sqlx::query("INSERT INTO briefing_segments (user_id, lens_id, blocks, markdown_raw, narration_text, source_keys, status, model, prompt_version, warnings) VALUES ($1, $2, '[]', '', '', $3, 'active', 'test', 'v1', '[]')")
            .bind(user_id).bind(lens_id).bind(json!([format!("news:{item_id}")])).execute(&pool).await.unwrap();
        sqlx::query("DELETE FROM briefing_pending_sources WHERE user_id::bigint = $1")
            .bind(user_id)
            .execute(&pool)
            .await
            .unwrap();
        assert!(demanded(&pool, item_id, false).await);
        sqlx::query(
            "UPDATE briefing_segments SET status = 'superseded' WHERE user_id::bigint = $1",
        )
        .bind(user_id)
        .execute(&pool)
        .await
        .unwrap();
        assert!(!demanded(&pool, item_id, false).await);
        sqlx::query("UPDATE briefing_segments SET status = 'degraded' WHERE user_id::bigint = $1")
            .bind(user_id)
            .execute(&pool)
            .await
            .unwrap();
        assert!(demanded(&pool, item_id, false).await);
        // Reading any member of the representative cluster consumes later-summary demand.
        let member_id: i64 = sqlx::query_scalar("INSERT INTO news_items (ingest_key, visibility_scope, platform, representative_news_item_id, ingested_at, created_at) VALUES ('discussion-demand-member', 'global', 'brutalist', $1, now(), now()) RETURNING id::bigint")
            .bind(item_id).fetch_one(&pool).await.unwrap();
        sqlx::query("INSERT INTO news_item_read_status (user_id, news_item_id, read_at, created_at) VALUES ($1, $2, now(), now())")
            .bind(user_id).bind(member_id).execute(&pool).await.unwrap();
        assert!(!demanded(&pool, item_id, false).await);
        assert!(demanded(&pool, item_id, true).await);
    }

    #[sqlx::test(migrations = false)]
    async fn set_based_candidates_match_single_item_demand_without_citation_casts(pool: PgPool) {
        crate::run_migrations(&pool).await.unwrap();
        let owner: i64 = sqlx::query_scalar("INSERT INTO users (apple_id,email,is_active,is_admin) VALUES ('set-demand','set-demand@example.com',true,false) RETURNING id::bigint")
            .fetch_one(&pool).await.unwrap();
        sqlx::query("INSERT INTO user_scraper_configs (user_id,scraper_type,is_active,config) VALUES ($1,'aggregator',true,$2)")
            .bind(owner).bind(json!({"key":"hackernews"})).execute(&pool).await.unwrap();
        let mut items = Vec::new();
        for (index, global, age) in [
            (0, false, 25),
            (1, false, 0),
            (2, false, 0),
            (3, true, 0),
            (4, false, 0),
        ] {
            let id: i64 = sqlx::query_scalar("INSERT INTO news_items (ingest_key,visibility_scope,owner_user_id,platform,status,raw_metadata,published_at,ingested_at,created_at) VALUES ($1,$2,$3,$4,'ready','{}',timezone('UTC',now()) - make_interval(hours => $5),now(),now()) RETURNING id::bigint")
                .bind(format!("set-demand-{index}"))
                .bind(if global { "global" } else { "user" })
                .bind(if global { None } else { Some(owner) })
                .bind(if global { "hackernews" } else { "reddit" })
                .bind(age).fetch_one(&pool).await.unwrap();
            items.push(id);
        }
        for id in [items[1], items[2]] {
            sqlx::query("INSERT INTO briefing_pending_sources (user_id,source_kind,source_id) VALUES ($1,'news',$2)")
                .bind(owner).bind(id).execute(&pool).await.unwrap();
        }
        for id in [items[0], items[2]] {
            sqlx::query("INSERT INTO news_item_read_status (user_id,news_item_id,read_at,created_at) VALUES ($1,$2,now(),now())")
                .bind(owner).bind(id).execute(&pool).await.unwrap();
        }
        let lens: i64 = sqlx::query_scalar("INSERT INTO briefing_lenses (user_id,key,tier,title,deck,position,status,centroid_weight) VALUES ($1,'set-demand','news','Demand','',0,'active',0) RETURNING id::bigint")
            .bind(owner).fetch_one(&pool).await.unwrap();
        sqlx::query("INSERT INTO briefing_segments (user_id,lens_id,blocks,markdown_raw,narration_text,source_keys,status,model,prompt_version,warnings) VALUES ($1,$2,'[]','','',$3,'active','test','v1','[]')")
            .bind(owner).bind(lens).bind(json!([format!("news:{}",items[3]),"news:not-a-number","news:9999999999999999999999999999999999999999"]))
            .execute(&pool).await.unwrap();
        let candidates: Vec<(i32, bool)> = sqlx::query_as(candidate_query!(
            r"
            SELECT id, unread_briefing_demand
                AND COALESCE(published_at,processed_at,ingested_at,created_at)
                    > timezone('UTC',now()) - interval '24 hours'
            FROM discussion_candidate_items
        "
        ))
        .fetch_all(&pool)
        .await
        .unwrap();
        for id in items {
            let candidate = candidates
                .iter()
                .find(|(candidate, _)| i64::from(*candidate) == id);
            assert_eq!(
                candidate.is_some(),
                demanded(&pool, id, true).await,
                "initial {id}"
            );
            assert_eq!(
                candidate.is_some_and(|(_, unread)| *unread),
                demanded(&pool, id, false).await,
                "merge {id}"
            );
        }
    }
}
