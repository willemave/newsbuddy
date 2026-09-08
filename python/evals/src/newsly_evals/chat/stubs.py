"""Reusable HTTP stubs, with an outbound proxy that permits selected model hosts."""

from __future__ import annotations

import json
import select
import socket
import threading
import time
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

from newsly_evals.chat.schema import Stub


class StubServer:
    def __init__(self, allowed_hosts: set[str] | None = None) -> None:
        self.allowed_hosts = allowed_hosts or set()
        self.rules: list[Stub] = []
        self.requests: list[dict[str, Any]] = []
        self.lock = threading.Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_: Any) -> None:
                pass

            def do_CONNECT(self) -> None:  # noqa: N802
                host, _, port = self.path.rpartition(":")
                if host not in owner.allowed_hosts or port != "443":
                    owner.record({"method": "CONNECT", "path": self.path, "matched": False})
                    self.send_error(502, "External destination has no mock")
                    return
                try:
                    with socket.create_connection((host, 443), timeout=15) as upstream:
                        self.send_response(200)
                        self.end_headers()
                        sockets = [self.connection, upstream]
                        while True:
                            readable, _, _ = select.select(sockets, [], [], 60)
                            if not readable:
                                break
                            for source in readable:
                                data = source.recv(65536)
                                if not data:
                                    return
                                target = upstream if source is self.connection else self.connection
                                target.sendall(data)
                except OSError:
                    self.close_connection = True

            def do_GET(self) -> None:  # noqa: N802
                self.respond()

            def do_POST(self) -> None:  # noqa: N802
                self.respond()

            def respond(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 <= length <= 1_000_000:
                    self.send_error(413)
                    return
                raw = self.rfile.read(length)
                try:
                    body = json.loads(raw) if raw else {}
                except ValueError:
                    body = {}
                path = urlsplit(self.path).path
                with owner.lock:
                    rule = next(
                        (
                            rule
                            for rule in owner.rules
                            if rule.method == self.command
                            and rule.path == path
                            and isinstance(body, dict)
                            and all(body.get(k) == v for k, v in rule.body_contains.items())
                        ),
                        None,
                    )
                # Never capture auth headers. Stub request bodies are synthetic eval inputs.
                owner.record(
                    {
                        "method": self.command,
                        "path": path,
                        "body": body,
                        "matched": rule is not None,
                    }
                )
                if rule is None:
                    self.send_error(502, "No matching scenario stub")
                    return
                time.sleep(rule.delay_seconds)
                response = rule.response
                if rule.embedding_vector is not None:
                    inputs = body.get("input", [])
                    if not isinstance(inputs, list):
                        inputs = [inputs]
                    response = {
                        "data": [
                            {"index": i, "embedding": rule.embedding_vector, "object": "embedding"}
                            for i in range(len(inputs))
                        ],
                        "model": body.get("model", "fixture"),
                        "usage": {"prompt_tokens": 0, "total_tokens": 0},
                    }
                payload = (rule.text if rule.text is not None else json.dumps(response)).encode()
                self.send_response(rule.status)
                self.send_header("Content-Type", rule.content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                with suppress(BrokenPipeError, ConnectionResetError):
                    self.wfile.write(payload)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def record(self, event: dict[str, Any]) -> None:
        with self.lock:
            self.requests.append(event)

    def reset(self, rules: list[Stub]) -> None:
        with self.lock:
            self.rules = list(rules)
            self.requests.clear()

    def __enter__(self) -> StubServer:
        self.thread.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
