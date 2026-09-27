# Vendor cost accounting

Newsly records observed units in `vendor_usage_records`. `cost_usd` is a
provider-reported amount, a configured-rate calculation, or a public list-price
estimate; `cost_basis` and `pricing_version` identify the basis for new records.
An unpriced record keeps `cost_usd` null. The usage report's `cost_usd` total
remains unknown whenever any record is unpriced, while its priced subtotal and
public-list-estimate portion remain visible. Historical null costs are not
backfilled from today's prices.

## Published variable rates checked 2026-09-27

| Meter | Published rate | Accounting rule |
| --- | --- | --- |
| [OpenAI GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna) | $0.10 input, $0.01 cached input, $0.125 cache write, $0.50 output per 1M tokens | Standard, short-context, single-request rows only; cache classes are mutually exclusive. |
| [OpenAI GPT-5.6 Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna) | $0.20 input, $0.02 cached input, $0.25 cache write, $1.20 output per 1M tokens | Same; Fast/priority, Batch/Flex, regional, and long-context requests need separate rates. |
| [OpenAI GPT-5.6 Terra](https://developers.openai.com/api/docs/models/gpt-5.6-terra) | $2 input, $0.20 cached input, $2.50 cache write, $12 output per 1M tokens | Same. |
| [OpenRouter Qwen3 Embedding 8B](https://openrouter.ai/qwen/qwen3-embedding-8b/pricing) | $0.01 per 1M input tokens | Price exact reported embedding tokens; credit-purchase fees and custom key arrangements are separate. |
| [OpenAI GPT Transcribe](https://developers.openai.com/api/docs/pricing) | Estimated $0.0045 per audio minute | Use measured audio duration. File bytes are not a duration meter. |
| [ElevenLabs API text to speech](https://elevenlabs.io/pricing/api) | Flash/Turbo $0.05 per 1,000 characters; other listed models may differ | Price exact submitted characters and model; contract or voice multipliers may differ. |
| [E2B sandbox](https://e2b.dev/pricing) | $0.000014 per vCPU-second + $0.0000045 per GiB-second; canonical 2 vCPU/2 GiB template is $0.000037/s ($0.1332/hour) | Estimate measured sandbox lifetime once after confirmed cleanup; running or missing-stop sessions stay unpriced. E2B's Pro base is $150/month plus usage, if that plan applies. |
| [Firecrawl](https://www.firecrawl.dev/pricing) | Basic scrape is one credit; USD per credit depends on plan and overage | Record credits and apply only the configured account rate. |
| [Exa](https://exa.ai/pricing) | Pay-as-you-go search begins at $7 per 1,000 requests for up to 10 results; content and summaries add usage | Record request and returned content units; leave unpriced until all billable dimensions are observed. |
| [Runware](https://runware.ai/docs/platform/pricing) | Model-dependent | Prefer the provider-reported response cost already captured by the image worker. |

X API charges depend on the account's current pay-per-use settings; use the
configured rates and deduplicated resource IDs rather than a public default.
The local document extractor, host/container runtime, PostgreSQL, object
storage, bandwidth/CDN, and monitoring have infrastructure or plan costs rather
than reliable per-call list prices. Reconcile those separately from provider
usage against actual invoices. Public rates and calculated costs are estimates,
not proof of the amount billed by an account-specific contract.

E2B lifetime estimates end at Newsly's confirmed kill acknowledgement. The
provider may bill a different rounded duration. An ambiguous create that races
account deletion can lose its user-owned cleanup intent; use the E2B console
and invoice to catch any such orphan. Application rows also cannot account for
provider requests that complete but crash before their usage receipt is saved.

## Maintenance

Add a dated `vendor_token_price_rates` row when a supported token price
changes; never rewrite the rate used by an existing event. Extend the pricing
allowlist only after verifying service tier, context band, cache semantics, and
measured units in that call path. Check the provider's invoice or usage portal
monthly for subscription fees, credits, rounding, custom rates, and usage lost
before an application-side observation could be persisted.
