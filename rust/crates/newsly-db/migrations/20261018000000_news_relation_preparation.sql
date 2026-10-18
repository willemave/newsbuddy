-- Private reusable inputs for the News relation matcher, separate from lens vectors.
CREATE TABLE news_relation_embeddings (
    news_item_id integer NOT NULL REFERENCES news_items(id) ON DELETE CASCADE,
    provider text NOT NULL,
    model text NOT NULL,
    encoder_version integer NOT NULL,
    text_hash text NOT NULL,
    dimensions integer NOT NULL CHECK (dimensions > 0),
    vector jsonb NOT NULL CHECK (jsonb_typeof(vector) = 'array'),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (news_item_id, provider, model, encoder_version, text_hash),
    CHECK (jsonb_array_length(vector) = dimensions)
);
CREATE TABLE news_relation_link_preparations (
    news_item_id integer NOT NULL REFERENCES news_items(id) ON DELETE CASCADE,
    input_hash text NOT NULL,
    model text NOT NULL,
    result jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (news_item_id, input_hash, model)
);

-- Recovery budget is durable and independent of provider failure retries.
CREATE TABLE news_grouping_deferrals (
    task_id integer PRIMARY KEY REFERENCES processing_tasks(id) ON DELETE CASCADE,
    retry_count integer NOT NULL DEFAULT 0,
    first_deferred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    deferral_count integer NOT NULL DEFAULT 1
);

-- Successful extracted bodies remain private until fenced enrichment publication.
CREATE TABLE news_enrichment_preparations (
    news_item_id integer NOT NULL REFERENCES news_items(id) ON DELETE CASCADE,
    source_fingerprint text NOT NULL,
    policy_version integer NOT NULL,
    prepared jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(news_item_id, source_fingerprint, policy_version)
);
