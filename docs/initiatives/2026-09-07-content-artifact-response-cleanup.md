# Content artifact response cleanup

Status: implemented and locally validated after Claude Fable review. No commit, push or deployment. Fable reviewed the plan via Claude Code; the implementation follows the narrowed required-key-preserving design below.

## Outcome and boundary

Article/podcast detail responses with a typed artifact expose that artifact once, under `longform_artifact`. Preserve every existing top-level response key: duplicate optional fields become null and duplicate arrays become empty. List responses retain compact previews. Evals grade and show the canonical artifact once while retaining unabridged HTTP evidence.

Keep the production artifact schema, source-hint/template selection, stored summaries, processing behavior, and genuine legacy summary/news formats unchanged. No endpoint, representation flag, client-version gate, data migration, schema rewrite, generated-client change, or client release is planned. No commit, push or deployment is authorized by this task.

## Verified evidence

- Deployed revision e0e8d39a's presenter exposes the same stored envelope as `metadata.summary`, `structured_summary`, and `longform_artifact`; the saved API fixtures confirm equality. `metadata.feed_preview` and `metadata.selection_trace` repeat envelope data too.
- `newsly-api/src/wire_presence.rs::require_schema_properties` makes all public response properties required. `APIModels.generated.swift` strictly decodes even optional values with `decode(T?.self, forKey:)`: nullable means a present key can contain null, not that it can disappear. The three flattened arrays are leniently decoded, but this cleanup will keep their keys and emit empty arrays.
- iOS `ContentDetail` prefers the canonical artifact. Both full and medium `ShareMarkdownBuilder` paths already prefer `buildLongformArtifactSummaryMarkdown`; the flattened summary helpers are fallbacks. The original draft incorrectly treated those fallback helpers as the primary path. Verify this precedence with fixtures; no sharing rewrite is currently indicated.
- `present_content_summary` calls the detail presenter, then derives classification from `structured_summary`, primary topic from `topics`, takeaway from metadata, and list previews from flattened fields. A detail-only pruning change would silently break lists. For artifacts, the current primary topic is the artifact type string; preserve that observable behavior.
- Artifact helper detection is broader than summary-kind gating. The DB's onboarding, X-sync and submission metadata gates use `summary_kind == longform_artifact` plus an object at `summary.artifact`. Use one consistent predicate in the presenter; keep malformed/mismatched and legacy-news behavior explicit and covered before pruning.
- The eval runner waits for `structured_summary` and projects repeated fields for judging. It must move first. `last-response.json` already retains the complete API response.
- CLI content-get passes JSON through; CLI library sync uses separate manifest/file endpoints. Admin eval and library fallback paths read stored metadata, which this work does not modify. The Share Extension does not decode content detail. No consumer migration is currently indicated there.
- The main checkout has unrelated OpenAPI/codegen/generated-Swift changes. This plan deliberately expects zero public-schema drift and requires no regeneration. Preserve those concurrent changes.

## Exact target wire behavior

For a detail response containing a typed artifact:

| Field | Target |
|---|---|
| `longform_artifact` | Entire existing envelope, unchanged |
| `structured_summary` | `null` |
| `metadata.summary` | Removed from the metadata map |
| `metadata.feed_preview` | Removed from the metadata map |
| `metadata.selection_trace` | Removed from the metadata map |
| Top-level `feed_preview`, `artifact_type`, `preview_bullets`, `reason_to_read` | Keys retained, values `null` |
| Top-level `bullet_points`, `quotes`, `topics` | Keys retained, values `[]` |
| Everything else | Unchanged, including `summary`, `short_summary`, `news_summary`, kind/version, identity, lifecycle, body/source/image/discussion/read fields and remaining metadata |

This keeps the harmless one-line aliases out of scope. The complete artifact includes its feed preview and selection trace once. List endpoints retain their existing compact preview shape. Legacy detail kinds and news retain their existing serialized shape and values. Do not globally strip metadata or globally null legacy fields.

## Implementation sequence

### 1. Canonical eval consumption and presenter baselines

Update summary-stage readiness to require `longform_artifact` for artifact-kind cases. Judge/report title plus that envelope once; keep full raw HTTP evidence in a separate artifact. Missing canonical output is an explicit contract/execution failure. Do not silently fall back to an obsolete alias for these cases. News Briefing collection stays separate and unchanged.

Version the judge projection and report format. Prove on the four saved responses that the canonical envelope equals the prior alias; do not describe representation-only score differences as processing improvements. Regrade those saved answers through the locally authenticated judge only if verifying the new projection warrants it; candidate calls are unnecessary for replay.

Before modifying presenters, add deterministic baseline fixtures/tests for detail and list projections. Use compact sanitized structural fixtures covering the four artifact envelopes, legacy editorial/bulleted/structured/interleaved versions, legacy news, and missing/malformed summary cases. Keep full production source bodies/eval output under ignored test-results. These tests are new: the current presenter tests do not establish before/after detail/list equivalence.

This slice runs against today's unchanged server.

### 2. Decouple list projection

Make the summary presenter derive classification, topic, key takeaway and preview directly from normalized stored metadata and existing free functions, rather than reading them from the public detail DTO. Reuse current normalization and source-resolution behavior. Avoid a new intermediate DTO or generalized rendering/serialization framework.

Preserve byte-equivalent list responses for the fixed fixtures, including values used by all list routes and mixed search. Source records, dates, ordering and read state remain identical. This is a behavior-preserving slice and lands before detail pruning.

