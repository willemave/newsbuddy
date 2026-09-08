"""Own only the disposable local processes/database created for this run."""

from __future__ import annotations

import getpass
import json
import os
import shlex
import signal
import socket
import subprocess
import time
import uuid
from pathlib import Path
from typing import IO, Any
from urllib.parse import quote, unquote, urlsplit, urlunsplit

import httpx

from newsly_evals.chat.stubs import StubServer

# Match ChatAgentConfig/ShareActionAgentConfig defaults; keep these visible in run.json.
CHAT_RUNTIME_LIMITS = {
    "CHAT_HISTORY_MESSAGE_LIMIT": "200",
    "CHAT_OUTPUT_TOKEN_LIMIT": "8000",
    "LLM_TASK_SANDBOX_REQUEST_LIMIT": "8",
    "LLM_TASK_SANDBOX_TOOL_CALL_LIMIT": "32",
    "LLM_TASK_SANDBOX_MAX_OUTPUT_CHARS": "20000",
    "LLM_TASK_SANDBOX_TIMEOUT_SECONDS": "300",
}


def local_url(value: str, schemes: set[str]) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in schemes
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("expected a loopback URL without query or fragment")
    return value


def load_environment(path: Path | None) -> dict[str, str]:
    result = dict(os.environ)
    if path:
        for line in path.read_text().splitlines():
            line = line.removeprefix("export ").strip()
            if not line or line.startswith("#"):
                continue
            key, separator, value = line.partition("=")
            if not separator or not key.strip().isidentifier():
                raise ValueError("invalid env-file entry")
            words = shlex.split(value, comments=True)
            result[key.strip()] = " ".join(words)
    return result


def psql(url: str, sql: str) -> str:
    local_url(url, {"postgresql", "postgres"})
    parsed = urlsplit(url)
    pg_env = {key: value for key, value in os.environ.items() if not key.startswith("PG")}
    pg_env.update(
        {
            "PGHOST": parsed.hostname or "localhost",
            "PGPORT": str(parsed.port or 5432),
            "PGDATABASE": unquote(parsed.path.lstrip("/")),
            "PGCONNECT_TIMEOUT": "5",
        }
    )
    if parsed.username:
        pg_env["PGUSER"] = unquote(parsed.username)
    if parsed.password:
        pg_env["PGPASSWORD"] = unquote(parsed.password)
    result = subprocess.run(
        ["psql", "-X", "-qAt", "-v", "ON_ERROR_STOP=1"],
        input=sql,
        text=True,
        capture_output=True,
        timeout=60,
        env=pg_env,
    )
    if result.returncode:
        # SQL contains fixture values only; do not print connection strings or server errors.
        raise RuntimeError("psql failed while applying local fixture SQL")
    return result.stdout.strip()


def stop(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)


