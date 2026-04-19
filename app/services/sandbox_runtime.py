"""Sandbox-backed access to one user's personal markdown library."""

from __future__ import annotations

import re
import shlex
import shutil
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from app.core.settings import get_settings
from app.services.personal_markdown_library import get_personal_markdown_user_root
from app.services.vendor_costs import record_vendor_usage_out_of_band

SEARCH_TOKEN_STOPWORDS = {
    "a",
    "an",
    "and",
    "article",
    "file",
    "files",
    "find",
    "first",
    "for",
    "give",
    "in",
    "library",
    "markdown",
    "me",
    "my",
    "of",
    "path",
    "quote",
    "saved",
    "search",
    "sentence",
    "summary",
    "the",
    "used",
    "you",
}


class SandboxRuntimeUnavailableError(RuntimeError):
    """Raised when the configured sandbox runtime cannot be started."""


@dataclass(frozen=True)
class SandboxCommandOutput:
    """Normalized command output returned by a sandbox backend."""

    stdout: str
    stderr: str
    exit_code: int


class PersonalLibrarySandboxSession(ABC):
    """Abstract interface over a hydrated personal markdown library."""

    provider: str

    @abstractmethod
    def list_files(self, *, subpath: str = "", limit: int = 200) -> str:
        """List markdown files relative to the library root."""

    @abstractmethod
    def search_files(self, *, query: str, glob: str = "*.md", limit: int = 20) -> str:
        """Search markdown files for matching text."""

    @abstractmethod
    def read_file(self, *, relative_path: str, max_chars: int = 12_000) -> str:
        """Read one markdown file relative to the library root."""

    @abstractmethod
    def close(self) -> None:
        """Release sandbox resources."""


@dataclass
class LocalPersonalLibrarySandboxSession(PersonalLibrarySandboxSession):
    """Local filesystem-backed sandbox session used in development and tests."""

    library_root: Path
    provider: str = "local"

    def list_files(self, *, subpath: str = "", limit: int = 200) -> str:
        target = _resolve_local_relative_path(self.library_root, subpath)
        if not target.exists():
            return "No personal markdown files available."

        files = sorted(path for path in target.rglob("*.md") if path.is_file())
        if not files:
            return "No personal markdown files available."

        rendered = [str(path.relative_to(self.library_root)) for path in files[:limit]]
        if len(files) > limit:
            rendered.append(f"... truncated to {limit} files")
        return "\n".join(rendered)

    def search_files(self, *, query: str, glob: str = "*.md", limit: int = 20) -> str:
        cleaned_query = query.strip()
        if not cleaned_query:
            return "Search query is empty."

        rg_path = shutil.which("rg")
        if rg_path is None:
            return _python_search_fallback(
                library_root=self.library_root,
                query=cleaned_query,
                glob=glob,
                limit=limit,
            )

        result = subprocess.run(
            [
                rg_path,
                "-n",
                "-S",
                "--glob",
                glob,
                "--max-count",
                str(limit),
                cleaned_query,
                ".",
            ],
            cwd=self.library_root,
            capture_output=True,
            text=True,
            check=False,
        )
        stdout = (result.stdout or "").strip()
        if stdout:
            return stdout
        stderr = (result.stderr or "").strip()
        if result.returncode not in {0, 1} and stderr:
            return f"Search failed: {stderr}"
        return _python_search_fallback(
            library_root=self.library_root,
            query=cleaned_query,
            glob=glob,
            limit=limit,
        )

    def read_file(self, *, relative_path: str, max_chars: int = 12_000) -> str:
        path = _resolve_local_relative_path(self.library_root, relative_path)
        if not path.exists() or not path.is_file():
            return f"File not found: {relative_path}"

        text = path.read_text(encoding="utf-8")
        if len(text) <= max_chars:
            return text
        return f"{text[:max_chars]}\n\n[... truncated ...]"

    def close(self) -> None:
        return


