-- Deck admission and execution must switch together with old writers/workers
-- stopped. The payload marker is the queue-owned, indexable execution category.
DO $$
BEGIN
    IF (EXISTS (SELECT 1 FROM llm_tasks) OR EXISTS (SELECT 1 FROM processing_tasks))
       AND current_setting('newsly.maintenance_barrier_confirmed', true) IS DISTINCT FROM 'on'
    THEN
        RAISE EXCEPTION 'Learning Deck queue requires a confirmed maintenance barrier; use deploy_blue_green.sh --maintenance-cutover';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM processing_tasks AS delivery
        JOIN llm_tasks AS task ON delivery.payload ->> 'llm_task_id' = task.id::text
        WHERE delivery.task_type = 'run_llm_task'
          AND delivery.status IN ('pending', 'processing')
          AND task.task_kind = 'learning_deck'
          AND delivery.owner_user_id IS DISTINCT FROM task.user_id
    ) THEN
        RAISE EXCEPTION 'Learning Deck delivery owner mismatch; resolve ownership before migration';
    END IF;
END
$$;

-- Match the text comparison used by reservation checks without casting payload IDs.
CREATE INDEX idx_learning_deck_generation_state_task_id_text
    ON learning_deck_generation_state ((llm_task_id::text));

UPDATE processing_tasks AS delivery
SET payload = (COALESCE(delivery.payload, '{}'::json)::jsonb
               || jsonb_build_object('task_kind', task.task_kind))::json
FROM llm_tasks AS task
WHERE delivery.task_type = 'run_llm_task'
  AND delivery.status IN ('pending', 'processing')
  AND delivery.payload ->> 'llm_task_id' = task.id::text
  AND task.task_kind IN ('learning_deck', 'share_action');

DO $$
BEGIN
    IF EXISTS (
        SELECT delivery.owner_user_id FROM processing_tasks AS delivery
        WHERE delivery.task_type = 'run_llm_task'
          AND delivery.payload ->> 'task_kind' = 'learning_deck'
          AND (
              delivery.status = 'processing'
              OR (delivery.status = 'pending' AND EXISTS (
                  SELECT 1 FROM learning_deck_generation_state AS generation
                  WHERE generation.llm_task_id::text = delivery.payload ->> 'llm_task_id'
              ))
          )
        GROUP BY delivery.owner_user_id HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION 'multiple reserved Learning Deck tasks for one owner; drain workers and resolve ownership before migration';
    END IF;
END
$$;

CREATE UNIQUE INDEX uq_llm_tasks_learning_deck_subject_active
    ON llm_tasks (subject_id)
    WHERE task_kind = 'learning_deck'
      AND status IN ('queued', 'preparing', 'running', 'awaiting_approval', 'applying');
DROP INDEX uq_llm_tasks_learning_deck_user_active;

CREATE UNIQUE INDEX uq_processing_tasks_learning_deck_owner_processing
    ON processing_tasks (owner_user_id)
    WHERE task_type = 'run_llm_task' AND payload ->> 'task_kind' = 'learning_deck'
      AND status = 'processing';
