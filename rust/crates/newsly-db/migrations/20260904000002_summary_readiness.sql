-- Artwork is optional; release summaries that were waiting for it under the old policy.
UPDATE contents
SET status = 'completed', updated_at = timezone('UTC', now())
WHERE status = 'awaiting_image' AND content_type IN ('article', 'podcast')
  AND jsonb_typeof(COALESCE(content_metadata::jsonb #> '{processing,summary}',
      content_metadata::jsonb #> '{domain,summary}', content_metadata::jsonb -> 'summary')) = 'object';
