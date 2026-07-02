from __future__ import annotations

from pydantic import BaseModel

from app.models.contracts_registry import (
    CONTRACT_ENUMS,
    CONTRACT_MODELS,
    ModelSpec,
    Target,
)
from scripts.contracts_codegen.introspect import expand_contract_models


class _Nested(BaseModel):
    value: int


class _IosParent(BaseModel):
    nested: _Nested


class _CliParent(BaseModel):
    nested: _Nested


class _Grandchild(BaseModel):
    value: int


class _Child(BaseModel):
    grandchild: _Grandchild


class _TransitiveParent(BaseModel):
    child: _Child


class _OverrideParent(BaseModel):
    nested: _Nested


class _OtherNested(BaseModel):
    value: str


class _DeterministicParent(BaseModel):
    nested: _Nested
    other: _OtherNested


class _NestedA(BaseModel):
    value: int


class _NestedB(BaseModel):
    value: int


class _FirstParent(BaseModel):
    a: _NestedA
    b: _NestedB


class _SecondParent(BaseModel):
    a: _NestedA


def test_expansion_synthesizes_nested_model_with_union_of_parent_targets() -> None:
    """A nested model referenced by two explicit specs picks up their target union."""
    specs = [
        ModelSpec(_IosParent, targets=Target.IOS),
        ModelSpec(_CliParent, targets=Target.CLI),
    ]

    expanded = expand_contract_models(specs, [])
    by_model = {spec.model: spec for spec in expanded}

    assert _Nested in by_model
    assert by_model[_Nested].targets == Target.IOS | Target.CLI


def test_expansion_reaches_fixpoint_for_transitively_nested_models() -> None:
    """Grandchild models resolve and inherit targets propagated through the chain."""
    specs = [ModelSpec(_TransitiveParent, targets=Target.CLI)]

    expanded = expand_contract_models(specs, [])
    by_model = {spec.model: spec for spec in expanded}

    assert _Child in by_model
    assert _Grandchild in by_model
    assert by_model[_Child].targets == Target.CLI
    assert by_model[_Grandchild].targets == Target.CLI


def test_explicit_spec_overrides_synthesized_one() -> None:
    """An explicit spec for a nested model is never mutated by expansion."""
    explicit_nested_spec = ModelSpec(_Nested, targets=Target.CLI, swift_name="CustomNestedName")
    specs = [ModelSpec(_OverrideParent, targets=Target.IOS), explicit_nested_spec]

    expanded = expand_contract_models(specs, [])
    by_model = {spec.model: spec for spec in expanded}

    # The explicit spec object is preserved byte-for-byte: targets stay CLI-only
    # (not widened to include IOS) and the naming override survives.
    assert by_model[_Nested] is explicit_nested_spec
    assert by_model[_Nested].targets == Target.CLI
    assert by_model[_Nested].swift_name == "CustomNestedName"


def test_expansion_ordering_is_deterministic() -> None:
    """Two expansion runs over the same input produce identical ordering."""
    specs = [ModelSpec(_DeterministicParent, targets=Target.IOS)]

    first = expand_contract_models(specs, [])
    second = expand_contract_models(specs, [])

    assert [spec.model for spec in first] == [spec.model for spec in second]
    assert [spec.targets for spec in first] == [spec.targets for spec in second]


def test_expansion_orders_explicit_specs_before_synthesized_ones() -> None:
    """Output order is explicit specs in registry order, then synthesized specs
    in first-discovery order (registry order, then field order)."""
    explicit_specs = [
        ModelSpec(_FirstParent, targets=Target.IOS),
        ModelSpec(_SecondParent, targets=Target.CLI),
    ]

    expanded = expand_contract_models(explicit_specs, [])

    assert [spec.model for spec in expanded] == [
        _FirstParent,
        _SecondParent,
        _NestedA,
        _NestedB,
    ]


def test_expansion_over_real_registry_does_not_lose_or_duplicate_models() -> None:
    """Expanding the checked-in registry should still cover every explicit spec once,
    and never register the same model twice."""
    expanded = expand_contract_models(CONTRACT_MODELS, CONTRACT_ENUMS)
    models = [spec.model for spec in expanded]

    assert len(models) == len(set(models))
    explicit_models = {spec.model for spec in CONTRACT_MODELS}
    assert explicit_models.issubset(set(models))
