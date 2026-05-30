"""arXiv Atom metadata extraction for display-only source context."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode, urlparse
from xml.etree import ElementTree

from app.core.logging import get_logger
from app.http_client.robust_http_client import RobustHttpClient
from app.models.metadata.source import (
    SourceMetadataAuthor,
    SourceMetadataCategory,
    SourceMetadataEnvelope,
)

logger = get_logger(__name__)

ARXIV_API_URL = "https://export.arxiv.org/api/query"
ARXIV_ABS_BASE_URL = "https://arxiv.org/abs"
ARXIV_PDF_BASE_URL = "https://arxiv.org/pdf"
ARXIV_NAMESPACES = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}

NEW_STYLE_ARXIV_ID_RE = re.compile(r"^\d{4}\.\d{4,5}(?:v\d+)?$")
OLD_STYLE_ARXIV_ID_RE = re.compile(r"^[A-Za-z-]+(?:\.[A-Za-z-]+)?/\d{7}(?:v\d+)?$")
SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+")


ArxivMetadataHttpClient = Any


def extract_arxiv_id(value: str | None) -> str | None:
    """Return a normalized arXiv identifier from an arXiv URL or raw ID."""
    if not isinstance(value, str) or not value.strip():
        return None

    candidate = value.strip()
    parsed = urlparse(candidate)
    if parsed.scheme and parsed.netloc:
        host = parsed.netloc.lower()
        if host != "arxiv.org" and not host.endswith(".arxiv.org"):
            return None

        path = parsed.path.strip("/")
        for prefix in ("abs/", "pdf/"):
            if path.lower().startswith(prefix):
                candidate = path[len(prefix) :]
                break
        else:
            return None

    candidate = candidate.strip().strip("/")
    if candidate.lower().endswith(".pdf"):
        candidate = candidate[:-4]
    candidate = candidate.strip()

    if NEW_STYLE_ARXIV_ID_RE.match(candidate) or OLD_STYLE_ARXIV_ID_RE.match(candidate):
        return candidate
    return None


def fetch_arxiv_source_metadata(
    url_or_id: str,
    *,
    http_client: ArxivMetadataHttpClient | None = None,
) -> SourceMetadataEnvelope | None:
    """Fetch and normalize display metadata for one arXiv URL or ID."""
    arxiv_id = extract_arxiv_id(url_or_id)
    if arxiv_id is None:
        return None

    client = http_client or RobustHttpClient(
        headers={"User-Agent": "Newsly/0.1 arxiv metadata fetcher"}
    )
    api_url = f"{ARXIV_API_URL}?{urlencode({'id_list': arxiv_id, 'max_results': '1'})}"
    try:
        response = client.get(api_url, timeout=10)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Unable to fetch arXiv metadata for %s: %s",
            arxiv_id,
            exc,
            extra={
                "component": "arxiv_metadata",
                "operation": "fetch_arxiv_source_metadata",
                "context_data": {"arxiv_id": arxiv_id},
            },
        )
        return None

    response_text = getattr(response, "text", None)
    if not isinstance(response_text, str) or not response_text.strip():
        logger.warning(
            "arXiv metadata response for %s did not contain text",
            arxiv_id,
            extra={
                "component": "arxiv_metadata",
                "operation": "fetch_arxiv_source_metadata",
                "context_data": {"arxiv_id": arxiv_id},
            },
        )
        return None

    return parse_arxiv_atom_source_metadata(response_text, requested_id=arxiv_id)


def parse_arxiv_atom_source_metadata(
    atom_xml: str,
    *,
    requested_id: str | None = None,
) -> SourceMetadataEnvelope | None:
    """Parse arXiv Atom XML into the display source metadata envelope."""
    try:
        root = ElementTree.fromstring(atom_xml)
    except ElementTree.ParseError as exc:
        logger.warning(
            "Unable to parse arXiv metadata XML: %s",
            exc,
            extra={"component": "arxiv_metadata", "operation": "parse_arxiv_atom"},
        )
        return None
    except TypeError as exc:
        logger.warning(
            "Invalid arXiv metadata XML payload: %s",
            exc,
            extra={"component": "arxiv_metadata", "operation": "parse_arxiv_atom"},
        )
        return None

    entry = root.find("atom:entry", ARXIV_NAMESPACES)
    if entry is None:
        return None

    entry_id_url = _text(entry, "atom:id")
    source_id = extract_arxiv_id(entry_id_url) or requested_id
    if source_id is None:
        return None

    abstract = _text(entry, "atom:summary")
    primary_category = _primary_category(entry)
    categories = _categories(entry, primary_term=primary_category)
    pdf_url = _pdf_url(entry) or f"{ARXIV_PDF_BASE_URL}/{source_id}"
    canonical_abs_url = _canonical_abs_url(entry_id_url, source_id=source_id)

    return SourceMetadataEnvelope(
        schema_version=1,
        kind="research_paper",
        provider="arxiv",
        source_id=source_id,
        canonical_abs_url=canonical_abs_url,
        pdf_url=pdf_url,
        title=_text(entry, "atom:title"),
        abstract=abstract,
        brief_synopsis=_brief_synopsis(abstract),
        authors=_authors(entry),
        categories=categories,
        published_at=_parse_datetime(_text(entry, "atom:published")),
        updated_at=_parse_datetime(_text(entry, "atom:updated")),
        doi=_text(entry, "arxiv:doi"),
        journal_ref=_text(entry, "arxiv:journal_ref"),
        comment=_text(entry, "arxiv:comment"),
        extracted_at=datetime.now(UTC),
    )


def _text(node: ElementTree.Element, path: str) -> str | None:
    value = node.findtext(path, namespaces=ARXIV_NAMESPACES)
    return _clean_text(value)


def _clean_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split()).strip()
    return cleaned or None


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _brief_synopsis(abstract: str | None) -> str | None:
    cleaned = _clean_text(abstract)
    if cleaned is None:
        return None

    sentences = [sentence.strip() for sentence in SENTENCE_BOUNDARY_RE.split(cleaned) if sentence]
    synopsis = " ".join(sentences[:2]) if sentences else cleaned
    if len(synopsis) <= 500:
        return synopsis

    truncated = synopsis[:500].rsplit(" ", 1)[0].rstrip(" ,;:")
    return f"{truncated}." if truncated and truncated[-1] not in ".!?" else truncated


def _authors(entry: ElementTree.Element) -> list[SourceMetadataAuthor]:
    authors: list[SourceMetadataAuthor] = []
    for author in entry.findall("atom:author", ARXIV_NAMESPACES):
        name = _text(author, "atom:name")
        if name is None:
            continue
        affiliation = _text(author, "arxiv:affiliation")
        authors.append(
            SourceMetadataAuthor(
                name=name,
                affiliation=affiliation,
                affiliation_source="arxiv_api" if affiliation else "missing",
                confidence="direct" if affiliation else "unknown",
            )
        )
    return authors


def _primary_category(entry: ElementTree.Element) -> str | None:
    primary = entry.find("arxiv:primary_category", ARXIV_NAMESPACES)
    if primary is None:
        return None
    return _clean_text(primary.attrib.get("term"))


def _categories(
    entry: ElementTree.Element,
    *,
    primary_term: str | None,
) -> list[SourceMetadataCategory]:
    categories: list[SourceMetadataCategory] = []
    seen: set[str] = set()

    if primary_term:
        categories.append(SourceMetadataCategory(term=primary_term, primary=True))
        seen.add(primary_term)

    for category in entry.findall("atom:category", ARXIV_NAMESPACES):
        term = _clean_text(category.attrib.get("term"))
        if term is None or term in seen:
            continue
        categories.append(SourceMetadataCategory(term=term, primary=term == primary_term))
        seen.add(term)
    return categories


def _pdf_url(entry: ElementTree.Element) -> str | None:
    for link in entry.findall("atom:link", ARXIV_NAMESPACES):
        href = _clean_text(link.attrib.get("href"))
        if href is None:
            continue
        title = _clean_text(link.attrib.get("title"))
        content_type = _clean_text(link.attrib.get("type"))
        if title == "pdf" or content_type == "application/pdf":
            return href.replace("http://", "https://", 1)
    return None


def _canonical_abs_url(entry_id_url: str | None, *, source_id: str) -> str:
    if entry_id_url:
        return entry_id_url.replace("http://", "https://", 1)
    return f"{ARXIV_ABS_BASE_URL}/{source_id}"
