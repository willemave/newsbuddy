-- Public estimates are applicable from catalog activation, not backdated invoice facts.
CREATE TABLE vendor_resource_price_rates (
    provider text NOT NULL,
    model text NOT NULL,
    unit text NOT NULL,
    effective_at timestamptz NOT NULL,
    rate_usd numeric(20, 12) NOT NULL CHECK (rate_usd >= 0 AND rate_usd <> 'NaN'::numeric),
    source_url text NOT NULL,
    pricing_version text NOT NULL,
    PRIMARY KEY (provider, model, unit, effective_at)
);

INSERT INTO vendor_resource_price_rates
    (provider, model, unit, effective_at, rate_usd, source_url, pricing_version)
SELECT provider, model, unit, transaction_timestamp(), rate_usd, source_url,
       'official-public-2026-09-29'
FROM (VALUES
    ('openai', 'gpt-transcribe', 'audio_minute', 0.0045,
     'https://developers.openai.com/api/docs/models/gpt-transcribe'),
    ('elevenlabs', 'eleven_flash_v2', 'character', 0.00004, 'https://elevenlabs.io/pricing/api'),
    ('elevenlabs', 'eleven_flash_v2_5', 'character', 0.00004, 'https://elevenlabs.io/pricing/api'),
    ('elevenlabs', 'eleven_turbo_v2', 'character', 0.00004, 'https://elevenlabs.io/pricing/api'),
    ('elevenlabs', 'eleven_turbo_v2_5', 'character', 0.00004, 'https://elevenlabs.io/pricing/api'),
    ('elevenlabs', 'eleven_v3_conversational', 'character', 0.00004, 'https://elevenlabs.io/pricing/api'),
    ('elevenlabs', 'eleven_multilingual_v2', 'character', 0.00008, 'https://elevenlabs.io/pricing/api'),
    ('elevenlabs', 'eleven_v3', 'character', 0.00008, 'https://elevenlabs.io/pricing/api'),
    ('runware', 'bytedance:seedream@5.0-lite', 'image', 0.035,
     'https://runware.ai/docs/models/bytedance-seedream-5-0-lite'),
    ('e2b', 'sandbox', 'vcpu_second', 0.000014, 'https://e2b.dev/pricing'),
    ('e2b', 'sandbox', 'gib_second', 0.0000045, 'https://e2b.dev/pricing'),
    ('exa', 'search', 'request_up_to_10_results', 0.007, 'https://exa.ai/pricing'),
    ('exa', 'search', 'additional_result', 0.001, 'https://exa.ai/pricing'),
    ('exa', 'search', 'ai_page_summary', 0.001, 'https://exa.ai/pricing'),
    ('exa', 'contents', 'page_content_type', 0.001, 'https://exa.ai/pricing'),
    ('x', 'posts.read', 'resource', 0.005, 'https://docs.x.com/x-api/getting-started/pricing'),
    ('x', 'users.read', 'resource', 0.010, 'https://docs.x.com/x-api/getting-started/pricing'),
    ('x', 'owned.read', 'resource', 0.001, 'https://docs.x.com/x-api/getting-started/pricing'),
    -- Firecrawl has plan-specific credit costs, not one universal per-request dollar price.
    ('firecrawl', 'hobby_annual', 'credit', 0.0032, 'https://www.firecrawl.dev/pricing')
) AS rates(provider, model, unit, rate_usd, source_url);

ALTER TABLE vendor_token_price_rates
    ADD COLUMN fast_multiplier numeric(8, 4) CHECK (fast_multiplier > 0),
    ADD COLUMN batch_multiplier numeric(8, 4) CHECK (batch_multiplier > 0),
    ADD COLUMN flex_multiplier numeric(8, 4) CHECK (flex_multiplier > 0),
    ADD COLUMN long_context_threshold integer,
    ADD COLUMN long_input_per_million_usd numeric(14, 6),
    ADD COLUMN long_cached_input_per_million_usd numeric(14, 6),
    ADD COLUMN long_cache_write_per_million_usd numeric(14, 6),
    ADD COLUMN long_output_per_million_usd numeric(14, 6),
    ADD CONSTRAINT complete_long_context_prices CHECK (
        (long_context_threshold IS NULL AND long_input_per_million_usd IS NULL
         AND long_cached_input_per_million_usd IS NULL AND long_cache_write_per_million_usd IS NULL
         AND long_output_per_million_usd IS NULL)
        OR (long_context_threshold > 0 AND long_input_per_million_usd >= 0
            AND long_cached_input_per_million_usd >= 0 AND long_cache_write_per_million_usd >= 0
            AND long_output_per_million_usd >= 0
            AND long_input_per_million_usd IS NOT NULL AND long_cached_input_per_million_usd IS NOT NULL
            AND long_cache_write_per_million_usd IS NOT NULL AND long_output_per_million_usd IS NOT NULL)
    );

INSERT INTO vendor_token_price_rates
    (provider, model, effective_at, input_per_million_usd,
     cached_input_per_million_usd, cache_write_per_million_usd,
     output_per_million_usd, long_context_threshold, long_input_per_million_usd,
     long_cached_input_per_million_usd, long_cache_write_per_million_usd,
     long_output_per_million_usd, source_url, pricing_version)
