"""Privilege hardening applied before a canonical E2B sandbox reaches an agent."""

from __future__ import annotations

from typing import Any

from app.services.agent_vm_runtime import AgentVmError

_REVOKE_DEFAULT_USER_SUDO = r"""set -eu
sed -i '/^user[[:space:]].*NOPASSWD:[[:space:]]*ALL[[:space:]]*$/d' /etc/sudoers
if id -nG user | tr ' ' '\n' | grep -qx sudo; then
  gpasswd -d user sudo >/dev/null
fi
if su -s /bin/sh user -c 'sudo -n true' >/dev/null 2>&1; then
  echo 'default user still has passwordless sudo' >&2
  exit 1
fi
"""


def harden_canonical_agent_vm(
    sandbox: Any,
    *,
    request_timeout_seconds: float | None,
) -> None:
    """Remove E2B's build-time sudo grant before corpus or model access."""
    result = sandbox.commands.run(
        _REVOKE_DEFAULT_USER_SUDO,
        user="root",
        timeout=min(30.0, request_timeout_seconds or 30.0),
        request_timeout=request_timeout_seconds,
    )
    exit_code = int(getattr(result, "exit_code", getattr(result, "exitCode", 0)) or 0)
    if exit_code != 0:
        stderr = str(getattr(result, "stderr", "") or "")
        raise AgentVmError(f"Unable to revoke agent VM root access: {stderr[:1000]}")
