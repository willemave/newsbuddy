from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, get_args, get_type_hints

import pytest
from pydantic import BaseModel

from app.models.api.base import UTCDateTime, lenient_field
from app.models.api.chat import ChatMessageDto
from app.models.api.content import ContentDetailResponse, ContentSummaryResponse
from app.models.api.content_actions import BulkMarkReadResponse
from app.models.contracts import (
    AgentLibraryDocumentVariant,
    ContentClassification,
    ContentStatus,
    ContentType,
    SummaryVersion,
)
from app.models.contracts_registry import (
    CONTRACT_ENUMS,
    CONTRACT_MODELS,
    CONTRACT_UNTYPED_FIELD_ALLOWLIST,
    EnumSpec,
    ModelSpec,
    Target,
)
from scripts.contracts_codegen.go_emitter import build_go_contracts
from scripts.contracts_codegen.introspect import (
    UnsupportedContractTypeError,
    expand_contract_models,
    introspect_model,
    validate_contract_models,
)
from scripts.contracts_codegen.policy import is_lenient_policy
from scripts.contracts_codegen.swift_emitter import build_swift_contracts, build_swift_models


class LocalEnum(StrEnum):
    VALUE = "value"


class LocalEnumModel(BaseModel):
    value: LocalEnum


class LenientPolicyModel(BaseModel):
    items: list[str] = lenient_field(
        default_factory=list,
        json_schema_extra={"contract": {"owner": "test"}, "x-test": True},
    )


class DatetimeFieldsModel(BaseModel):
    required_at: UTCDateTime
    optional_at: UTCDateTime | None = None


class ListOfDatetimesModel(BaseModel):
    occurred_at: list[UTCDateTime]


class DictOfDatetimesModel(BaseModel):
    occurred_at: dict[str, UTCDateTime]


def test_swift_enum_emitter_matches_checked_in_artifact() -> None:
    """The new emitter package should preserve today's generated enum artifact."""
    expected = Path("client/newsly/newsly/Models/Generated/APIContracts.generated.swift")
    assert build_swift_contracts() == expected.read_text()


def test_swift_model_emitter_matches_checked_in_artifact() -> None:
    """The model emitter should preserve the checked-in Swift model artifact."""
    expected = Path("client/newsly/newsly/Models/Generated/APIModels.generated.swift")
    assert build_swift_models() == expected.read_text()


def test_go_contract_emitter_matches_checked_in_artifact() -> None:
    """The Go emitter should preserve the checked-in CLI contract artifact."""
    expected = Path("cli/internal/api/contracts_gen.go")
    assert build_go_contracts() == expected.read_text()


def test_go_model_emitter_outputs_json_tags_and_pointer_optionals() -> None:
    """Generated Go models should keep wire tags and nullable scalars as pointers."""
    output = build_go_contracts()

    assert "type SubmitContentRequest struct {" in output
    assert 'URL string `json:"url"`' in output
    assert 'ContentType *ContentType `json:"content_type,omitempty"`' in output
    assert (
        'SaveToKnowledgeAndMarkRead *bool `json:"save_to_knowledge_and_mark_read,omitempty"`'
        in output
    )
    assert 'Metadata json.RawMessage `json:"metadata"`' in output
    assert 'CreatedAt time.Time `json:"created_at"`' in output


def test_go_enum_emitter_outputs_known_method_without_strict_unmarshal() -> None:
    """Generated Go enums should expose Known while letting JSON decode future values."""
    output = build_go_contracts()

    assert "type ContentStatus string" in output
    assert 'ContentStatusAwaitingImage ContentStatus = "awaiting_image"' in output
    assert "func (v ContentStatus) Known() bool {" in output
    assert "func (v ContentStatus) UnmarshalJSON" not in output


