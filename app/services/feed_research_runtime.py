"""E2B-backed network runtime for feed research and validation."""

from __future__ import annotations

import re
import shlex
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from time import monotonic
from typing import Any
from uuid import uuid4

import httpx

from app.core.logging import get_logger
from app.services.agent_vm_runtime import (
    AgentVmDeadlineExceeded,
    AgentVmError,
    AgentVmFileSizeLimitExceeded,
    AgentVmSession,
)
from app.services.agent_vm_sessions import create_agent_vm_session, evict_agent_vm_session
from app.services.feed_detection import FeedDetector
from app.services.llm_tasks import build_llm_task_paths

logger = get_logger(__name__)

MAX_FEED_RESPONSE_BYTES = 2_000_000
FEED_FETCH_TIMEOUT_SECONDS = 30
FEED_CONNECT_TIMEOUT_SECONDS = 10
FEED_COMMAND_TIMEOUT_OVERHEAD_SECONDS = 5
MIN_FEED_REQUEST_BUDGET_SECONDS = 0.001
# Curl completed locally but the remote origin, protocol, or response failed.
# Keep these failures scoped to one candidate so another URL can use the same
# healthy sandbox. Invocation and local I/O failures (for example exit 2) stay
# fatal and evict the session.
_CANDIDATE_LOCAL_CURL_EXIT_CODES = frozenset(
    {
        3,  # malformed candidate URL
        6,  # host resolution
        7,  # connection
        8,  # invalid server response
        16,  # HTTP/2 framing
        18,  # partial response
        22,  # HTTP status with --fail
        28,  # request timeout
        35,  # TLS handshake
        47,  # redirect limit
        52,  # empty server response
        55,  # send failure
        56,  # receive failure
        60,  # peer certificate
        61,  # transfer encoding
        63,  # response exceeds max-filesize
    }
)
_HEADER_BLOCK_SEPARATOR = re.compile(rb"\r?\n\r?\n")


class FeedResearchRuntimeError(AgentVmError):
    """Raised when an isolated feed-research request cannot be completed."""


class FeedResearchCandidateError(RuntimeError):
    """Raised when one feed candidate fails without poisoning its sandbox."""


class FeedResearchDeadlineExceeded(FeedResearchCandidateError):
    """Raised when a deadline-bound feed probe has no remaining budget."""


@dataclass(frozen=True)
class FeedResearchRuntime:
    """One detector whose outbound requests execute inside an agent VM."""

    detector: FeedDetector
    http_service: SandboxFeedHttpService


AgentVmSessionFactory = Callable[..., AgentVmSession]


