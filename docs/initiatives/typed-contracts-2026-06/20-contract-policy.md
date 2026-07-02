# Contract Policy

This policy covers public `/api/*` response/request models that are consumed by iOS, the Go CLI, or external machine clients.

## Source of Truth

- Backend API DTOs live under `app/models/api/`.
- Cross-client enums live in `app/models/contracts.py`.
- The reviewed generated-client surface is `app/models/contracts_registry.py`.
- Generated artifacts are checked in and must be regenerated with `scripts/regenerate_public_contracts.sh`.
- `scripts/check_public_contracts.sh` is the drift gate for OpenAPI, Swift, and Go artifacts.

## Decode Tolerance

Default behavior is strict.

- Required fields must decode with no client fallback.
- `is_read`, `is_saved_to_knowledge`, and `body_available` are required when present in their response models; missing values are backend bugs.
- A field may be tolerant only when the backend model marks it with `lenient_field`.
- Lenient fallbacks must be explicit in generated or hand-written decode code.
- No `try?` or silent fallback belongs in a decode path without a comment naming the backend compatibility reason.
- Every new lenient fallback should have a fixture that covers the missing-key case.

## Datetimes

- Backend DTO datetime fields must use `UTCDateTime`, which serializes one UTC RFC3339 shape.
- Go generated contracts map `UTCDateTime` to `time.Time`.
- Swift generated contracts map `UTCDateTime` to `Date`. Parsing and formatting live in generated `init(from:)`/`encode(to:)`, not in `JSONDecoder` date strategies, so every decoder path gets the same behavior regardless of how it was constructed.
  - Decode uses the canonical parser (`ServerDate.parse`, a 4-format tolerance chain). Required fields and present-but-value optional fields throw `DecodingError.dataCorruptedError` on an unparseable string; a missing optional key decodes to `nil`. Lenient fields fall back to their default on a missing key *or* an unparseable value.
  - Encode uses `ServerDate.format`, which emits ISO8601 UTC with fractional seconds and a `Z` suffix (matching backend `serialize_utc_datetime`). Optional datetime fields are omitted when `nil`, matching Swift's default `encodeIfPresent` behavior for other optional fields.
  - `list[UTCDateTime]` and `dict[str, UTCDateTime]` are not supported by the generator; it raises rather than half-supporting nested datetime containers. No registered model currently needs this shape.
  - `ServerDate.parse` remains available for hand-written decode paths that are not generated fields — chiefly untyped metadata dictionaries (`ArticleMetadata`, `PodcastMetadata`, `SourceMetadata`) and presentation-facing string date fields whose backing DTO field is a plain `str` rather than `UTCDateTime` (e.g. `ContentSummary`/`ContentDetail` timestamps, pending migration in a future batch). New generated `Date` fields must not reintroduce per-use-site `ServerDate.parse` calls.

## Enums

Open enums decode future raw values without failing. Unknown values must render neutrally, avoid destructive actions, and never silently fall through business logic.

Open enums:

- `ContentType`
- `ContentStatus`
- `TaskType`
- `TaskStatus`
- `SummaryKind`
- `SubmissionOutcome`
- `FeedType`
- `FeedFormat`
- `AudioEpisodeKind`
- `AudioEpisodeStatus`
- `CliLinkStatus`
- `AgentSearchResultKind`
- `OnboardingSuggestionType`
- `OnboardingSelectedSourceType`
- `NewsItemVisibilityScope`
- `NewsItemStatus`
- `LearningDeckSourceKind`
- `LearningDeckRunStatus`
- `LearningDeckStatus`
- `MessageProcessingStatus`
- `ChatMessageDisplayType`

Closed enums reject unknown values and require a coordinated client/backend change:

- `ContentClassification`
- `SummaryVersion`
- `SavedSource`
- `OperationStatus`
- `KnowledgeMutationStatus`
- `ContentInteractionType`
- `NarrationTargetType`
- `SubmissionKind`
- `DiscussionMode`
- `AgentLibraryDocumentVariant`
- `IntegrationDisconnectStatus`
- `DeleteStatus`
- `UserLlmProvider`
- `ChatMessageRole`
- `LLMProvider`
- `TweetLength`

## Evolution Rules

- Additive changes are preferred: new optional fields, new models, and new open-enum values.
- Never repurpose a field name with new meaning.
- New enum values may be added only to open enums unless every generated client is updated in the same release.
- Removing a field requires one released app version where no generated client consumes it.
- Tightening a nullable field to required requires a fixture proving all backend paths send it.
- Free-form `dict[str, Any]` fields require an allowlist entry and a named owner.

## Endpoint Definition of Done

For a new or changed endpoint:

- The route has a concrete Pydantic `response_model`.
- Request and response models use supported generator field types.
- Any consuming client target is listed in `CONTRACT_MODELS` and needed enums in `CONTRACT_ENUMS`.
- Generated Swift/Go artifacts and OpenAPI exports are regenerated.
- A canonical fixture exists for new user-visible response models.
- Adversarial fixtures cover unknown open-enum values and null-vs-absent behavior when those semantics matter.
- Focused backend and consuming-client tests pass.
