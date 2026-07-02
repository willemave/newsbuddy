# Contracts Follow-Ups Implementation Plan

Date: 2026-07-02

Follow-up batch to `docs/initiatives/typed-contracts-2026-06/`. That initiative is complete; this one executes the deferred items it named, plus generator-pipeline ergonomics. The generated-operations layer (endpoint paths/methods) is explicitly **out of scope** here and gets its own initiative.

## Phases

Executed strictly in order — every phase regenerates the shared artifacts, so no parallel phase work.

- Phase A — Regeneration tooling (single-process regen, actionable drift messages)
- Phase B — Registry transitive closure (stop double-registering nested models)
- Phase C — Swift dates: generated `Date` fields via `ServerDate`
- Phase D — Deferred typing batches (discussion summary/stats, bullet points/quotes, raw-string enums, `SubmissionStatusItem`, AnyCodable helper consolidation)

## Implementation status

Phase A — Regeneration tooling

- [x] A1 Single-process `scripts/regenerate_public_contracts.py`; `.sh` becomes a thin wrapper
- [x] A2 Actionable drift message in `scripts/check_public_contracts.sh`

Phase B — Registry transitive closure

- [x] B1 `expand_contract_models()` fixpoint expansion in `scripts/contracts_codegen/`
- [x] B2 Emitters + `validate_contract_models` consume the expanded spec list
- [x] B3 Prune `CONTRACT_MODELS` to top-level request/response models
- [x] B4 Unit tests for expansion (target union, determinism, explicit-spec override)
- [x] B5 Regenerate artifacts; Go + iOS tests green (iOS build not runnable in this environment; see notes)

Phase C — Swift dates

- [x] C1 `ServerDate.format(_:)` canonical encoder + keep tolerant `parse`
- [x] C2 Emitter: datetime → `Date`, generated decode via `ServerDate.parse`, generated `encode(to:)`
- [x] C3 Migrate iOS call sites off per-use-site `ServerDate.parse` for generated fields
- [x] C4 Adversarial fixture: unparseable datetime fails decode
- [x] C5 Update policy doc §Datetimes

Phase D — Deferred typing batches

- [x] D1 Type `ContentDiscussionResponse.summary` / `.stats`; shrink allowlist
- [x] D2 Type `ContentDetailResponse.bullet_points` / `.quotes`
- [x] D3 iOS `ContentSummary`/`ContentDetail`: `contentType`/`status` raw strings → generated enums
- [x] D4 iOS `SubmissionStatusItem`: hand-written wire structs → generated models + domain extension
- [x] D5 Consolidate iOS AnyCodable dictionary-decoding helpers

---

## Phase A — Regeneration tooling

Today `scripts/regenerate_public_contracts.sh` runs four Python scripts, each importing the whole FastAPI app (~4 cold imports). `scripts/check_public_contracts.sh` fails CI with a bare diff and no instruction.

### A1 Single-process regeneration

New `scripts/regenerate_public_contracts.py`:

- Imports the app once; writes all four artifacts using the existing library functions (`scripts/export_openapi_schema.py`, `scripts/export_agent_openapi_schema.py`, `scripts/generate_ios_contracts.py`, `scripts/generate_go_contracts.py` expose importable build functions today — refactor them minimally if any logic lives only under `if __name__ == "__main__":`).
- Accepts `--check` mode: write to a temp dir and diff against checked-in artifacts, exiting non-zero on drift (so the shell scripts can delegate to it).
- Existing entrypoint scripts keep working unchanged (CI and docs reference them).
- Rewrite `regenerate_public_contracts.sh` as a thin `uv run python scripts/regenerate_public_contracts.py` wrapper.
- `check_public_contracts.sh` keeps its `--python-only`/`--go-only` interface but delegates the Python-side artifact generation to the single-process script. Careful: the Go section also rebuilds/syncs Go fixtures — leave that logic in the shell.

### A2 Actionable drift message

On drift, `check_public_contracts.sh` prints, after the diff:

```
Contract artifacts are stale. Run scripts/regenerate_public_contracts.sh and commit the resulting diff.
```

### Validation (Phase A)

- `scripts/regenerate_public_contracts.sh` produces byte-identical artifacts vs. current checked-in files (no model changes in this phase → zero diff expected).
- `scripts/check_public_contracts.sh` passes; deliberately touch a model field locally, confirm the new message appears, revert.
- `ruff check` on touched Python files. Time old vs. new regen path; report the numbers.

