"""Extract and curate article outbound links for reader affordances."""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from pydantic import BaseModel, Field

from app.core.logging import get_logger
from app.core.model_defaults import CHEAP_MODEL_SPEC
from app.models.metadata.summaries import InterestingExternalLink
from app.services.llm_agents import get_basic_agent
from app.services.llm_models import resolve_model
from app.services.prompt_library import load_prompt, render_prompt
from app.services.vendor_usage import record_model_usage

logger = get_logger(__name__)

MAX_CANDIDATE_LINKS = 30
MAX_SELECTED_LINKS = 6
MAX_CONTEXT_CHARS = 240
LINK_SELECTION_TIMEOUT_SECONDS = 30.0
LINK_SELECTION_MODEL = CHEAP_MODEL_SPEC

MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[([^\]\n]{1,300})\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
PLAIN_URL_RE = re.compile(r"https?://[^\s<>\]\"')]+")
WHITESPACE_RE = re.compile(r"\s+")

TRACKING_QUERY_PREFIXES = ("utm_",)
TRACKING_QUERY_NAMES = {
    "fbclid",
    "gclid",
    "igshid",
    "mc_cid",
    "mc_eid",
    "mkt_tok",
    "ref",
    "ref_src",
    "source",
    "spm",
}
JUNK_PATH_SEGMENTS = {
    "about",
    "account",
    "advertise",
    "author",
    "category",
    "contact",
    "feed",
    "feeds",
    "login",
    "logout",
    "newsletter",
    "privacy",
    "rss",
    "signin",
    "sign-in",
    "signup",
    "sign-up",
    "subscribe",
    "tag",
    "tags",
    "terms",
}
ASSET_EXTENSIONS = (
    ".7z",
    ".avi",
    ".css",
    ".gif",
    ".ico",
    ".jpeg",
    ".jpg",
    ".js",
    ".mov",
    ".mp3",
    ".mp4",
    ".png",
    ".svg",
    ".webm",
    ".webp",
    ".zip",
)
SHARE_HOSTS = {
    "bsky.app",
    "facebook.com",
    "linkedin.com",
    "reddit.com",
    "threads.net",
    "twitter.com",
    "x.com",
}
SHARE_PATH_MARKERS = ("intent", "share", "sharer", "submit")

LINK_SELECTION_SYSTEM_PROMPT = load_prompt("content/interesting_links#system")


@dataclass(frozen=True)
class LinkCandidate:
    """One deterministic candidate extracted before LLM ranking."""

    url: str
    title: str | None
    context: str | None


class InterestingExternalLinksSelection(BaseModel):
    """Typed LLM response for curated article links."""

    links: list[InterestingExternalLink] = Field(
        default_factory=list,
        max_length=MAX_SELECTED_LINKS,
    )


def extract_interesting_link_candidates(
    content_text: str | None,
    *,
    source_url: str | None,
    limit: int = MAX_CANDIDATE_LINKS,
) -> list[LinkCandidate]:
    """Extract normalized non-spam external links from markdown/plain article text."""
    if not content_text or not content_text.strip():
        return []

    seen: set[str] = set()
    candidates: list[LinkCandidate] = []

    def add_candidate(raw_url: str, *, title: str | None, start: int, end: int) -> None:
        normalized = _normalize_candidate_url(raw_url, source_url)
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        candidates.append(
            LinkCandidate(
                url=normalized,
                title=_clean_text(title),
                context=_extract_context(content_text, start, end),
            )
        )

    for match in MARKDOWN_LINK_RE.finditer(content_text):
        add_candidate(match.group(2), title=match.group(1), start=match.start(), end=match.end())
        if len(candidates) >= limit:
            return candidates

    for match in PLAIN_URL_RE.finditer(content_text):
        add_candidate(match.group(0), title=None, start=match.start(), end=match.end())
        if len(candidates) >= limit:
            return candidates

    return candidates


def select_interesting_external_links(
    content_text: str | None,
    *,
    source_url: str | None,
    title: str | None = None,
    usage_persist: dict[str, Any] | None = None,
) -> list[InterestingExternalLink]:
    """Use an LLM to rank deterministic candidate links, failing closed on errors."""
    candidates = extract_interesting_link_candidates(content_text, source_url=source_url)
    if not candidates:
        return []

    try:
        provider, model_spec = resolve_model(None, LINK_SELECTION_MODEL)
        persist = dict(usage_persist or {})
        if persist and "provider" not in persist:
            persist["provider"] = provider

        agent = get_basic_agent(
            model_spec,
            InterestingExternalLinksSelection,
            LINK_SELECTION_SYSTEM_PROMPT,
        )
        prompt = _build_selection_prompt(candidates, source_url=source_url, title=title)
        result = agent.run_sync(
            prompt,
            model_settings={"timeout": LINK_SELECTION_TIMEOUT_SECONDS},
        )
        record_model_usage(
            "interesting_external_links",
            result,
            model_spec=model_spec,
            persist=persist,
        )
        output = _extract_agent_output(result)
        if not isinstance(output, InterestingExternalLinksSelection):
            output = InterestingExternalLinksSelection.model_validate(output)
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Interesting external link selection failed",
            extra={
                "component": "interesting_external_links",
                "operation": "select_links",
                "context_data": {
                    "source_url": source_url,
                    "candidate_count": len(candidates),
                    "error": str(exc),
                },
            },
        )
        return []

    return _filter_selection(output.links, candidates, source_url=source_url)


