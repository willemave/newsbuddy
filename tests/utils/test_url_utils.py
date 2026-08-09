"""Tests for shared URL and hostname boundary helpers."""

from __future__ import annotations

import pytest

from app.utils.url_utils import is_domain_or_subdomain, is_http_url, normalize_http_url


@pytest.mark.parametrize(
    ("raw_url", "expected"),
    [
        ("https://example.com/path?q=1#fragment", "https://example.com/path?q=1#fragment"),
        ("http://example.com/path", "https://example.com/path"),
        ("example.com/path", "https://example.com/path"),
        ("example.com:8080/path", "https://example.com:8080/path"),
        ("localhost", "https://localhost"),
        ("internal", "https://internal"),
        ("//example.com/path", "https://example.com/path"),
    ],
)
def test_normalize_http_url_accepts_http_and_schemeless_hosts(raw_url: str, expected: str) -> None:
    assert normalize_http_url(raw_url) == expected


@pytest.mark.parametrize(
    "raw_url",
    [
        "",
        "/relative/path",
        "./relative/path",
        "../relative/path",
        "mailto:user@example.com",
        "javascript:alert(1)",
        "https:example.com",
        "http:///missing-host",
        "https://exa mple.com",
        "https://example.com:not-a-port",
    ],
)
def test_normalize_http_url_rejects_relative_or_malformed_values(raw_url: str) -> None:
    assert normalize_http_url(raw_url) is None


def test_is_http_url_requires_an_explicit_valid_http_authority() -> None:
    assert is_http_url("https://example.com/path") is True
    assert is_http_url("http://localhost:8080") is True
    assert is_http_url("example.com/path") is False
    assert is_http_url("https://example.com:not-a-port") is False


@pytest.mark.parametrize(
    "hostname",
    ["example.com", "www.example.com", "deep.api.example.com", "EXAMPLE.COM."],
)
def test_is_domain_or_subdomain_accepts_only_dns_boundaries(hostname: str) -> None:
    assert is_domain_or_subdomain(hostname, "example.com") is True


@pytest.mark.parametrize(
    "hostname",
    [None, "", "notexample.com", "example.com.evil.test", "evil-example.com"],
)
def test_is_domain_or_subdomain_rejects_lookalikes(hostname: str | None) -> None:
    assert is_domain_or_subdomain(hostname, "example.com") is False
