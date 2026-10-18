-- Writers are stopped for this cutover. Keep the guard, ownership check, index
-- and SQLx history in one transaction so an interruption leaves no orphan index.
DO $$
BEGIN
    IF (EXISTS (SELECT 1 FROM news_items) OR EXISTS (SELECT 1 FROM processing_tasks))
       AND current_setting('newsly.maintenance_barrier_confirmed', true) IS DISTINCT FROM 'on'
    THEN
        RAISE EXCEPTION 'Briefing owner exclusion requires a confirmed maintenance barrier; use deploy_blue_green.sh --maintenance-cutover';
    END IF;
    IF EXISTS (
        SELECT owner_user_id
        FROM processing_tasks
        WHERE task_type = 'briefing_refresh' AND status = 'processing'
        GROUP BY owner_user_id
        HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION 'multiple processing Briefing tasks for one owner; drain workers and resolve ownership before migration';
    END IF;
END
$$;

CREATE UNIQUE INDEX uq_processing_tasks_briefing_owner_processing
    ON processing_tasks (owner_user_id)
    WHERE task_type = 'briefing_refresh' AND status = 'processing';
