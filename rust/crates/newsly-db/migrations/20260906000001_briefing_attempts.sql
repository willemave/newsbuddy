CREATE TABLE briefing_composition_attempts (
    attempt_id uuid PRIMARY KEY,
    task_id bigint NOT NULL,
    user_id bigint NOT NULL,
    fingerprint text NOT NULL,
    outcome text NOT NULL,
    observed_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX briefing_attempts_fingerprint ON briefing_composition_attempts(user_id, fingerprint, observed_at DESC);
CREATE TABLE briefing_composition_cooldowns (
    user_id bigint NOT NULL,
    fingerprint text NOT NULL,
    retry_after timestamptz NOT NULL,
    PRIMARY KEY(user_id, fingerprint)
);
