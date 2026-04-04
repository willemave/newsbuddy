"""Shared harness for Maestro-driven iOS E2E tests."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import pytest
import requests
import uvicorn
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.db import get_db_session, get_readonly_db_session
from app.main import app
from app.models.schema import Base

REPO_ROOT = Path(__file__).resolve().parents[2]
MAESTRO_FLOW_DIR = REPO_ROOT / ".maestro" / "flows"
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
def test_db() -> Iterator:
    """Create a file-backed SQLite engine for threaded iOS E2E traffic."""
    with tempfile.NamedTemporaryFile(suffix="-ios-e2e.db", delete=False) as database_file:
        database_path = Path(database_file.name)

    engine = create_engine(
        f"sqlite:///{database_path}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    try:
        yield engine
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
        database_path.unlink(missing_ok=True)


@pytest.fixture
def live_server(test_db) -> Iterator[LiveServer]:
    """Expose the FastAPI app over HTTP with test DB dependency overrides."""
    session_local = sessionmaker(autocommit=False, autoflush=False, bind=test_db)

    def override_get_db() -> Iterator[Session]:
        db = session_local()
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
def run_maestro_flow(maestro_bin: str) -> callable:
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
