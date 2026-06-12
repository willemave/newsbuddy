# Typed Contracts Implementation Plan

Date: 2026-06-09

## Goal

Make the API contract between the FastAPI backend, the SwiftUI iOS app, and the Go CLI drift-proof by generating all client wire models from the Pydantic source of truth, with CI gates that turn any divergence into a red PR. After this initiative, a backend field rename, enum addition, or optionality change is either a compile error or a visible generated-file diff on every client — never a silent decode default.

This builds on the contract groundwork already landed (CI gates via `check_public_contracts.sh --python-only/--go-only`, deletion of the dead swift-openapi client, the `UTCDateTime` serializer, `ServerDate.parse` on iOS).

## Recorded decisions

These were decided up front and are not open questions for implementers:

1. **Summary payloads stay as they are on the wire.** `structured_summary`, `longform_artifact`, and `feed_preview` remain free-form objects in the spec and `[String: AnyCodable]`-shaped on iOS, with the existing hand-rolled `ContentDetailDecodedPayloads` parsing untouched. We are not forcing a discriminated union into the contract. These fields live on the untyped-surface allowlist permanently (revisit only if the summary system itself is reworked).
2. **Homegrown generator (Option B), not swift-openapi-generator.** We extend the existing `scripts/generate_ios_contracts.py` pipeline from enums to full wire structs, generated directly from the Pydantic models (richer than OpenAPI introspection: we see discriminators, defaults, docstrings, and our own policy annotations). The checked-in-artifact + CI-diff pattern already works here; we extend it.
3. **Go gets clean generated models too, replacing ogen.** The ogen-generated client (`oas_*_gen.go`, ~7 files including a patched datetime decoder) is replaced by the same homegrown generator emitting clean Go structs + enums, consumed by a thin hand-written client in the style `cli/internal/runtime` already uses (`doJSON` with retry/polling). The CLI calls 18 operations; hand-writing 18 client methods against generated types is less total machinery than ogen.
4. **The OpenAPI export stays** (`docs/library/reference/openapi.json`) as the documentation artifact and freshness check, but no client code generates from it anymore.

## Target shape

```text
app/models/
  contracts.py                  # ALL cross-client enums (single source of truth)
  contracts_registry.py         # NEW: which models/enums ship to which clients, open/closed enum policy
  api/                          # Pydantic response/request models (the contract source)

scripts/
  contracts_codegen/            # NEW package (replaces single-file generate_ios_contracts.py internals)
    __init__.py
    introspect.py               # Pydantic model -> neutral IR (fields, types, optionality, policy)
    policy.py                   # lenient_field() marker, escape-hatch rules
    swift_emitter.py            # IR -> Swift enums + Codable structs
    go_emitter.py               # IR -> Go types + enum constants
    fixture_sync.py             # copies canonical fixtures to iOS/Go test trees
  generate_ios_contracts.py     # thin entrypoint (same name/path, now emits enums + models)
  generate_go_contracts.py      # NEW thin entrypoint
  check_public_contracts.sh     # extended to diff the new artifacts

client/newsly/newsly/Models/
  Generated/
    APIContracts.generated.swift   # enums (existing file, extended)
    APIModels.generated.swift      # NEW: wire structs + request models
  <Name>+Domain.swift              # hand-written extensions: computed props, fallbacks, display logic

cli/internal/api/
  contracts_gen.go              # NEW: generated types + enums (replaces oas_*_gen.go)
  client.go                     # NEW: thin hand-written client, one method per operation

tests/contracts/
  fixtures/*.json               # NEW: canonical golden fixtures, written by backend tests
client/newsly/newslyTests/Fixtures/contracts/*.json   # synced copies (CI-checked)
cli/internal/api/testdata/contracts/*.json            # synced copies (CI-checked)
```

## Implementation status

Phase 0 — Spec truth + ratchet

