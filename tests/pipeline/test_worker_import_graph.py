"""Guards on what the worker import graph is allowed to pull in.

Importing torch costs ~450 MB of RSS in every worker process. Only the media
queue actually transcribes, so torch/whisper must stay behind lazy imports.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

HEAVY_MODULES = (
    "torch",
    "transformers",
    "sentence_transformers",
    "faster_whisper",
    "ctranslate2",
)

_PROBE = """
import json
import sys

import app.pipeline.sequential_task_processor  # noqa: F401

print(json.dumps([name for name in {modules!r} if name in sys.modules]))
"""


def _imported_heavy_modules(module_names: tuple[str, ...]) -> list[str]:
    """Import the processor in a clean interpreter and report heavy imports."""
    result = subprocess.run(
        [sys.executable, "-c", _PROBE.format(modules=module_names)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_task_processor_import_does_not_load_torch() -> None:
    """The shared processor module must not drag ML runtimes into every worker."""
    assert _imported_heavy_modules(HEAVY_MODULES) == []
