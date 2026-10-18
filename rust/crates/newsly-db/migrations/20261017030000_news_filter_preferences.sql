ALTER TABLE onboarding_discovery_runs
    ADD COLUMN original_input_text text,
    ADD COLUMN original_input_kind text CHECK (original_input_kind IN ('typed', 'speech_transcript', 'agent_brief'));

CREATE TABLE news_filter_preferences (
    user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    revision bigint NOT NULL CHECK (revision > 0),
    text text NOT NULL CHECK (char_length(text) <= 8000),
    enabled boolean NOT NULL,
    origin text NOT NULL CHECK (origin IN ('settings', 'onboarding')),
    input_kind text CHECK (input_kind IN ('typed', 'speech_transcript', 'agent_brief')),
    source_run_id integer REFERENCES onboarding_discovery_runs(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, revision),
    CHECK (NOT enabled OR (length(btrim(text)) > 0 AND octet_length(text) <= 8192))
);