def test_swift_model_emitter_outputs_coding_keys_and_strict_decode() -> None:
    """Generated Swift models should carry explicit wire keys and strict required decodes."""
    output = build_swift_models([ModelSpec(BulkMarkReadResponse, targets=Target.IOS)])

    assert "struct APIBulkMarkReadResponse: Codable {" in output
    assert "let markedCount: Int" in output
    assert 'case markedCount = "marked_count"' in output
    assert "markedCount = try container.decode(Int.self, forKey: .markedCount)" in output
    assert "failedIds = try container.decode([Int].self, forKey: .failedIds)" in output


def test_swift_model_emitter_outputs_lenient_decode_fallback() -> None:
    """Lenient source policy should become an explicit decode fallback."""
    output = build_swift_models([ModelSpec(LenientPolicyModel, targets=Target.IOS)])

    assert "struct APILenientPolicyModel: Codable {" in output
    assert "let items: [String]" in output
    assert 'case items = "items"' in output
    assert "items = try container.decodeIfPresent([String].self, forKey: .items) ?? []" in output


def test_swift_model_emitter_maps_datetime_to_date() -> None:
    """UTCDateTime fields should map to Swift Date, not String."""
    output = build_swift_models([ModelSpec(DatetimeFieldsModel, targets=Target.IOS)])

    assert "struct APIDatetimeFieldsModel: Codable {" in output
    assert "let requiredAt: Date" in output
    assert "let optionalAt: Date?" in output


def test_swift_model_emitter_required_datetime_throws_on_unparseable() -> None:
    """Required datetime fields decode strictly and guard-throw on unparseable values."""
    output = build_swift_models([ModelSpec(DatetimeFieldsModel, targets=Target.IOS)])

    assert "let requiredAtRaw = try container.decode(String.self, forKey: .requiredAt)" in output
    assert "guard let requiredAtParsed = ServerDate.parse(requiredAtRaw) else {" in output
    assert (
        "throw DecodingError.dataCorruptedError(forKey: .requiredAt, in: container, "
        'debugDescription: "Unparseable date for requiredAt")' in output
    )
    assert "requiredAt = requiredAtParsed" in output


def test_swift_model_emitter_optional_datetime_throws_when_present_but_unparseable() -> None:
    """Optional-but-present datetime values are strict; only a missing key defaults to nil."""
    output = build_swift_models([ModelSpec(DatetimeFieldsModel, targets=Target.IOS)])

    assert (
        "if let optionalAtRaw = try container.decodeIfPresent(String.self, forKey: .optionalAt) {"
        in output
    )
    assert "guard let optionalAtParsed = ServerDate.parse(optionalAtRaw) else {" in output
    assert "optionalAt = optionalAtParsed" in output
    assert "optionalAt = nil" in output


def test_swift_model_emitter_encodes_datetime_via_server_date_format() -> None:
    """Generated encode(to:) must format Date fields through ServerDate, not synthesize them."""
    output = build_swift_models([ModelSpec(DatetimeFieldsModel, targets=Target.IOS)])

    assert "func encode(to encoder: Encoder) throws {" in output
    assert "try container.encode(ServerDate.format(requiredAt), forKey: .requiredAt)" in output
    assert (
        "try container.encodeIfPresent(optionalAt.map(ServerDate.format), forKey: .optionalAt)"
        in output
    )


def test_swift_model_emitter_lenient_datetime_with_literal_default_fails_loudly() -> None:
    """A lenient datetime field cannot carry a Python literal default: no Swift literal exists
    for an arbitrary datetime, so the generator must fail loudly instead of guessing."""

    class LenientDatetimeModel(BaseModel):
        occurred_at: UTCDateTime = lenient_field(
            default_factory=lambda: datetime(2000, 1, 1),
            json_schema_extra={"contract": {"owner": "test"}},
        )

    with pytest.raises(ValueError, match="datetime fields do not support literal defaults"):
        build_swift_models([ModelSpec(LenientDatetimeModel, targets=Target.IOS)])


