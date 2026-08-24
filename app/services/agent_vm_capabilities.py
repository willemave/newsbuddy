"""Capability probes for E2B-backed agent VM sessions."""

from __future__ import annotations

import json
from typing import Any

from app.services.agent_vm_runtime import AgentVmError

AGENT_VM_CAPABILITY_PROBE = r"""python - <<'PY'
import json
import shutil
import subprocess

capabilities = {
    name: shutil.which(name) or False
    for name in ("bash", "python", "node", "git", "curl", "jq", "rg")
}
browser_error = None
if capabilities["node"]:
    try:
        browser_probe = subprocess.run(
            [
                "node",
                "-e",
                "const { chromium } = require('playwright'); "
                "(async () => { const browser = await chromium.launch({headless:true}); "
                "await browser.close(); })().catch(error => { console.error(error); "
                "process.exit(1); });",
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        browser_ready = browser_probe.returncode == 0
        if not browser_ready:
            browser_output = (browser_probe.stderr or browser_probe.stdout).strip()
            browser_error = (
                "Node Playwright package is unavailable"
                if "Cannot find module 'playwright'" in browser_output
                else browser_output[:1000]
            )
    except (OSError, subprocess.TimeoutExpired) as exc:
        browser_ready = False
        browser_error = f"{type(exc).__name__}: {exc}"
else:
    browser_ready = False
    browser_error = "Node.js is unavailable"

capabilities["playwright"] = browser_ready
capabilities["chromium"] = browser_ready
if browser_error:
    capabilities["browser_validation_error"] = browser_error
print(json.dumps(capabilities, sort_keys=True))
PY"""


def probe_agent_vm_template(
    sandbox: Any,
    *,
    request_timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """Require the generic capabilities from the canonical E2B template.

    The template intentionally has no Newsly-specific helper or credential
    bootstrap. Its revision is the cache key; the probe itself is portable.
    """
    result = sandbox.commands.run(
        AGENT_VM_CAPABILITY_PROBE,
        timeout=min(30.0, request_timeout_seconds or 30.0),
        request_timeout=request_timeout_seconds,
    )
    if int(getattr(result, "exit_code", getattr(result, "exitCode", 0)) or 0) != 0:
        raise AgentVmError("Agent VM template capability probe failed")
    try:
        payload = json.loads(str(getattr(result, "stdout", "") or ""))
    except (TypeError, ValueError) as exc:
        raise AgentVmError("Agent VM template returned an invalid capability manifest") from exc
    if not isinstance(payload, dict):
        raise AgentVmError("Agent VM template returned an invalid capability manifest")
    required = {
        "bash",
        "python",
        "node",
        "git",
        "curl",
        "jq",
        "rg",
        "chromium",
        "playwright",
    }
    missing = sorted(name for name in required if not payload.get(name))
    if missing:
        raise AgentVmError(f"Agent VM template is missing capabilities: {', '.join(missing)}")
    return payload
