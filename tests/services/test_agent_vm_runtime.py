from pathlib import PurePosixPath

import pytest

from app.services.agent_vm_runtime import (
    SYSTEM_USER_ID,
    AgentVmError,
    AgentVmPathError,
    resolve_sandbox_user_id,
    resolve_workspace_relative_path,
)

WORKSPACE_ROOT = PurePosixPath("/tmp/newsly/tasks/55")


@pytest.mark.parametrize("value", [None, False, True, 0, -1, "12", 12.0])
def test_resolve_sandbox_user_id_uses_system_namespace_for_invalid_values(
    value: object,
) -> None:
    assert resolve_sandbox_user_id(value) == SYSTEM_USER_ID


def test_resolve_sandbox_user_id_preserves_positive_integer() -> None:
    assert resolve_sandbox_user_id(12) == 12


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("", "."),
        (".", "."),
        (" output/source-notes.md ", "output/source-notes.md"),
        ("./output/index.html", "output/index.html"),
        ("/tmp/newsly/tasks/55", "."),
        ("/tmp/newsly/tasks/55/output/source-notes.md", "output/source-notes.md"),
    ],
)
def test_resolve_workspace_relative_path_is_canonical_and_idempotent(
    path: str,
    expected: str,
) -> None:
    resolved = resolve_workspace_relative_path(path, workspace_root=WORKSPACE_ROOT)

    assert resolved.as_posix() == expected
    assert (
        resolve_workspace_relative_path(
            resolved.as_posix(),
            workspace_root=WORKSPACE_ROOT,
        )
        == resolved
    )


@pytest.mark.parametrize(
    "path",
    [
        "/tmp/newsly/tasks/550/output/index.html",
        "/tmp/newsly/tasks/54/output/index.html",
        "/tmp/newsly/users/1/shared/context.md",
        "/etc/passwd",
        "../output/index.html",
        "output/../input/source.txt",
    ],
)
def test_resolve_workspace_relative_path_rejects_paths_outside_workspace(path: str) -> None:
    with pytest.raises(AgentVmPathError, match="workspace-relative paths"):
        resolve_workspace_relative_path(path, workspace_root=WORKSPACE_ROOT)


def test_resolve_workspace_relative_path_requires_absolute_workspace_root() -> None:
    with pytest.raises(AgentVmError, match="Invalid VM workspace root"):
        resolve_workspace_relative_path(
            "output/index.html",
            workspace_root=PurePosixPath("tmp/newsly/tasks/55"),
        )
