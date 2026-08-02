from urllib.parse import parse_qs

import httpx
import pytest

from app.services import apple_account


def test_exchange_and_revoke_apple_authorization(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = apple_account.get_settings()
    monkeypatch.setattr(settings, "apple_client_id", "org.example.app")
    monkeypatch.setattr(settings, "apple_token_url", "https://apple.example/token")
    monkeypatch.setattr(settings, "apple_revoke_url", "https://apple.example/revoke")
    monkeypatch.setattr(apple_account, "_client_secret", lambda: "signed-secret")
    requests: list[tuple[str, dict[str, list[str]]]] = []

    class FakeClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *_args: object) -> None:
            pass

        def post(self, url: str, *, data: dict[str, str]) -> httpx.Response:
            requests.append((url, parse_qs(str(httpx.QueryParams(data)))))
            request = httpx.Request("POST", url)
            if url.endswith("/token"):
                return httpx.Response(200, request=request, json={"refresh_token": "grant"})
            return httpx.Response(200, request=request)

    monkeypatch.setattr(apple_account.httpx, "Client", FakeClient)

    apple_account.exchange_and_revoke_apple_authorization(" fresh-code ")

    assert requests == [
        (
            "https://apple.example/token",
            {
                "client_id": ["org.example.app"],
                "client_secret": ["signed-secret"],
                "code": ["fresh-code"],
                "grant_type": ["authorization_code"],
            },
        ),
        (
            "https://apple.example/revoke",
            {
                "client_id": ["org.example.app"],
                "client_secret": ["signed-secret"],
                "token": ["grant"],
                "token_type_hint": ["refresh_token"],
            },
        ),
    ]


def test_exchange_and_revoke_requires_authorization_code() -> None:
    with pytest.raises(ValueError, match="authorization code is required"):
        apple_account.exchange_and_revoke_apple_authorization("  ")
