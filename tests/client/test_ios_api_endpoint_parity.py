import re
from pathlib import Path

from app.main import app

REPO_ROOT = Path(__file__).resolve().parents[2]
SWIFT_ROOTS = (
    REPO_ROOT / "client/newsly/newsly",
    REPO_ROOT / "client/newsly/ShareExtension",
)
HTTP_PATH_LITERAL = re.compile(r'"(/(?:api|auth)/(?:[^"\\]|\\.)*)"')
SWIFT_INTERPOLATION = re.compile(r"\\\([^)]*\)")
OPENAPI_PARAMETER = re.compile(r"\{[^}]+\}")


def _normalize_path(path: str) -> str:
    normalized = SWIFT_INTERPOLATION.sub("{}", path)
    normalized = OPENAPI_PARAMETER.sub("{}", normalized)
    return normalized.rstrip("/") or "/"


def test_every_ios_http_path_exists_in_the_server_openapi_surface() -> None:
    """Keep app and Share Extension calls from silently pointing at removed routes."""
    client_paths = {
        _normalize_path(match.group(1))
        for root in SWIFT_ROOTS
        for source_path in root.rglob("*.swift")
        for match in HTTP_PATH_LITERAL.finditer(source_path.read_text())
    }
    server_paths = {_normalize_path(path) for path in app.openapi()["paths"]}

    assert sorted(client_paths - server_paths) == []
