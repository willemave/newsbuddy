-- Cross-list topic visibility extends the canonical admission rule for every caller.
CREATE OR REPLACE FUNCTION aggregator_config_admits(config jsonb, platform text, metadata json)
RETURNS boolean LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT config ->> 'key' = platform
       AND CASE
               WHEN metadata #>> '{aggregator,topic}' IS NULL
                 OR jsonb_typeof(config -> 'topics') IS DISTINCT FROM 'array'
                 OR config -> 'topics' = '[]'::jsonb
               THEN TRUE
               ELSE config -> 'topics' ? (metadata #>> '{aggregator,topic}')
                 OR CASE WHEN jsonb_typeof(metadata::jsonb #> '{aggregator,topics}') = 'array'
                    THEN jsonb_path_exists(config -> 'topics', '$[*] ? (@ == $t[*])',
                         jsonb_set('{"t":[]}'::jsonb,'{t}',metadata::jsonb #> '{aggregator,topics}'))
                    ELSE FALSE END
           END
$$;