- [x] 0.1 Typed response models for the 8 dict-returning endpoints
- [x] 0.2 Consolidate cross-client enums into `contracts.py`
- [x] 0.3 Ratchet test: every route has a real `response_model`; untyped-field allowlist
- [x] 0.4 Ratchet test: every datetime field in `app/models/api/` is `UTCDateTime`

Phase 1 — Generator core

- [x] 1.1 `contracts_codegen` package: introspection IR + registry
- [x] 1.2 Policy annotations (`lenient_field`) and escape-hatch rules
- [x] 1.3 Swift emitter: enums with open/closed support (regenerate existing 7 + add missing enums)
- [x] 1.4 Migrate iOS call sites for open-enum shape changes

Phase 2 — Swift wire structs

- [x] 2.1 Swift struct emission + `APIModels.generated.swift` wired into the check script
- [x] 2.2 Batch 1: simple responses (counts, mark-read/knowledge, jobs, narration)
- [x] 2.3 Batch 2: `ContentSummary` + list responses
- [x] 2.4 Batch 3: chat models
- [x] 2.5 Batch 4: `ContentDetail` (wire/domain split; summary fields stay as-is)
- [x] 2.6 Batch 5: request models — typed `SubmitContentRequest` for ShareExtension; delete `requestRaw`
- [x] 2.7 Coverage ratchet: shrink-only manifest of remaining hand-rolled models

Phase 3 — Go clean models

- [x] 3.1 Go emitter + `contracts_gen.go` from the same registry
- [x] 3.2 Thin hand-written client; port `cmd/` call sites; fold `runtime` hand-rolled structs
- [x] 3.3 Delete ogen generation + `decodeFlexibleDateTime` patch; update `--go-only` check
- [x] 3.4 Decide fate of `cli/openapi/agent-openapi.json` (keep export only if consumed)

Phase 4 — Cross-language golden fixtures

- [x] 4.1 Backend fixture tests write checked-in canonical JSON (fail on diff)
- [x] 4.2 iOS tests decode the synced fixtures (replace inline string literals)
- [x] 4.3 Go tests decode the synced fixtures
- [x] 4.4 Adversarial fixtures: unknown enum values, absent-vs-null optionals

Phase 5 — Policy

- [x] 5.1 Write `20-contract-policy.md` (tolerance rules, enum table, evolution rules, endpoint definition-of-done)

---

## Phase 0 — Spec truth + ratchet

The generator can only be as good as the Pydantic models it reads. This phase makes the contract surface honest and adds tests so it stays honest. (Per recorded decision 1, the summary payload fields are explicitly exempt.)

### 0.1 Typed response models for the 8 dict-returning endpoints

Current offenders (verified):

| Endpoint | File | Today |
| --- | --- | --- |
| `mark_content_read` | `app/routers/api/read_status.py:36` | `-> dict` |
| `mark_content_unread` | `app/routers/api/read_status.py:61` | `-> dict` |
| `bulk_mark_read` | `app/routers/api/read_status.py:86` | `-> dict` |
| `save_to_knowledge` | `app/routers/api/knowledge.py:33` | `-> dict` |
| `remove_from_knowledge` | `app/routers/api/knowledge.py:56` | `-> dict` |
| `delete_llm_integration` | `app/routers/api/integrations.py:145` | `-> dict[str, str]` |
| `complete_onboarding` | `app/routers/api/agent.py:118` | `-> dict[str, object]` |
| `mark_news_items_read` | `app/routers/api/news.py:68` | `-> dict[str, Any]` |

Define small response models (e.g. `MarkReadResponse`, `BulkMarkReadResponse{marked_count, failed_ids}`, `SaveToKnowledgeResponse{is_saved_to_knowledge}`) and set `response_model`. **Behavioral fix bundled here:** `mark_read` currently returns `200 {"status": "error"}` on repository failure and the iOS `requestVoid` path counts that as success — replace soft errors with `raise HTTPException`, and on iOS verify the optimistic mark-read state rolls back on a thrown error.

