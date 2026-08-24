from __future__ import annotations

import socket
from types import SimpleNamespace

from app.services import agent_vm_e2b_config
from app.services.agent_vm_template import AGENT_VM_TEMPLATE_NAME


def test_create_uses_one_template_and_memory_preserving_auto_pause() -> None:
    settings = SimpleNamespace(
        llm_task_sandbox_timeout_seconds=300,
        llm_task_sandbox_allow_internet_access=True,
        llm_task_sandbox_e2b_api_key="key",
        public_base_url=None,
    )

    create_kwargs = agent_vm_e2b_config.build_e2b_create_kwargs(
        user_id=4,
        vm_namespace="user:4",
        feature="chat",
        settings=settings,
        deadline=None,
    )

    assert create_kwargs["template"] == AGENT_VM_TEMPLATE_NAME
    assert create_kwargs["lifecycle"] == {"on_timeout": {"action": "pause", "keep_memory": True}}
    assert "volume_mounts" not in create_kwargs


def test_newsly_origin_is_resolved_to_cidrs_for_e2b_deny_rules(monkeypatch) -> None:
    def fake_getaddrinfo(
        hostname: str,
        port: int,
        *,
        type: socket.SocketKind,
    ) -> list[tuple[int, int, int, str, tuple[object, ...]]]:
        assert hostname == "app.newsly.example"
        assert port == 443
        assert type == socket.SOCK_STREAM
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:4700:4700::1111", 443, 0, 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
        ]

    agent_vm_e2b_config._resolved_public_host_cidrs.cache_clear()
    monkeypatch.setattr(agent_vm_e2b_config.socket, "getaddrinfo", fake_getaddrinfo)

    denied = agent_vm_e2b_config.default_network_denials("https://app.newsly.example")

    assert "app.newsly.example" not in denied
    assert "8.8.8.8/32" in denied
    assert "2606:4700:4700::1111/128" in denied
    assert "127.0.0.1/32" not in denied
    agent_vm_e2b_config._resolved_public_host_cidrs.cache_clear()


def test_newsly_origin_dns_failure_keeps_base_e2b_deny_rules(monkeypatch) -> None:
    def fail_getaddrinfo(*_args: object, **_kwargs: object) -> object:
        raise socket.gaierror("temporary DNS failure")

    agent_vm_e2b_config._resolved_public_host_cidrs.cache_clear()
    monkeypatch.setattr(agent_vm_e2b_config.socket, "getaddrinfo", fail_getaddrinfo)

    denied = agent_vm_e2b_config.default_network_denials("https://app.newsly.example")

    assert "10.0.0.0/8" in denied
    assert all("newsly.example" not in selector for selector in denied)
    agent_vm_e2b_config._resolved_public_host_cidrs.cache_clear()
