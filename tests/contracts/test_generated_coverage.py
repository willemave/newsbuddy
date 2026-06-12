from __future__ import annotations

import json
import re
from pathlib import Path

MANIFEST_PATH = Path("tests/contracts/ios_handrolled_wire_models_allowlist.json")
GENERATED_MODELS_PATH = Path("client/newsly/newsly/Models/Generated/APIModels.generated.swift")
SCAN_ROOTS = (
    Path("client/newsly/newsly/Models"),
    Path("client/newsly/newsly/Repositories"),
    Path("client/newsly/newsly/Services"),
)
SWIFT_DECLARATION_RE = re.compile(
    r"^\s*"
    r"(?:public\s+|private\s+|fileprivate\s+|final\s+|indirect\s+)*"
    r"(struct|enum|class)\s+(\w+)\s*:\s*([^{]+)"
)
GENERATED_MODEL_RE = re.compile(r"^struct\s+(API\w+):\s+Codable", re.MULTILINE)
CONTRACT_CONFORMANCES = ("Codable", "Decodable", "Encodable")
DERIVED_NAME_SUFFIXES = ("Response", "Request", "Dto")
MANUAL_NAME_OVERRIDES = {
    # The app-facing podcast search result has a shorter historical name.
    "PodcastSearchResult",
}


def test_ios_handrolled_wire_model_manifest_is_current() -> None:
    """Manual iOS wire-model overlap with generated API models should only shrink."""
    manifest = json.loads(MANIFEST_PATH.read_text())
    expected = manifest["models"]
    actual = _handrolled_wire_model_entries()

    assert expected == sorted(set(expected)), "manifest entries must be unique and sorted"
    assert actual == expected


def _handrolled_wire_model_entries() -> list[str]:
    generated_names = _generated_api_model_names()
    entries: set[str] = set()

    for root in SCAN_ROOTS:
        for path in sorted(root.rglob("*.swift")):
            if "Generated" in path.parts:
                continue
            relative_path = path.as_posix()
            for line in path.read_text().splitlines():
                match = SWIFT_DECLARATION_RE.match(line)
                if not match:
                    continue

                name = match.group(2)
                conformances = match.group(3)
                if name not in generated_names:
                    continue
                if not any(conformance in conformances for conformance in CONTRACT_CONFORMANCES):
                    continue
                entries.add(f"{relative_path}:{name}")

    return sorted(entries)


def _generated_api_model_names() -> set[str]:
    names: set[str] = set(MANUAL_NAME_OVERRIDES)
    generated_text = GENERATED_MODELS_PATH.read_text()

    for match in GENERATED_MODEL_RE.finditer(generated_text):
        api_name = match.group(1)
        if not api_name.startswith("API"):
            continue

        name = api_name.removeprefix("API")
        names.add(name)
        for suffix in DERIVED_NAME_SUFFIXES:
            if name.endswith(suffix):
                names.add(name[: -len(suffix)])

    return names
