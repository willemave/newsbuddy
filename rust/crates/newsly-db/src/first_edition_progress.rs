use sqlx::{PgConnection, PgPool};

#[derive(Debug, Clone, PartialEq, Eq, sqlx::FromRow)]
pub struct FirstRunTierProjection {
    pub tier: String,
    pub source_names: Vec<String>,
    pub discovered: i64,
    pub ready: i64,
    pub processing: i64,
    pub failed: i64,
    pub skipped: i64,
    pub source_count: i64,
    pub pending_sources: i64,
    pub unavailable_sources: i64,
}

pub async fn attach_content(
    connection: &mut PgConnection,
    run_id: i64,
    user_id: i64,
    ids: &[i64],
) -> Result<(), sqlx::Error> {
    sqlx::query(r"INSERT INTO onboarding_first_edition_items(run_id,source_kind,source_id)
        SELECT r.id,'content',c.id FROM onboarding_first_edition_runs r
        JOIN content_status s ON s.user_id=r.user_id JOIN contents c ON c.id=s.content_id
        WHERE r.id::bigint=$1 AND r.user_id::bigint=$2 AND r.status='active' AND c.id::bigint=ANY($3)
        ON CONFLICT DO NOTHING").bind(run_id).bind(user_id).bind(ids).execute(connection).await?;
    Ok(())
}

pub async fn tiers(pool: &PgPool, run_id: i64) -> Result<Vec<FirstRunTierProjection>, sqlx::Error> {
    sqlx::query_as(r"WITH run AS (SELECT * FROM onboarding_first_edition_runs WHERE id::bigint=$1 AND status='active'),
        items AS (
            SELECT CASE WHEN c.content_type='podcast' THEN 'audio' ELSE 'longform' END AS tier,
                CASE
                    WHEN c.status IN ('failed','skipped') THEN c.status
                    WHEN cs.status <> 'inbox' OR c.classification='skip' THEN 'skipped'
                    WHEN NOT EXISTS(SELECT 1 FROM processing_tasks t WHERE t.content_id=c.id AND t.status IN ('pending','processing'))
                      AND (c.status <> 'completed'
                        OR COALESCE(c.content_metadata::jsonb #>> '{domain,artwork_status}',c.content_metadata::jsonb->>'artwork_status')='failed'
                        OR NULLIF(COALESCE(c.content_metadata::jsonb #> '{domain,image_generated_at}',c.content_metadata::jsonb->'image_generated_at'),'null'::jsonb) IS NULL
                        OR NULLIF(COALESCE(c.content_metadata::jsonb #> '{domain,image_url}',c.content_metadata::jsonb->'image_url',c.content_metadata::jsonb #> '{domain,thumbnail_url}',c.content_metadata::jsonb->'thumbnail_url'),'null'::jsonb) IS NULL)
                    THEN 'failed'
                    ELSE c.status
                END AS status,
                EXISTS(SELECT 1 FROM briefing_segments s WHERE s.user_id=r.user_id AND s.status IN ('active','degraded') AND s.source_keys::jsonb ? ('content:' || c.id::text))
                    OR EXISTS(SELECT 1 FROM content_read_status rs WHERE rs.user_id=r.user_id AND rs.content_id=c.id) AS published
            FROM run r JOIN onboarding_first_edition_items i ON i.run_id=r.id AND i.source_kind='content'
            JOIN contents c ON c.id=i.source_id
            JOIN content_status cs ON cs.content_id=c.id AND cs.user_id=r.user_id
        ), sources AS (
            SELECT CASE WHEN c.scraper_type='podcast_rss' THEN 'audio' ELSE 'longform' END AS tier, s.status, s.display_name
            FROM run r JOIN onboarding_first_edition_sources s ON s.run_id=r.id AND s.source_kind='feed'
            JOIN user_scraper_configs c ON s.source_key='feed:' || c.id::text AND c.user_id=r.user_id AND c.is_active
        )
        SELECT tier,
            ARRAY(SELECT s.display_name FROM sources s WHERE s.tier=t.tier ORDER BY s.display_name) AS source_names,
            (SELECT count(*) FROM items i WHERE i.tier=t.tier) AS discovered,
            (SELECT count(*) FROM items i WHERE i.tier=t.tier AND i.published) AS ready,
            (SELECT count(*) FROM items i WHERE i.tier=t.tier AND NOT i.published AND i.status NOT IN ('failed','skipped')) AS processing,
            (SELECT count(*) FROM items i WHERE i.tier=t.tier AND NOT i.published AND i.status='failed') AS failed,
            (SELECT count(*) FROM items i WHERE i.tier=t.tier AND NOT i.published AND i.status='skipped') AS skipped,
            (SELECT count(*) FROM sources s WHERE s.tier=t.tier) AS source_count,
            (SELECT count(*) FROM sources s WHERE s.tier=t.tier AND s.status NOT IN ('processed','unavailable')) AS pending_sources,
            (SELECT count(*) FROM sources s WHERE s.tier=t.tier AND s.status='unavailable') AS unavailable_sources
        FROM (VALUES ('longform'),('audio')) AS t(tier)
    ").bind(run_id).fetch_all(pool).await
}

/// Reconcile one fixed source check per run. Terminal source results never follow later polls.
pub async fn reconcile_sources(
    connection: &mut PgConnection,
    user_id: i64,
) -> Result<(), sqlx::Error> {
    expire_runs(connection, user_id).await?;
    sqlx::query(r"UPDATE onboarding_first_edition_sources s SET status='unavailable', completed_at=timezone('UTC',now())
        FROM onboarding_first_edition_runs r WHERE s.run_id=r.id AND r.user_id::bigint=$1 AND r.status='active'
        AND s.status NOT IN ('processed','unavailable') AND NOT EXISTS (
          SELECT 1 FROM user_scraper_configs c WHERE c.user_id=r.user_id AND c.is_active AND (
            (s.source_kind='feed' AND s.source_key='feed:' || c.id::text)
            OR (s.source_kind='aggregator' AND s.source_key='scraper:' || lower(c.config::jsonb->>'key'))
            OR (s.source_kind NOT IN ('feed','aggregator'))))")
        .bind(user_id).execute(&mut *connection).await?;
    sqlx::query(r"UPDATE onboarding_first_edition_sources s SET check_cutoff=now()
        FROM onboarding_first_edition_runs r, source_ingestion_health h
        WHERE s.run_id=r.id AND r.user_id::bigint=$1 AND r.status='active' AND s.source_kind='aggregator'
          AND s.status NOT IN ('processed','unavailable') AND s.check_cutoff IS NULL
          AND h.source_key='aggregator:' || substr(s.source_key,9)
          AND h.last_success_at >= (r.started_at AT TIME ZONE 'UTC') - interval '1 hour'
          AND (h.error_code IS NULL OR h.last_success_at >= r.started_at AT TIME ZONE 'UTC')")
        .bind(user_id).execute(&mut *connection).await?;
    sqlx::query(r"WITH counts AS (
        SELECT s.id, count(n.id) FILTER(WHERE n.status='ready' AND n.representative_news_item_id IS NULL)::integer AS ready,
          count(n.id) FILTER(WHERE n.status IN ('new','processing')) AS pending
        FROM onboarding_first_edition_sources s JOIN onboarding_first_edition_runs r ON r.id=s.run_id
        LEFT JOIN news_items n ON n.visibility_scope='global' AND lower(n.platform)=substr(s.source_key,9)
          AND n.ingested_at >= timezone('UTC',s.check_cutoff)-interval '24 hours'
          AND n.ingested_at <= timezone('UTC',s.check_cutoff)
          AND COALESCE(n.published_at,n.ingested_at) >= timezone('UTC',s.check_cutoff)-interval '24 hours'
          AND EXISTS(SELECT 1 FROM user_scraper_configs c WHERE c.user_id=r.user_id AND c.is_active AND c.scraper_type='aggregator'
            AND lower(c.config::jsonb->>'key')=lower(n.platform)
            AND (lower(n.platform)<>'brutalist' OR COALESCE(c.config::jsonb->'topics','[]'::jsonb)='[]'::jsonb
              OR c.config::jsonb->'topics' ? (n.raw_metadata::jsonb #>> '{aggregator,topic}')))
        WHERE r.user_id::bigint=$1 AND r.status='active' AND s.source_kind='aggregator'
          AND s.status NOT IN ('processed','unavailable') AND s.check_cutoff IS NOT NULL GROUP BY s.id
    ) UPDATE onboarding_first_edition_sources s SET processed_item_count=c.ready,
        status=CASE WHEN c.pending=0 THEN 'processed' ELSE 'processing' END,
        completed_at=CASE WHEN c.pending=0 THEN timezone('UTC',now()) END
      FROM counts c WHERE s.id=c.id AND (s.processed_item_count IS DISTINCT FROM c.ready OR s.status IS DISTINCT FROM CASE WHEN c.pending=0 THEN 'processed' ELSE 'processing' END)")
        .bind(user_id).execute(&mut *connection).await?;
    sqlx::query(r"UPDATE onboarding_first_edition_sources s SET status='unavailable',completed_at=timezone('UTC',now())
        FROM onboarding_first_edition_runs r, source_ingestion_health h
        WHERE s.run_id=r.id AND r.user_id::bigint=$1 AND r.status='active' AND s.source_kind='aggregator'
          AND s.status NOT IN ('processed','unavailable') AND s.check_cutoff IS NULL
          AND h.source_key='aggregator:' || substr(s.source_key,9) AND h.error_code IS NOT NULL
          AND h.last_attempt_at >= r.started_at AT TIME ZONE 'UTC'")
        .bind(user_id).execute(connection).await?;
    Ok(())
}

pub async fn expire_runs(connection: &mut PgConnection, user_id: i64) -> Result<(), sqlx::Error> {
    sqlx::query("UPDATE onboarding_first_edition_runs SET status='expired', completed_at=timezone('UTC',now()) WHERE user_id::bigint=$1 AND status='active' AND started_at < timezone('UTC',now())-interval '24 hours'")
        .bind(user_id).execute(connection).await?;
    Ok(())
}
