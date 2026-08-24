from __future__ import annotations

import ast
import base64
import json
from pathlib import Path
from time import monotonic
from typing import cast

import httpx
import pytest

from app.services.agent_vm_runtime import (
    SYSTEM_USER_ID,
    AgentCommandResult,
    AgentVmDeadlineExceeded,
    AgentVmLease,
    AgentVmSession,
)
from app.services.feed_research_runtime import (
    FeedResearchCandidateError,
    FeedResearchDeadlineExceeded,
    FeedResearchRuntimeError,
    SandboxFeedHttpService,
    feed_research_runtime,
)
from app.services.http import HttpService

RSS_BODY = b"<?xml version='1.0'?><rss><channel><title>Example</title></channel></rss>"


def _row(
    url: str,
    *,
    status: int = 200,
    body: bytes = RSS_BODY,
    headers: bytes = b"HTTP/2 200\r\nContent-Type: application/rss+xml\r\n\r\n",
    curl_exit: int = 0,
    stderr: str = "",
    effective_url: str | None = None,
) -> dict[str, object]:
    return {
        "index": 0,
        "url": url,
        "effective_url": effective_url or url,
        "status": status,
        "headers_b64": base64.b64encode(headers).decode(),
        "body_b64": base64.b64encode(body).decode(),
        "curl_exit": curl_exit,
        "stderr": stderr,
    }


class _FakeFeedSandbox:
    provider = "e2b"
    sandbox_id = "sandbox-feed-test"
    lease = AgentVmLease(
        provider="e2b",
        vm_namespace=f"user:{SYSTEM_USER_ID}",
        sandbox_id=sandbox_id,
        reuse_scope="persistent_user",
        reused=False,
    )

    def __init__(self, outputs: list[object] | None = None) -> None:
        self.outputs = list(outputs or [])
        self.commands: list[dict[str, object]] = []
        self.allowed_hosts: list[list[str]] = []
        self.reset_calls = 0
        self.closed = False

    def execute_bash(
        self,
        command: str,
        *,
        timeout_seconds: float | None = None,
        max_output_chars: int | None = None,
    ) -> AgentCommandResult:
        self.commands.append(
            {
                "command": command,
                "timeout_seconds": timeout_seconds,
                "max_output_chars": max_output_chars,
            }
        )
        output = self.outputs.pop(0) if self.outputs else []
        if isinstance(output, Exception):
            raise output
        if isinstance(output, AgentCommandResult):
            return output
        assert isinstance(output, list)
        return AgentCommandResult(
            stdout="\n".join(json.dumps(row) for row in output),
            stderr="",
            exit_code=0,
        )

    def set_allowed_outbound_hosts(self, hosts: list[str]) -> None:
        self.allowed_hosts.append(hosts)

    def reset_network_policy(self) -> None:
        self.reset_calls += 1

    def close(self) -> None:
        self.closed = True


def test_sandbox_feed_http_service_returns_batch_output_without_scratch_io() -> None:
    url = "https://example.com/blog"
    sandbox = _FakeFeedSandbox(
        [
            [
                _row(
                    url,
                    headers=(
                        b"HTTP/1.1 301 Moved Permanently\r\nLocation: /feed.xml\r\n\r\n"
                        b"HTTP/2 200\r\nContent-Type: application/rss+xml\r\n"
                        b"Content-Encoding: gzip\r\nContent-Length: 999\r\n\r\n"
                    ),
                    effective_url="https://example.com/feed.xml",
                )
            ]
        ]
    )
    service = SandboxFeedHttpService(sandbox)  # type: ignore[arg-type]

    response = service.fetch(url)

    assert response.status_code == 200
    assert response.url == httpx.URL("https://example.com/feed.xml")
    assert response.headers["content-type"] == "application/rss+xml"
    assert "content-encoding" not in response.headers
    assert response.headers["content-length"] == str(len(response.content))
    assert b"<rss>" in response.content
    assert len(sandbox.commands) == 1
    assert "ThreadPoolExecutor" in str(sandbox.commands[0]["command"])
    assert sandbox.allowed_hosts == [["example.com"]]
    assert sandbox.reset_calls == 1


def test_sandbox_feed_http_service_fails_closed_without_network_policy() -> None:
    class NoNetworkPolicySandbox:
        sandbox_id = "sandbox-without-network-policy"

    service = SandboxFeedHttpService(NoNetworkPolicySandbox())  # type: ignore[arg-type]

    with pytest.raises(FeedResearchRuntimeError, match="cannot enforce network policy"):
        service.fetch("https://example.com/feed.xml")

    assert service.is_unhealthy is True