---

## Phase B — Registry transitive closure

`app/models/contracts_registry.py` requires an import + `ModelSpec` for all ~130 models even when a model is only reachable as a nested field of another registered model. The resolver (`ContractTypeResolver.validate_model_reference`) already errors on unregistered nested models — so registration is pure double-entry.

### B1 Expansion function

New `expand_contract_models(specs: list[ModelSpec], enum_specs: list[EnumSpec]) -> list[ModelSpec]` in `scripts/contracts_codegen/introspect.py` (or a sibling `expand.py`):

- Walk each explicit spec's fields with the existing annotation resolution (reuse `_resolve_annotation` machinery via an unchecked resolver; do NOT duplicate type-walking logic).
- For every nested `BaseModel` not already in the spec list, synthesize `ModelSpec(model, targets=<union of targets of every spec that references it>)`, iterating to a fixpoint (nested models can nest further; targets propagate transitively).
- Explicit specs always win (a hand-written spec for a nested model keeps its targets/name overrides; expansion only unions **additional** targets into synthesized specs, never mutates explicit ones — if an explicit spec's targets are narrower than its referencers require, keep the existing hard validation error rather than silently widening).
- Deterministic output order: explicit specs first in registry order, then synthesized specs in first-discovery order (registry order, then field order). Emitters must produce identical output on every run.

### B2 Wire into emitters and validation

`build_swift_models`, `build_go_contracts`, and `validate_contract_models` call the expansion before resolving/emitting. The resolver keeps `require_registry=True` semantics but now runs against the expanded list, so "nested model not registered" errors disappear for reachable models. Enum references are unchanged — enums stay explicitly registered (they carry open/closed policy that cannot be inferred).

### B3 Prune the registry

Remove `ModelSpec` entries (and their imports) for models that are only reachable as nested fields of other registered models — e.g. `PaginationMetadata`, `DetectedFeed`, `TweetSuggestion`, `DiscussionCommentResponse`/`DiscussionItemResponse`/`DiscussionGroupResponse`/`DiscussionLinkResponse`, `OnboardingSuggestion`, `LearningDeckTimelineEntry`, `ChatMessageDto`, etc. Determine the exact prune set mechanically: a spec is prunable iff removing it leaves generated output identical (same models, same names, same targets). Keep any spec whose targets are *wider* than what expansion would infer (e.g. a nested model also fetched standalone by the CLI), and document each kept-wide spec with a one-line comment.

### B4 Tests

New `tests/contracts/test_registry_expansion.py`:

- Nested model synthesized with union of parents' targets (build toy models inline).
- Fixpoint: grandchild nesting resolves; targets propagate transitively.
- Explicit spec overrides synthesized one.
- Determinism: two expansion runs produce identical ordering.

### Validation (Phase B)

- Regenerate. Expected diff: **reordering only** in `APIModels.generated.swift` / `contracts_gen.go` (synthesized specs may move relative to the old hand-ordering). No model additions/removals, no field changes — verify by sorting struct names from old and new artifacts and diffing the sorted sets (must be empty).
- `pytest tests/contracts/` green; `cd cli && go build ./... && go test ./...` green.
- iOS: `xcodebuild build` for the app target (struct reordering cannot break compilation, but confirm).

---

## Phase C — Swift dates: `Date` in generated models

Policy (`docs/initiatives/typed-contracts-2026-06/20-contract-policy.md` §Datetimes) deliberately shipped `UTCDateTime → String` in Swift, with per-use-site `ServerDate.parse`. The named unlock is a generated decoder path through the canonical parser. There are 54 `UTCDateTime` fields across `app/models/api/`; iOS has 9 `ServerDate.parse` call sites across 8 files.

Design choice: parsing lives in **generated `init(from:)`/`encode(to:)`**, not in `JSONDecoder` date strategies — the app has ~15 scattered `JSONDecoder()` instantiations plus test decoders, and a strategy-based approach silently breaks on any decoder that misses the config. Self-contained generated code works with every decoder.

### C1 `ServerDate.format`

In `client/newsly/newsly/Models/ServerDate.swift` add:

```swift
static func format(_ date: Date) -> String
```

ISO8601 UTC with fractional seconds and `Z` suffix (matches backend `serialize_utc_datetime`, which emits `isoformat()` + `Z`; backend accepts fractional on parse). Keep `parse` exactly as-is (4-format tolerance chain).

### C2 Emitter changes (`scripts/contracts_codegen/swift_emitter.py`)

- `_SwiftTypeContext._swift_non_optional_type`: `datetime` → `Date`.
- Decoder generation per datetime field:
  - required: decode `String`, then `guard let v = ServerDate.parse(raw) else { throw DecodingError.dataCorruptedError(forKey:in:debugDescription:) }`.
  - optional: `decodeIfPresent(String...)`; if present, same guard-throw on unparseable (strict per policy — present-but-garbage is a backend bug, not a nil).
  - lenient: `decodeIfPresent` + parse, falling back to the default expression on missing **or** unparseable.
  - Nested containers: `list[UTCDateTime]` / `dict[str, UTCDateTime]` — check whether any registered model uses these (likely none); if none, raise a clear "unsupported" error in the emitter rather than half-supporting them.
- Encoding: generate an explicit `encode(to:)` for **every** struct (uniform; today encode is synthesized). Datetime fields encode `ServerDate.format(value)`; everything else encodes directly. This is required — synthesized encode would emit `Date` as a timestamp double.
- Memberwise-init defaults: datetime fields have no literal defaults in the registry today; if one appears, fail loudly.

### C3 iOS call-site migration

Regenerate, then chase the compiler:

- Domain models fed by generated wire models (`ChatMessage`, `ChatSessionSummary`, `ScraperConfig`, `SubmissionStatusItem` consumers, `ContentTimestampText`, `LearningDeck`, `ContentSummary`/`ContentDetail` date fields) — store `Date` directly and delete their `ServerDate.parse` calls and String-typed date properties.
- Keep `ServerDate.parse` only where dates come out of untyped metadata dicts (`ArticleMetadata`, `PodcastMetadata`, `SourceMetadata`) — those are not generated fields.
- Search: `grep -rn "ServerDate.parse" client/newsly` must end with only the metadata-dict call sites.

### C4 Fixtures

- Existing golden fixtures decode unchanged (they contain canonical strings; generated decode now parses them).
- Add one adversarial fixture case: a datetime field with value `"not-a-date"` → decode throws (iOS test target), and missing-key on a lenient datetime → default.

### C5 Policy update

Rewrite `20-contract-policy.md` §Datetimes: generated Swift now maps `UTCDateTime` to `Date` through `ServerDate.parse`/`ServerDate.format` in generated Codable paths; per-use-site parsing is reserved for untyped metadata payloads.

### Validation (Phase C)

- Regenerate; `pytest tests/contracts/`; `cd cli && go test ./...` (Go untouched but artifacts regenerate together).
- iOS: full unit test suite via `xcodebuild test` (scheme `newsly`, iPhone simulator). Fixture decode tests + new adversarial cases green.
- `scripts/check_public_contracts.sh` green.

---

## Phase D — Deferred typing batches

Four independent sub-batches; land in order D1→D5 (D3 and D4 both touch content presentation files; D4 depends on C for `Date` fields).

### D1 Type `ContentDiscussionResponse.summary` / `.stats`

Currently `dict[str, Any]` on the `should_shrink` allowlist (`app/models/api/content_discussions.py:73-75`).

1. Find the producers (services building the discussion payload — trace writes to `summary` and `stats`) and enumerate actual keys/value types from code + existing fixtures.
2. Define `DiscussionSummaryResponse` / `DiscussionStatsResponse` in `content_discussions.py` with full types; use `lenient_field` for genuinely variable members. If a member is truly free-form, keep it `dict[str, Any]` with a **new, named** allowlist entry rather than faking a type.
3. Remove the two entries from `should_shrink` in `contracts_registry.py`.
4. Regenerate; migrate iOS `ContentDiscussion.swift` off AnyCodable for these fields; update/extend fixtures.

### D2 Type `ContentDetailResponse.bullet_points` / `.quotes`

Currently `list[dict[str, str]]` lenient fields (`app/models/api/content.py:389-393`). Fixtures show shapes `{"text": ..., "context"?: ...}`. Define `SummaryBulletPoint` / `SummaryQuote` models (`text: str`, plus the context/attribution keys the producers actually emit — verify against the summarization service before writing the model). Keep `lenient_field` on the list fields. Regenerate; update iOS `StructuredSummary`-adjacent decode paths and fixtures. Note: this does **not** touch `structured_summary` itself — recorded decision 1 stands.

### D3 iOS raw-string enum projections

`ContentSummary.swift` / `ContentDetail.swift` store `contentType: String` / `status: String` and re-derive enums at use sites (`APIContentType(rawValue: contentType)` computed props).

- Change stored properties to `APIContentType` / `APIContentStatus`; construct straight from the wire model's already-typed fields (delete the `.rawValue` round-trip at `ContentSummary.swift:187,192`).
- Chase compiler through use sites: string comparisons become enum comparisons with explicit `.unknown` arms (unknown renders neutral, never falls through business logic — policy §Enums).
- Delete the now-redundant computed enum projections; keep a `rawValue`-based display fallback only where UI intentionally prints the raw string.
- Update the policy doc's Known Follow-Ups section (remove the first bullet).

### D4 iOS `SubmissionStatusItem` migration

`SubmissionStatusItem.swift` hand-writes three wire structs (`SubmissionFeedInitialDownload`, `SubmissionFeedSubscription`, `SubmissionStatusListResponse`) duplicating generated `APISubmissionFeedInitialDownloadResponse` / `APISubmissionFeedSubscriptionResponse` / `APISubmissionStatusListResponse`, plus domain logic mixed in.

- Replace wire decoding with the generated structs; keep `SubmissionStatusItem` as a domain type constructed from `APISubmissionStatusResponse` (mirroring the `ContentDetail` wire/domain split), with all the display logic (`statusLabel`, `effectiveOutcome`, etc.) moved onto it or an extension.
- `status`/`outcome`/`submissionKind` comparisons should use the generated enums (`APIContentStatus`, `SubmissionOutcome`, `SubmissionKind`) instead of lowercased strings where the wire model provides them.
- Dates: `createdAt`/`processedAt` become `Date` (Phase C) — drop `parseDate`.
- Update call sites (`SubmissionDetailView`, `SubmissionStatusRow`, `ChatSessionViewModel`, list VMs) and tests (`test_list_submission_statuses` backend tests are unaffected; iOS tests under `newslyTests` referencing these types are).
- Remove the four `SubmissionStatusItem.swift:*` entries from `tests/contracts/ios_handrolled_wire_models_allowlist.json` (shrink-only manifest — this is the designed direction).

**Caution:** `SubmissionStatusItem.swift`, `SubmissionStatusRow.swift`, `SubmissionDetailView.swift` have uncommitted working-tree changes from in-flight work. Read the current state carefully and preserve its behavior; do not revert anything.

### D5 Consolidate AnyCodable decode helpers

`ContentDetail.swift`, `ContentSummary.swift`, `ContentDiscussion.swift`, `LongformArtifact.swift`, `NewsMetadata.swift`, `StructuredSummary.swift`, `LearningDeck.swift` each carry local `[String: AnyCodable]` → typed-struct re-serialization helpers. Extract one utility (e.g. `AnyCodable+Decoding.swift`: `func decodePayload<T: Decodable>(_ dict: [String: AnyCodable], as: T.Type) -> T?` plus the shared key-access helpers) and migrate all files to it. Pure refactor — no behavior change; existing decode tests are the safety net.

### Validation (Phase D, per sub-batch)

- Backend batches (D1, D2): `ruff check` touched files; `pytest tests/contracts/ tests/routers/` plus the owning service tests; regenerate + `check_public_contracts.sh`; fixtures updated in the same change.
- iOS batches (D3–D5): `xcodebuild test` unit suite; manifest ratchet test green; no new `try?` in decode paths without a policy comment.

---

## Sequencing

```
Phase A ──► Phase B ──► Phase C ──► D1 ──► D2 ──► D3 ──► D4 ──► D5
```

Strictly serial: every phase regenerates shared artifacts. Each phase leaves the tree green (ruff + pytest + go test + iOS tests where touched) before the next starts.

## Risks and mitigations

- **Uncommitted working-tree changes** across submission/learning-deck/chat files predate this initiative. Phases must read current file state, never revert WIP, and keep unrelated hunks untouched. Nothing is committed by this initiative; the user reviews the combined diff.
- **Generated-file reordering (Phase B)** produces a large one-time diff. Mitigate by verifying the sorted-struct-name set is unchanged, and by landing B in isolation.
- **Strict datetime decode (Phase C)** turns unparseable dates into decode failures. The backend serializes one shape (`serialize_utc_datetime`); fixtures prove it. This is the same strictness posture the June initiative took for booleans.
- **D1 shape discovery**: if `summary`/`stats` producers emit genuinely heterogeneous shapes, prefer keeping a named allowlist entry over a wrong type. The allowlist shrink is a goal, not a mandate.
