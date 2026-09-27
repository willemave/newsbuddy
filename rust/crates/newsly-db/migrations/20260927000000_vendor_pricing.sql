-- Forward-looking public list estimates. Existing usage is intentionally untouched:
-- historical service tiers, contracts, and metered units cannot be reconstructed.
ALTER TABLE vendor_usage_records
    ADD COLUMN idempotency_key text,
    ADD COLUMN cost_basis text;

CREATE UNIQUE INDEX vendor_usage_idempotency_key_unique
    ON vendor_usage_records (idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE TABLE vendor_token_price_rates (
    provider text NOT NULL,
    model text NOT NULL,
    effective_at timestamptz NOT NULL,
    input_per_million_usd numeric(14, 6) NOT NULL,
    cached_input_per_million_usd numeric(14, 6) NOT NULL,
    cache_write_per_million_usd numeric(14, 6) NOT NULL,
    output_per_million_usd numeric(14, 6) NOT NULL,
    source_url text NOT NULL,
    pricing_version text NOT NULL,
    PRIMARY KEY (provider, model, effective_at),
    CHECK (input_per_million_usd >= 0 AND cached_input_per_million_usd >= 0
       AND cache_write_per_million_usd >= 0 AND output_per_million_usd >= 0)
);

-- Standard, short-context API prices in USD per million tokens on 2026-09-27.
-- OpenAI Fast/priority, Batch/Flex, regional, long-context, and custom rates are
-- deliberately excluded by the trigger below.
INSERT INTO vendor_token_price_rates
    (provider, model, effective_at, input_per_million_usd,
     cached_input_per_million_usd, cache_write_per_million_usd,
     output_per_million_usd, source_url, pricing_version)
VALUES
    ('openai', 'gpt-6-luna', '2026-09-27 00:00:00+00',
     0.10, 0.01, 0.125, 0.50,
     'https://developers.openai.com/api/docs/models/gpt-6-luna',
     'openai-standard-2026-09-27'),
    ('openai', 'gpt-5.6-luna', '2026-09-27 00:00:00+00',
     0.20, 0.02, 0.25, 1.20,
     'https://developers.openai.com/api/docs/models/gpt-5.6-luna',
     'openai-standard-2026-09-27'),
    ('openai', 'gpt-5.6-terra', '2026-09-27 00:00:00+00',
     2.00, 0.20, 2.50, 12.00,
     'https://developers.openai.com/api/docs/models/gpt-5.6-terra',
     'openai-standard-2026-09-27'),
    ('openrouter', 'qwen/qwen3-embedding-8b', '2026-09-27 00:00:00+00',
     0.01, 0.01, 0.01, 0.00,
     'https://openrouter.ai/qwen/qwen3-embedding-8b/pricing',
     'openrouter-standard-2026-09-27');

CREATE FUNCTION price_vendor_token_usage() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    normalized_model text;
    price vendor_token_price_rates%ROWTYPE;
    read_tokens bigint;
    write_tokens bigint;
BEGIN
    -- Provider-reported or explicitly configured costs remain authoritative.
    IF NEW.cost_usd IS NOT NULL THEN
        IF NEW.cost_basis IS NULL THEN
            NEW.cost_basis := CASE NEW.provider
                WHEN 'runware' THEN 'provider_reported'
                WHEN 'firecrawl' THEN 'configured_rate'
                WHEN 'x' THEN 'configured_rate'
                ELSE NULL
            END;
        END IF;
        RETURN NEW;
    END IF;

    -- Non-token meters may carry an explicit reason for remaining unpriced.
    IF NEW.provider NOT IN ('openai', 'openrouter') THEN
        RETURN NEW;
    END IF;

    -- A stale version on an unpriced row must not imply a known price.
    NEW.pricing_version := NULL;
    NEW.cost_basis := NULL;

    IF NEW.request_count IS DISTINCT FROM 1
       OR NEW.input_tokens IS NULL OR NEW.output_tokens IS NULL
       OR NEW.input_tokens <= 0 OR NEW.output_tokens < 0 THEN
        RETURN NEW;
    END IF;

    read_tokens := COALESCE(NEW.cache_read_tokens, 0);
    write_tokens := COALESCE(NEW.cache_write_tokens, 0);
    IF read_tokens < 0 OR write_tokens < 0
       OR read_tokens + write_tokens > NEW.input_tokens THEN
        RETURN NEW;
    END IF;

    IF NEW.provider = 'openai' THEN
        -- These operations use standard processing in the current owned routes.
        -- New operations must be reviewed before joining this allowlist.
        IF NEW.input_tokens > 272000
           OR NOT (NEW.operation LIKE 'news_processing.%'
                OR NEW.operation LIKE 'news_discussions.%'
                OR NEW.operation LIKE 'summarization.%'
                OR NEW.operation = 'briefing.compose_window.observed'
                OR NEW.operation = 'content_analyzer.analyze_url')
           OR COALESCE(NEW.metadata::jsonb ->> 'service_tier', 'standard') <> 'standard' THEN
            RETURN NEW;
        END IF;
        normalized_model := CASE WHEN NEW.model LIKE 'openai:%'
            THEN substring(NEW.model FROM 8) ELSE NEW.model END;
    ELSE
        normalized_model := lower(CASE WHEN NEW.model LIKE 'openrouter:%'
            THEN substring(NEW.model FROM 12) ELSE NEW.model END);
    END IF;

    SELECT * INTO price FROM vendor_token_price_rates AS rate
    WHERE rate.provider = NEW.provider AND rate.model = normalized_model
      AND rate.effective_at <= NEW.created_at AT TIME ZONE 'UTC'
    ORDER BY rate.effective_at DESC LIMIT 1;
    IF NOT FOUND THEN
        RETURN NEW;
    END IF;

    NEW.cost_usd := ((NEW.input_tokens - read_tokens - write_tokens)
        * price.input_per_million_usd
        + read_tokens * price.cached_input_per_million_usd
        + write_tokens * price.cache_write_per_million_usd
        + NEW.output_tokens * price.output_per_million_usd)::double precision
        / 1000000.0;
    NEW.pricing_version := price.pricing_version;
    NEW.cost_basis := 'public_list_estimate';
    NEW.metadata := jsonb_set(
        COALESCE(NEW.metadata::jsonb, '{}'::jsonb), '{pricing}',
        jsonb_build_object('basis', 'public_list_estimate',
                           'source_url', price.source_url,
                           'version', price.pricing_version), true)::json;
    RETURN NEW;
END;
$$;

CREATE TRIGGER vendor_token_usage_price_on_insert
BEFORE INSERT ON vendor_usage_records
FOR EACH ROW EXECUTE FUNCTION price_vendor_token_usage();
