"""Build a neutral contract IR from Pydantic models."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from types import UnionType
from typing import Annotated, Any, Literal, Union, get_args, get_origin, get_type_hints

from pydantic import BaseModel
from pydantic.fields import PydanticUndefined

from app.models.api.base import UTCDateTime
from app.models.contracts_registry import EnumSpec, ModelSpec, Target
from scripts.contracts_codegen.policy import contract_policy_from_extra

TypeIRKind = Literal["scalar", "datetime", "enum", "model", "list", "dict", "untyped"]
ScalarIRKind = Literal["string", "int", "float", "bool"]


@dataclass(frozen=True)
class TypeIR:
    """Resolved, emitter-neutral type tree for one contract field."""

    kind: TypeIRKind
    optional: bool = False
    scalar: ScalarIRKind | None = None
    name: str | None = None
    item_type: TypeIR | None = None
    value_type: TypeIR | None = None


@dataclass(frozen=True)
class FieldIR:
    """Neutral representation of one Pydantic model field."""

    python_name: str
    wire_name: str
    type_ir: TypeIR
    required: bool
    default: Any
    default_factory: str | None
    description: str | None
    json_schema_extra: dict[str, Any]
    contract_policy: dict[str, Any]


@dataclass(frozen=True)
class ModelIR:
    """Neutral representation of one generated-client model."""

    name: str
    model: type[BaseModel]
    fields: tuple[FieldIR, ...]


class UnsupportedContractTypeError(ValueError):
    """Raised when a registered contract field uses an unsupported annotation."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = tuple(errors)
        message = "Unsupported contract field types:\n" + "\n".join(
            f"- {error}" for error in errors
        )
        super().__init__(message)


@dataclass(frozen=True)
class ContractTypeResolver:
    """Registry-backed resolver for model/enum references inside TypeIR."""

    model_specs: dict[type[BaseModel], ModelSpec]
    enum_specs: dict[type[Enum], EnumSpec]
    target: Target | None
    untyped_field_allowlist: frozenset[str]
    require_registry: bool = True
    enforce_untyped_allowlist: bool = True

    @classmethod
    def from_specs(
        cls,
        model_specs: list[ModelSpec],
        enum_specs: list[EnumSpec],
        *,
        target: Target | None,
        untyped_field_allowlist: frozenset[str],
    ) -> ContractTypeResolver:
        return cls(
            model_specs={spec.model: spec for spec in model_specs},
            enum_specs={spec.enum: spec for spec in enum_specs},
            target=target,
            untyped_field_allowlist=untyped_field_allowlist,
        )

    @classmethod
    def unchecked(cls) -> ContractTypeResolver:
        return cls(
            model_specs={},
            enum_specs={},
            target=None,
            untyped_field_allowlist=frozenset(),
            require_registry=False,
            enforce_untyped_allowlist=False,
        )

    def resolved_model_name(self, model: type[BaseModel]) -> str:
        spec = self.model_specs.get(model)
        if self.target == Target.IOS:
            return (
                spec.swift_name
                if spec and spec.swift_name
                else f"API{_strip_dto_suffix(model.__name__)}"
            )
        if self.target == Target.CLI:
            return spec.go_name if spec and spec.go_name else _strip_dto_suffix(model.__name__)
        return _strip_dto_suffix(model.__name__)

    def resolved_enum_name(self, enum: type[Enum]) -> str:
        spec = self.enum_specs.get(enum)
        if self.target == Target.IOS:
            return spec.swift_name if spec and spec.swift_name else f"API{enum.__name__}"
        if self.target == Target.CLI:
            return spec.go_name if spec and spec.go_name else enum.__name__
        return enum.__name__

    def validate_model_reference(self, model: type[BaseModel], *, field_path: str) -> None:
        spec = self.model_specs.get(model)
        if spec is None:
            if self.require_registry:
                raise _UnsupportedAnnotationError(
                    f"{field_path}: nested model {model.__name__} is not registered"
                )
            return
        if self.target is not None and not spec.targets & self.target:
            raise _UnsupportedAnnotationError(
                f"{field_path}: {_target_label(self.target)} model references "
                f"non-{_target_label(self.target)} model {model.__name__}"
            )

    def validate_enum_reference(self, enum: type[Enum], *, field_path: str) -> None:
        spec = self.enum_specs.get(enum)
        if spec is None:
            if self.require_registry:
                raise _UnsupportedAnnotationError(
                    f"{field_path}: enum {enum.__name__} is not registered"
                )
            return
        if self.target is not None and not spec.targets & self.target:
            raise _UnsupportedAnnotationError(
                f"{field_path}: {_target_label(self.target)} model references "
                f"non-{_target_label(self.target)} enum {enum.__name__}"
            )

    def validate_untyped_field(self, *, field_path: str) -> None:
        if self.enforce_untyped_allowlist and field_path not in self.untyped_field_allowlist:
            raise _UnsupportedAnnotationError(
                f"{field_path}: dict[str, Any] requires an allowlist entry"
            )