def test_fetch_many_uses_one_command_and_keeps_candidate_failures_local() -> None:
    urls = [
        "https://one.example/feed.xml",
        "https://two.example/feed.xml",
        "https://three.example/feed.xml",
    ]
    rows = [
        _row(urls[0]),
        _row(urls[1], curl_exit=28, stderr="timed out"),
        _row(urls[2], status=404),
    ]
    sandbox = _FakeFeedSandbox([rows])
    service = SandboxFeedHttpService(sandbox)  # type: ignore[arg-type]

    results = service.fetch_many(urls)

    assert len(sandbox.commands) == 1
    assert isinstance(results[0], httpx.Response)
    assert isinstance(results[1], FeedResearchCandidateError)
    assert isinstance(results[2], httpx.HTTPStatusError)
    assert service.is_unhealthy is False
    assert sandbox.allowed_hosts == [["one.example", "three.example", "two.example"]]
    assert sandbox.reset_calls == 1


def test_sandbox_feed_http_service_rejects_non_http_urls_without_dispatch() -> None:
    sandbox = _FakeFeedSandbox()
    service = SandboxFeedHttpService(sandbox)  # type: ignore[arg-type]

    with pytest.raises(FeedResearchRuntimeError, match="HTTP or HTTPS"):
        service.fetch("file:///etc/passwd")

    assert sandbox.commands == []


def test_feed_research_runtime_uses_persistent_system_namespace_and_closes_lease() -> None:
    sandbox = _FakeFeedSandbox()
    calls: list[dict[str, object]] = []

    def _factory(**kwargs):
        calls.append(kwargs)
        return sandbox

    with feed_research_runtime(
        user_id=7,
        execution_id=42,
        session_factory=_factory,
        use_llm=False,
    ) as runtime:
        assert runtime.detector.http_service is runtime.http_service

    assert calls == [
        {
            "user_id": SYSTEM_USER_ID,
            "llm_task_id": 42,
            "vm_namespace": f"user:{SYSTEM_USER_ID}",
            "workspace_path": "/data/workspace/tasks/42",
            "shared_workspace_path": "/data/workspace/users/0/shared",
            "feature": "feed_research",
        }
    ]
    assert sandbox.closed is True


def test_feed_research_runtime_rejects_non_e2b_session() -> None:
    sandbox = _FakeFeedSandbox()
    sandbox.provider = "local"

    with (
        pytest.raises(FeedResearchRuntimeError, match="requires the E2B"),
        feed_research_runtime(
            user_id=7,
            execution_id=42,
            session_factory=lambda **_kwargs: cast(AgentVmSession, sandbox),
            use_llm=False,
        ),
    ):
        pytest.fail("non-E2B feed runtime must not be entered")

    assert sandbox.closed is True


def test_feed_research_runtime_rejects_expired_deadline_before_session_creation() -> None:
    session_created = False

    def _factory(**_kwargs):
        nonlocal session_created
        session_created = True
        return _FakeFeedSandbox()

    with (
        pytest.raises(FeedResearchDeadlineExceeded),
        feed_research_runtime(
            user_id=7,
            execution_id=42,
            session_factory=_factory,
            use_llm=False,
            deadline=monotonic() - 1,
        ),
    ):
        pytest.fail("an expired runtime must not be entered")

    assert session_created is False


def test_feed_research_runtime_bounds_session_acquisition_with_shared_deadline() -> None:
    deadline = monotonic() + 1
    calls: list[dict[str, object]] = []

    def _factory(**kwargs):
        calls.append(kwargs)
        raise AgentVmDeadlineExceeded("sandbox acquisition timed out")

    with (
        pytest.raises(FeedResearchDeadlineExceeded),
        feed_research_runtime(
            user_id=7,
            execution_id=42,
            session_factory=_factory,
            use_llm=False,
            deadline=deadline,
        ),
    ):
        pytest.fail("a timed-out sandbox acquisition must not enter the runtime")

    assert calls[0]["deadline"] == deadline


def test_feed_validator_never_falls_back_to_host_http(monkeypatch) -> None:
    url = "https://example.com/feed.xml"
    sandbox = _FakeFeedSandbox([[_row(url)]])

    def _unexpected_host_http(*_args, **_kwargs):
        raise AssertionError("feed validation must not use host HTTP")

    monkeypatch.setattr(HttpService, "fetch", _unexpected_host_http)

    with feed_research_runtime(
        user_id=7,
        execution_id=44,
        session_factory=lambda **_kwargs: cast(AgentVmSession, sandbox),
        use_llm=False,
    ) as runtime:
        validated = runtime.detector.validate_feed_url(url)

    assert validated == {
        "feed_url": url,
        "feed_format": "rss",
        "title": "Example",
    }
    assert len(sandbox.commands) == 1


