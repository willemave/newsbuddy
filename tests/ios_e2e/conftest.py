"""Shared harness for Maestro-driven iOS E2E tests."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from collections.abc import Awaitable, Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import pytest
import requests
import uvicorn
from sqlalchemy.orm import Session, sessionmaker

from app.core.db import get_db_session, get_readonly_db_session
from app.main import app
from tests.support.fixture_files import encode_launch_fixture

REPO_ROOT = Path(__file__).resolve().parents[2]
IOS_E2E_DIR = Path(__file__).resolve().parent
MAESTRO_FLOW_DIR = IOS_E2E_DIR / "flows"
DEFAULT_APP_ID = "org.willemaw.newsly"


@dataclass(frozen=True)
class LiveServer:
    """Live FastAPI server metadata for Maestro-backed tests."""

    base_url: str


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_http_ready(base_url: str, timeout_seconds: float = 15.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None

    while time.monotonic() < deadline:
        try:
            response = requests.get(f"{base_url}/docs", timeout=0.5)
            if response.status_code < 500:
                return
        except requests.RequestException as exc:  # pragma: no cover - only hit on boot race
            last_error = exc
        time.sleep(0.1)

    message = f"Timed out waiting for live server at {base_url}"
    if last_error is not None:
        raise RuntimeError(message) from last_error
    raise RuntimeError(message)


def _require_booted_simulator() -> None:
    result = subprocess.run(
        ["xcrun", "simctl", "list", "devices", "booted"],
        capture_output=True,
        text=True,
        check=False,
    )
    if "Booted" not in result.stdout:
        pytest.skip("No booted iOS simulator. Use tests/scripts/ios_maestro.sh.")


@pytest.fixture
def maestro_bin() -> str:
    """Return the Maestro CLI path or skip if unavailable."""
    binary = shutil.which("maestro")
    if binary is None:
        pytest.skip("Maestro CLI not installed. Run tests/scripts/install_maestro.sh first.")
    return binary


@pytest.fixture
def live_server(db_session_factory: sessionmaker) -> Iterator[LiveServer]:
    """Expose the FastAPI app over HTTP with test DB dependency overrides."""
    def override_get_db() -> Iterator[Session]:
        db = db_session_factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    app.dependency_overrides[get_db_session] = override_get_db
    app.dependency_overrides[get_readonly_db_session] = override_get_db

    port = _find_free_port()
    base_url = f"http://127.0.0.1:{port}"
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    try:
        _wait_for_http_ready(base_url)
        yield LiveServer(base_url=base_url)
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        app.dependency_overrides.clear()


@pytest.fixture
def run_maestro_flow(maestro_bin: str) -> Callable[..., subprocess.CompletedProcess[str]]:
    """Run a Maestro flow with shared Newsly E2E launch arguments."""

    _require_booted_simulator()

    def _run(
        flow_name: str,
        *,
        live_server: LiveServer,
        user_id: int,
        extra_env: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        flow_path = MAESTRO_FLOW_DIR / flow_name
        if not flow_path.exists():
            raise FileNotFoundError(f"Missing Maestro flow: {flow_path}")

        parsed_url = urlparse(live_server.base_url)
        env_values: dict[str, str] = {
            "APP_ID": os.environ.get("NEWSLY_MAESTRO_APP_ID", DEFAULT_APP_ID),
            "SERVER_HOST": parsed_url.hostname or "127.0.0.1",
            "SERVER_PORT": str(parsed_url.port or 80),
            "USER_ID": str(user_id),
        }
        if extra_env:
            env_values.update({key: str(value) for key, value in extra_env.items()})

        rendered_flow = flow_path.read_text(encoding="utf-8")
        for key, value in env_values.items():
            escaped_value = value.replace("\\", "\\\\").replace('"', '\\"')
            rendered_flow = rendered_flow.replace(f"${{{key}}}", escaped_value)

        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=f"-{flow_path.name}",
            prefix="maestro-rendered-",
            delete=False,
        ) as temp_flow:
            temp_flow.write(rendered_flow)
            rendered_path = temp_flow.name

        try:
            result = subprocess.run(
                [maestro_bin, "test", rendered_path],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
                env={**os.environ, "PATH": os.environ.get("PATH", "")},
            )
        finally:
            Path(rendered_path).unlink(missing_ok=True)

        if result.returncode != 0:
            pytest.fail(
                "Maestro flow failed\n"
                f"flow={flow_path}\n"
                f"stdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}"
            )
        return result

    return _run


@pytest.fixture
def run_ios_flow(
    live_server: LiveServer,
    run_maestro_flow,
    test_user,
) -> Callable[..., subprocess.CompletedProcess[str]]:
    """Run a Maestro flow against the shared live server and default test user."""

    def _run(
        flow_name: str,
        *,
        extra_env: Mapping[str, str] | None = None,
        user_id: int | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return run_maestro_flow(
            flow_name,
            live_server=live_server,
            user_id=test_user.id if user_id is None else user_id,
            extra_env=extra_env,
        )

    return _run


@pytest.fixture
def completed_chat_processors_factory(
    db_session_factory: sessionmaker,
) -> Callable[..., tuple[Callable[..., Awaitable[None]], Callable[..., Awaitable[None]]]]:
    """Build deterministic async chat processor stubs backed by the test DB."""
    from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

    from app.services.chat_agent import update_message_completed

    def _build(
        *,
        assistant_reply: str,
    ) -> tuple[Callable[..., Awaitable[None]], Callable[..., Awaitable[None]]]:
        async def _process_message_async(
            session_id: int,
            message_id: int,
            prompt: str,
            *,
            source: str = "chat",
            task_id: int | None = None,
        ) -> None:
            del session_id, source, task_id
            worker_db = db_session_factory()
            try:
                update_message_completed(
                    worker_db,
                    message_id,
                    [
                        ModelRequest(parts=[UserPromptPart(content=prompt)]),
                        ModelResponse(parts=[TextPart(content=assistant_reply)]),
                    ],
                    display_user_prompt=prompt,
                )
            finally:
                worker_db.close()

        async def _process_assistant_turn_async(
            session_id: int,
            message_id: int,
            prompt: str,
            *,
            screen_context,
            source: str = "assistant",
        ) -> None:
            del screen_context
            await _process_message_async(
                session_id,
                message_id,
                prompt,
                source=source,
            )

        return _process_message_async, _process_assistant_turn_async

    return _build

@pytest.fixture
def ios_onboarding_personalized_fixture() -> str:
    """Return the personalized onboarding fixture encoded for launch arguments."""
    return encode_launch_fixture("ios_onboarding_personalized")
