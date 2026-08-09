from __future__ import annotations

import ast
import shlex
from pathlib import Path
from time import monotonic
from types import SimpleNamespace

import httpx
import pytest

from app.services.agent_vm_runtime import (
    AgentCommandResult,
    AgentVmDeadlineExceeded,
    AgentVmFileSizeLimitExceeded,
    AgentVmLease,
)
from app.services.feed_research_runtime import (
    FeedResearchCandidateError,
    FeedResearchDeadlineExceeded,
    FeedResearchRuntimeError,
    SandboxFeedHttpService,
    feed_research_runtime,
)
from app.services.http import HttpService


class _FakeFeedSandbox:
    provider = "e2b"
    sandbox_id = "sandbox-feed-test"
    lease = AgentVmLease(
        provider="e2b",
        vm_namespace="user:7",
        sandbox_id=sandbox_id,
        reuse_scope="process_namespace",
        reused=False,
    )

    def __init__(self) -> None:
        self.commands: list[tuple[str, float | None]] = []
        self.closed = False

    def execute_bash(
        self,
        command: str,
        *,
        timeout_seconds: float | None = None,
    ) -> AgentCommandResult:
        self.commands.append((command, timeout_seconds))
        if "curl" in command:
            return AgentCommandResult(
                stdout="https://example.com/feed.xml\n200",
                stderr="",
                exit_code=0,
            )
        return AgentCommandResult(stdout="", stderr="", exit_code=0)

    def read_file_bytes(
        self,
        path: str,
        *,
        max_bytes: int | None = None,
        timeout_seconds: float | None = None,
    ) -> bytes:
        del max_bytes, timeout_seconds
        if path.endswith(".headers"):
            return (
                b"HTTP/1.1 301 Moved Permanently\r\nLocation: /feed.xml\r\n\r\n"
                b"HTTP/2 200\r\nContent-Type: application/rss+xml\r\n"
                b"Content-Encoding: gzip\r\nContent-Length: 999\r\n\r\n"
            )
        return b"<?xml version='1.0'?><rss><channel><title>Example</title></channel></rss>"

    def close(self) -> None:
        self.closed = True


def test_sandbox_feed_http_service_fetches_and_cleans_up_inside_vm() -> None:
    sandbox = _FakeFeedSandbox()
    service = SandboxFeedHttpService(sandbox)  # type: ignore[arg-type]

    response = service.fetch("https://example.com/blog")

    assert response.status_code == 200
    assert response.url == httpx.URL("https://example.com/feed.xml")
    assert response.headers["content-type"] == "application/rss+xml"
    assert "content-encoding" not in response.headers
    assert response.headers["content-length"] == str(len(response.content))
    assert b"<rss>" in response.content
    curl_command = sandbox.commands[0][0]
    assert "curl" in curl_command
    assert "https://example.com/blog" in curl_command
    assert sandbox.commands[-1][0].startswith("rm -f scratch/feed-http-")


def test_sandbox_feed_http_service_rejects_non_http_urls_without_dispatch() -> None:
    sandbox = _FakeFeedSandbox()
    service = SandboxFeedHttpService(sandbox)  # type: ignore[arg-type]

    with pytest.raises(FeedResearchRuntimeError, match="HTTP or HTTPS"):
        service.fetch("file:///etc/passwd")

    assert sandbox.commands == []


