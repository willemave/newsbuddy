from types import SimpleNamespace

import pytest

from app.services.agent_vm_runtime import AgentVmError
from app.services.agent_vm_security import harden_canonical_agent_vm


def test_canonical_sandbox_revokes_default_user_sudo_as_root() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def run(command: str, **kwargs: object) -> SimpleNamespace:
        calls.append((command, kwargs))
        return SimpleNamespace(exit_code=0, stderr="")

    harden_canonical_agent_vm(
        SimpleNamespace(commands=SimpleNamespace(run=run)),
        request_timeout_seconds=12.0,
    )

    command, kwargs = calls[0]
    assert "NOPASSWD" in command
    assert "gpasswd -d user sudo" in command
    assert kwargs == {"user": "root", "timeout": 12.0, "request_timeout": 12.0}


def test_canonical_sandbox_rejects_failed_sudo_revocation() -> None:
    sandbox = SimpleNamespace(
        commands=SimpleNamespace(
            run=lambda *_args, **_kwargs: SimpleNamespace(
                exit_code=1,
                stderr="default user still has passwordless sudo",
            )
        )
    )

    with pytest.raises(AgentVmError, match="Unable to revoke agent VM root access"):
        harden_canonical_agent_vm(sandbox, request_timeout_seconds=None)