def test_swift_model_emitter_optional_lenient_datetime_defaults_on_missing_or_unparseable() -> None:
    """An optional lenient datetime field falls back to nil on missing OR unparseable values."""

    class OptionalLenientDatetimeModel(BaseModel):
        occurred_at: UTCDateTime | None = lenient_field(
            default=None,
            json_schema_extra={"contract": {"owner": "test"}},
        )

    output = build_swift_models([ModelSpec(OptionalLenientDatetimeModel, targets=Target.IOS)])

    assert "let occurredAt: Date?" in output
    assert (
        "if let occurredAtRaw = try container.decodeIfPresent(String.self, forKey: .occurredAt), "
        "let occurredAtParsed = ServerDate.parse(occurredAtRaw) {" in output
    )
    assert "occurredAt = occurredAtParsed" in output
    assert "occurredAt = nil" in output


def test_swift_model_emitter_rejects_list_of_datetimes() -> None:
    """list[UTCDateTime] is not supported; the emitter should fail loudly, not half-support it."""
    with pytest.raises(ValueError, match="list\\[UTCDateTime\\] is not supported"):
        build_swift_models([ModelSpec(ListOfDatetimesModel, targets=Target.IOS)])


def test_swift_model_emitter_rejects_dict_of_datetimes() -> None:
    """dict[str, UTCDateTime] is not supported; the emitter should fail loudly."""
    with pytest.raises(ValueError, match="dict\\[str, UTCDateTime\\] is not supported"):
        build_swift_models([ModelSpec(DictOfDatetimesModel, targets=Target.IOS)])


def test_no_registered_model_uses_nested_datetime_containers() -> None:
    """Guard the emitter's unsupported-shape error stays untriggered by real registry models."""
    build_swift_models()


def test_swift_open_enum_emits_non_failing_unknown_case() -> None:
    """Open Swift enums should decode future raw values instead of throwing."""
    output = build_swift_contracts(
        [EnumSpec(ContentStatus, targets=Target.IOS, open=True, swift_name="APIContentStatus")]
    )

    assert "enum APIContentStatus: Codable, Equatable, Hashable {" in output
    assert "case unknown(String)" in output
    assert "static let knownCases: [APIContentStatus]" in output
    assert "init(rawValue: String)" in output
    assert "default: self = .unknown(rawValue)" in output
    assert "CaseIterable" not in output


def test_swift_open_enum_handles_known_unknown_value() -> None:
    """ContentType keeps its known .unknown case and uses a distinct future fallback."""
    output = build_swift_contracts(
        [EnumSpec(ContentType, targets=Target.IOS, open=True, swift_name="APIContentType")]
    )

    assert "case unknown\n" in output
    assert "case unknownRaw(String)" in output
    assert 'case "unknown": self = .unknown' in output
    assert "default: self = .unknownRaw(rawValue)" in output


def test_swift_closed_enum_keeps_raw_representable_shape() -> None:
    """Closed Swift enums should keep the strict CaseIterable raw-value shape."""
    output = build_swift_contracts(
        [
            EnumSpec(
                ContentClassification,
                targets=Target.IOS,
                open=False,
                swift_name="APIContentClassification",
            )
        ]
    )

    assert "enum APIContentClassification: String, Codable, CaseIterable {" in output
    assert "case unknown(String)" not in output


def test_swift_open_int_enum_is_rejected() -> None:
    """Open enums need the unknownRaw shape, which is only defined for string enums."""
    with pytest.raises(ValueError) as exc_info:
        build_swift_contracts(
            [
                EnumSpec(
                    SummaryVersion,
                    targets=Target.IOS,
                    open=True,
                    swift_name="APISummaryVersion",
                )
            ]
        )

    assert "Open IntEnum is not supported for iOS: SummaryVersion" in str(exc_info.value)