### 3. Trim artifact detail projection

Use one artifact-kind predicate consistent with the existing DB gates and the legacy-news short-circuit. Apply the exact table above at the public presentation boundary. Establish that a canonical envelope is available before removing its copies; malformed/mismatched cases must not silently become completed responses with no usable summary. Preserve their baseline behavior rather than broadening this into a repair or validation migration.

Stop creating artifact-only duplicate projections when no caller needs them. Keep the functions that still serve legitimate legacy formats or list projection. Do not mutate the stored summary or worker/finalization code.

Keep `ContentDetailResponse` fields and required-key behavior intact. Contract drift must be zero; no schema/client regeneration is expected. A required schema change is a signal to revisit implementation scope, not permission to absorb unrelated codegen edits.

## Minimal validation

- **Rust presenter baselines:** canonical envelope byte/value equality, duplicate metadata entries absent, null/empty mirrors, every top-level key preserved, legacy detail output and all list fields unchanged. Explicit normal/missing/malformed/artifact-kind-mismatch coverage.
- **Native compatibility:** decode a canonical-only artifact response through the generated API model and handwritten `ContentDetail`; verify detail-section and medium/full Markdown share output matches the prior duplicated response. Keep legacy fixtures. Current artifact-first code should pass without changes.
- **End-to-end fixture:** add an artifact-kind item to the existing local native/API fixture set and exercise one existing long-form reader path. This covers the gap left by current long-structured-only seeds; do not add a separate UI harness or screenshot judge.
- **Evals:** test new canonical readiness/reporting and raw-response retention. Reuse the same real-source SQL fixtures and mocks; run one local article and podcast summary-stage case against the cleaned API. Judge only returned content, never tool selection. Keep news evals separate and do not add a CI eval gate.
- **Checks:** Ruff/MyPy/focused Python tests; cargo fmt, warning-denied API Clippy and focused Rust tests; public contract drift check; focused native decode/share tests. Broaden builds only when affected code or failures require it.
- **Payload measurement:** measure UTF-8 compact JSON bytes before/after for the same inputs and unchanged artifact; report actual uncompressed payload change, not inferred compressed bandwidth savings.

## Measured reduction

Replaying the four saved response records through the implemented Rust presenter gives:

| Fixture | Before bytes | After bytes | Reduction |
|---|---:|---:|---:|
| Import AI | 41,599 | 12,884 | 69.0% |
| Latent Space AEO | 37,994 | 11,996 | 68.4% |
| Constitutional AI podcast | 41,227 | 12,560 | 69.5% |
| Stripe podcast | 39,534 | 12,081 | 69.4% |

These are actual presenter outputs measured as uncompressed UTF-8 compact JSON bytes, not compressed network bandwidth. Replay asserted the complete output equals the old response with only the specified carriers cleared, including an identical artifact and top-level key set. Results are in `test-results/artifact-cleanup-evals-20260907/payload-measurements.json`.

## Implementation validation

- Rust: 15 pre-change detail/list fixtures cover four artifact types, legacy summary/news kinds, both interleaved versions and missing/malformed/mismatched summaries; all list fields and unaffected detail fields remain identical. Four production response replays matched the precise cleanup table.
- Native: 41 focused tests passed, including generated API decoding and artifact-first handwritten decoding/section/share output. The existing local detail fixture now uses an artifact; the Knowledge fixture remains legacy. A fresh local API plus Simulator reader displayed takeaway, key points, quote/attribution and evidence. Runtime snapshot is saved beside the eval report.
- Python: 8 focused tests passed; 2 optional full-bootstrap tests were skipped. Ruff and MyPy passed. Actual local API/worker evals exercised full-source Latent Space AEO article and Stripe podcast; Luna candidates and Sol CLI judge both passed all five criteria. No candidate/template/prompt changes in this cleanup, so this is not evidence of a model-quality improvement.
- Warning-denied API Clippy, focused Rust tests, touched-file rustfmt, diff checks and public contract drift checks passed. No generated files changed.
- New raw HTTP responses and canonical reports live under `test-results/artifact-cleanup-evals-20260907/`. No live provider rerun of the full suite and no deployment performed.

## Review disposition

Fable's verdict: approve the direction after narrowing scope; keep keys and null/empty duplicates, migrate eval readiness first, split lists next, then clean the presenter. Full review is saved at `test-results/artifact-cleanup-plan-review/fable-review.md`; raw CLI response and request are adjacent.

Verified and adopted: required-nullable decode behavior, artifact-first sharing, list coupling, missing presenter baselines, metadata preview/trace duplicates, generic CLI behavior, and no need for a client-retirement mechanism. Removed the draft's omission preference, field deletion, speculative compatibility bridge, broad native migration and codegen work. Submission law C14 is not used as a content-detail compatibility requirement.

Independently recomputed payload sizes using the exact target fields; report the reproducible numbers above rather than Fable's differently scoped byte estimate. Do not infer deployed compression behavior from repository configuration alone. Preserve existing behavior for malformed records; no content repair is included.

## Documentation and handoff

On implementation, add one content-law invariant: artifact-kind detail exposes one complete artifact and null/empty duplicate carriers. Document the detail/list boundary in architecture and record validation in docs/log. Keep installed legacy shapes supported by their current contract, without new request modes. Present the tested diff and measurements before any separately authorized release. Rollback is a presenter redeploy because persisted content never changes.
