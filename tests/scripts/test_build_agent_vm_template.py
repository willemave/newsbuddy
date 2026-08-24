from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.agent_vm_template import (
    AGENT_VM_TEMPLATE_NAME,
    AGENT_VM_TEMPLATE_REVISION,
    build_agent_vm_template_revision,
)
from scripts import build_agent_vm_template


def test_live_template_build_uses_application_e2b_key(monkeypatch, capsys) -> None:
    build_calls: list[dict[str, object]] = []
    build_names: list[str] = []

    def fake_build(_template, name, **kwargs):
        build_names.append(name)
        build_calls.append(kwargs)
        return SimpleNamespace(template_id="template-1", build_id="build-1")

    monkeypatch.setattr(
        build_agent_vm_template,
        "get_settings",
        lambda: SimpleNamespace(llm_task_sandbox_e2b_api_key="settings-key"),
    )
    monkeypatch.setattr(build_agent_vm_template.Template, "build", fake_build)
    monkeypatch.setattr(
        "sys.argv",
        ["build_agent_vm_template.py"],
    )

    build_agent_vm_template.main()

    assert build_calls[0]["api_key"] == "settings-key"
    assert build_names == [AGENT_VM_TEMPLATE_NAME]
    assert '"template_id": "template-1"' in capsys.readouterr().out


def test_live_template_build_requires_configured_e2b_key(monkeypatch) -> None:
    monkeypatch.setattr(
        build_agent_vm_template,
        "get_settings",
        lambda: SimpleNamespace(llm_task_sandbox_e2b_api_key=None),
    )
    monkeypatch.setattr(
        "sys.argv",
        ["build_agent_vm_template.py"],
    )

    with pytest.raises(SystemExit, match="E2B_API_KEY must be configured"):
        build_agent_vm_template.main()


def test_agent_template_exposes_playwright_on_runtime_lookup_paths() -> None:
    dockerfile = Path(build_agent_vm_template.DOCKERFILE_PATH).read_text(encoding="utf-8")

    assert "FROM e2bdev/code-interpreter@sha256:" in dockerfile
    assert "npm install --save-exact playwright@1.62.1" in dockerfile
    assert "ln -s /opt/newsly-agent/node_modules /node_modules" in dockerfile
    assert "ln -s /opt/ms-playwright /home/user/.cache/ms-playwright" in dockerfile
    assert "chown root:root /data" in dockerfile
    assert "chown user:user /data/workspace" in dockerfile


def test_agent_template_revision_covers_all_reusable_runtime_inputs() -> None:
    source_root = Path(build_agent_vm_template.DOCKERFILE_PATH).parent
    runtime_inputs = (
        Path(build_agent_vm_template.DOCKERFILE_PATH),
        source_root / "app/services/agent_vm_security.py",
        source_root / "app/services/agent_vm_corpus.py",
    )

    assert build_agent_vm_template_revision(runtime_inputs) == AGENT_VM_TEMPLATE_REVISION


def test_agent_template_revision_changes_with_each_runtime_input(tmp_path: Path) -> None:
    runtime_inputs = tuple(tmp_path / name for name in ("Dockerfile", "security.py", "corpus.py"))
    for path in runtime_inputs:
        path.write_text(path.name, encoding="utf-8")
    baseline = build_agent_vm_template_revision(runtime_inputs)

    for path in runtime_inputs:
        original = path.read_text(encoding="utf-8")
        path.write_text(f"{original}-changed", encoding="utf-8")
        assert build_agent_vm_template_revision(runtime_inputs) != baseline
        path.write_text(original, encoding="utf-8")
