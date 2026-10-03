-- Roll-forward council retirement. Stop all API/worker/scheduler writers and drain council
-- messages before an explicitly attested maintenance cutover. Ordinary queued turns survive.
-- Historical columns and candidate metadata remain for data preservation and installed-client
-- compatibility; neither the new runtime nor an old rollback slot can start another council.

LOCK TABLE public.chat_sessions, public.chat_messages IN ACCESS EXCLUSIVE MODE;

DO $$
DECLARE
    parent RECORD;
    child RECORD;
    boundary_id integer;
BEGIN
    IF (EXISTS (SELECT 1 FROM users) OR EXISTS (SELECT 1 FROM chat_sessions))
       AND current_setting('newsly.maintenance_barrier_confirmed', true) IS DISTINCT FROM 'on'
    THEN
        RAISE EXCEPTION 'Council retirement requires a confirmed maintenance barrier; run verify-council-retirement, drain council work, and use deploy_blue_green.sh --maintenance-cutover';
    END IF;

    IF EXISTS (
        SELECT 1 FROM chat_messages AS message
        LEFT JOIN chat_sessions AS session ON session.id = message.session_id
        WHERE message.status = 'processing'
          AND (message.processing_context ->> 'kind' = 'council'
               OR COALESCE(message.processing_context::jsonb -> 'council_run', 'null'::jsonb) <> 'null'::jsonb
               OR session.council_mode OR session.is_hidden_from_history)
    ) THEN
        RAISE EXCEPTION 'Council retirement is blocked by processing messages; drain or explicitly repair them before retrying (verify-council-retirement)';
    END IF;

    IF EXISTS (
        SELECT 1 FROM chat_sessions AS branch
        LEFT JOIN chat_sessions AS owner ON owner.id = branch.parent_session_id
        WHERE branch.is_hidden_from_history
          AND (owner.id IS NULL OR owner.user_id <> branch.user_id)
    ) THEN
        RAISE EXCEPTION 'Council retirement found an orphaned or cross-user branch';
    END IF;

    IF EXISTS (
        SELECT 1 FROM chat_sessions
        WHERE NOT council_mode AND (active_child_session_id IS NOT NULL OR council_message_id IS NOT NULL)
    ) THEN
        RAISE EXCEPTION 'Council retirement found branch pointers on a non-council session';
    END IF;

    FOR parent IN SELECT * FROM chat_sessions WHERE council_mode ORDER BY id LOOP
        SELECT * INTO child FROM chat_sessions WHERE id = parent.active_child_session_id;
        IF child.id IS NULL OR child.parent_session_id IS DISTINCT FROM parent.id
           OR child.user_id IS DISTINCT FROM parent.user_id OR NOT child.is_hidden_from_history
           OR NOT EXISTS (SELECT 1 FROM chat_messages WHERE id = parent.council_message_id AND session_id = parent.id)
        THEN
            RAISE EXCEPTION 'Council retirement found an invalid selected branch or parent message for session %', parent.id;
        END IF;

        boundary_id := child.branch_start_message_id;
        IF boundary_id IS NULL THEN
            -- All-failed councils never acquired a successful boundary. The latest run is the
            -- boundary, so cloned prehistory and initial/retry prompts are not duplicated.
            SELECT max(id) INTO boundary_id FROM chat_messages
            WHERE session_id = child.id
              AND COALESCE(processing_context::jsonb -> 'council_run', 'null'::jsonb) <> 'null'::jsonb;
        END IF;
        -- A persisted successful boundary remains authoritative even for older terminal rows
        -- without a processing_context. Only the missing-boundary fallback requires run metadata.
        IF boundary_id IS NULL OR NOT EXISTS (
            SELECT 1 FROM chat_messages WHERE id = boundary_id AND session_id = child.id
        ) THEN
            RAISE EXCEPTION 'Council retirement found an invalid history boundary for session %', parent.id;
        END IF;

        -- Follow-ups have kind=council too; only council_run identifies start/retry rows.
        -- Move rather than copy to preserve durable message IDs and references.
        UPDATE chat_messages SET session_id = parent.id
        WHERE session_id = child.id AND id > boundary_id
          AND COALESCE(processing_context::jsonb -> 'council_run', 'null'::jsonb) = 'null'::jsonb;

        UPDATE chat_sessions SET council_mode = FALSE, active_child_session_id = NULL,
            council_message_id = NULL,
            last_message_at = GREATEST(last_message_at,
                (SELECT max(created_at) FROM chat_messages WHERE session_id = parent.id))
        WHERE id = parent.id;
    END LOOP;

    UPDATE chat_sessions SET is_archived = TRUE WHERE is_hidden_from_history AND NOT is_archived;
END;
$$;

ALTER TABLE chat_sessions ALTER COLUMN council_mode SET DEFAULT FALSE;
ALTER TABLE chat_sessions ALTER COLUMN is_hidden_from_history SET DEFAULT FALSE;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'public.chat_sessions'::regclass AND conname = 'chat_sessions_council_retired') THEN
        ALTER TABLE chat_sessions ADD CONSTRAINT chat_sessions_council_retired CHECK (NOT council_mode);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'public.chat_sessions'::regclass AND conname = 'chat_sessions_no_active_branch') THEN
        ALTER TABLE chat_sessions ADD CONSTRAINT chat_sessions_no_active_branch CHECK (active_child_session_id IS NULL AND council_message_id IS NULL);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'public.chat_sessions'::regclass AND conname = 'chat_sessions_hidden_history_archived') THEN
        ALTER TABLE chat_sessions ADD CONSTRAINT chat_sessions_hidden_history_archived CHECK (NOT is_hidden_from_history OR is_archived);
    END IF;
END;
$$;
