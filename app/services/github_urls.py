"""Helpers for normalizing GitHub file URLs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import quote, unquote, urlparse


@dataclass(frozen=True)
class GitHubFileUrl:
    """Normalized details for a GitHub-hosted file URL."""

    owner: str
    repo: str
    ref: str
    path: str
    canonical_blob_url: str
    raw_url: str

    @property
    def filename(self) -> str:
        return PurePosixPath(self.path).name

    @property
    def repo_full_name(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def repo_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}"

    @property
    def is_pdf(self) -> bool:
        return self.filename.lower().endswith(".pdf")


def parse_github_file_url(url: str) -> GitHubFileUrl | None:
    """Parse GitHub blob/raw file URLs and return canonical raw/blob URLs."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"}:
        return None

    host = parsed.netloc.lower()
    if host in {"github.com", "www.github.com"}:
        return _parse_github_dot_com_path(parsed.path)
    if host == "raw.githubusercontent.com":
        return _parse_raw_githubusercontent_path(parsed.path)
    return None


def normalize_github_file_url_to_raw(url: str) -> str | None:
    """Return the raw.githubusercontent.com URL for a GitHub file URL."""
    parsed = parse_github_file_url(url)
    return parsed.raw_url if parsed is not None else None


def _parse_github_dot_com_path(path: str) -> GitHubFileUrl | None:
    parts = [part for part in path.strip("/").split("/") if part]
    if len(parts) < 5 or parts[2] not in {"blob", "raw"}:
        return None
    owner, repo = unquote(parts[0]), unquote(parts[1]).removesuffix(".git")
    ref = unquote(parts[3])
    file_path = "/".join(unquote(part) for part in parts[4:])
    if not owner or not repo or not ref or not file_path:
        return None
    return _build_file_url(owner=owner, repo=repo, ref=ref, path=file_path)


def _parse_raw_githubusercontent_path(path: str) -> GitHubFileUrl | None:
    parts = [part for part in path.strip("/").split("/") if part]
    if len(parts) < 4:
        return None
    owner, repo = unquote(parts[0]), unquote(parts[1]).removesuffix(".git")
    ref = unquote(parts[2])
    file_path = "/".join(unquote(part) for part in parts[3:])
    if not owner or not repo or not ref or not file_path:
        return None
    return _build_file_url(owner=owner, repo=repo, ref=ref, path=file_path)


def _build_file_url(*, owner: str, repo: str, ref: str, path: str) -> GitHubFileUrl:
    quoted_path = "/".join(quote(part) for part in path.split("/"))
    quoted_ref = quote(ref)
    canonical_blob_url = f"https://github.com/{owner}/{repo}/blob/{quoted_ref}/{quoted_path}"
    raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{quoted_ref}/{quoted_path}"
    return GitHubFileUrl(
        owner=owner,
        repo=repo,
        ref=ref,
        path=path,
        canonical_blob_url=canonical_blob_url,
        raw_url=raw_url,
    )