class SandboxFeedHttpService:
    """Small ``HttpService``-compatible adapter backed by sandboxed curl."""

    def __init__(self, session: AgentVmSession, *, deadline: float | None = None) -> None:
        self._session = session
        self._deadline = deadline
        self._fatal_error: Exception | None = None

    @property
    def is_unhealthy(self) -> bool:
        """Return whether the sandbox transport produced a fatal result."""
        return self._fatal_error is not None

    def raise_if_unhealthy(self) -> None:
        """Surface sandbox transport failures hidden by candidate-level probing."""
        error = self._fatal_error
        if error is None:
            return
        if isinstance(error, FeedResearchRuntimeError):
            raise error
        raise FeedResearchRuntimeError(
            "Feed research sandbox became unavailable during validation"
        ) from error

    def _record_fatal_error(self, error: Exception) -> None:
        if self._fatal_error is None:
            self._fatal_error = error

    def fetch(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        *,
        log_client_errors: bool = True,
        log_exceptions: bool = True,
    ) -> httpx.Response:
        del log_client_errors, log_exceptions
        return self._request(url, headers=headers)

    def _request(
        self,
        url: str,
        *,
        headers: dict[str, str] | None,
    ) -> httpx.Response:
        _validate_http_url(url)
        self.raise_if_unhealthy()
        remaining_seconds = self._remaining_seconds()
        curl_timeout_seconds = min(
            FEED_FETCH_TIMEOUT_SECONDS,
            remaining_seconds,
        )
        connect_timeout_seconds = min(
            FEED_CONNECT_TIMEOUT_SECONDS,
            curl_timeout_seconds,
        )
        request_key = uuid4().hex
        body_path = f"scratch/feed-http-{request_key}.body"
        header_path = f"scratch/feed-http-{request_key}.headers"
        command = _build_curl_command(
            url=url,
            body_path=body_path,
            header_path=header_path,
            headers=headers,
            connect_timeout_seconds=connect_timeout_seconds,
            max_time_seconds=curl_timeout_seconds,
        )
        try:
            result = self._session.execute_bash(
                command,
                timeout_seconds=self._bounded_timeout(
                    FEED_FETCH_TIMEOUT_SECONDS + FEED_COMMAND_TIMEOUT_OVERHEAD_SECONDS
                ),
            )
            if result.exit_code != 0:
                error = (
                    f"Sandbox feed request failed with curl exit {result.exit_code}: "
                    f"{result.stderr.strip()[:500]}"
                )
                if result.exit_code in _CANDIDATE_LOCAL_CURL_EXIT_CODES:
                    raise FeedResearchCandidateError(error)
                raise FeedResearchRuntimeError(error)
            self._remaining_seconds()
            effective_url, status_code = _parse_curl_metadata(result.stdout)
            raw_headers = self._session.read_file_bytes(
                header_path,
                max_bytes=128_000,
            )
            self._remaining_seconds()
            content = self._session.read_file_bytes(
                body_path,
                max_bytes=MAX_FEED_RESPONSE_BYTES,
            )
            self._remaining_seconds()
            response = httpx.Response(
                status_code,
                headers=_parse_final_headers(raw_headers),
                content=content,
                request=httpx.Request("GET", effective_url),
            )
            if status_code >= 400:
                response.raise_for_status()
        except AgentVmDeadlineExceeded as exc:
            raise FeedResearchDeadlineExceeded("Feed research deadline was exceeded") from exc
        except AgentVmFileSizeLimitExceeded as exc:
            raise FeedResearchCandidateError(
                "Feed candidate exceeded the response size limit"
            ) from exc
        except (FeedResearchCandidateError, httpx.HTTPStatusError):
            raise
        except Exception as exc:
            self._record_fatal_error(exc)
            raise
        finally:
            cleanup_timeout = self._bounded_timeout(5, allow_expired=True)
            if cleanup_timeout is None:
                self._record_fatal_error(
                    FeedResearchRuntimeError(
                        "Feed research scratch cleanup missed its request deadline"
                    )
                )
                logger.debug(
                    "Unable to remove feed-research scratch files before the request deadline",
                    extra={
                        "component": "feed_research",
                        "operation": "scratch_cleanup",
                        "sandbox_id": self._session.sandbox_id,
                    },
                )
            else:
                try:
                    cleanup_result = self._session.execute_bash(
                        f"rm -f {shlex.quote(body_path)} {shlex.quote(header_path)}",
                        timeout_seconds=cleanup_timeout,
                    )
                    if cleanup_result.exit_code != 0:
                        raise FeedResearchRuntimeError(
                            "Feed research scratch cleanup returned a non-zero exit code"
                        )
                except AgentVmDeadlineExceeded as exc:
                    self._record_fatal_error(exc)
                    logger.debug(
                        "Feed-research scratch cleanup reached its request deadline",
                        extra={
                            "component": "feed_research",
                            "operation": "scratch_cleanup",
                            "sandbox_id": self._session.sandbox_id,
                        },
                    )
                except Exception as exc:  # noqa: BLE001
                    self._record_fatal_error(exc)
                    logger.debug(
                        "Unable to remove feed-research scratch files",
                        extra={
                            "component": "feed_research",
                            "operation": "scratch_cleanup",
                            "sandbox_id": self._session.sandbox_id,
                        },
                    )
        self._remaining_seconds()
        return response

    def _remaining_seconds(self) -> float:
        deadline = self._deadline
        if deadline is None:
            return float(FEED_FETCH_TIMEOUT_SECONDS)
        remaining = deadline - monotonic()
        if remaining < MIN_FEED_REQUEST_BUDGET_SECONDS:
            raise FeedResearchDeadlineExceeded("Feed research deadline was exceeded")
        return remaining

    def _bounded_timeout(
        self,
        maximum_seconds: float,
        *,
        allow_expired: bool = False,
    ) -> float | None:
        deadline = self._deadline
        if deadline is None:
            return maximum_seconds
        remaining = deadline - monotonic()
        if remaining <= 0:
            if allow_expired:
                return None
            raise FeedResearchDeadlineExceeded("Feed research deadline was exceeded")
        return min(maximum_seconds, remaining)


@contextmanager
def sandboxed_http_service(
    *,
    user_id: int,
    execution_id: int | None = None,
    session_factory: AgentVmSessionFactory | None = None,
    deadline: float | None = None,
) -> Iterator[SandboxFeedHttpService]:
    """Create one E2B-backed HTTP transport with poison eviction and cleanup."""
    if deadline is not None and monotonic() >= deadline:
        raise FeedResearchDeadlineExceeded("Feed research deadline was exceeded")
    resolved_execution_id = execution_id or uuid4().int
    paths = build_llm_task_paths(user_id=user_id, llm_task_id=resolved_execution_id)
    create_session = session_factory or create_agent_vm_session
    session_kwargs: dict[str, Any] = {
        "user_id": user_id,
        "llm_task_id": resolved_execution_id,
        "vm_namespace": paths.vm_namespace,
        "workspace_path": paths.workspace_path,
        "shared_workspace_path": paths.shared_workspace_path,
        "feature": "feed_research",
    }
    if deadline is not None:
        session_kwargs["deadline"] = deadline
    try:
        session = create_session(**session_kwargs)
    except AgentVmDeadlineExceeded as exc:
        raise FeedResearchDeadlineExceeded("Feed research deadline was exceeded") from exc
    except Exception as exc:
        raise FeedResearchRuntimeError("Unable to initialize the feed research sandbox") from exc
    if session.provider != "e2b":
        try:
            session.close()
        except Exception:  # noqa: BLE001 - preserve the provider-boundary error
            logger.exception("Unable to close rejected non-E2B feed session")
        raise FeedResearchRuntimeError("Feed research requires the E2B sandbox provider")
    logger.info(
        "Feed research sandbox ready",
        extra={
            "component": "feed_research",
            "operation": "sandbox_ready",
            "user_id": user_id,
            "item_id": str(resolved_execution_id),
            "context_data": {
                "sandbox_provider": session.provider,
                "sandbox_id": session.sandbox_id,
                "reused": session.lease.reused,
            },
        },
    )
    http_service = SandboxFeedHttpService(session, deadline=deadline)
    try:
        try:
            yield http_service
            http_service.raise_if_unhealthy()
        finally:
            if http_service.is_unhealthy:
                evict_agent_vm_session(session)
    finally:
        session.close()


