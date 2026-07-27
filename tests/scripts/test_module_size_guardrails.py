"""Tests for automatic module-size coverage."""

from pathlib import Path

from scripts.check_module_size_guardrails import DEFAULT_MAX_LINES, discover_guardrails


def test_discover_guardrails_covers_new_python_and_swift_modules(tmp_path: Path) -> None:
    python_module = tmp_path / "app/services/new_service.py"
    swift_module = tmp_path / "client/newsly/newsly/Services/NewService.swift"
    python_module.parent.mkdir(parents=True)
    swift_module.parent.mkdir(parents=True)
    python_module.write_text("pass\n", encoding="utf-8")
    swift_module.write_text("struct NewService {}\n", encoding="utf-8")

    guardrails = discover_guardrails(tmp_path, {})

    assert guardrails["app/services/new_service.py"] == DEFAULT_MAX_LINES
    assert guardrails["client/newsly/newsly/Services/NewService.swift"] == DEFAULT_MAX_LINES


def test_discover_guardrails_preserves_ratchets_and_ignores_generated_swift(
    tmp_path: Path,
) -> None:
    ratcheted_module = tmp_path / "app/services/ratcheted.py"
    generated_module = tmp_path / "client/newsly/newsly/Models/Generated/APIModels.generated.swift"
    ratcheted_module.parent.mkdir(parents=True)
    generated_module.parent.mkdir(parents=True)
    ratcheted_module.write_text("pass\n", encoding="utf-8")
    generated_module.write_text("struct Generated {}\n", encoding="utf-8")

    guardrails = discover_guardrails(
        tmp_path,
        {"app/services/ratcheted.py": 42},
    )

    assert guardrails["app/services/ratcheted.py"] == 42
    assert "client/newsly/newsly/Models/Generated/APIModels.generated.swift" not in guardrails
