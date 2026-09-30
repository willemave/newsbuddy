# Vendor cost accounting

Researched against official price sheets on 2026-09-29. These are published
usage estimates, not invoices. Account discounts, included credits, taxes and
fixed infrastructure/subscription fees require separate reconciliation.

## Catalog and applicability

`vendor_token_price_rates` prices measured tokens and
`vendor_resource_price_rates` prices measured non-token units. Both select the
latest matching rate at the usage timestamp. The September 29 catalog becomes
effective when its migration runs; it does not reprice historical requests.
Each quote snapshots its source, rate, version, quantity and effective date.
Unknown models, endpoint/tier mismatches, incomplete meters and malformed
provider costs remain unknown rather than zero.

| Provider/model | Published USD rate | Evidence and applicability |
| --- | --- | --- |
| OpenAI GPT-6 Luna | Per million: input 0.10, cache read 0.01, cache write 0.125, output 0.50 | [Model sheet](https://developers.openai.com/api/docs/models/gpt-6-luna); observed Responses endpoint and actual tier |
| OpenAI GPT-6 Sol | Per million: input 2.00, cache read 0.20, cache write 2.50, output 10.00 | [Model sheet](https://developers.openai.com/api/docs/models/gpt-6-sol) |
| OpenAI GPT-6.1 Sol | Per million: input 2.00, cache read 0.10, cache write 2.50, output 10.00 | [Model sheet](https://developers.openai.com/api/docs/models/gpt-6.1-sol) |
| OpenRouter Qwen3 Embedding 8B | 0.01/million input tokens, zero output | [Public embedding catalog](https://openrouter.ai/api/v1/embeddings/models), prompt `0.00000001` USD/token; no inferred cache discount |
| OpenAI GPT-Transcribe | 0.0045/audio minute | [Model sheet](https://developers.openai.com/api/docs/models/gpt-transcribe); measured media duration, standard direct endpoint |
| ElevenLabs Flash/Turbo and v3 Conversational | 0.04/1,000 characters | [API pricing](https://elevenlabs.io/pricing/api); exact supported model and direct endpoint |
| ElevenLabs multilingual v2 and v3 | 0.08/1,000 characters | Same API sheet; successful synthesis input characters |
| Runware Seedream 5.0 Lite | 0.035/image | [Model sheet](https://runware.ai/docs/models/bytedance-seedream-5-0-lite); prefer finite nonnegative response `cost`, otherwise exact single-image standard-endpoint quote |
| E2B sandbox | 0.000014/vCPU-second + 0.0000045/GiB-second | [Pricing](https://e2b.dev/pricing); measured provider start/confirmed stop and resource configuration |
| Exa standard Search | 0.007/request up to 10 results; 0.001/additional result; 0.001/AI page summary | [Pricing](https://exa.ai/pricing); catalog reference, response estimate preferred |
| Exa Contents | 0.001/page/content type | Same sheet; needs complete content-type and page meters |
| X reads | Posts 0.005/resource; users 0.010/resource; eligible owned reads 0.001/resource | [Pricing](https://docs.x.com/x-api/getting-started/pricing); catalog reference, account eligibility and UTC-day deduplication matter |
| Firecrawl Hobby annual | 0.0032/included credit (16/month equivalent divided by 5,000 credits) | [Pricing](https://www.firecrawl.dev/pricing); plan allocation reference, not a universal per-request or marginal charge |

OpenAI requests above 272,000 input tokens use twice the input/cache rates and
1.5 times the output rate for the whole request. Cache reads and writes are
exclusive input categories, not additive charges; see [prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching).
For these three newly sourced GPT-6 models, observed Fast/Priority costs twice
Standard and Batch/Flex costs half Standard, including qualifying long-context
rates. Unknown tiers and models without verified tier rates remain unpriced.
Regional endpoints are excluded because their 10% premium is not an observed
standard direct endpoint. Existing dated legacy
model rows remain available for matching observations.

OpenRouter response `usage.cost` is the [amount charged to the account](https://openrouter.ai/docs/cookbook/administration/usage-accounting), distinct from upstream inference cost; finite nonnegative values, including zero, take precedence over list estimates.

Exa `costDollars.total` is a [provider estimate](https://exa.ai/docs/reference/search),
not an invoice charge. Missing, null, negative and malformed values remain
unknown. Onboarding aggregates retain the known estimated subtotal and missing
attempt count; a partial subtotal must not become a complete task price.
Firecrawl and X preserve explicit configured account rates. The reference
catalog does not override those account assumptions or assert X owned-read
eligibility. Google image mixed text/image output usage has no safe single
output-token rate: without a reported charge, it stays unpriced.

## Recording and reports

Model responses carry run/response identity, model, feature, actual endpoint,
requested and observed service tier, input/output, cache and reasoning counts.
OpenAI wire usage is preserved through SDK normalization so cache-write counts
are retained; the ledger stores usage fields rather than raw response content.
Independent short accounting transactions record each completed response before
validation, tool execution or publication can fail. Response identity, or run
plus request sequence when absent, makes duplicate observation harmless.
Audio synthesis records each successful chunk before assembly. Image usage is
observed before download/decoding/storage. E2B charges use a separate durable
session lifecycle, independent of product leases.

Reports separate `provider_reported`, `provider_estimate`,
`public_list_estimate`, `configured_rate`, and `non_billable` bases. Any unpriced
row makes the total unknown; the known subtotal remains visible along with
estimate portions, unknown reasons and resource quantities grouped by unit.
Self-hosted readability/Crawl4AI/policy decisions are known zero only in the
external vendor ledger; their compute is not free infrastructure. A sandbox
creation confirmed never delivered is zero; ambiguous creation or stop time is
unknown. Fixed fees do not belong in per-call estimates.

A response observer cannot prove a charge when a request times out, the process
dies before receiving the response, or the provider omits usage. Such usage
cannot be reconstructed from a successful product output or retry count.
Reconcile the ledger with provider usage exports/invoices to identify these
missing observations, plan fees and account-specific discounts. Worker model accounting uses bounded persistence retries; API and chunk
observers emit structured errors if their independent write fails. Observers
cannot guarantee delivery across a process crash before database persistence.

## Updating prices

Add dated catalog rows with official source URLs and explicit units; do not
replace old snapshots or overwrite historical charges. Check endpoint, model,
tier, plan and quantity semantics before enabling a quote. Validate unknown
handling, cache/context boundaries and repeated observation using isolated
PostgreSQL and mocked providers. Production catalog activation and runtime
instrumentation require the normal authorized release workflow.