def test_contract_registry_entries_are_unique() -> None:
    """The registry should expose each reviewed model and enum once."""
    enum_keys = [(spec.enum.__module__, spec.enum.__qualname__) for spec in CONTRACT_ENUMS]
    model_keys = [(spec.model.__module__, spec.model.__qualname__) for spec in CONTRACT_MODELS]

    assert len(enum_keys) == len(set(enum_keys))
    assert len(model_keys) == len(set(model_keys))


def test_contract_registry_seeds_ios_and_cli_surfaces() -> None:
    """The initial registry should include both client targets."""
    assert any(spec.targets & Target.IOS for spec in CONTRACT_MODELS)
    assert any(spec.targets & Target.CLI for spec in CONTRACT_MODELS)
    assert any(spec.targets & Target.IOS for spec in CONTRACT_ENUMS)
    assert any(spec.targets & Target.CLI for spec in CONTRACT_ENUMS)


def test_contract_registry_models_use_supported_types() -> None:
    """Every registered model field should fit the generator's supported type table."""
    validate_contract_models(
        expand_contract_models(CONTRACT_MODELS, CONTRACT_ENUMS),
        CONTRACT_ENUMS,
        untyped_field_allowlist=CONTRACT_UNTYPED_FIELD_ALLOWLIST,
    )


def test_contract_model_registry_expansion_covers_nested_pydantic_dependencies() -> None:
    """Every nested Pydantic model reachable from the registry must resolve, either
    through an explicit spec or transitive-closure expansion. Registration for
    reachable nested models is intentionally *not* required directly on
    CONTRACT_MODELS (see `expand_contract_models`); this test guards the invariant
    that expansion never leaves a nested model unresolved."""
    expanded = expand_contract_models(CONTRACT_MODELS, CONTRACT_ENUMS)
    registered = {spec.model for spec in expanded}
    missing: set[type[BaseModel]] = set()

    for spec in expanded:
        type_hints = get_type_hints(spec.model, include_extras=True)
        for field_name, annotation in type_hints.items():
            if field_name in spec.model.model_fields:
                _collect_missing_nested_models(annotation, registered, missing)

    assert missing == set()


def test_introspect_model_captures_pydantic_field_contract() -> None:
    """The neutral IR should preserve field names, defaults, and requiredness."""
    model_ir = introspect_model(BulkMarkReadResponse)
    fields = {field.python_name: field for field in model_ir.fields}

    assert model_ir.name == "BulkMarkReadResponse"
    assert fields["marked_count"].wire_name == "marked_count"
    assert fields["marked_count"].required is True
    assert fields["failed_ids"].wire_name == "failed_ids"
    assert fields["failed_ids"].required is False
    assert fields["failed_ids"].default_factory == "list"


def test_introspect_model_resolves_cross_client_enum_references() -> None:
    """IR type trees should preserve canonical enum identity without raw annotations."""
    model_ir = introspect_model(ChatMessageDto)
    fields = {field.python_name: field for field in model_ir.fields}

    assert fields["role"].type_ir.kind == "enum"
    assert fields["role"].type_ir.name == "ChatMessageRole"
    assert fields["display_type"].type_ir.kind == "enum"
    assert fields["display_type"].type_ir.name == "ChatMessageDisplayType"


def test_introspect_model_preserves_lenient_policy_metadata() -> None:
    """Fields marked lenient should carry policy metadata into the neutral IR."""
    model_ir = introspect_model(LenientPolicyModel)
    field = model_ir.fields[0]

    assert field.json_schema_extra["x-test"] is True
    assert field.contract_policy == {"owner": "test", "lenient": True}
    assert is_lenient_policy(field.contract_policy) is True


def test_known_lenient_fields_are_marked_in_source_models() -> None:
    """Current client fallback collections should be explicit backend policy."""
    chat_fields = {field.python_name: field for field in introspect_model(ChatMessageDto).fields}
    detail_fields = {
        field.python_name: field for field in introspect_model(ContentDetailResponse).fields
    }

    for field_name in ("feed_options", "council_candidates"):
        assert is_lenient_policy(chat_fields[field_name].contract_policy) is True
    for field_name in ("bullet_points", "quotes", "topics"):
        assert is_lenient_policy(detail_fields[field_name].contract_policy) is True