These endpoints are also the first generated-struct consumers (Phase 2 batch 1) and the `[String: Any]` fishing for `is_saved_to_knowledge` in three view models is what `requestRaw` deletion (2.6) depends on.

### 0.2 Consolidate cross-client enums into `contracts.py`

Move into `app/models/contracts.py` (they are API-surface enums defined elsewhere today):

- `ChatMessageRole`, `ChatMessageDisplayType` (from `app/models/api/chat.py:17-30`)
- `TweetLength` (from `app/models/api/content_actions.py:158`)

Already in `contracts.py` but not exported to iOS — add to the registry in Phase 1: `MessageProcessingStatus`, `LLMProvider`, `LearningDeckRunStatus`, `LearningDeckStatus`, `LearningDeckSourceKind`, `NewsItemStatus`, `NewsItemVisibilityScope`. (iOS currently hand-retypes the chat enums in `ChatMessage.swift:11-36` — those hand copies get deleted in Phase 1.4.)

### 0.3 Ratchet test: response models + untyped-field allowlist

New `tests/contracts/test_contract_surface.py`:

1. Walk `app.routes` for every `/api/*` route: fail if `response_model` is missing, `dict`, or any non-`BaseModel` type. Seed exceptions (file-download/streaming endpoints) into an explicit list in the test.
2. Walk the generated OpenAPI schema: collect every schema property that is a free-form object (`additionalProperties: true` or bare `object`). Fail if the set differs from a checked-in allowlist (`tests/contracts/untyped_surface_allowlist.json`).

Seed the allowlist with today's 18 known untyped fields, in three tiers (tier is a comment, not mechanics):

- **Permanent (recorded decision):** `ContentDetailResponse.structured_summary`, `.longform_artifact`, `.feed_preview`, `ContentSummaryResponse.feed_preview`, `ContentDetailResponse.metadata`.
- **Intentional escape hatches:** `RecordContentInteractionRequest.context_data`, `JobStatusResponse.payload`, `ScraperConfigResponse.config`, `OnboardingSelectedSource.config`, `AgentOnboardingStartRequest.preferences`, `LearningDeckResponse.source_metadata`.
- **Should shrink:** `ContentDiscussionResponse.summary/.stats`, `ContentDetailResponse.bullet_points/.quotes` (typed `BulletPoint`/`Quote` models exist), `DiscoverySubscribeResponse.errors`, `DiscoveryAddItemResponse.errors`, `UserResponse.council_personas` legacy union.

Shrinking the list is routine; growing it is a reviewed decision visible in the diff.

### 0.4 Ratchet test: datetime fields use `UTCDateTime`

Walk every model in `app/models/api/` and fail on any `datetime`-annotated field that is not `UTCDateTime` (from `app/models/api/base.py`). This is the precondition for Phase 3's Go emitter to use `time.Time` with plain RFC3339 and for eventually deleting the iOS multi-format fallback chain.

**Validation (Phase 0):** `ruff` + full `pytest`; regenerated `openapi.json` and `APIContracts.generated.swift` committed (the CI gate enforces this); iOS mark-read flows manually spot-checked against the new error semantics.

---

## Phase 1 — Generator core

### 1.1 The `contracts_codegen` package and registry

Restructure `scripts/generate_ios_contracts.py`'s internals into `scripts/contracts_codegen/` (the entrypoint script keeps its name, path, and `--output` flag so `check_public_contracts.sh` and muscle memory keep working).

`introspect.py` converts a Pydantic model class into a neutral IR via `model_fields`: field name, wire name (alias or snake_case name), resolved type, optionality, default, docstring, policy annotations. Emitters consume only the IR — Swift and Go emitters cannot disagree about what a model contains.

`app/models/contracts_registry.py` is the explicit, reviewed surface definition:

