"""E2B-backed network runtime for feed research and validation."""

from __future__ import annotations

import base64
import json
import threading
import weakref
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from time import monotonic
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4

import httpx
from sqlalchemy import text

from app.core.db import get_session_factory
from app.core.logging import get_logger
from app.services.agent_vm_runtime import (
    SYSTEM_USER_ID,
    AgentVmDeadlineExceeded,
    AgentVmError,
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
_NETWORK_LOCK_GUARD = threading.Lock()
_NETWORK_LOCKS: weakref.WeakValueDictionary[str, threading.Lock] = weakref.WeakValueDictionary()
_FEED_NETWORK_ADVISORY_LOCK_ID = 6_142_100_000


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


@runtime_checkable
class _NetworkPolicySession(Protocol):
    def set_allowed_outbound_hosts(self, hosts: list[str]) -> None: ...

    def reset_network_policy(self) -> None: ...


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
        result = self.fetch_many([url], headers=headers)[0]
        if isinstance(result, Exception):
            raise result
        return result

    def fetch_many(
        self,
        urls: list[str],
        *,
        headers: dict[str, str] | None = None,
    ) -> list[httpx.Response | Exception]:
        """Fetch a candidate batch in one VM command and isolate URL failures."""
        if not urls:
            return []
        for url in urls:
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
        command = _build_batch_curl_command(
            urls=urls,
            headers=headers,
            connect_timeout_seconds=connect_timeout_seconds,
            max_time_seconds=curl_timeout_seconds,
        )
        results: list[httpx.Response | Exception] = []
        try:
            with _candidate_network_scope(self._session, urls=urls):
                result = self._session.execute_bash(
                    command,
                    timeout_seconds=self._bounded_timeout(
                        FEED_FETCH_TIMEOUT_SECONDS + FEED_COMMAND_TIMEOUT_OVERHEAD_SECONDS
                    ),
                    max_output_chars=min(
                        60_000_000,
                        len(urls) * (MAX_FEED_RESPONSE_BYTES * 2 + 200_000),
                    ),
                )
            if result.exit_code != 0:
                raise FeedResearchRuntimeError(
                    "Sandbox feed batch command failed: " + result.stderr.strip()[:500]
                )
            rows = _parse_batch_rows(result.stdout, expected_count=len(urls))
            for url, row in zip(urls, rows, strict=True):
                curl_exit = _batch_integer(row, "curl_exit")
                if curl_exit != 0:
                    error = (
                        f"Sandbox feed request failed with curl exit {curl_exit}: "
                        f"{str(row.get('stderr') or '')[:500]}"
                    )
                    if curl_exit in _CANDIDATE_LOCAL_CURL_EXIT_CODES:
                        results.append(FeedResearchCandidateError(error))
                        continue
                    raise FeedResearchRuntimeError(error)
                response = _response_from_batch_row(url, row)
                if response.status_code >= 400:
                    try:
                        response.raise_for_status()
                    except httpx.HTTPStatusError as exc:
                        results.append(exc)
                        continue
                results.append(response)
        except AgentVmDeadlineExceeded as exc:
            raise FeedResearchDeadlineExceeded("Feed research deadline was exceeded") from exc
        except Exception as exc:
            self._record_fatal_error(exc)
            raise
        self._remaining_seconds()
        return results

    def _remaining_seconds(self) -> float:
        deadline = self._deadline
        if deadline is None:
            return float(FEED_FETCH_TIMEOUT_SECONDS)
        remaining = deadline - monotonic()
        if remaining < MIN_FEED_REQUEST_BUDGET_SECONDS:
            raise FeedResearchDeadlineExceeded("Feed research deadline was exceeded")
        return remaining

    def _bounded_timeout(self, maximum_seconds: float) -> float:
        deadline = self._deadline
        if deadline is None:
            return maximum_seconds
        remaining = deadline - monotonic()
        if remaining <= 0:
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
    paths = build_llm_task_paths(user_id=SYSTEM_USER_ID, llm_task_id=resolved_execution_id)
    create_session = session_factory or create_agent_vm_session
    session_kwargs: dict[str, Any] = {
        "user_id": SYSTEM_USER_ID,
        "llm_task_id": resolved_execution_id,
        "vm_namespace": f"user:{SYSTEM_USER_ID}",
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


def _build_batch_curl_command(
    *,
    urls: list[str],
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
    clean_headers: dict[str, str] = {}
    for name, value in request_headers.items():
        clean_name = str(name).replace("\r", "").replace("\n", "").strip()
        clean_value = str(value).replace("\r", "").replace("\n", "").strip()
        if clean_name and clean_value:
            clean_headers[clean_name] = clean_value

    payload = base64.b64encode(
        json.dumps(
            {
                "urls": urls,
                "headers": clean_headers,
                "connect_timeout": _format_timeout_seconds(connect_timeout_seconds),
                "max_time": _format_timeout_seconds(max_time_seconds),
                "max_bytes": MAX_FEED_RESPONSE_BYTES,
            },
            separators=(",", ":"),
        ).encode("utf-8")
    ).decode("ascii")
    return f"""python3 - <<'PY'
import base64
import concurrent.futures
import json
import pathlib
import subprocess
import tempfile

request = json.loads(base64.b64decode({payload!r}))

def fetch(entry):
    index, url = entry
    with tempfile.TemporaryDirectory(prefix="newsly-feed-") as directory:
        root = pathlib.Path(directory)
        body_path = root / "body"
        header_path = root / "headers"
        args = [
            "curl", "--location", "--silent", "--show-error", "--compressed",
            "--connect-timeout", request["connect_timeout"],
            "--max-time", request["max_time"],
            "--max-filesize", str(request["max_bytes"]),
        ]
        for name, value in request["headers"].items():
            args.extend(["--header", f"{{name}}: {{value}}"])
        args.extend([
            "--dump-header", str(header_path), "--output", str(body_path),
            "--write-out", "%{{url_effective}}\\n%{{http_code}}", url,
        ])
        completed = subprocess.run(args, capture_output=True, text=True, check=False)
        metadata = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
        effective_url = metadata[-2] if len(metadata) >= 2 else url
        try:
            status = int(metadata[-1]) if metadata else 0
        except ValueError:
            status = 0
        body = body_path.read_bytes() if body_path.exists() else b""
        raw_headers = header_path.read_bytes() if header_path.exists() else b""
        return {{
            "index": index,
            "url": url,
            "effective_url": effective_url,
            "status": status,
            "headers_b64": base64.b64encode(raw_headers).decode("ascii"),
            "body_b64": base64.b64encode(body).decode("ascii"),
            "curl_exit": completed.returncode,
            "stderr": completed.stderr[-2000:],
        }}

with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(request["urls"]))) as pool:
    rows = list(pool.map(fetch, enumerate(request["urls"])))
for row in sorted(rows, key=lambda item: item["index"]):
    print(json.dumps(row, separators=(",", ":")))
PY"""


def _format_timeout_seconds(seconds: float) -> str:
    return f"{seconds:.6f}".rstrip("0").rstrip(".")


def _parse_batch_rows(stdout: str, *, expected_count: int) -> list[dict[str, object]]:
    if "[... truncated ...]" in stdout:
        raise FeedResearchRuntimeError("Sandbox feed batch output exceeded its explicit limit")
    rows: list[dict[str, object]] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise FeedResearchRuntimeError("Sandbox feed batch returned invalid JSONL") from exc
        if not isinstance(row, dict):
            raise FeedResearchRuntimeError("Sandbox feed batch returned invalid JSONL")
        rows.append(row)
    if len(rows) != expected_count:
        raise FeedResearchRuntimeError("Sandbox feed batch returned an incomplete result set")
    return rows


def _response_from_batch_row(url: str, row: dict[str, object]) -> httpx.Response:
    try:
        status_code = _batch_integer(row, "status")
        content = base64.b64decode(str(row.get("body_b64") or ""), validate=True)
        raw_headers = base64.b64decode(str(row.get("headers_b64") or ""), validate=True)
    except (ValueError, TypeError) as exc:
        raise FeedResearchRuntimeError("Sandbox feed batch returned invalid response data") from exc
    if not 100 <= status_code <= 599:
        raise FeedResearchCandidateError("Feed candidate returned no valid HTTP status")
    if len(content) > MAX_FEED_RESPONSE_BYTES:
        raise FeedResearchCandidateError("Feed candidate exceeded the response size limit")
    effective_url = str(row.get("effective_url") or url)
    return httpx.Response(
        status_code,
        headers=_parse_final_headers(raw_headers),
        content=content,
        request=httpx.Request("GET", effective_url),
    )


def _batch_integer(row: dict[str, object], field: str) -> int:
    value = row.get(field)
    if value is None:
        return 0
    if not isinstance(value, (int, str)) or isinstance(value, bool):
        raise FeedResearchRuntimeError(f"Sandbox feed batch returned invalid {field}")
    try:
        return int(value)
    except ValueError as exc:
        raise FeedResearchRuntimeError(f"Sandbox feed batch returned invalid {field}") from exc


def _parse_final_headers(raw_headers: bytes) -> dict[str, str]:
    normalized = raw_headers.replace(b"\r\n", b"\n")
    blocks = [block for block in normalized.split(b"\n\n") if block.strip()]
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


@contextmanager
def _candidate_network_scope(
    session: AgentVmSession,
    *,
    urls: list[str],
) -> Iterator[None]:
    if not isinstance(session, _NetworkPolicySession):
        raise FeedResearchRuntimeError("Feed research sandbox cannot enforce network policy")
    sandbox_id = str(session.sandbox_id or id(session))
    with _NETWORK_LOCK_GUARD:
        lock = _NETWORK_LOCKS.get(sandbox_id)
        if lock is None:
            lock = threading.Lock()
            _NETWORK_LOCKS[sandbox_id] = lock
    hosts = sorted({str(httpx.URL(url).host) for url in urls if httpx.URL(url).host})
    with lock, _distributed_feed_network_lock():
        session.set_allowed_outbound_hosts(hosts)
        try:
            yield
        finally:
            session.reset_network_policy()


@contextmanager
def _distributed_feed_network_lock() -> Iterator[None]:
    """Serialize network-policy replacement for the shared system VM."""
    session_factory = get_session_factory()
    with session_factory() as db:
        if db.get_bind().dialect.name == "postgresql":
            db.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": _FEED_NETWORK_ADVISORY_LOCK_ID},
            )
        try:
            yield
        finally:
            db.rollback()
