-- One shared credit gate for all workers using the application Firecrawl account.
CREATE TABLE firecrawl_credit_state (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    retry_after timestamptz,
    probe_token uuid,
    probe_expires_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((probe_token IS NULL) = (probe_expires_at IS NULL)),
    CHECK (probe_token IS NULL OR retry_after IS NOT NULL)
);
INSERT INTO firecrawl_credit_state(singleton) VALUES (true);
