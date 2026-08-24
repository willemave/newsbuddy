"""E2B sandbox creation and network policy construction."""

from __future__ import annotations

import ipaddress
import socket
from functools import lru_cache
from typing import Any
from urllib.parse import urlparse

from app.core.logging import get_logger
from app.services.agent_vm_io import remaining_deadline_seconds
from app.services.agent_vm_template import AGENT_VM_TEMPLATE_NAME

logger = get_logger(__name__)


def build_e2b_create_kwargs(
    *,
    user_id: int,
    vm_namespace: str,
    feature: str,
    settings: Any,
    deadline: float | None,
    template: str = AGENT_VM_TEMPLATE_NAME,
) -> dict[str, Any]:
    """Build one secure, auto-pausing E2B create request."""
    create_kwargs: dict[str, Any] = {
        "timeout": settings.llm_task_sandbox_timeout_seconds,
        "allow_internet_access": bool(
            user_id > 0 and settings.llm_task_sandbox_allow_internet_access
        ),
        "network": {
            "deny_out": default_network_denials(settings.public_base_url),
            "allow_public_traffic": False,
        },
        "lifecycle": {
            "on_timeout": {"action": "pause", "keep_memory": True},
        },
        "envs": {"NEWSLY_USER_ID": str(user_id)},
        "api_key": settings.llm_task_sandbox_e2b_api_key,
        "template": template,
        "metadata": {
            "feature": feature,
            "user_id": str(user_id),
            "vm_namespace": vm_namespace,
        },
    }
    request_timeout = remaining_deadline_seconds(deadline)
    if request_timeout is not None:
        create_kwargs["request_timeout"] = request_timeout
    return create_kwargs


def default_network_denials(public_base_url: object) -> list[str]:
    denied = [
        "10.0.0.0/8",
        "100.64.0.0/10",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "fc00::/7",
        "fe80::/10",
    ]
    if public_base_url:
        hostname = urlparse(str(public_base_url)).hostname
        if hostname:
            denied.extend(_resolved_public_host_cidrs(hostname.lower()))
    return denied


@lru_cache(maxsize=32)
def _resolved_public_host_cidrs(hostname: str) -> tuple[str, ...]:
    """Resolve a trusted public origin to selectors accepted by E2B deny rules.

    E2B's current control plane accepts hostnames in ``allow_out`` but rejects
    them in ``deny_out`` despite the SDK type documentation. Resolve the
    configured Newsly origin on the host so a production create request remains
    valid while still blocking the addresses serving that origin.
    """
    try:
        address_info = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        logger.warning(
            "Unable to resolve public app host for agent VM deny policy",
            extra={"hostname": hostname, "failure_class": type(exc).__name__},
        )
        return ()

    selectors: set[str] = set()
    for item in address_info:
        try:
            address = ipaddress.ip_address(item[4][0])
        except ValueError:
            continue
        # Never break Chromium's sandbox-local loopback. Production origins are
        # required to be HTTPS and should resolve to globally routable addresses.
        if not address.is_global:
            continue
        prefix_length = 32 if address.version == 4 else 128
        selectors.add(f"{address}/{prefix_length}")
    return tuple(sorted(selectors))
