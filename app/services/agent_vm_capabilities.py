"""Capability probes for E2B-backed agent VM sessions."""

from __future__ import annotations

import json
from typing import Any

from app.services.agent_vm_runtime import AgentVmError

E2B_DEFAULT_CAPABILITY_PROBE = r"""python - <<'PY'
import json
import shutil
import subprocess

capabilities = {
    name: shutil.which(name) or False
    for name in ("bash", "python", "node", "git", "curl", "jq")
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


def probe_configured_e2b_sandbox(
    sandbox: Any,
    *,
    request_timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """Require the declared capabilities from a configured E2B template."""
    result = sandbox.commands.run(
        "newsly-sandbox-probe --json",
        timeout=min(30.0, request_timeout_seconds or 30.0),
        request_timeout=request_timeout_seconds,
    )
    if int(getattr(result, "exit_code", getattr(result, "exitCode", 0)) or 0) != 0:
        raise AgentVmError("Configured E2B template failed newsly-sandbox-probe")
    try:
        payload = json.loads(str(getattr(result, "stdout", "") or ""))
    except (TypeError, ValueError) as exc:
        raise AgentVmError(
            "Configured E2B template returned an invalid capability manifest"
        ) from exc
    required = {"bash", "python", "node", "git", "curl", "jq", "chromium", "playwright"}
    missing = sorted(name for name in required if not payload.get(name))
    if missing:
        raise AgentVmError(f"Configured E2B template is missing capabilities: {', '.join(missing)}")
    return payload


def probe_default_e2b_sandbox(
    sandbox: Any,
    *,
    request_timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """Inspect the provider default so feature code never mistakes unknown for capable."""
    result = sandbox.commands.run(
        E2B_DEFAULT_CAPABILITY_PROBE,
        timeout=min(30.0, request_timeout_seconds or 30.0),
        request_timeout=request_timeout_seconds,
    )
    if int(getattr(result, "exit_code", getattr(result, "exitCode", 0)) or 0) != 0:
        raise AgentVmError("Default E2B sandbox capability probe failed")
    try:
        payload = json.loads(str(getattr(result, "stdout", "") or ""))
    except (TypeError, ValueError) as exc:
        raise AgentVmError("Default E2B sandbox returned invalid capabilities") from exc
    if not isinstance(payload, dict):
        raise AgentVmError("Default E2B sandbox returned invalid capabilities")
    return payload
