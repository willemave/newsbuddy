"""Regenerate all public contract artifacts in a single process.

Imports the FastAPI app once and writes all four generated artifacts:

- docs/library/reference/openapi.json
- client/newsly/newsly/Models/Generated/APIContracts.generated.swift
- client/newsly/newsly/Models/Generated/APIModels.generated.swift
- cli/openapi/agent-openapi.json
- cli/internal/api/contracts_gen.go

Supports ``--check``: artifacts are written to a temp directory and byte-diffed
against the checked-in files, exiting non-zero on drift.
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.core.settings import get_settings  # noqa: E402
from scripts.contracts_codegen.go_emitter import build_go_contracts  # noqa: E402
from scripts.contracts_codegen.swift_emitter import (  # noqa: E402
    build_swift_contracts,
    build_swift_models,
)
from scripts.export_agent_openapi_schema import build_agent_openapi_schema  # noqa: E402

DEFAULT_OPENAPI_PATH = REPO_ROOT / "docs/library/reference/openapi.json"
DEFAULT_IOS_CONTRACTS_PATH = (
    REPO_ROOT / "client/newsly/newsly/Models/Generated/APIContracts.generated.swift"
)
DEFAULT_IOS_MODELS_PATH = (
    REPO_ROOT / "client/newsly/newsly/Models/Generated/APIModels.generated.swift"
)
DEFAULT_AGENT_OPENAPI_PATH = REPO_ROOT / "cli/openapi/agent-openapi.json"
DEFAULT_GO_CONTRACTS_PATH = REPO_ROOT / "cli/internal/api/contracts_gen.go"


@dataclass(frozen=True)
class ContractArtifact:
    """A single generated artifact: its checked-in path and rendered contents."""

    label: str
    checked_in_path: Path
    contents: str


def _ensure_runtime_directories() -> None:
    """Create runtime directories needed by app imports in fresh checkouts."""
    settings = get_settings()
    settings.images_base_dir.resolve().mkdir(parents=True, exist_ok=True)


def _build_openapi_contents() -> str:
    from app.main import app

    return json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"


def _build_agent_openapi_contents() -> str:
    return json.dumps(build_agent_openapi_schema(), indent=2, sort_keys=True) + "\n"


@dataclass(frozen=True)
class ContractArtifactSpec:
    """One buildable artifact: its label, checked-in path, and lazy builder."""

    label: str
    checked_in_path: Path
    build: Callable[[], str]


# Split by check_public_contracts.sh mode: a --go-only CI job must never fail on a
# Swift/OpenAPI generator problem, and vice versa, so builders only run when selected.
PYTHON_ARTIFACT_SPECS: tuple[ContractArtifactSpec, ...] = (
    ContractArtifactSpec("OpenAPI schema", DEFAULT_OPENAPI_PATH, _build_openapi_contents),
    ContractArtifactSpec("iOS Swift contracts", DEFAULT_IOS_CONTRACTS_PATH, build_swift_contracts),
    ContractArtifactSpec("iOS Swift models", DEFAULT_IOS_MODELS_PATH, build_swift_models),
)
GO_ARTIFACT_SPECS: tuple[ContractArtifactSpec, ...] = (
    ContractArtifactSpec(
        "Agent OpenAPI schema", DEFAULT_AGENT_OPENAPI_PATH, _build_agent_openapi_contents
    ),
    ContractArtifactSpec("Go CLI contracts", DEFAULT_GO_CONTRACTS_PATH, build_go_contracts),
)


def build_contract_artifacts(
    specs: tuple[ContractArtifactSpec, ...] = PYTHON_ARTIFACT_SPECS + GO_ARTIFACT_SPECS,
) -> list[ContractArtifact]:
    """Import the FastAPI app once and render the selected contract artifacts.

    Returns:
        Rendered artifacts, each paired with its checked-in destination path.
    """
    _ensure_runtime_directories()
    return [
        ContractArtifact(
            label=spec.label,
            checked_in_path=spec.checked_in_path,
            contents=spec.build(),
        )
        for spec in specs
    ]


def _write_artifacts(artifacts: list[ContractArtifact], destinations: dict[Path, Path]) -> None:
    """Write each artifact's contents to its resolved destination path."""
    for artifact in artifacts:
        destination = destinations[artifact.checked_in_path]
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(artifact.contents, encoding="utf-8")


def _report_drift(artifact: ContractArtifact, actual_path: Path) -> None:
    """Print a diff plus an actionable message for one drifted artifact."""
    expected_text = (
        artifact.checked_in_path.read_text(encoding="utf-8")
        if artifact.checked_in_path.exists()
        else ""
    )
    actual_text = actual_path.read_text(encoding="utf-8")
    diff = difflib.unified_diff(
        expected_text.splitlines(keepends=True),
        actual_text.splitlines(keepends=True),
        fromfile=str(artifact.checked_in_path),
        tofile=f"regenerated ({artifact.label})",
    )
    print(f"Contract drift detected: {artifact.checked_in_path}")
    sys.stdout.writelines(diff)


def run_check(artifacts: list[ContractArtifact]) -> int:
    """Write artifacts to a temp dir and diff against checked-in files.

    Returns:
        0 if all artifacts match the checked-in files, 1 if any drifted.
    """
    with tempfile.TemporaryDirectory(prefix="regenerate_public_contracts_") as tmp:
        tmp_root = Path(tmp)
        destinations = {
            artifact.checked_in_path: tmp_root / f"{index:02d}_{artifact.checked_in_path.name}"
            for index, artifact in enumerate(artifacts)
        }
        _write_artifacts(artifacts, destinations)

        drifted = False
        for artifact in artifacts:
            actual_path = destinations[artifact.checked_in_path]
            checked_in_path = artifact.checked_in_path
            unchanged = checked_in_path.exists() and (
                actual_path.read_bytes() == checked_in_path.read_bytes()
            )
            if unchanged:
                continue
            drifted = True
            _report_drift(artifact, actual_path)

        if drifted:
            print(
                "Contract artifacts are stale. Run scripts/regenerate_public_contracts.sh "
                "and commit the resulting diff."
            )
            return 1

    print("Public contract artifacts are up to date.")
    return 0


def run_write(artifacts: list[ContractArtifact]) -> int:
    """Write artifacts directly to their checked-in paths."""
    destinations = {artifact.checked_in_path: artifact.checked_in_path for artifact in artifacts}
    _write_artifacts(artifacts, destinations)
    for artifact in artifacts:
        print(f"Wrote {artifact.label}: {artifact.checked_in_path}")
    return 0


def parse_args() -> argparse.Namespace:
    """Parse script arguments."""
    parser = argparse.ArgumentParser(
        description="Regenerate all public contract artifacts in a single process"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Write to a temp dir and diff against checked-in artifacts instead of writing them",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--python-only",
        action="store_true",
        help="Only handle the OpenAPI schema and iOS Swift artifacts",
    )
    group.add_argument(
        "--go-only",
        action="store_true",
        help="Only handle the agent OpenAPI schema and Go CLI contract artifacts",
    )
    return parser.parse_args()


def main() -> int:
    """CLI entrypoint."""
    args = parse_args()
    if args.python_only:
        specs = PYTHON_ARTIFACT_SPECS
    elif args.go_only:
        specs = GO_ARTIFACT_SPECS
    else:
        specs = PYTHON_ARTIFACT_SPECS + GO_ARTIFACT_SPECS
    artifacts = build_contract_artifacts(specs)
    if args.check:
        return run_check(artifacts)
    return run_write(artifacts)


if __name__ == "__main__":
    raise SystemExit(main())