def test_feed_research_runtime_uses_user_workspace_and_closes_session() -> None:
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
        assert isinstance(runtime.detector.http_service, SandboxFeedHttpService)
        assert runtime.detector.http_service is runtime.http_service
        assert not hasattr(runtime.detector, "use_exa_search")

    assert calls == [
        {
            "user_id": 7,
            "llm_task_id": 42,
            "vm_namespace": "user:7",
            "workspace_path": "/tmp/newsly/tasks/42",
            "shared_workspace_path": "/tmp/newsly/users/7/shared",
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
            session_factory=lambda **_kwargs: sandbox,
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


def test_feed_research_runtime_normalizes_session_acquisition_failure() -> None:
    def _factory(**_kwargs):
        raise RuntimeError("E2B allocation unavailable")

    with (
        pytest.raises(FeedResearchRuntimeError, match="initialize"),
        feed_research_runtime(
            user_id=7,
            execution_id=42,
            session_factory=_factory,
            use_llm=False,
        ),
    ):
        pytest.fail("a failed sandbox acquisition must not enter the runtime")


def test_feed_validator_never_falls_back_to_host_http(monkeypatch) -> None:
    sandbox = _FakeFeedSandbox()

    def _unexpected_host_http(*_args, **_kwargs):
        raise AssertionError("feed validation must not use host HTTP")

    monkeypatch.setattr(HttpService, "fetch", _unexpected_host_http)

    with feed_research_runtime(
        user_id=7,
        execution_id=44,
        session_factory=lambda **_kwargs: sandbox,
        use_llm=False,
    ) as runtime:
        validated = runtime.detector.validate_feed_url("https://example.com/feed.xml")

    assert validated == {
        "feed_url": "https://example.com/feed.xml",
        "feed_format": "rss",
        "title": "Example",
    }
    assert sum("curl" in command for command, _timeout in sandbox.commands) == 1


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


def test_feed_research_runtime_surfaces_hidden_sandbox_transport_failure() -> None:
    class _BrokenFeedSandbox(_FakeFeedSandbox):
        def execute_bash(self, command: str, *, timeout_seconds: int | None = None):
            if "curl" in command:
                raise RuntimeError("sandbox transport disconnected")
            return super().execute_bash(command, timeout_seconds=timeout_seconds)

    sandbox = _BrokenFeedSandbox()

    with (
        pytest.raises(FeedResearchRuntimeError, match="became unavailable"),
        feed_research_runtime(
            user_id=7,
            execution_id=43,
            session_factory=lambda **_kwargs: sandbox,
            use_llm=False,
        ) as runtime,
    ):
        assert runtime.detector.validate_feed_url("https://example.com/feed.xml") is None

    assert sandbox.closed is True


@pytest.mark.parametrize(
    "exit_code",
    [3, 6, 7, 8, 16, 18, 22, 28, 35, 47, 52, 55, 56, 60, 61, 63],
)
def test_feed_research_runtime_keeps_curl_target_failure_candidate_local_and_continues(
    exit_code: int,
) -> None:
    class _FailedCandidateSandbox(_FakeFeedSandbox):
        def __init__(self) -> None:
            super().__init__()
            self.curl_calls = 0

        def execute_bash(self, command: str, *, timeout_seconds: int | None = None):
            result = super().execute_bash(command, timeout_seconds=timeout_seconds)
            if "curl" in command:
                self.curl_calls += 1
                if self.curl_calls == 1:
                    return AgentCommandResult(
                        stdout="",
                        stderr=f"curl: ({exit_code}) candidate request failed",
                        exit_code=exit_code,
                    )
            return result

    sandbox = _FailedCandidateSandbox()

    with feed_research_runtime(
        user_id=7,
        execution_id=45,
        session_factory=lambda **_kwargs: sandbox,
        use_llm=False,
    ) as runtime:
        assert runtime.detector.validate_feed_url("https://broken.example/feed.xml") is None
        assert runtime.detector.validate_feed_url("https://example.com/feed.xml") == {
            "feed_url": "https://example.com/feed.xml",
            "feed_format": "rss",
            "title": "Example",
        }
        assert runtime.http_service.is_unhealthy is False

    assert sandbox.curl_calls == 2
    assert sandbox.closed is True


def test_feed_research_runtime_keeps_file_size_limit_candidate_local_and_continues() -> None:
    class _OversizedCandidateSandbox(_FakeFeedSandbox):
        def __init__(self) -> None:
            super().__init__()
            self.body_reads = 0

        def read_file_bytes(
            self,
            path: str,
            *,
            max_bytes: int | None = None,
            timeout_seconds: float | None = None,
        ) -> bytes:
            if not path.endswith(".headers"):
                self.body_reads += 1
                if self.body_reads == 1:
                    raise AgentVmFileSizeLimitExceeded("candidate body exceeded its limit")
            return super().read_file_bytes(
                path,
                max_bytes=max_bytes,
                timeout_seconds=timeout_seconds,
            )

    sandbox = _OversizedCandidateSandbox()

    with feed_research_runtime(
        user_id=7,
        execution_id=47,
        session_factory=lambda **_kwargs: sandbox,
        use_llm=False,
    ) as runtime:
        assert runtime.detector.validate_feed_url("https://oversized.example/feed.xml") is None
        assert runtime.detector.validate_feed_url("https://example.com/feed.xml") == {
            "feed_url": "https://example.com/feed.xml",
            "feed_format": "rss",
            "title": "Example",
        }
        assert runtime.http_service.is_unhealthy is False

    assert sandbox.body_reads == 2
    assert sandbox.closed is True


def test_sandbox_feed_http_service_clamps_all_timeouts_to_remaining_budget() -> None:
    sandbox = _FakeFeedSandbox()
    service = SandboxFeedHttpService(
        sandbox,  # type: ignore[arg-type]
        deadline=monotonic() + 0.5,
    )

    service.fetch("https://example.com/blog")

    curl_command, execute_timeout = sandbox.commands[0]
    curl_args = shlex.split(curl_command.split("&&", 1)[1])
    connect_timeout = float(curl_args[curl_args.index("--connect-timeout") + 1])
    max_time = float(curl_args[curl_args.index("--max-time") + 1])
    assert execute_timeout is not None
    assert 0 < execute_timeout <= 0.5
    assert 0 < connect_timeout <= 0.5
    assert 0 < max_time <= 0.5
    cleanup_timeout = sandbox.commands[-1][1]
    assert cleanup_timeout is not None
    assert 0 < cleanup_timeout <= 0.5


def test_sandbox_feed_http_service_rejects_expired_deadline_without_dispatch() -> None:
    sandbox = _FakeFeedSandbox()
    service = SandboxFeedHttpService(
        sandbox,  # type: ignore[arg-type]
        deadline=monotonic() - 1,
    )

    with pytest.raises(FeedResearchDeadlineExceeded):
        service.fetch("https://example.com/blog")

    assert sandbox.commands == []


def test_deadline_expiry_after_curl_preserves_error_and_poisons_unclean_session() -> None:
    class _DeadlineSandbox(_FakeFeedSandbox):
        def __init__(self) -> None:
            super().__init__()
            self.file_reads = 0

        def execute_bash(self, command: str, *, timeout_seconds: int | None = None):
            result = super().execute_bash(command, timeout_seconds=timeout_seconds)
            if "curl" in command:
                service._deadline = monotonic() - 1
            return result

        def read_file_bytes(
            self,
            path: str,
            *,
            max_bytes: int | None = None,
            timeout_seconds: float | None = None,
        ) -> bytes:
            self.file_reads += 1
            return super().read_file_bytes(
                path,
                max_bytes=max_bytes,
                timeout_seconds=timeout_seconds,
            )

    sandbox = _DeadlineSandbox()
    service = SandboxFeedHttpService(
        sandbox,  # type: ignore[arg-type]
        deadline=monotonic() + 2,
    )

    with pytest.raises(FeedResearchCandidateError, match="deadline"):
        service.fetch("https://example.com/blog")

    assert service.is_unhealthy is True
    assert sandbox.file_reads == 0
    assert len(sandbox.commands) == 1


def test_cleanup_command_deadline_poisons_feed_sandbox() -> None:
    class _CleanupDeadlineSandbox(_FakeFeedSandbox):
        def execute_bash(
            self,
            command: str,
            *,
            timeout_seconds: float | None = None,
        ) -> AgentCommandResult:
            if command.startswith("rm -f "):
                self.commands.append((command, timeout_seconds))
                raise AgentVmDeadlineExceeded("cleanup timed out")
            return super().execute_bash(command, timeout_seconds=timeout_seconds)

    sandbox = _CleanupDeadlineSandbox()
    service = SandboxFeedHttpService(sandbox)  # type: ignore[arg-type]

    response = service.fetch("https://example.com/blog")

    assert response.status_code == 200
    assert service.is_unhealthy is True
    with pytest.raises(FeedResearchRuntimeError, match="became unavailable"):
        service.raise_if_unhealthy()


def test_nonzero_cleanup_poisons_feed_sandbox() -> None:
    class _FailedCleanupSandbox(_FakeFeedSandbox):
        def execute_bash(
            self,
            command: str,
            *,
            timeout_seconds: float | None = None,
        ) -> AgentCommandResult:
            result = super().execute_bash(command, timeout_seconds=timeout_seconds)
            if command.startswith("rm -f "):
                return AgentCommandResult(stdout="", stderr="cleanup failed", exit_code=1)
            return result

    sandbox = _FailedCleanupSandbox()
    service = SandboxFeedHttpService(sandbox)  # type: ignore[arg-type]

    response = service.fetch("https://example.com/blog")

    assert response.status_code == 200
    assert service.is_unhealthy is True
    with pytest.raises(FeedResearchRuntimeError, match="scratch cleanup"):
        service.raise_if_unhealthy()


def test_feed_runtime_evicts_session_after_scratch_cleanup_failure(monkeypatch) -> None:
    class _FailedCleanupSandbox(_FakeFeedSandbox):
        def execute_bash(
            self,
            command: str,
            *,
            timeout_seconds: float | None = None,
        ) -> AgentCommandResult:
            result = super().execute_bash(command, timeout_seconds=timeout_seconds)
            if command.startswith("rm -f "):
                return AgentCommandResult(stdout="", stderr="cleanup failed", exit_code=1)
            return result

    sandbox = _FailedCleanupSandbox()
    evicted: list[object] = []
    monkeypatch.setattr(
        "app.services.feed_research_runtime.evict_agent_vm_session",
        evicted.append,
    )

    with (
        pytest.raises(FeedResearchRuntimeError, match="scratch cleanup"),
        feed_research_runtime(
            user_id=7,
            execution_id=48,
            session_factory=lambda **_kwargs: sandbox,
            use_llm=False,
        ) as runtime,
    ):
        assert runtime.http_service.fetch("https://example.com/blog").status_code == 200

    assert evicted == [sandbox]
    assert sandbox.closed is True


def test_sandbox_command_deadline_preserves_error_and_poisons_unclean_session() -> None:
    class _TimedOutSandbox(_FakeFeedSandbox):
        def execute_bash(
            self,
            command: str,
            *,
            timeout_seconds: float | None = None,
        ) -> AgentCommandResult:
            del command, timeout_seconds
            service._deadline = monotonic() - 1
            raise AgentVmDeadlineExceeded("sandbox request timed out")

    service = SandboxFeedHttpService(
        _TimedOutSandbox(),  # type: ignore[arg-type]
        deadline=monotonic() + 1,
    )

    with pytest.raises(FeedResearchDeadlineExceeded):
        service.fetch("https://example.com/blog")

    assert service.is_unhealthy is True


def test_feed_research_runtime_surfaces_malformed_curl_metadata() -> None:
    class _MalformedMetadataSandbox(_FakeFeedSandbox):
        def execute_bash(self, command: str, *, timeout_seconds: int | None = None):
            result = super().execute_bash(command, timeout_seconds=timeout_seconds)
            if "curl" in command:
                return AgentCommandResult(
                    stdout="not-curl-metadata",
                    stderr="",
                    exit_code=0,
                )
            return result

    sandbox = _MalformedMetadataSandbox()

    with (
        pytest.raises(FeedResearchRuntimeError, match="invalid curl metadata"),
        feed_research_runtime(
            user_id=7,
            execution_id=46,
            session_factory=lambda **_kwargs: sandbox,
            use_llm=False,
        ) as runtime,
    ):
        assert runtime.detector.validate_feed_url("https://example.com/feed.xml") is None

    assert sandbox.closed is True


def test_feed_research_runtime_surfaces_sandbox_file_channel_failure() -> None:
    class _BrokenFileSandbox(_FakeFeedSandbox):
        def read_file_bytes(
            self,
            path: str,
            *,
            max_bytes: int | None = None,
            timeout_seconds: float | None = None,
        ) -> bytes:
            del path, max_bytes, timeout_seconds
            raise RuntimeError("sandbox file channel disconnected")

    sandbox = _BrokenFileSandbox()

    with (
        pytest.raises(FeedResearchRuntimeError, match="became unavailable"),
        feed_research_runtime(
            user_id=7,
            execution_id=46,
            session_factory=lambda **_kwargs: sandbox,
            use_llm=False,
        ) as runtime,
    ):
        assert runtime.detector.validate_feed_url("https://example.com/feed.xml") is None

    assert sandbox.closed is True


def test_feed_research_runtime_treats_unexpected_curl_exit_as_fatal() -> None:
    class _ProtocolFailureSandbox(_FakeFeedSandbox):
        def execute_bash(self, command: str, *, timeout_seconds: int | None = None):
            result = super().execute_bash(command, timeout_seconds=timeout_seconds)
            if "curl" in command:
                return AgentCommandResult(
                    stdout="",
                    stderr="curl invocation rejected",
                    exit_code=2,
                )
            return result

    sandbox = _ProtocolFailureSandbox()

    with (
        pytest.raises(FeedResearchRuntimeError, match="curl exit 2"),
        feed_research_runtime(
            user_id=7,
            execution_id=46,
            session_factory=lambda **_kwargs: sandbox,
            use_llm=False,
        ) as runtime,
    ):
        assert runtime.detector.validate_feed_url("https://example.com/feed.xml") is None

    assert sandbox.closed is True


def test_sandbox_feed_http_service_surfaces_http_failure() -> None:
    class _MissingFeedSandbox(_FakeFeedSandbox):
        def execute_bash(self, command: str, *, timeout_seconds: int | None = None):
            result = super().execute_bash(command, timeout_seconds=timeout_seconds)
            if "curl" in command:
                return SimpleNamespace(
                    stdout="https://example.com/missing\n404",
                    stderr="",
                    exit_code=0,
                )
            return result

    service = SandboxFeedHttpService(_MissingFeedSandbox())  # type: ignore[arg-type]

    with pytest.raises(httpx.HTTPStatusError):
        service.fetch("https://example.com/missing")


@pytest.mark.parametrize("status_code", [404, 500])
def test_feed_research_runtime_keeps_http_failure_candidate_local(status_code: int) -> None:
    class _MissingFeedSandbox(_FakeFeedSandbox):
        def execute_bash(self, command: str, *, timeout_seconds: int | None = None):
            result = super().execute_bash(command, timeout_seconds=timeout_seconds)
            if "curl" in command:
                return AgentCommandResult(
                    stdout=f"https://example.com/missing\n{status_code}",
                    stderr="",
                    exit_code=0,
                )
            return result

    sandbox = _MissingFeedSandbox()

    with feed_research_runtime(
        user_id=7,
        execution_id=47,
        session_factory=lambda **_kwargs: sandbox,
        use_llm=False,
    ) as runtime:
        assert runtime.detector.validate_feed_url("https://example.com/missing") is None

    assert sandbox.closed is True