```python
class Target(Flag):
    IOS = auto()
    CLI = auto()

CONTRACT_ENUMS: list[EnumSpec] = [
    EnumSpec(ContentType, targets=IOS | CLI, open=True),
    EnumSpec(ContentStatus, targets=IOS | CLI, open=True),
    EnumSpec(ChatMessageRole, targets=IOS, open=False),
    EnumSpec(ChatMessageDisplayType, targets=IOS, open=True),
    ...
]

CONTRACT_MODELS: list[ModelSpec] = [
    ModelSpec(ContentSummaryResponse, targets=IOS | CLI),
    ModelSpec(MarkReadResponse, targets=IOS),
    ModelSpec(AgentSearchRequest, targets=CLI),
    ...
]
```

Seeds: the ~37 iOS wire models inventoried in `client/newsly/newsly/Models/`, and the request/response models behind the CLI's 18 `ALLOWED_OPERATIONS` in `scripts/export_agent_openapi_schema.py:18-39`. The registry lives in `app/` (not `scripts/`) because it is contract metadata: reviewers see surface changes next to model changes.

**Supported type table — the anti-sprawl contract.** The generator supports exactly: scalars (`str`, `int`, `float`, `bool`), `UTCDateTime`, registered enums, `list[T]`, `dict[str, T]` for supported `T`, nested registered models, `T | None`, and allowlisted `dict[str, Any]` escape fields. Anything else (general unions, `Any` outside the allowlist, tuples, generics) **fails generation with a clear error naming the field**. If a backend model needs an unsupported shape, the contract is the thing to simplify — not the generator to extend. This rule is what keeps Option B a few hundred lines of Python instead of a compiler project.

Type mapping:

| Pydantic | Swift | Go |
| --- | --- | --- |
| `str` / `int` / `float` / `bool` | `String` / `Int` / `Double` / `Bool` | `string` / `int` / `float64` / `bool` |
| `UTCDateTime` | `String` (+ `ServerDate.parse` at use sites — status quo; revisit `Date` later) | `time.Time` (RFC3339; guaranteed by ratchet 0.4) |
| registered enum | generated `API*` enum | generated string type + constants |
| `list[T]` / `dict[str, T]` | `[T]` / `[String: T]` | `[]T` / `map[string]T` |
| `T \| None` | `T?` | pointer or zero-value per Go emitter convention (document one choice) |
| allowlisted `dict[str, Any]` | `[String: AnyCodable]` | `json.RawMessage` |

Determinism: registry order is emission order; no timestamps; fixed header (`// Generated by scripts/generate_ios_contracts.py. Do not edit manually.` — same as today).

### 1.2 Policy annotations

Default decode policy is **strict**: required fields decode with `decode` (a missing key fails the whole decode — this is the point), optional fields with `decodeIfPresent` mapping to `nil`/pointer-nil.

Deliberate tolerance is opt-in at the source, so the backend model documents it:

```python
feed_options: list[AssistantFeedOption] = lenient_field(default_factory=list)
# emits Swift: decodeIfPresent(...) ?? []  + Logger.contractDrift warning when the fallback fires
```

`lenient_field()` wraps `Field(json_schema_extra={"contract": {"lenient": True}}, ...)`. Migration rule for existing tolerance: the currently-lenient collections (chat `feed_options`/`council_candidates`, summary arrays) get `lenient_field`; the load-bearing booleans (`is_read`, `is_saved_to_knowledge`, `body_available`) do **not** — they decode strictly, which is a deliberate behavior change from today's `?? false` (the backend always sends them; a contract test in Phase 4 proves it).

### 1.3 Swift enum emission with open/closed support

Closed enums keep today's shape (`String, Codable, CaseIterable`). Open enums (most status/kind enums — anything the backend may extend while old app builds are live) emit as:

