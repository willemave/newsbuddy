"""Exercise only the public HTTP surface after SQL bootstrap."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx

from newsly_evals.chat.runtime import local_url


class CandidateTurnError(RuntimeError):
    def __init__(self, status: dict[str, Any]) -> None:
        self.status = status
        detail = status.get("error") or "no public error detail"
        super().__init__(f"candidate turn ended with status {status['status']}: {detail}")


class ChatClient:
    def __init__(self, base_url: str, user_id: int, *, timeout: float = 180) -> None:
        self.client = httpx.Client(
            base_url=local_url(base_url, {"http", "https"}),
            timeout=15,
            trust_env=False,
            follow_redirects=False,
        )
        self.timeout = timeout
        try:
            login = self.request(
                "POST",
                "/auth/debug/new-user",
                {
                    "user_id": user_id,
                    "has_completed_onboarding": True,
                },
            )
            self.client.headers["Authorization"] = "Bearer " + login["access_token"]
        except BaseException:
            self.client.close()
            raise

    def request_json(self, method: str, path: str, payload: Any = None) -> Any:
        try:
            response = self.client.request(method, path, json=payload)
        except httpx.HTTPError as error:
            raise RuntimeError(f"Newsly {method} {path} connection failed") from error
        if response.is_error:
            raise RuntimeError(f"Newsly {method} {path} returned HTTP {response.status_code}")
        response.raise_for_status()
        return response.json()

    def request(self, method: str, path: str, payload: Any = None) -> dict[str, Any]:
        value = self.request_json(method, path, payload)
        if not isinstance(value, dict):
            raise ValueError(f"Newsly {path} did not return an object")
        return value

    def set_model(self, session_id: int, model: str) -> None:
        provider, separator, _ = model.partition(":")
        if not separator or provider not in {"openai", "anthropic", "openrouter"}:
            raise ValueError("model must be provider:model, e.g. openai:gpt-5.6-terra")
        self.request(
            "PATCH",
            f"/api/content/chat/sessions/{session_id}",
            {"llm_provider": provider, "llm_model_hint": model},
        )

    def turn(
        self,
        session_id: int,
        message: str,
        context: dict[str, Any],
        check_processes: Callable[[], None] = lambda: None,
    ) -> dict[str, Any]:
        accepted = self.request(
            "POST",
            "/api/content/chat/assistant/turns",
            {
                "session_id": session_id,
                "message": message,
                "screen_context": context,
            },
        )
        message_id = accepted["message_id"]
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            check_processes()
            status = self.request("GET", f"/api/content/chat/messages/{message_id}/status")
            if status["status"] == "completed":
                assistant = status.get("assistant_message")
                if not assistant or not isinstance(assistant.get("content"), str):
                    raise ValueError("completed turn has no final assistant message")
                transcript = self.request("GET", f"/api/content/chat/sessions/{session_id}")
                return {
                    "query": message,
                    "message_id": message_id,
                    "answer": assistant["content"],
                    "assistant": assistant,
                    "session": transcript["session"],
                    "messages": transcript["messages"],
                }
            if status["status"] != "processing":
                raise CandidateTurnError(status)
            time.sleep(min(0.5, max(0, deadline - time.monotonic())))
        raise TimeoutError("candidate turn timed out")

    def content_state(self, ids: dict[str, int]) -> dict[str, Any]:
        result = {}
        for name, content_id in ids.items():
            detail = self.request("GET", f"/api/content/{content_id}")
            result[name] = {
                key: detail[key] for key in ("is_read", "is_saved_to_knowledge") if key in detail
            }
            if not result[name]:
                raise ValueError("content detail did not expose read/saved state")
        return result

    def subscription_state(self) -> list[dict[str, Any]]:
        values = self.request_json("GET", "/api/scrapers/")
        if not isinstance(values, list):
            raise ValueError("scraper listing did not return an array")
        return sorted(
            [{key: item[key] for key in ("id", "feed_url", "is_active")} for item in values],
            key=lambda item: item["id"],
        )

    def close(self) -> None:
        self.client.close()