def test_production_feed_detectors_are_only_built_by_sandbox_runtime() -> None:
    app_root = Path(__file__).resolve().parents[2] / "app"
    constructors: list[str] = []

    for path in app_root.rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "FeedDetector"
            ):
                constructors.append(str(path.relative_to(app_root)))

    assert constructors == ["services/feed_research_runtime.py"]


@pytest.mark.parametrize("exit_code", [3, 6, 7, 18, 28, 35, 47, 60, 63])
def test_curl_target_failure_is_candidate_local_and_next_request_continues(
    exit_code: int,
) -> None:
    broken_url = "https://broken.example/feed.xml"
    good_url = "https://example.com/feed.xml"
    sandbox = _FakeFeedSandbox(
        [
            [_row(broken_url, curl_exit=exit_code, stderr="candidate failed")],
            [_row(good_url)],
        ]
    )

    with feed_research_runtime(
        user_id=7,
        execution_id=45,
        session_factory=lambda **_kwargs: cast(AgentVmSession, sandbox),
        use_llm=False,
    ) as runtime:
        assert runtime.detector.validate_feed_url(broken_url) is None
        assert runtime.detector.validate_feed_url(good_url) == {
            "feed_url": good_url,
            "feed_format": "rss",
            "title": "Example",
        }
        assert runtime.http_service.is_unhealthy is False

    assert len(sandbox.commands) == 2


def test_malformed_batch_output_is_fatal_and_evicts_session(monkeypatch) -> None:
    sandbox = _FakeFeedSandbox([AgentCommandResult(stdout="not-jsonl", stderr="", exit_code=0)])
    evicted: list[object] = []
    monkeypatch.setattr(
        "app.services.feed_research_runtime.evict_agent_vm_session",
        evicted.append,
    )

    with (
        pytest.raises(FeedResearchRuntimeError, match="invalid JSONL"),
        feed_research_runtime(
            user_id=7,
            execution_id=46,
            session_factory=lambda **_kwargs: cast(AgentVmSession, sandbox),
            use_llm=False,
        ) as runtime,
    ):
        assert runtime.detector.validate_feed_url("https://example.com/feed.xml") is None

    assert evicted == [sandbox]
    assert sandbox.closed is True


def test_unexpected_curl_exit_is_fatal() -> None:
    url = "https://example.com/feed.xml"
    sandbox = _FakeFeedSandbox([[_row(url, curl_exit=2, stderr="bad invocation")]])

    with pytest.raises(FeedResearchRuntimeError, match="curl exit 2"):
        SandboxFeedHttpService(sandbox).fetch(url)  # type: ignore[arg-type]

    assert sandbox.reset_calls == 1


@pytest.mark.parametrize("status_code", [404, 500])
def test_http_failure_stays_candidate_local(status_code: int) -> None:
    url = "https://example.com/missing"
    sandbox = _FakeFeedSandbox([[_row(url, status=status_code)]])
    service = SandboxFeedHttpService(sandbox)  # type: ignore[arg-type]

    with pytest.raises(httpx.HTTPStatusError):
        service.fetch(url)

    assert service.is_unhealthy is False


def test_command_deadline_preserves_session_and_resets_network_policy() -> None:
    sandbox = _FakeFeedSandbox([AgentVmDeadlineExceeded("request timed out")])
    service = SandboxFeedHttpService(sandbox)  # type: ignore[arg-type]

    with pytest.raises(FeedResearchDeadlineExceeded):
        service.fetch("https://example.com/feed.xml")

    assert service.is_unhealthy is False
    assert sandbox.reset_calls == 1


def test_batch_command_uses_remaining_deadline_and_explicit_output_bound() -> None:
    url = "https://example.com/feed.xml"
    sandbox = _FakeFeedSandbox([[_row(url)]])
    service = SandboxFeedHttpService(
        sandbox,  # type: ignore[arg-type]
        deadline=monotonic() + 0.5,
    )

    service.fetch(url)

    command = sandbox.commands[0]
    timeout_seconds = command["timeout_seconds"]
    assert isinstance(timeout_seconds, float)
    assert 0 < timeout_seconds <= 0.5
    assert isinstance(command["max_output_chars"], int)
    assert int(command["max_output_chars"]) > 2_000_000
