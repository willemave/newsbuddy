-- Existing writers must be drained before installing the new contention protocol.
DO $$
BEGIN
    IF (EXISTS (SELECT 1 FROM news_items) OR EXISTS (SELECT 1 FROM processing_tasks))
       AND current_setting('newsly.maintenance_barrier_confirmed', true) IS DISTINCT FROM 'on'
    THEN
        RAISE EXCEPTION 'News grouping requires a confirmed maintenance barrier; use deploy_blue_green.sh --maintenance-cutover';
    END IF;
END
$$;

-- Two-integer advisory keys occupy a different lock space from the existing bigint
-- scheduler, demand and ownership keys. Separate NULL from owner 0 deliberately:
-- candidate discovery compares the complete (visibility_scope, owner_user_id) tuple.
CREATE FUNCTION newsly_news_grouping_scope_namespace(scope text, owner_id bigint)
RETURNS integer LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE AS $$
BEGIN
    CASE scope
        WHEN 'global' THEN RETURN CASE WHEN owner_id IS NULL THEN 19018001 ELSE 19018002 END;
        WHEN 'user' THEN RETURN CASE WHEN owner_id IS NULL THEN 19018003 ELSE 19018004 END;
        ELSE RAISE EXCEPTION 'unsupported News grouping scope' USING ERRCODE = '22023';
    END CASE;
END
$$;

CREATE FUNCTION newsly_try_lock_news_grouping_scope(scope text, owner_id bigint)
RETURNS boolean LANGUAGE sql VOLATILE AS $$
    SELECT pg_try_advisory_xact_lock(
        newsly_news_grouping_scope_namespace(scope, owner_id), COALESCE(owner_id, 0)::integer)
$$;

-- Match the volatile-key exclusions in the Rust source fingerprint. Unknown keys
-- remain guarded; a future input cannot silently bypass publication coordination.
CREATE FUNCTION newsly_news_grouping_metadata(metadata json)
RETURNS jsonb LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT CASE WHEN jsonb_typeof(value) = 'object' THEN
        CASE WHEN jsonb_typeof(value #> '{aggregator,metadata}') = 'object' THEN
            (value - ARRAY['summary_checkpoint', 'processing_error', 'comment_count', 'scraped_at'])
                #- '{aggregator,metadata,score}'
                #- '{aggregator,metadata,comments_count}'
                #- '{aggregator,metadata,upvote_ratio}'
        ELSE value - ARRAY['summary_checkpoint', 'processing_error', 'comment_count', 'scraped_at'] END
    ELSE value END
    FROM (SELECT COALESCE(metadata, '{}'::json)::jsonb AS value) AS normalized
$$;

CREATE FUNCTION newsly_guard_news_grouping_write()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    old_namespace integer;
    new_namespace integer;
    old_owner integer;
    new_owner integer;
    guard record;
    scope_changed boolean := false;
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'ready' THEN RETURN NEW; END IF;
        new_namespace := newsly_news_grouping_scope_namespace(NEW.visibility_scope, NEW.owner_user_id);
        new_owner := COALESCE(NEW.owner_user_id, 0);
    ELSIF TG_OP = 'DELETE' THEN
        IF OLD.status <> 'ready' THEN RETURN OLD; END IF;
        old_namespace := newsly_news_grouping_scope_namespace(OLD.visibility_scope, OLD.owner_user_id);
        old_owner := COALESCE(OLD.owner_user_id, 0);
    ELSE
        scope_changed := ROW(OLD.visibility_scope, OLD.owner_user_id)
            IS DISTINCT FROM ROW(NEW.visibility_scope, NEW.owner_user_id);
        IF NOT scope_changed AND OLD.status <> 'ready' AND NEW.status <> 'ready' THEN
            RETURN NEW;
        END IF;
        IF NOT scope_changed AND ROW(
            OLD.id, OLD.status, OLD.representative_news_item_id, OLD.cluster_size,
            OLD.platform, OLD.source_type, OLD.source_label, OLD.source_external_id,
            OLD.canonical_item_url, OLD.canonical_story_url, OLD.article_url,
            OLD.article_domain, OLD.discussion_url, OLD.summary_key_points::jsonb,
            OLD.summary_text, OLD.ingested_at, newsly_news_grouping_metadata(OLD.raw_metadata)
        ) IS NOT DISTINCT FROM ROW(
            NEW.id, NEW.status, NEW.representative_news_item_id, NEW.cluster_size,
            NEW.platform, NEW.source_type, NEW.source_label, NEW.source_external_id,
            NEW.canonical_item_url, NEW.canonical_story_url, NEW.article_url,
            NEW.article_domain, NEW.discussion_url, NEW.summary_key_points::jsonb,
            NEW.summary_text, NEW.ingested_at, newsly_news_grouping_metadata(NEW.raw_metadata)
        ) THEN RETURN NEW; END IF;
        old_namespace := newsly_news_grouping_scope_namespace(OLD.visibility_scope, OLD.owner_user_id);
        old_owner := COALESCE(OLD.owner_user_id, 0);
        new_namespace := newsly_news_grouping_scope_namespace(NEW.visibility_scope, NEW.owner_user_id);
        new_owner := COALESCE(NEW.owner_user_id, 0);
    END IF;

    -- All required OLD and NEW scope keys are acquired in the same stable order.
    -- Never wait here: UPDATE/DELETE may already hold a story row lock.
    FOR guard IN
        SELECT DISTINCT namespace, owner_id
        FROM (VALUES (old_namespace, old_owner), (new_namespace, new_owner)) AS scopes(namespace, owner_id)
        WHERE namespace IS NOT NULL ORDER BY namespace, owner_id
    LOOP
        IF NOT pg_try_advisory_xact_lock(guard.namespace, guard.owner_id) THEN
            RAISE EXCEPTION 'News grouping scope is busy' USING
                ERRCODE = '40001', CONSTRAINT = 'news_grouping_scope_busy';
        END IF;
    END LOOP;

    -- Moving an intact member/root would cross owner boundaries or strand members.
    -- Detach/reconcile the group explicitly before moving an individual source.
    IF scope_changed AND (
        OLD.representative_news_item_id IS NOT NULL
        OR NEW.representative_news_item_id IS NOT NULL
        OR EXISTS (SELECT 1 FROM news_items WHERE representative_news_item_id = OLD.id)
    ) THEN
        RAISE EXCEPTION 'grouped News items cannot change visibility or owner' USING
            ERRCODE = '23514', CONSTRAINT = 'news_grouping_scope_membership';
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER news_grouping_write_guard
BEFORE INSERT OR UPDATE OR DELETE ON news_items
FOR EACH ROW EXECUTE FUNCTION newsly_guard_news_grouping_write();

COMMENT ON TRIGGER news_grouping_write_guard ON news_items IS
'Coordinate candidate writers with fresh guarded matching. Operator repairs must not disable triggers, use replication bypass, or TRUNCATE news_items. Drain old processors before enabling parallel processing.';