def _build_selection_prompt(
    candidates: list[LinkCandidate],
    *,
    source_url: str | None,
    title: str | None,
) -> str:
    candidate_payload = [
        {
            "url": candidate.url,
            "title": candidate.title,
            "context": candidate.context,
        }
        for candidate in candidates
    ]
    return render_prompt(
        "content/interesting_links#user",
        title=title or "Untitled",
        source_url=source_url or "unknown",
        candidate_payload=json.dumps(candidate_payload, ensure_ascii=False, indent=2),
        max_selected_links=MAX_SELECTED_LINKS,
    )


def _filter_selection(
    links: list[InterestingExternalLink],
    candidates: list[LinkCandidate],
    *,
    source_url: str | None,
) -> list[InterestingExternalLink]:
    candidate_by_url = {candidate.url: candidate for candidate in candidates}
    selected: list[InterestingExternalLink] = []
    seen: set[str] = set()

    for link in links:
        normalized = _normalize_candidate_url(link.url, source_url)
        if not normalized or normalized not in candidate_by_url or normalized in seen:
            continue
        seen.add(normalized)
        candidate = candidate_by_url[normalized]
        selected.append(
            InterestingExternalLink(
                url=normalized,
                title=_clean_text(link.title) or candidate.title or _host_label(normalized),
                reason=link.reason,
                category=link.category,
                confidence=link.confidence,
            )
        )
        if len(selected) >= MAX_SELECTED_LINKS:
            break

    return selected


def _extract_agent_output(result: Any) -> Any:
    if hasattr(result, "output"):
        return result.output
    if hasattr(result, "data"):
        return result.data
    return result


def _normalize_candidate_url(raw_url: str, source_url: str | None) -> str | None:
    cleaned = html.unescape(str(raw_url or "")).strip().strip("<>")
    cleaned = cleaned.rstrip(".,;:")
    if not cleaned:
        return None

    absolute = urljoin(source_url or "", cleaned)
    parsed = urlparse(absolute)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None

    host = _normalized_host(parsed.netloc)
    source_host = _normalized_host(urlparse(source_url or "").netloc)
    if not host or (source_host and _same_site(host, source_host)):
        return None

    path = parsed.path or "/"
    lower_path = path.lower()
    if lower_path.endswith(ASSET_EXTENSIONS):
        return None

    segments = {segment for segment in lower_path.strip("/").split("/") if segment}
    if segments & JUNK_PATH_SEGMENTS:
        return None

    if host in SHARE_HOSTS and any(marker in lower_path for marker in SHARE_PATH_MARKERS):
        return None

    query_items = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in TRACKING_QUERY_NAMES
        and not key.lower().startswith(TRACKING_QUERY_PREFIXES)
    ]
    query = urlencode(query_items, doseq=True)
    normalized = parsed._replace(
        scheme="https",
        netloc=host,
        path=path,
        params="",
        query=query,
        fragment="",
    )
    return urlunparse(normalized)


def _same_site(host: str, source_host: str) -> bool:
    return (
        host == source_host or host.endswith(f".{source_host}") or source_host.endswith(f".{host}")
    )


def _normalized_host(raw_host: str | None) -> str:
    host = (raw_host or "").lower().strip()
    if "@" in host:
        host = host.rsplit("@", 1)[-1]
    if ":" in host:
        host = host.split(":", 1)[0]
    return host.removeprefix("www.")


def _extract_context(text: str, start: int, end: int) -> str | None:
    context_start = max(0, start - 120)
    context_end = min(len(text), end + 120)
    return _clean_text(text[context_start:context_end], max_chars=MAX_CONTEXT_CHARS)


def _clean_text(value: str | None, *, max_chars: int | None = None) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = WHITESPACE_RE.sub(" ", value).strip()
    if not cleaned:
        return None
    if max_chars and len(cleaned) > max_chars:
        return cleaned[: max_chars - 1].rstrip() + "..."
    return cleaned


def _host_label(url: str) -> str:
    host = _normalized_host(urlparse(url).netloc)
    return host or url