```swift
enum APIContentStatus: Codable, Equatable {
    case new, pending, processing, awaiting_image, completed, failed, skipped
    case unknown(String)

    static let knownCases: [APIContentStatus] = [.new, .pending, ...]
    var rawValue: String { ... }
    init(rawValue: String) { ... }          // never fails; unknown values -> .unknown(raw)
    init(from decoder: Decoder) throws { self.init(rawValue: try ...) }
}
```

Open/closed assignment (from the registry): **open** — `ContentType`, `ContentStatus`, `SummaryKind`, `TaskType`, `TaskStatus`, `ChatMessageDisplayType`, `MessageProcessingStatus`, `LearningDeckRunStatus`, `LearningDeckStatus`, `NewsItemStatus`; **closed** — `ChatMessageRole`, `ContentClassification`, `SummaryVersion`, `TweetLength`, `LLMProvider`.

### 1.4 iOS call-site migration for the new enum shapes

The open-enum change is the churn-heavy step: `CaseIterable` conformance disappears (use `knownCases`), `init(rawValue:)` becomes non-optional, and `switch` statements gain a `.unknown` arm — which is the feature: the compiler now forces every call site to decide what an unknown status means (typically: render a neutral state, stop polling with a logged error — never silently fall through). Also here: delete the hand-rolled `ChatMessageRole`/`ChatMessageDisplayType`/`MessageProcessingStatus` Swift copies in `ChatMessage.swift` in favor of the generated ones, and migrate the status-string comparisons in `OnboardingViewModel`/`DiscoveryPersonalizeViewModel`/`Narration.swift` to the generated enums.

**Validation (Phase 1):** generated enum file diff reviewed case-by-case against the old one (only shape changes, no value changes); iOS unit tests; a new decode test per open enum asserting `"some_future_value"` → `.unknown("some_future_value")`.

---

## Phase 2 — Swift wire structs

### 2.1 Emission and wiring