VALUES
    ('openai', 'gpt-6-luna', transaction_timestamp(), 0.10, 0.01, 0.125, 0.50,
     272000, 0.20, 0.02, 0.25, 0.75,
     'https://developers.openai.com/api/docs/models/gpt-6-luna', 'openai-standard-2026-09-29'),
    ('openai', 'gpt-6-sol', transaction_timestamp(), 2.00, 0.20, 2.50, 10.00,
     272000, 4.00, 0.40, 5.00, 15.00,
     'https://developers.openai.com/api/docs/models/gpt-6-sol', 'openai-standard-2026-09-29'),
    ('openai', 'gpt-6.1-sol', transaction_timestamp(), 2.00, 0.10, 2.50, 10.00,
     272000, 4.00, 0.20, 5.00, 15.00,
     'https://developers.openai.com/api/docs/models/gpt-6.1-sol', 'openai-standard-2026-09-29');

-- The public embedding-model API reports prompt USD/token, not USD/million.
INSERT INTO vendor_token_price_rates
    (provider, model, effective_at, input_per_million_usd,
     cached_input_per_million_usd, cache_write_per_million_usd,
     output_per_million_usd, source_url, pricing_version)
VALUES ('openrouter', 'qwen/qwen3-embedding-8b', transaction_timestamp(),
        0.01, 0.01, 0.01, 0,
        'https://openrouter.ai/api/v1/embeddings/models', 'openrouter-standard-2026-09-29');

-- Verified tier multipliers apply only to the newly sourced model prices.
UPDATE vendor_token_price_rates
SET fast_multiplier = 2, batch_multiplier = 0.5, flex_multiplier = 0.5
WHERE pricing_version = 'openai-standard-2026-09-29'
  AND provider = 'openai' AND model IN ('gpt-6-luna','gpt-6-sol','gpt-6.1-sol');

-- This classification is a known absence of an external provider charge, not an
-- invented historical infrastructure price. Firecrawl calls have their own rows.
CREATE FUNCTION classify_local_extractor_usage() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.provider = 'document_extractor'
       AND NEW.model IN ('crawl4ai', 'static_readability', 'policy-v1')
       AND NEW.cost_usd IS NULL THEN
        NEW.cost_usd := 0;
        NEW.cost_basis := 'non_billable';
        NEW.pricing_version := NULL;
        NEW.metadata := (COALESCE(NEW.metadata::jsonb, '{}'::jsonb)
            || jsonb_build_object('cost_scope', 'external_vendor_charge',
                                 'cost_reason', 'self_hosted_extractor_infrastructure_accounted_separately'))::json;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER vendor_local_extractor_cost_on_insert
BEFORE INSERT ON vendor_usage_records
FOR EACH ROW EXECUTE FUNCTION classify_local_extractor_usage();

UPDATE vendor_usage_records
SET cost_usd = 0, cost_basis = 'non_billable', pricing_version = NULL,
    metadata = (COALESCE(metadata::jsonb, '{}'::jsonb)
        || jsonb_build_object('cost_scope', 'external_vendor_charge',
                             'cost_reason', 'self_hosted_extractor_infrastructure_accounted_separately'))::json
WHERE provider = 'document_extractor'
  AND model IN ('crawl4ai', 'static_readability', 'policy-v1') AND cost_usd IS NULL;

-- Replace silent exclusions with precise unknown reasons and support observed agent calls.
CREATE OR REPLACE FUNCTION price_vendor_token_usage() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    normalized_model text;
    price vendor_token_price_rates%ROWTYPE;
    read_tokens bigint;
    write_tokens bigint;
    reason text;
    input_rate numeric;
    read_rate numeric;
    write_rate numeric;
    output_rate numeric;
    tier text;
    tier_multiplier numeric := 1;
