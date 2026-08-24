"""Validate or build the Newsly agent VM template with the installed E2B SDK."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from e2b import Template

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.settings import get_settings  # noqa: E402
from app.services.agent_vm_template import (  # noqa: E402
    AGENT_VM_TEMPLATE_NAME,
    AGENT_VM_TEMPLATE_REVISION,
)

DOCKERFILE_PATH = PROJECT_ROOT / "e2b.Dockerfile"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cpu-count", type=int, default=2)
    parser.add_argument("--memory-mb", type=int, default=2048)
    parser.add_argument("--skip-cache", action="store_true")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate the local Dockerfile/template definition without contacting E2B.",
    )
    args = parser.parse_args()

    if not DOCKERFILE_PATH.is_file():
        raise SystemExit(f"Missing template Dockerfile: {DOCKERFILE_PATH}")
    template = Template(file_context_path=PROJECT_ROOT).from_dockerfile(DOCKERFILE_PATH.name)
    if args.check:
        print(
            f"Validated {DOCKERFILE_PATH.name} for template "
            f"{AGENT_VM_TEMPLATE_NAME} ({AGENT_VM_TEMPLATE_REVISION})"
        )
        return

    api_key = (get_settings().llm_task_sandbox_e2b_api_key or "").strip()
    if not api_key:
        raise SystemExit("LLM_TASK_SANDBOX_E2B_API_KEY or E2B_API_KEY must be configured")

    build = Template.build(
        template,
        AGENT_VM_TEMPLATE_NAME,
        api_key=api_key,
        cpu_count=max(1, args.cpu_count),
        memory_mb=max(512, args.memory_mb),
        skip_cache=args.skip_cache,
        on_build_logs=lambda entry: print(entry.message),
    )
    print(
        json.dumps(
            {
                "template_id": getattr(build, "template_id", None),
                "build_id": getattr(build, "build_id", None),
                "template_name": AGENT_VM_TEMPLATE_NAME,
                "template_revision": AGENT_VM_TEMPLATE_REVISION,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