class LocalRuntime:
    def __init__(
        self,
        *,
        postgres_url: str,
        binary_dir: Path,
        output: Path,
        environment: dict[str, str],
        stubs: StubServer,
        keep: bool = False,
        workers: tuple[str, ...] = ("chat",),
    ) -> None:
        self.postgres_url = local_url(postgres_url, {"postgresql", "postgres"})
        self.name = "newsly_eval_" + uuid.uuid4().hex[:16]
        parsed = urlsplit(postgres_url)
        if parsed.username is None:
            parsed = parsed._replace(netloc=quote(getpass.getuser(), safe="") + "@" + parsed.netloc)
            self.postgres_url = urlunsplit(parsed)
        self.database_url = urlunsplit(parsed._replace(path="/" + self.name))
        self.binary_dir, self.output = binary_dir.resolve(), output.resolve()
        self.environment, self.stubs, self.keep = environment, stubs, keep
        self.processes: list[subprocess.Popen[bytes]] = []
        self.logs: list[IO[bytes]] = []
        self.created = False
        self.workers = workers
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            self.port = reservation.getsockname()[1]
        self.api_url = f"http://127.0.0.1:{self.port}"

    def __enter__(self) -> LocalRuntime:
        for binary in ["newsly-db", "newsly-api", "newsly-worker"]:
            if not (self.binary_dir / binary).is_file():
                raise ValueError(
                    f"missing binary: {self.binary_dir / binary}; build Rust binaries first"
                )
        try:
            self.output.mkdir(parents=True, exist_ok=True)
            psql(self.postgres_url, f'CREATE DATABASE "{self.name}";')
            self.created = True
            env = {
                **self.environment,
                "DATABASE_URL": self.database_url,
                "NEWSLY_DATABASE_URL": self.database_url,
                "ENVIRONMENT": "development",
                "DEBUG": "true",
                "NEWSLY_RUST_BIND_ADDR": f"127.0.0.1:{self.port}",
                "JWT_SECRET_KEY": uuid.uuid4().hex + uuid.uuid4().hex,
                "ADMIN_PASSWORD": uuid.uuid4().hex,
                "CONTENT_BODY_STORAGE_PROVIDER": "local",
                "CONTENT_BODY_LOCAL_ROOT": str(self.output / "bodies"),
                "MEDIA_BASE_DIR": str(self.output / "media"),
                "PUBLIC_BASE_URL": self.api_url,
                "EXA_API_BASE_URL": self.stubs.url,
                "EXA_API_KEY": "eval-stub",
                "E2B_API_KEY": "eval-disabled",
                "LLM_TASK_SANDBOX_E2B_API_KEY": "eval-disabled",
                "HTTP_PROXY": self.stubs.url,
                "HTTPS_PROXY": self.stubs.url,
                "ALL_PROXY": self.stubs.url,
                "NO_PROXY": "localhost,127.0.0.1,::1",
                "http_proxy": self.stubs.url,
                "https_proxy": self.stubs.url,
                "all_proxy": self.stubs.url,
                "no_proxy": "localhost,127.0.0.1,::1",
                "CHAT_HISTORY_MESSAGE_LIMIT": "30",
                "CHAT_OUTPUT_TOKEN_LIMIT": "2000",
                "LLM_TASK_SANDBOX_REQUEST_LIMIT": "6",
                "LLM_TASK_SANDBOX_TOOL_CALL_LIMIT": "12",
                "MAX_TASK_RETRIES": "0",
            }
            if self.workers == ("chat",):
                env.update(CHAT_RUNTIME_LIMITS)
                env["RUST_LOG"] = "warn,newsly_worker=info"
            if "briefing_refresh" in self.workers:
                env.update(
                    {"OPENROUTER_BASE_URL": self.stubs.url + "/", "OPENROUTER_API_KEY": "eval-stub"}
                )
            # The chat worker initializes this unrelated gateway even for non-OpenAI candidates.
            env.setdefault("OPENAI_API_KEY", "eval-unconfigured")
            migration = subprocess.run(
                [str(self.binary_dir / "newsly-db"), "migrate", "--maintenance-barrier-confirmed"],
                env=env,
                capture_output=True,
                timeout=180,
            )
            (self.output / "migration.log").write_bytes(migration.stdout + migration.stderr)
            if migration.returncode:
                raise RuntimeError("database migration failed; see migration.log")
            for binary, extra in [
                ("newsly-api", {}),
                *[("newsly-worker", {"NEWSLY_WORKER_PROCESS": worker}) for worker in self.workers],
            ]:
                log_name = extra.get("NEWSLY_WORKER_PROCESS", binary)
                log = (self.output / (log_name + ".log")).open("wb")
                self.logs.append(log)
                self.processes.append(
                    subprocess.Popen(
                        [str(self.binary_dir / binary)],
                        env={**env, **extra},
                        stdout=log,
                        stderr=log,
                        start_new_session=True,
                    )
                )
            deadline = time.monotonic() + 30
            with httpx.Client(trust_env=False, timeout=2) as client:
                while time.monotonic() < deadline:
                    self.check_processes()
                    try:
                        if client.get(self.api_url + "/health/ready").status_code == 200:
                            return self
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.25)
            raise RuntimeError("local API did not become ready")
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def check_processes(self) -> None:
        if any(process.poll() is not None for process in self.processes):
            raise RuntimeError("a Newsly process exited; see runtime logs")

    def seed(self, sql: str) -> dict[str, int]:
        return dict(json.loads(psql(self.database_url, sql)))

    def __exit__(self, *_: Any) -> None:
        for process in reversed(self.processes):
            stop(process)
        for log in self.logs:
            log.close()
        if self.created and not self.keep:
            psql(self.postgres_url, f'DROP DATABASE "{self.name}" WITH (FORCE);')
            self.created = False
