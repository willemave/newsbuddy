"""Helpers for deterministic image URLs."""

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

IMAGE_VERSION_QUERY_PARAM = "v"


def _normalized_version(version: object | None) -> str | None:
    if version is None:
        return None
    normalized = str(version).strip()
    return normalized or None


def append_image_version(url: str, version: object | None) -> str:
    """Append an immutable-cache version query to generated static image URLs."""
    normalized = _normalized_version(version)
    if normalized is None:
        return url

    split = urlsplit(url)
    if not split.path.startswith("/static/images/"):
        return url

    query_items = parse_qsl(split.query, keep_blank_values=True)
    if any(key == IMAGE_VERSION_QUERY_PARAM for key, _value in query_items):
        return url

    query_items.append((IMAGE_VERSION_QUERY_PARAM, normalized))
    return urlunsplit(
        (
            split.scheme,
            split.netloc,
            split.path,
            urlencode(query_items),
            split.fragment,
        )
    )


def build_content_image_url(content_id: int, *, version: object | None = None) -> str:
    """Build the URL for a generated content image."""
    return append_image_version(f"/static/images/content/{content_id}.png", version)


def build_news_thumbnail_url(content_id: int, *, version: object | None = None) -> str:
    """Build the URL for a generated news thumbnail image."""
    return append_image_version(f"/static/images/news_thumbnails/{content_id}.png", version)


def build_thumbnail_url(content_id: int, *, version: object | None = None) -> str:
    """Build the URL for a 200px thumbnail image."""
    return append_image_version(f"/static/images/thumbnails/{content_id}.png", version)
