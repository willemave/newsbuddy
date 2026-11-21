-- Roll-forward only. Briefing Source Cards retired figure and pullquote blocks, and the public
-- block enum no longer has those cases. Strip them from every stored segment, whatever its
-- status, keeping the remaining blocks in their original order. Then bump the user-scoped
-- Briefing version exactly once for each user whose visible (active or degraded) segments
-- changed (law B7). Removed blocks are not recoverable; nothing produces them anymore, and a
-- rerun finds nothing to strip.
-- Live publication, compaction, and read-marking lock briefing_states before briefing_segments.
-- Take every affected user's state row first, in a fixed order, so an online deploy cannot
-- deadlock with them.
SELECT 1
FROM briefing_states AS state
WHERE state.user_id IN (
    SELECT segment.user_id
    FROM briefing_segments AS segment
    WHERE jsonb_typeof(segment.blocks) = 'array'
      AND jsonb_path_exists(
          segment.blocks,
          '$[*] ? (@.type == "figure" || @.type == "pullquote")'
      )
)
ORDER BY state.user_id
FOR UPDATE;

WITH stripped AS (
    UPDATE briefing_segments AS segment
    SET blocks = (
            SELECT COALESCE(jsonb_agg(item.block ORDER BY item.position), '[]'::jsonb)
            FROM jsonb_array_elements(segment.blocks) WITH ORDINALITY AS item(block, position)
            WHERE NOT COALESCE(item.block ->> 'type' IN ('figure', 'pullquote'), false)
        ),
        updated_at = timezone('UTC', clock_timestamp())
    WHERE jsonb_typeof(segment.blocks) = 'array'
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