@dataclass
class E2BPersonalLibrarySandboxSession(PersonalLibrarySandboxSession):
    """E2B-backed sandbox session hydrated with one user's markdown library."""

    user_id: int
    local_root: Path
    provider: str = "e2b"

    def __post_init__(self) -> None:
        settings = get_settings()
        api_key = settings.chat_sandbox_e2b_api_key
        if not api_key:
            raise SandboxRuntimeUnavailableError("E2B API key is not configured")

        try:
            from e2b_code_interpreter import Sandbox
        except ImportError as exc:  # pragma: no cover - exercised when dependency missing
            raise SandboxRuntimeUnavailableError("e2b-code-interpreter is not installed") from exc

        create_kwargs: dict[str, Any] = {
            "timeout": settings.chat_sandbox_timeout_seconds,
            "allow_internet_access": settings.chat_sandbox_allow_internet_access,
            "api_key": api_key,
        }
        if settings.chat_sandbox_template:
            create_kwargs["template"] = settings.chat_sandbox_template

        self._sandbox = Sandbox.create(**create_kwargs)
        self._library_root = PurePosixPath(settings.chat_sandbox_library_root)
        self._max_output_chars = settings.chat_sandbox_max_output_chars
        record_vendor_usage_out_of_band(
            provider="e2b",
            model=settings.chat_sandbox_template or "default",
            feature="chat_sandbox",
            operation="chat_sandbox.e2b_create",
            source="chat",
            usage={"request_count": 1},
            user_id=self.user_id,
            metadata={
                "allow_internet_access": settings.chat_sandbox_allow_internet_access,
                "timeout_seconds": settings.chat_sandbox_timeout_seconds,
            },
        )
        self._hydrate()

    def list_files(self, *, subpath: str = "", limit: int = 200) -> str:
        target = self._resolve_sandbox_relative_path(subpath)
        result = self._run_command(
            f"cd {shlex.quote(target)} && find . -type f -name '*.md' | sort | head -n {limit}"
        )
        stdout = result.stdout.strip()
        return stdout or "No personal markdown files available."

    def search_files(self, *, query: str, glob: str = "*.md", limit: int = 20) -> str:
        cleaned_query = query.strip()
        if not cleaned_query:
            return "Search query is empty."
        return self._python_search_fallback(
            query=cleaned_query,
            glob=glob,
            limit=limit,
        )

    def read_file(self, *, relative_path: str, max_chars: int = 12_000) -> str:
        full_path = self._resolve_sandbox_relative_path(relative_path)
        payload = self._sandbox.files.read(full_path)
        text = payload.decode("utf-8") if isinstance(payload, bytes) else str(payload)
        if len(text) <= max_chars:
            return text
        return f"{text[:max_chars]}\n\n[... truncated ...]"

    def close(self) -> None:
        try:
            self._sandbox.kill()
        except Exception:
            return

    def _hydrate(self) -> None:
        if not self.local_root.exists():
            return
        for path in sorted(self.local_root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(self.local_root).as_posix()
            destination = (self._library_root / relative).as_posix()
            self._sandbox.files.write(destination, path.read_text(encoding="utf-8"))

    def _run_command(self, command: str) -> SandboxCommandOutput:
        result = self._sandbox.commands.run(command)
        stdout = _truncate_output(str(getattr(result, "stdout", "") or ""), self._max_output_chars)
        stderr = _truncate_output(str(getattr(result, "stderr", "") or ""), self._max_output_chars)
        exit_code = int(getattr(result, "exit_code", getattr(result, "exitCode", 0)) or 0)
        return SandboxCommandOutput(stdout=stdout, stderr=stderr, exit_code=exit_code)

    def _python_search_fallback(
        self,
        *,
        query: str,
        glob: str,
        limit: int,
    ) -> str:
        search_terms = _extract_search_terms(query)
        script = f"""
import fnmatch
import pathlib
import re

root = pathlib.Path({self._library_root.as_posix()!r})
query = {query!r}.lower()
search_terms = {search_terms!r}
glob = {glob!r}
limit = {int(limit)}
candidates = []


def score_text(text, path_weight):
    lowered = text.lower()
    score = 0
    if query and query in lowered:
        score += 12
    score += sum(path_weight for term in search_terms if term in lowered)
    return score

for path in sorted(root.rglob('*')):
    if not path.is_file():
        continue
    if not fnmatch.fnmatch(path.name, glob):
        continue
    relative_path = str(path.relative_to(root))
    path_score = score_text(relative_path, 3)
    line_hits = []
    for line_number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), start=1):
        line_score = score_text(line, 1)
        if line_score <= 0:
            continue
        line_hits.append((line_score, f"{{relative_path}}:{{line_number}}:{{line}}"))
    if path_score <= 0 and not line_hits:
        continue
    line_hits.sort(key=lambda item: item[0], reverse=True)
    rendered_hits = [hit for _, hit in line_hits[:3]]
    if not rendered_hits:
        rendered_hits = [f"{{relative_path}}:path-match"]
    total_score = path_score + sum(score for score, _ in line_hits[:3])
    candidates.append((total_score, relative_path, rendered_hits))

rendered = []
for _, _, hits in sorted(candidates, key=lambda item: (-item[0], item[1])):
    for hit in hits:
        rendered.append(hit)
        if len(rendered) >= limit:
            break
    if len(rendered) >= limit:
        break

if rendered:
    print("\\n".join(rendered))
"""
        command = f"python3 - <<'PY'\n{script}\nPY"
        result = self._run_command(command)
        stdout = result.stdout.strip()
        if stdout:
            return stdout
        if result.stderr.strip():
            return f"Search failed: {result.stderr.strip()}"
        return "No matches found in the personal markdown library."

    def _resolve_sandbox_relative_path(self, relative_path: str) -> str:
        candidate = PurePosixPath(relative_path.strip() or ".")
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError("Path must stay within the personal markdown library")
        return (self._library_root / candidate).as_posix()


def create_personal_library_sandbox_session(
    *,
    user_id: int,
) -> PersonalLibrarySandboxSession | None:
    """Create a sandbox session for one user's personal markdown library."""
    settings = get_settings()
    provider = settings.chat_sandbox_provider
    if provider == "disabled":
        return None

    local_root = get_personal_markdown_user_root(user_id)
    if provider == "local":
        return LocalPersonalLibrarySandboxSession(library_root=local_root)
    if provider == "e2b":
        return E2BPersonalLibrarySandboxSession(user_id=user_id, local_root=local_root)
    raise SandboxRuntimeUnavailableError(f"Unsupported sandbox provider: {provider}")


def _resolve_local_relative_path(library_root: Path, relative_path: str) -> Path:
    candidate = (library_root / relative_path.strip()).resolve()
    if candidate != library_root.resolve() and library_root.resolve() not in candidate.parents:
        raise ValueError("Path must stay within the personal markdown library")
    return candidate


def _python_search_fallback(
    *,
    library_root: Path,
    query: str,
    glob: str,
    limit: int,
) -> str:
    lowered_query = query.lower()
    search_terms = _extract_search_terms(query)
    candidates: list[tuple[int, str, list[str]]] = []

    for path in sorted(library_root.rglob(glob)):
        if not path.is_file():
            continue

        relative_path = str(path.relative_to(library_root))
        path_score = _score_search_text(relative_path, lowered_query, search_terms, path_weight=3)
        line_hits: list[tuple[int, str]] = []

        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            line_score = _score_search_text(line, lowered_query, search_terms, path_weight=1)
            if line_score <= 0:
                continue
            line_hits.append((line_score, f"{relative_path}:{line_number}:{line}"))

        if path_score <= 0 and not line_hits:
            continue

        top_line_hits = sorted(line_hits, key=lambda item: item[0], reverse=True)[:3]
        rendered_hits = [hit for _, hit in top_line_hits]
        if not rendered_hits:
            rendered_hits = [f"{relative_path}:path-match"]
        total_score = path_score + sum(score for score, _ in top_line_hits)
        candidates.append((total_score, relative_path, rendered_hits))

    if not candidates:
        return "No matches found in the personal markdown library."

    rendered: list[str] = []
    for _score, _path, hits in sorted(candidates, key=lambda item: (-item[0], item[1])):
        for hit in hits:
            rendered.append(hit)
            if len(rendered) >= limit:
                return "\n".join(rendered)
    if rendered:
        return "\n".join(rendered)
    return "No matches found in the personal markdown library."


def _truncate_output(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}\n\n[... truncated ...]"


def _extract_search_terms(query: str) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for raw_term in re.findall(r"[a-z0-9]+", query.lower()):
        if len(raw_term) < 2 or raw_term in SEARCH_TOKEN_STOPWORDS:
            continue
        if raw_term in seen:
            continue
        seen.add(raw_term)
        terms.append(raw_term)
    return terms


def _score_search_text(
    text: str,
    lowered_query: str,
    search_terms: list[str],
    *,
    path_weight: int,
) -> int:
    lowered_text = text.lower()
    score = 0
    if lowered_query and lowered_query in lowered_text:
        score += 12
    score += sum(path_weight for term in search_terms if term in lowered_text)
    return score
