-- Roll-forward only. Briefing Source Cards retired figure and pullquote blocks, and the public
-- block enum no longer has those cases. Strip them from every stored segment, whatever its
-- status, keeping the remaining blocks in their original order. Then bump the user-scoped
-- Briefing version exactly once for each user whose visible (active or degraded) segments
-- changed (law B7). Removed blocks are not recoverable; nothing produces them anymore, and a
-- rerun finds nothing to strip.
-- Live publication, compaction, and read-marking lock briefing_states before briefing_segments.
-- Use one statement snapshot for candidate selection, state locks, and mutation so a worker
-- cannot introduce a newly affected user between the state lock and the strip.
WITH candidates AS MATERIALIZED (
    SELECT id, user_id
    FROM briefing_segments
    WHERE jsonb_typeof(blocks) = 'array'
      AND jsonb_path_exists(blocks, '$[*] ? (@.type == "figure" || @.type == "pullquote")')
), locked_states AS MATERIALIZED (
    SELECT state.user_id
    FROM briefing_states AS state
    WHERE state.user_id IN (SELECT user_id FROM candidates)
    ORDER BY state.user_id
    FOR UPDATE
), stripped AS (
    UPDATE briefing_segments AS segment
    SET blocks = (
            SELECT COALESCE(jsonb_agg(item.block ORDER BY item.position), '[]'::jsonb)
            FROM jsonb_array_elements(segment.blocks) WITH ORDINALITY AS item(block, position)
            WHERE NOT COALESCE(item.block ->> 'type' IN ('figure', 'pullquote'), false)
        ),
        updated_at = timezone('UTC', clock_timestamp())
    FROM candidates AS candidate
    WHERE segment.id = candidate.id
      AND (
          candidate.user_id IN (SELECT user_id FROM locked_states)
          OR NOT EXISTS (SELECT 1 FROM briefing_states AS state WHERE state.user_id = candidate.user_id)
      )
      AND jsonb_typeof(segment.blocks) = 'array'
      AND jsonb_path_exists(
          segment.blocks,
          '$[*] ? (@.type == "figure" || @.type == "pullquote")'
      )
    RETURNING segment.user_id, segment.status
)
UPDATE briefing_states AS state
SET version = state.version + 1
WHERE state.user_id IN (
    SELECT DISTINCT user_id FROM stripped WHERE status IN ('active', 'degraded')
);