@contextmanager
def feed_research_runtime(
    *,
    user_id: int,
    execution_id: int | None = None,
    session_factory: AgentVmSessionFactory | None = None,
    use_llm: bool = True,
    deadline: float | None = None,
) -> Iterator[FeedResearchRuntime]:
    """Create a feed detector whose live HTTP probes stay inside E2B."""
    with sandboxed_http_service(
        user_id=user_id,
        execution_id=execution_id,
        session_factory=session_factory,
        deadline=deadline,
    ) as http_service:
        yield FeedResearchRuntime(
            detector=FeedDetector(
                use_llm=use_llm,
                http_service=http_service,
            ),
            http_service=http_service,
        )


def _validate_http_url(url: str) -> None:
    try:
        parsed = httpx.URL(url)
    except Exception as exc:  # noqa: BLE001
        raise FeedResearchRuntimeError("Feed research URL is invalid") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.host:
        raise FeedResearchRuntimeError("Feed research URL must use HTTP or HTTPS")


def _build_curl_command(
    *,
    url: str,
    body_path: str,
    header_path: str,
    headers: dict[str, str] | None,
    connect_timeout_seconds: float = FEED_CONNECT_TIMEOUT_SECONDS,
    max_time_seconds: float = FEED_FETCH_TIMEOUT_SECONDS,
) -> str:
    request_headers = {
        "Accept": (
            "text/html,application/xhtml+xml,application/rss+xml,"
            "application/atom+xml,application/xml;q=0.9,*/*;q=0.8"
        ),
        "User-Agent": "NewslyFeedResearch/1.0",
        **(headers or {}),
    }
    header_args: list[str] = []
    for name, value in request_headers.items():
        clean_name = str(name).replace("\r", "").replace("\n", "").strip()
        clean_value = str(value).replace("\r", "").replace("\n", "").strip()
        if clean_name and clean_value:
            header_args.extend(("--header", f"{clean_name}: {clean_value}"))

    args = [
        "curl",
        "--location",
        "--silent",
        "--show-error",
        "--compressed",
        "--connect-timeout",
        _format_timeout_seconds(connect_timeout_seconds),
        "--max-time",
        _format_timeout_seconds(max_time_seconds),
        "--max-filesize",
        str(MAX_FEED_RESPONSE_BYTES),
        *header_args,
        "--dump-header",
        header_path,
        "--output",
        body_path,
        "--write-out",
        "%{url_effective}\\n%{http_code}",
        url,
    ]
    return "mkdir -p scratch && " + shlex.join(args)


def _format_timeout_seconds(seconds: float) -> str:
    return f"{seconds:.6f}".rstrip("0").rstrip(".")


def _parse_curl_metadata(stdout: str) -> tuple[str, int]:
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if len(lines) < 2:
        raise FeedResearchRuntimeError("Sandbox feed request returned invalid curl metadata")
    effective_url = lines[-2]
    try:
        status_code = int(lines[-1])
    except ValueError as exc:
        raise FeedResearchRuntimeError(
            "Sandbox feed request returned an invalid HTTP status"
        ) from exc
    if status_code < 100 or status_code > 599:
        raise FeedResearchRuntimeError("Sandbox feed request returned an invalid HTTP status")
    return effective_url, status_code


def _parse_final_headers(raw_headers: bytes) -> dict[str, str]:
    blocks = [block for block in _HEADER_BLOCK_SEPARATOR.split(raw_headers) if block.strip()]
    for block in reversed(blocks):
        lines = block.splitlines()
        if not lines or not lines[0].startswith(b"HTTP/"):
            continue
        headers: dict[str, str] = {}
        for raw_line in lines[1:]:
            if b":" not in raw_line:
                continue
            raw_name, raw_value = raw_line.split(b":", 1)
            name = raw_name.decode("latin-1").strip()
            value = raw_value.decode("latin-1").strip()
            if name.lower() in {"content-encoding", "content-length"}:
                continue
            if name:
                headers[name] = value
        return headers
    return {}