class _UnsupportedAnnotationError(ValueError):
    """Internal single-field type resolution failure."""


def introspect_model(
    model: type[BaseModel],
    *,
    resolver: ContractTypeResolver | None = None,
    errors: list[str] | None = None,
) -> ModelIR:
    """Convert one Pydantic model class into the emitter-neutral IR."""
    type_resolver = resolver or ContractTypeResolver.unchecked()
    type_hints = get_type_hints(model, include_extras=True)
    fields: list[FieldIR] = []
    for field_name, field in model.model_fields.items():
        default_factory = None
        if field.default_factory is not None:
            default_factory = getattr(
                field.default_factory,
                "__name__",
                repr(field.default_factory),
            )

        json_schema_extra = (
            dict(field.json_schema_extra) if isinstance(field.json_schema_extra, dict) else {}
        )
        field_path = f"{model.__name__}.{field_name}"
        annotation = type_hints.get(field_name, field.annotation)
        try:
            type_ir = _resolve_annotation(
                annotation,
                field_path=field_path,
                resolver=type_resolver,
            )
        except _UnsupportedAnnotationError as error:
            if errors is None:
                raise UnsupportedContractTypeError([str(error)]) from error
            errors.append(str(error))
            type_ir = TypeIR(kind="untyped")
        fields.append(
            FieldIR(
                python_name=field_name,
                wire_name=field.alias or field_name,
                type_ir=type_ir,
                required=field.is_required(),
                default=None if field.default is PydanticUndefined else field.default,
                default_factory=default_factory,
                description=field.description,
                json_schema_extra=json_schema_extra,
                contract_policy=contract_policy_from_extra(json_schema_extra),
            )
        )

    return ModelIR(
        name=model.__name__,
        model=model,
        fields=tuple(fields),
    )


def introspect_models(
    models: list[type[BaseModel]],
    *,
    resolver: ContractTypeResolver | None = None,
) -> list[ModelIR]:
    """Convert multiple Pydantic model classes into IR in input order."""
    return [introspect_model(model, resolver=resolver) for model in models]


def validate_contract_models(
    model_specs: list[ModelSpec],
    enum_specs: list[EnumSpec],
    *,
    untyped_field_allowlist: frozenset[str],
) -> None:
    """Fail if registered models use shapes outside the supported type table."""
    errors: list[str] = []

    for spec in model_specs:
        for target in _iter_targets(spec.targets):
            resolver = ContractTypeResolver.from_specs(
                model_specs,
                enum_specs,
                target=target,
                untyped_field_allowlist=untyped_field_allowlist,
            )
            introspect_model(spec.model, resolver=resolver, errors=errors)

    if errors:
        raise UnsupportedContractTypeError(errors)


def _resolve_annotation(
    annotation: Any,
    *,
    field_path: str,
    resolver: ContractTypeResolver,
) -> TypeIR:
    inner_annotation, optional = _split_optional(annotation)
    resolved = _resolve_non_optional_annotation(
        inner_annotation,
        field_path=field_path,
        resolver=resolver,
    )
    if optional:
        return replace(resolved, optional=True)
    return resolved


