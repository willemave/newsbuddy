"""Thin HTTP client for the remote Newsly agent CLI."""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic, sleep
from typing import Any

import requests

TERMINAL_JOB_STATUSES = {"completed", "failed", "skipped"}


class AgentClientError(RuntimeError):
    """Raised when the remote Newsly API request fails."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        payload: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


@dataclass(frozen=True)
class WaitOptions:
    """Polling configuration for client-side wait behavior."""

    interval_seconds: float = 2.0
    timeout_seconds: float = 120.0


class NewslyAgentClient:
    """Minimal authenticated HTTP client for Newsly server routes."""

    def __init__(
        self,
        *,
        server_url: str,
        api_key: str,
        timeout_seconds: float = 30.0,
        session: requests.Session | None = None,
    ) -> None:
        self._server_url = server_url.rstrip("/")
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._session = session or requests.Session()

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        """Send one API request and return the decoded JSON response."""
        response = self._session.request(
            method=method.upper(),
            url=self._build_url(path),
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            params=params,
            json=json_body,
            timeout=self._timeout_seconds,
        )
        payload = self._decode_response(response)
        if response.status_code >= 400:
            message = self._extract_error_message(payload, response.text)
            raise AgentClientError(
                message,
                status_code=response.status_code,
                payload=payload,
            )
        return payload

    def wait_for_job(self, job_id: int, options: WaitOptions | None = None) -> Any:
        """Poll a job endpoint until it reaches a terminal state."""
        wait_options = options or WaitOptions()
        started_at = monotonic()
        while True:
            job = self.request("GET", f"/api/jobs/{job_id}")
            status = str(job.get("status", "")).lower()
            if status in TERMINAL_JOB_STATUSES:
                return job
            if monotonic() - started_at >= wait_options.timeout_seconds:
                raise AgentClientError(
                    f"Timed out waiting for job {job_id}",
                    payload=job,
                )
            sleep(wait_options.interval_seconds)

    def _build_url(self, path: str) -> str:
        normalized_path = path if path.startswith("/") else f"/{path}"
        return f"{self._server_url}{normalized_path}"

    @staticmethod
    def _decode_response(response: requests.Response) -> Any:
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return {"raw": response.text}

    @staticmethod
    def _extract_error_message(payload: Any, fallback_text: str) -> str:
        if isinstance(payload, dict):
            detail = payload.get("detail")
            if isinstance(detail, str) and detail.strip():
                return detail
            message = payload.get("message")
            if isinstance(message, str) and message.strip():
                return message
        fallback = fallback_text.strip()
        if fallback:
            return fallback
        return "Remote request failed"
