CREATE FUNCTION newsly_brutalist_visible(selected_topics jsonb, item_metadata jsonb)
RETURNS boolean
LANGUAGE sql IMMUTABLE PARALLEL SAFE
AS $$
    SELECT selected_topics IS NULL
        OR jsonb_typeof(selected_topics) <> 'array'
        OR selected_topics = '[]'::jsonb
        OR EXISTS (
            SELECT 1
            FROM jsonb_array_elements_text(selected_topics) selected(topic)
            CROSS JOIN LATERAL jsonb_array_elements_text(
                CASE WHEN jsonb_typeof(item_metadata #> '{aggregator,topics}') = 'array'
                    THEN item_metadata #> '{aggregator,topics}'
                    ELSE jsonb_build_array(item_metadata #>> '{aggregator,topic}')
                END
            ) item(topic)
            WHERE lower(btrim(selected.topic)) = lower(btrim(item.topic))
        )
$$;
