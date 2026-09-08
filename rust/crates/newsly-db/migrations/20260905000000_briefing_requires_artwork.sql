-- Long-form Briefing sources require generated artwork. Retire segments published during the
-- optional-artwork window. Historical provider work is never authorized by a schema migration.
WITH retired AS (
    UPDATE briefing_segments AS segment
    SET status = 'retired', updated_at = timezone('UTC', clock_timestamp())
    WHERE segment.status IN ('active', 'degraded')
      AND EXISTS (
          SELECT 1
          FROM jsonb_array_elements_text(segment.source_keys::jsonb) AS source(key)
          JOIN contents AS content
            ON source.key = 'content:' || content.id::text
          WHERE content.content_type IN ('article', 'podcast')
            AND content.status = 'completed'
            AND jsonb_typeof(COALESCE(
                content.content_metadata::jsonb #> '{processing,summary}',
                content.content_metadata::jsonb #> '{domain,summary}',
                content.content_metadata::jsonb -> 'summary'
            )) = 'object'
            AND NULLIF(COALESCE(
                content.content_metadata::jsonb #> '{domain,image_generated_at}',
                content.content_metadata::jsonb -> 'image_generated_at'
            ), 'null'::jsonb) IS NULL
      )
    RETURNING segment.user_id
)
UPDATE briefing_states AS state
SET version = state.version + 1
WHERE state.user_id IN (SELECT DISTINCT user_id FROM retired);

-- Use the reviewed, bounded `newsly-admin tasks artwork-backfill` operation for historical work.
-- Completed summaries remain readable; Briefing eligibility independently enforces artwork.
