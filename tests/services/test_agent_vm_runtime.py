import pytest

from app.services.agent_vm_runtime import SYSTEM_USER_ID, resolve_sandbox_user_id


@pytest.mark.parametrize("value", [None, False, True, 0, -1, "12", 12.0])
def test_resolve_sandbox_user_id_uses_system_namespace_for_invalid_values(
    value: object,
) -> None:
    assert resolve_sandbox_user_id(value) == SYSTEM_USER_ID


def test_resolve_sandbox_user_id_preserves_positive_integer() -> None:
    assert resolve_sandbox_user_id(12) == 12