def _resolve_non_optional_annotation(
    annotation: Any,
    *,
    field_path: str,
    resolver: ContractTypeResolver,
) -> TypeIR:
    if _is_utc_datetime(annotation):
        return TypeIR(kind="datetime")
    annotation = _unwrap_annotated(annotation)
    if annotation is str:
        return TypeIR(kind="scalar", scalar="string")
    if annotation is int:
        return TypeIR(kind="scalar", scalar="int")
    if annotation is float:
        return TypeIR(kind="scalar", scalar="float")
    if annotation is bool:
        return TypeIR(kind="scalar", scalar="bool")
    if annotation is Any:
        raise _UnsupportedAnnotationError(
            f"{field_path}: Any is only supported as allowlisted dict[str, Any]"
        )

    if isinstance(annotation, type):
        if issubclass(annotation, Enum):
            resolver.validate_enum_reference(annotation, field_path=field_path)
            return TypeIR(
                kind="enum",
                name=resolver.resolved_enum_name(annotation),
            )
        if issubclass(annotation, BaseModel):
            resolver.validate_model_reference(annotation, field_path=field_path)
            return TypeIR(
                kind="model",
                name=resolver.resolved_model_name(annotation),
            )

    origin = get_origin(annotation)
    args = get_args(annotation)

    if origin in {Union, UnionType}:
        non_none_args = [arg for arg in args if arg is not type(None)]
        if len(non_none_args) == 1 and len(non_none_args) != len(args):
            resolved = _resolve_annotation(
                non_none_args[0],
                field_path=field_path,
                resolver=resolver,
            )
            return replace(resolved, optional=True)
        raise _UnsupportedAnnotationError(f"{field_path}: general unions are not supported")

    if origin is list:
        if len(args) != 1:
            raise _UnsupportedAnnotationError(
                f"{field_path}: list fields must declare one item type"
            )
        return TypeIR(
            kind="list",
            item_type=_resolve_annotation(
                args[0],
                field_path=field_path,
                resolver=resolver,
            ),
        )

    if origin is dict:
        if len(args) != 2:
            raise _UnsupportedAnnotationError(
                f"{field_path}: dict fields must declare key and value types"
            )
        key_type, value_type = args
        if key_type is not str:
            raise _UnsupportedAnnotationError(f"{field_path}: only dict[str, T] is supported")
        if value_type is Any:
            resolver.validate_untyped_field(field_path=field_path)
            return TypeIR(kind="dict", value_type=TypeIR(kind="untyped"))
        return TypeIR(
            kind="dict",
            value_type=_resolve_annotation(
                value_type,
                field_path=field_path,
                resolver=resolver,
            ),
        )

    if origin is Literal:
        raise _UnsupportedAnnotationError(
            f"{field_path}: Literal is not supported; use a registered StrEnum"
        )

    raise _UnsupportedAnnotationError(
        f"{field_path}: unsupported annotation {_format_annotation(annotation)}"
    )


def _split_optional(annotation: Any) -> tuple[Any, bool]:
    origin = get_origin(annotation)
    if origin is Annotated:
        inner_annotation = get_args(annotation)[0]
        if get_origin(inner_annotation) in {Union, UnionType}:
            return _split_optional(inner_annotation)
        return annotation, False
    if origin not in {Union, UnionType}:
        return annotation, False
    args = get_args(annotation)
    non_none_args = [arg for arg in args if arg is not type(None)]
    if len(non_none_args) == 1 and len(non_none_args) != len(args):
        return non_none_args[0], True
    return annotation, False


def _unwrap_annotated(annotation: Any) -> Any:
    while get_origin(annotation) is Annotated:
        args = get_args(annotation)
        if not args:
            raise _UnsupportedAnnotationError("Annotated type is missing an inner type")
        annotation = args[0]
    return annotation


def _is_utc_datetime(annotation: Any) -> bool:
    return annotation == UTCDateTime


def _iter_targets(targets: Target) -> tuple[Target, ...]:
    return tuple(target for target in (Target.IOS, Target.CLI) if targets & target)


def _target_label(target: Target) -> str:
    if target == Target.IOS:
        return "iOS"
    if target == Target.CLI:
        return "CLI"
    return str(target)


def _strip_dto_suffix(name: str) -> str:
    return name.removesuffix("Dto")


def _format_annotation(annotation: Any) -> str:
    name = getattr(annotation, "__name__", None)
    if name:
        return name
    return repr(annotation)