def test_load_bearing_boolean_fields_stay_strict() -> None:
    """Boolean fallbacks called out by the plan should not be marked lenient."""
    summary_fields = {
        field.python_name: field for field in introspect_model(ContentSummaryResponse).fields
    }
    detail_fields = {
        field.python_name: field for field in introspect_model(ContentDetailResponse).fields
    }

    for fields in (summary_fields, detail_fields):
        assert is_lenient_policy(fields["is_read"].contract_policy) is False
        assert is_lenient_policy(fields["is_saved_to_knowledge"].contract_policy) is False
    assert is_lenient_policy(detail_fields["body_available"].contract_policy) is False


def test_contract_model_validation_rejects_literal_fields() -> None:
    """Finite string sets must be named registry enums, not ad hoc Literals."""

    class LiteralModel(BaseModel):
        mode: Literal["compact", "expanded"]

    with pytest.raises(UnsupportedContractTypeError) as exc_info:
        validate_contract_models(
            [ModelSpec(LiteralModel, targets=Target.IOS)],
            CONTRACT_ENUMS,
            untyped_field_allowlist=CONTRACT_UNTYPED_FIELD_ALLOWLIST,
        )

    assert "LiteralModel.mode" in str(exc_info.value)
    assert "Literal is not supported" in str(exc_info.value)


def test_contract_model_validation_rejects_unallowlisted_any_dicts() -> None:
    """Free-form objects need explicit reviewed allowlist entries."""

    class FreeFormModel(BaseModel):
        payload: dict[str, Any]

    with pytest.raises(UnsupportedContractTypeError) as exc_info:
        validate_contract_models(
            [ModelSpec(FreeFormModel, targets=Target.IOS)],
            CONTRACT_ENUMS,
            untyped_field_allowlist=CONTRACT_UNTYPED_FIELD_ALLOWLIST,
        )

    assert "FreeFormModel.payload" in str(exc_info.value)
    assert "dict[str, Any] requires an allowlist entry" in str(exc_info.value)


def test_contract_model_validation_rejects_unregistered_enums() -> None:
    """Enum annotations must be reviewed through CONTRACT_ENUMS."""

    with pytest.raises(UnsupportedContractTypeError) as exc_info:
        validate_contract_models(
            [ModelSpec(LocalEnumModel, targets=Target.IOS)],
            CONTRACT_ENUMS,
            untyped_field_allowlist=CONTRACT_UNTYPED_FIELD_ALLOWLIST,
        )

    assert "LocalEnumModel.value" in str(exc_info.value)
    assert "enum LocalEnum is not registered" in str(exc_info.value)


def test_contract_model_validation_rejects_target_mismatched_enums() -> None:
    """Validation should catch target availability before emitters run."""

    class IosModelReferencesCliEnum(BaseModel):
        variant: AgentLibraryDocumentVariant

    with pytest.raises(UnsupportedContractTypeError) as exc_info:
        validate_contract_models(
            [ModelSpec(IosModelReferencesCliEnum, targets=Target.IOS)],
            CONTRACT_ENUMS,
            untyped_field_allowlist=CONTRACT_UNTYPED_FIELD_ALLOWLIST,
        )

    assert "IosModelReferencesCliEnum.variant" in str(exc_info.value)
    assert "iOS model references non-iOS enum AgentLibraryDocumentVariant" in str(exc_info.value)


def _collect_missing_nested_models(
    annotation: Any,
    registered: set[type[BaseModel]],
    missing: set[type[BaseModel]],
) -> None:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        if annotation not in registered:
            missing.add(annotation)
        return
    for argument in get_args(annotation):
        if argument is not type(None):
            _collect_missing_nested_models(argument, registered, missing)