`swift_emitter.py` emits `APIModels.generated.swift` next to the enums file (the `Models/Generated/` group is filesystem-synchronized into the app target, so no pbxproj surgery — verify on first build). Each registered model becomes a Codable struct with explicit `CodingKeys` (snake_case → camelCase, matching the app's existing convention with the default `JSONDecoder`). `check_public_contracts.sh --python-only` gains the third compare.

**Wire/domain split.** Generated structs are pure wire shape. Everything hand-rolled models do beyond decoding — display-title fallback chains (`ContentDetail.swift:273-280`), cached date parsing, `apiContentType`-style accessors, formatting — moves to hand-written extension files (`ContentDetail+Domain.swift`). The generator never emits domain logic; the compiler guarantees the extensions match the wire struct.

### 2.2–2.5 Migration batches

Each batch: add models to registry → regenerate → delete the hand-rolled file → fix what the compiler flags → tests green. One PR per batch.

1. **Batch 1 — simple responses:** `UnreadCountsResponse`, `ProcessingCountResponse`, the Phase 0.1 mutation responses, `JobStatusResponse`, `Narration`/`AudioEpisode` wire parts, `ScraperConfig`. Proves the pipeline end-to-end with low blast radius.
2. **Batch 2 — `ContentSummary` + list/pagination wrappers** (`ContentListResponse`, mixed search). First model with `lenient_field` collections and heavy domain extensions (cached dates, display text).
3. **Batch 3 — chat:** `ChatMessage`, `ChatSessionSummary`, `ChatSessionDetail`, the five small chat response wrappers. Depends on Phase 1.4 enum work.
4. **Batch 4 — `ContentDetail`.** The hairiest: 16-field custom decoder today. Wire struct generated with `metadata: [String: AnyCodable]` and the three summary payload fields kept as allowlisted escape fields (recorded decision 1) — `ContentDetailDecodedPayloads` and all summary rendering code are untouched. Fallback chains and `RelevantLink` extraction move to `ContentDetail+Domain.swift`.
5. **Batch 5 — requests:** generate `Encodable` request structs (`SubmitContentRequest` first). ShareExtension's `[String: Any]` body (`ShareViewController.swift:450-467`) and `ContentService.submitContent` both adopt it — request-key drift becomes a compile error in both targets. Then delete `requestRaw` (its last consumer died with the Phase 0.1 typed responses).

### 2.7 Coverage ratchet

`tests/contracts/test_generated_coverage.py`: the set of hand-rolled wire models still allowed (checked-in list) only shrinks. Anything decoding API JSON outside `Models/Generated/` + the allowlist fails the test. Seed with the full 37; drain per batch.

**Validation (Phase 2, per batch):** iOS unit tests + the Phase 4 fixtures once they exist; Maestro visual baselines after batches 2 and 4 (feed and detail rendering paths); decode-failure logging visible in the Xcode console when a fixture is deliberately corrupted (manual spot check).

---

## Phase 3 — Go clean models

### 3.1 Emit `contracts_gen.go`

`go_emitter.py` emits one file into the existing `cli/internal/api` package (keeping the import path; the ogen `oas_*_gen.go` files are deleted in 3.3). Types from the same registry (`targets=CLI`), enums as string types with constants plus `func (v T) Known() bool` — decoding an unknown enum value does not error (matching the iOS open-enum policy; ogen's hard failure on unknown values is exactly the skew bomb we're defusing), but `Known()` lets commands surface it.

### 3.2 Thin client + call-site port

One hand-written method per operation in `client.go`, using the `doJSON` retry/polling helper that `cli/internal/runtime/link_and_library.go:126-189` already proves out. Port the `cmd/` call sites from ogen types (`api.AgentSearchRequest` etc.) to the generated ones, and fold the duplicate hand-rolled structs (`CLILinkStartResponse`, `AgentLibraryManifestResponse`, … at `link_and_library.go:18-42`) onto generated types — that duplication was the CLI's one real drift surface.

### 3.3 Delete ogen

Remove the ogen invocation from `generate_agent_cli_artifacts.sh`, delete `oas_*_gen.go` including the patched `decodeFlexibleDateTime` (ratchet 0.4 + RFC3339 `time.Time` make it unnecessary), and point `check_public_contracts.sh --go-only` at `generate_go_contracts.py` + `contracts_gen.go` diff.

### 3.4 `agent-openapi.json` fate

`export_agent_openapi_schema.py` / `cli/openapi/agent-openapi.json` no longer feed codegen. Keep them only if something consumes the file (check `docs/library`, agent-facing docs, external tooling); otherwise delete both and the `ALLOWED_OPERATIONS` list migrates into the registry as the CLI target surface. Decide by grep, not nostalgia.

**Validation (Phase 3):** `go test ./...` in the Go CI job; `go vet`; one end-to-end CLI smoke (auth link flow + search) against a local server; the `--go-only` gate green.

---

## Phase 4 — Cross-language golden fixtures

Schema/codegen gates catch *shape* drift. Fixtures catch *semantic* drift — date formats, null-vs-absent, numeric coercion — the class of bug the Go datetime patch and the iOS 4-format date chain exist to absorb.

1. **4.1 Backend:** extend `tests/contracts/test_content_api_fixtures.py` so each scenario writes its `model_dump(mode="json")` output to `tests/contracts/fixtures/<model>__<scenario>.json` and fails if the file on disk differs (regenerate with an explicit `--update-fixtures` flag/env). Fixture builders must be deterministic — fixed datetimes, no `now()`.
2. **`fixture_sync.py`** copies fixtures into `client/newsly/newslyTests/Fixtures/contracts/` and `cli/internal/api/testdata/contracts/`; `check_public_contracts.sh --python-only` diffs the copies so they cannot go stale. (Verify the iOS test bundle picks up the synced JSON via the filesystem-synchronized test group.)
3. **4.2 iOS:** `ContentSummaryTests`, `ContentDetailTests`, chat decode tests load the fixture files instead of inline string literals (the literals are the client testing itself against its own assumptions — that is the mechanism behind the `key_takeaway` drift in commit `26084e54`).
4. **4.3 Go:** one table-driven test decoding every CLI-relevant fixture through the generated types.
5. **4.4 Adversarial fixtures:** for each open enum, a fixture with an unknown value asserting `.unknown`/`Known() == false` handling; fixtures distinguishing absent vs `null` optionals; one fixture per `lenient_field` with the key absent, asserting the default *and* (iOS) that the drift log fires. Also the fixture that proves `is_read`/`is_saved_to_knowledge`/`body_available` are always present — the justification for strict decoding of them.

**Validation:** corrupt a fixture locally → both client test suites fail; change a Pydantic field → backend fixture test fails until regenerated → regenerated fixture fails clients until adapted. That full loop demonstrated once, in the PR description.

---

## Phase 5 — Policy

Write `20-contract-policy.md` in this initiative directory (and link it from `docs/architecture.md`):

- **Decode tolerance defaults:** strict unless `lenient_field`; every fallback logs; no `try?` in decode paths without a justifying comment.
- **The open/closed enum table** (from 1.3) and what `.unknown` must mean per surface (render neutral, stop polling with error, never silent fall-through).
- **Evolution rules:** additive only; never repurpose a field; new enum values only on open enums; removing a field requires it to be absent from all generated clients for one released app version first.
- **Definition of done for a new endpoint:** typed `response_model` (ratchet enforces), registry entry per consuming client, regenerate artifacts (CI enforces), fixture scenario for any new model.

---

## Risks and mitigations

- **Generator sprawl.** Mitigated by the supported-type table + fail-loud rule (1.1). If `contracts_codegen` exceeds ~1k lines of emitter logic, stop and re-evaluate against swift-openapi-generator types-only mode — that fallback stays documented in the 2026-06-09 investigation.
- **Strict-decode regressions** (the `?? false` → strict change). Mitigated by the Phase 4 fixture proving the fields are always sent, and by batch-by-batch rollout with Maestro baselines. If a production response path genuinely omits a field, that is a backend bug this initiative exists to surface — fix it there.
- **Open-enum churn** (1.4) touches many switch statements at once. Do it as its own PR with no other changes, so review is mechanical.
- **`ContentDetail` complexity.** Scheduled last among models (batch 4), wire/domain split keeps the summary-payload decision (recorded decision 1) untouched, and its fixture scenarios land *before* the migration (Phase 4 can start in parallel with Phase 2).
- **Two emitters drifting from each other.** They share one IR (`introspect.py`); emitter unit tests assert both emit the same field set for a shared model.

## Sequencing

```
Phase 0  ──────►  Phase 1  ──────►  Phase 2 (batches 1..5)
   │                                   ▲
   └────────►  Phase 4.1 fixtures ─────┘ (start after 0; protects the migration itself)
                       │
Phase 1 ───────────────┴──────►  Phase 3 (Go; independent of Phase 2 batches)
Phase 5 written alongside, finalized last
```

Rough effort: Phase 0 ~2-3 days; Phase 1 ~3-4 days; Phase 2 ~1.5-2 weeks spread across batches; Phase 3 ~3-4 days; Phase 4 ~2 days; Phase 5 ~half a day.

## Definition of done

- Every API wire model consumed by iOS or the CLI is generated from the Pydantic source, except the checked-in, shrink-only hand-rolled allowlist (target: summary payload internals only).
- `check_public_contracts.sh` covers: OpenAPI doc, Swift enums, Swift models, Go models, fixture copies — all diff-gated in CI.
- The ratchet tests (response models, untyped allowlist, `UTCDateTime`, generated coverage) are green and their allowlists reviewed.
- A backend contract change cannot reach `main` without a visible diff in every affected generated artifact and fixture.
- ogen, `decodeFlexibleDateTime`, `requestRaw`, the ShareExtension `[String: Any]` body, and the hand-rolled chat enums no longer exist.
