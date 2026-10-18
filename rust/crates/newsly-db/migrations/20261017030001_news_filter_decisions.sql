-- Decisions are immutable for an exact user preference and summary identity.
CREATE TABLE news_filter_decisions (
    user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    news_item_id integer NOT NULL REFERENCES news_items(id) ON DELETE CASCADE,
    revision bigint NOT NULL,
    input_hash text NOT NULL,
    evaluator_version text NOT NULL,
    choice text NOT NULL CHECK (choice IN ('match','no_match','insufficient_evidence')),
    no_match_probability double precision NOT NULL CHECK (no_match_probability BETWEEN 0 AND 1),
    probabilities jsonb NOT NULL,
    created_at timestamp NOT NULL DEFAULT timezone('UTC',now()),
    PRIMARY KEY (user_id, news_item_id, revision, input_hash, evaluator_version),
    FOREIGN KEY (user_id, revision) REFERENCES news_filter_preferences(user_id, revision) ON DELETE CASCADE
);
CREATE INDEX news_filter_exclusions ON news_filter_decisions(user_id,revision,evaluator_version,news_item_id DESC)
    WHERE choice='no_match' AND no_match_probability>=0.90;
CREATE TABLE news_filter_daily_usage (
    user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    day date NOT NULL,
    background_count integer NOT NULL DEFAULT 0 CHECK (background_count BETWEEN 0 AND 500),
    preview_count integer NOT NULL DEFAULT 0 CHECK (preview_count BETWEEN 0 AND 200),
    preview_token uuid,
    preview_busy_until timestamptz,
    PRIMARY KEY (user_id, day)
);
ALTER TABLE briefing_pending_sources
    ADD COLUMN filter_revision bigint NOT NULL DEFAULT 0,
    ADD COLUMN filter_input_hash text,
    ADD COLUMN filter_outcome text;

-- Hash only evidence supplied to Jev; classification, ownership and publication are separate gates.
CREATE FUNCTION news_filter_input_hash(news news_items) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT encode(sha256(convert_to(jsonb_build_array(
        'news-evidence-v1',
        COALESCE(NULLIF(btrim(news.raw_metadata::jsonb #>> '{summary,title}'),''),
                 NULLIF(btrim(news.raw_metadata::jsonb #>> '{article,title}'),''),
                 left(btrim(news.summary_text),120), 'News item'),
        btrim(news.summary_text), COALESCE(news.summary_key_points::jsonb,'[]'::jsonb)
    )::text,'UTF8')),'hex')
$$;

-- Admission is distinct from readability: callers still enforce canonical ownership and readiness.
CREATE FUNCTION news_filter_admits(p_user bigint,p_news bigint,p_unavailable boolean DEFAULT false)
RETURNS boolean LANGUAGE sql STABLE AS $$
    SELECT CASE
      WHEN NOT COALESCE(pref.enabled,false) THEN true
      WHEN EXISTS(SELECT 1 FROM briefing_segments s WHERE s.user_id=p_user
          AND s.status IN ('active','degraded') AND s.source_keys::jsonb ? ('news:' || n.id::text)) THEN true
      WHEN COALESCE(n.published_at,n.ingested_at) < timezone('UTC',now())-interval '7 days' THEN false
      ELSE COALESCE((SELECT NOT(d.choice='no_match' AND d.no_match_probability>=0.90) FROM news_filter_decisions d
          WHERE d.user_id=p_user AND d.news_item_id=n.id AND d.revision=pref.revision
            AND d.input_hash=news_filter_input_hash(n) AND d.evaluator_version='jev-1.14-20261014-prose-choice-v1-p90'
            ),COALESCE(p_unavailable,false)) END
    FROM news_items n LEFT JOIN LATERAL (SELECT revision,enabled FROM news_filter_preferences
         WHERE user_id=p_user ORDER BY revision DESC LIMIT 1) pref ON true
    WHERE n.id=p_news
$$;

-- Excluded, outside-window and budget-deferred cohort items settle rather than hanging onboarding.
CREATE FUNCTION news_filter_settled(p_user bigint,p_news bigint) RETURNS boolean LANGUAGE sql STABLE AS $$
    SELECT COALESCE(pref.enabled,false) AND (
        COALESCE(n.published_at,n.ingested_at)<timezone('UTC',now())-interval '7 days'
        OR EXISTS(SELECT 1 FROM news_filter_decisions d WHERE d.user_id=p_user AND d.news_item_id=n.id
          AND d.revision=pref.revision AND d.input_hash=news_filter_input_hash(n)
          AND d.evaluator_version='jev-1.14-20261014-prose-choice-v1-p90' AND d.choice='no_match' AND d.no_match_probability>=0.90)
        OR (NOT EXISTS(SELECT 1 FROM news_filter_decisions d WHERE d.user_id=p_user AND d.news_item_id=n.id
          AND d.revision=pref.revision AND d.input_hash=news_filter_input_hash(n)
          AND d.evaluator_version='jev-1.14-20261014-prose-choice-v1-p90')
          AND EXISTS(SELECT 1 FROM news_filter_daily_usage u WHERE u.user_id=p_user AND u.day=(now() AT TIME ZONE 'UTC')::date AND u.background_count>=500))
    ) FROM news_items n LEFT JOIN LATERAL (SELECT revision,enabled FROM news_filter_preferences
        WHERE user_id=p_user ORDER BY revision DESC LIMIT 1) pref ON true WHERE n.id=p_news
$$;

-- A fresh matching decision cannot validate prose composed from an older pending snapshot.
CREATE FUNCTION news_filter_pending_admits(p_user bigint,p_news bigint,p_revision bigint,p_hash text,p_outcome text)
RETURNS boolean LANGUAGE sql STABLE AS $$
    SELECT CASE
      WHEN NOT COALESCE(pref.enabled,false) THEN true
      WHEN EXISTS(SELECT 1 FROM briefing_segments s WHERE s.user_id=p_user
        AND s.status IN ('active','degraded') AND s.source_keys::jsonb ? ('news:' || n.id::text)) THEN true
      ELSE p_revision=pref.revision AND p_hash=news_filter_input_hash(n)
        AND news_filter_admits(p_user,p_news,p_outcome='unavailable') END
    FROM news_items n LEFT JOIN LATERAL (SELECT revision,enabled FROM news_filter_preferences
        WHERE user_id=p_user ORDER BY revision DESC LIMIT 1) pref ON true WHERE n.id=p_news
$$;
