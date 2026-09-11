CREATE TABLE news_lens_embedding_models (
    model text PRIMARY KEY,
    dimensions integer NOT NULL CHECK (dimensions > 0),
    UNIQUE (model, dimensions)
);
CREATE TABLE news_lens_embeddings (
    news_item_id integer NOT NULL REFERENCES news_items(id) ON DELETE CASCADE,
    model text NOT NULL,
    input_hash text NOT NULL,
    encoder_version integer NOT NULL,
    dimensions integer NOT NULL CHECK (dimensions > 0),
    vector jsonb NOT NULL CHECK (jsonb_typeof(vector) = 'array'),
    created_at timestamptz NOT NULL DEFAULT now(),
    checked_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (model, dimensions) REFERENCES news_lens_embedding_models(model, dimensions),
    PRIMARY KEY (news_item_id, model),
    CHECK (jsonb_array_length(vector) = dimensions)
);
ALTER TABLE onboarding_first_edition_runs
    ADD COLUMN news_seeded boolean NOT NULL DEFAULT false,
    ADD COLUMN news_seed_settled boolean NOT NULL DEFAULT false;
CREATE TABLE onboarding_first_edition_items (
    run_id integer NOT NULL REFERENCES onboarding_first_edition_runs(id) ON DELETE CASCADE,
    source_kind text NOT NULL CHECK (source_kind IN ('content', 'news')),
    source_id integer NOT NULL,
    PRIMARY KEY (run_id, source_kind, source_id)
);
CREATE INDEX ix_first_edition_items_source ON onboarding_first_edition_items(source_kind, source_id);
INSERT INTO runtime_ownership (resource_kind, resource_key, active_owner, active_version, updated_by, reason)
VALUES ('task_type', 'prepare_news_lens', 'rust', 1, 'migration', 'Shared news lens preparation')
ON CONFLICT DO NOTHING;

ALTER TABLE onboarding_first_edition_sources ADD COLUMN check_cutoff timestamptz;




ALTER TABLE processing_tasks ADD COLUMN priority integer NOT NULL DEFAULT 0;
