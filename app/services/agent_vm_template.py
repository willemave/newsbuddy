"""Canonical E2B runtime identity for every Newsly agent VM."""

from hashlib import sha256
from pathlib import Path

AGENT_VM_TEMPLATE_NAME = "newsly-agent"
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_RUNTIME_INPUTS = (
    _REPOSITORY_ROOT / "e2b.Dockerfile",
    Path(__file__).with_name("agent_vm_security.py"),
    Path(__file__).with_name("agent_vm_corpus.py"),
)


def build_agent_vm_template_revision(paths: tuple[Path, ...] = _RUNTIME_INPUTS) -> str:
    """Hash every input that affects a reusable sandbox or snapshot."""
    digest = sha256()
    for path in paths:
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return f"{AGENT_VM_TEMPLATE_NAME}-{digest.hexdigest()[:16]}"


AGENT_VM_TEMPLATE_REVISION = build_agent_vm_template_revision()