BEGIN
    IF NEW.cost_usd IS NOT NULL THEN
        -- Never turn a caller's estimate into a provider-reported charge by provider name.
        IF NEW.cost_basis IS NULL THEN
            NEW.cost_basis := CASE NEW.provider
                WHEN 'firecrawl' THEN 'configured_rate'
                WHEN 'x' THEN 'configured_rate'
                ELSE 'unclassified'
            END;
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.provider NOT IN ('openai', 'openrouter')
       OR NEW.operation = 'transcription.openai' THEN
        RETURN NEW;
    END IF;
    NEW.pricing_version := NULL;
    NEW.cost_basis := NULL;
    read_tokens := COALESCE(NEW.cache_read_tokens, 0);
    write_tokens := COALESCE(NEW.cache_write_tokens, 0);
    IF NEW.request_count IS DISTINCT FROM 1 THEN
        reason := 'aggregate_or_unobserved_requests';
    ELSIF NEW.metadata::jsonb ->> 'usage_is_observed' = 'false' THEN
        reason := 'missing_provider_usage';
    ELSIF NEW.input_tokens IS NULL OR NEW.output_tokens IS NULL
          OR NEW.input_tokens <= 0 OR NEW.output_tokens < 0 THEN
        reason := 'missing_or_invalid_token_usage';
    ELSIF read_tokens < 0 OR write_tokens < 0 OR read_tokens + write_tokens > NEW.input_tokens THEN
        reason := 'invalid_cache_token_usage';
    END IF;
    IF NEW.provider = 'openai' THEN
        normalized_model := CASE WHEN NEW.model LIKE 'openai:%'
            THEN substring(NEW.model FROM 8) ELSE NEW.model END;
        IF reason IS NULL THEN
            IF NEW.metadata::jsonb ? 'model_request_sequence' THEN
                -- Per-response observations report the owned endpoint and returned tier.
                IF NEW.metadata::jsonb ->> 'endpoint' IS DISTINCT FROM 'https://api.openai.com/v1/responses'
                   OR COALESCE(NEW.metadata::jsonb ->> 'service_tier', '') NOT IN ('standard','default','fast','priority','batch','flex') THEN
                    reason := 'unverified_endpoint_or_service_tier';
                END IF;
            ELSIF NOT (NEW.operation LIKE 'news_processing.%'
                OR NEW.operation LIKE 'news_discussions.%'
                OR NEW.operation LIKE 'summarization.%'
                OR NEW.operation = 'briefing.compose_window.observed'
                OR NEW.operation = 'content_analyzer.analyze_url')
                OR COALESCE(NEW.metadata::jsonb ->> 'service_tier', 'standard') NOT IN ('standard','default') THEN
                reason := 'unverified_operation_or_service_tier';
            END IF;
        END IF;
    ELSE
        normalized_model := lower(CASE WHEN NEW.model LIKE 'openrouter:%'
            THEN substring(NEW.model FROM 12) ELSE NEW.model END);
    END IF;
    IF reason IS NULL THEN
        SELECT * INTO price FROM vendor_token_price_rates AS rate
        WHERE rate.provider = NEW.provider AND rate.model = normalized_model
          AND rate.effective_at <= NEW.created_at AT TIME ZONE 'UTC'
        ORDER BY rate.effective_at DESC LIMIT 1;
        IF NOT FOUND THEN
            reason := 'missing_applicable_model_rate';
        END IF;
    END IF;
    IF reason IS NULL THEN
        input_rate := price.input_per_million_usd;
        read_rate := price.cached_input_per_million_usd;
        write_rate := price.cache_write_per_million_usd;
        output_rate := price.output_per_million_usd;
        IF NEW.provider = 'openai' AND NEW.input_tokens > COALESCE(price.long_context_threshold, 272000) THEN
            IF price.long_context_threshold IS NULL THEN
                reason := 'missing_long_context_rate';
            ELSE
                input_rate := price.long_input_per_million_usd;
                read_rate := price.long_cached_input_per_million_usd;
                write_rate := price.long_cache_write_per_million_usd;
                output_rate := price.long_output_per_million_usd;
            END IF;
        END IF;
    END IF;
    IF reason IS NULL AND NEW.provider = 'openai' THEN
        tier := COALESCE(NEW.metadata::jsonb ->> 'service_tier', 'standard');
        tier_multiplier := CASE tier
            WHEN 'standard' THEN 1 WHEN 'default' THEN 1
            WHEN 'fast' THEN price.fast_multiplier WHEN 'priority' THEN price.fast_multiplier
            WHEN 'batch' THEN price.batch_multiplier WHEN 'flex' THEN price.flex_multiplier
            ELSE NULL END;
        IF tier_multiplier IS NULL THEN
            reason := 'missing_service_tier_rate';
        ELSE
            input_rate := input_rate * tier_multiplier;
            read_rate := read_rate * tier_multiplier;
            write_rate := write_rate * tier_multiplier;
            output_rate := output_rate * tier_multiplier;
        END IF;
    END IF;
    IF reason IS NOT NULL THEN
        NEW.metadata := (COALESCE(NEW.metadata::jsonb, '{}'::jsonb)
            || jsonb_build_object('cost_reason', reason))::json;
        RETURN NEW;
    END IF;
    NEW.cost_usd := ((NEW.input_tokens - read_tokens - write_tokens) * input_rate
        + read_tokens * read_rate + write_tokens * write_rate + NEW.output_tokens * output_rate)::double precision / 1000000.0;
    NEW.pricing_version := price.pricing_version;
    NEW.cost_basis := 'public_list_estimate';
    NEW.metadata := (COALESCE(NEW.metadata::jsonb, '{}'::jsonb) - 'cost_reason'
        || jsonb_build_object('pricing', jsonb_build_object(
            'basis', 'public_list_estimate', 'source_url', price.source_url,
            'version', price.pricing_version, 'effective_at', price.effective_at,
            'unit', 'million_tokens', 'service_tier', tier, 'tier_multiplier', tier_multiplier,
            'input_rate_usd', input_rate,
            'cache_read_rate_usd', read_rate, 'cache_write_rate_usd', write_rate,
            'output_rate_usd', output_rate)))::json;
    RETURN NEW;
END;
$$;
