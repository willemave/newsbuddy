#!/usr/bin/env python3
# ruff: noqa: E402
"""Evaluate metadata-first recovery for self-submitted YouTube URLs.

This script pulls recent production self-submitted YouTube rows, tries a few
recovery steps locally, and reports whether we can find an equivalent
non-YouTube URL that our current analyzer can process.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import Mock
from urllib.parse import parse_qs, urlparse

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.processing_strategies.youtube_strategy import YouTubeProcessorStrategy
from app.services.content_analyzer import AnalysisError
from app.services.exa_client import exa_search
from app.services.gateways.llm_gateway import get_llm_gateway
from app.services.podcast_search import search_podcast_episodes

DEFAULT_LIMIT = 10
YOUTUBE_EXCLUDE_DOMAINS = ["youtube.com", "youtu.be", "m.youtube.com", "music.youtube.com"]


def _fetch_prod_rows(limit: int) -> list[dict[str, Any]]:
    sql = f"""
select id, created_at, title, url, status, error_message
from contents
where source = 'self submission'
  and (platform = 'youtube' or url ilike '%youtube.com%' or url ilike '%youtu.be%')
order by id desc
limit {limit}
""".strip()
    result = subprocess.run(
        ["uv", "run", "admin", "db", "query", "--sql", sql],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    rows = payload.get("rows", [])
    if not isinstance(rows, list):
        raise ValueError("Unexpected admin db query response shape")
    return [row for row in rows if isinstance(row, dict)]


def _normalize_youtube_watch_url(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    if host.endswith("youtu.be"):
        video_id = parsed.path.strip("/").split("/", 1)[0]
        return f"https://www.youtube.com/watch?v={video_id}" if video_id else url
    if "youtube.com" not in host:
        return url
    if parsed.path == "/watch":
        watch_video_id = parse_qs(parsed.query).get("v", [None])[0]
        return f"https://www.youtube.com/watch?v={watch_video_id}" if watch_video_id else url
    return url


def _probe_oembed(url: str) -> dict[str, Any]:
    canonical_url = _normalize_youtube_watch_url(url)
    endpoint = "https://www.youtube.com/oembed"
    try:
        response = httpx.get(
            endpoint,
            params={"url": canonical_url, "format": "json"},
            timeout=15.0,
            follow_redirects=True,
        )
        response.raise_for_status()
        data = response.json()
        return {
            "success": True,
            "title": data.get("title"),
            "author_name": data.get("author_name"),
            "author_url": data.get("author_url"),
            "thumbnail_url": data.get("thumbnail_url"),
        }
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc)}


def _probe_current_youtube_path(url: str) -> dict[str, Any]:
    strategy = YouTubeProcessorStrategy(http_client=Mock())
    try:
        result = asyncio.run(strategy.extract_data(b"", url))
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error_type": type(exc).__name__, "error": str(exc)}

    if result.get("skip_processing"):
        return {
            "success": False,
            "skip_processing": True,
            "skip_reason": result.get("skip_reason"),
            "title": result.get("title"),
        }

    raw_metadata = result.get("metadata")
    metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
    return {
        "success": True,
        "title": result.get("title"),
        "author": result.get("author"),
        "video_id": result.get("video_id"),
        "thumbnail_url": result.get("thumbnail_url"),
        "has_transcript": metadata.get("has_transcript"),
    }


def _build_search_query(oembed: dict[str, Any], youtube_probe: dict[str, Any]) -> str | None:
    title = oembed.get("title") or youtube_probe.get("title")
    author = oembed.get("author_name") or youtube_probe.get("author")
    if isinstance(title, str):
        title = title.strip()
    if isinstance(author, str):
        author = author.strip()
    if not title:
        return None
    parts = [title]
    if author:
        parts.append(author)
    return " ".join(parts)


def _search_podcast_candidates(query: str) -> list[dict[str, Any]]:
    hits = search_podcast_episodes(query, limit=3)
    results: list[dict[str, Any]] = []
    for hit in hits:
        results.append(
            {
                "url": hit.episode_url,
                "title": hit.title,
                "podcast_title": hit.podcast_title,
                "provider": hit.provider,
                "feed_url": hit.feed_url,
                "published_at": hit.published_at,
                "score": hit.score,
                "snippet": hit.snippet,
                "source": "podcast_search",
            }
        )
    return results


def _search_exa_candidates(
    query: str, title: str | None, author: str | None
) -> list[dict[str, Any]]:
    subject = title or query
    byline = author or ""
    natural_query = (
        f"Find the same podcast episode, interview, or canonical article published outside YouTube "
        f'for "{subject}" by "{byline}". Prefer podcast pages, Apple Podcasts, Spotify, '
        "publisher pages, or direct episode pages. Exclude YouTube."
    )
    hits = exa_search(
        natural_query,
        num_results=5,
        exclude_domains=YOUTUBE_EXCLUDE_DOMAINS,
    )
    results: list[dict[str, Any]] = []
    for hit in hits:
        results.append(
            {
                "url": hit.url,
                "title": hit.title,
                "snippet": hit.snippet,
                "published_at": hit.published_date,
                "source": "exa_search",
            }
        )
    return results


def _dedupe_candidates(*candidate_lists: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    for candidates in candidate_lists:
        for candidate in candidates:
            url = candidate.get("url")
            if not isinstance(url, str) or not url.strip():
                continue
            normalized = url.strip()
            if normalized in seen:
                continue
            seen.add(normalized)
            merged.append(candidate)
    return merged


def _analyze_candidate(url: str) -> dict[str, Any]:
    gateway = get_llm_gateway()
    result = gateway.analyze_url(url)
    if isinstance(result, AnalysisError):
        return {
            "success": False,
            "error": result.message,
            "recoverable": result.recoverable,
        }

    analysis = result.analysis
    audio_probe = _probe_media_url(analysis.media_url)
    return {
        "success": True,
        "analysis": {
            "content_type": analysis.content_type,
            "platform": analysis.platform,
            "title": analysis.title,
            "description": analysis.description,
            "media_url": analysis.media_url,
            "media_format": analysis.media_format,
            "duration_seconds": analysis.duration_seconds,
            "confidence": analysis.confidence,
        },
        "audio_probe": audio_probe,
    }


def _probe_media_url(url: str | None) -> dict[str, Any] | None:
    if not isinstance(url, str) or not url.strip():
        return None
    try:
        response = httpx.get(
            url,
            headers={"Range": "bytes=0-0"},
            timeout=20.0,
            follow_redirects=True,
        )
        return {
            "success": response.status_code < 400,
            "status_code": response.status_code,
            "content_type": response.headers.get("content-type"),
            "content_length": response.headers.get("content-length"),
            "final_url": str(response.url),
        }
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc)}


def _evaluate_row(row: dict[str, Any], analyze_found_url: bool) -> dict[str, Any]:
    youtube_url = row.get("url")
    if not isinstance(youtube_url, str) or not youtube_url.strip():
        raise ValueError("Row is missing a usable url")

    oembed = _probe_oembed(youtube_url)
    youtube_probe = _probe_current_youtube_path(youtube_url)
    query = _build_search_query(oembed, youtube_probe)

    podcast_candidates: list[dict[str, Any]] = []
    exa_candidates: list[dict[str, Any]] = []
    if query:
        podcast_candidates = _search_podcast_candidates(query)
        exa_candidates = _search_exa_candidates(
            query,
            title=oembed.get("title") if oembed.get("success") else None,
            author=oembed.get("author_name") if oembed.get("success") else None,
        )

    candidates = _dedupe_candidates(podcast_candidates, exa_candidates)
    chosen = candidates[0] if candidates else None
    chosen_analysis = None
    if analyze_found_url and chosen and isinstance(chosen.get("url"), str):
        chosen_analysis = _analyze_candidate(chosen["url"])

    return {
        "prod_row": row,
        "metadata": {
            "oembed": oembed,
            "current_youtube_path": youtube_probe,
            "search_query": query,
        },
        "candidates": {
            "podcast_search": podcast_candidates,
            "exa_search": exa_candidates,
            "chosen": chosen,
        },
        "chosen_analysis": chosen_analysis,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument(
        "--skip-analyze-found-url",
        action="store_true",
        help="Only search for equivalent URLs; skip analyzer calls on the chosen result.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output machine-readable JSON.",
    )
    args = parser.parse_args()

    rows = _fetch_prod_rows(limit=args.limit)
    evaluated = [
        _evaluate_row(row, analyze_found_url=not args.skip_analyze_found_url) for row in rows
    ]

    output = {
        "row_count": len(rows),
        "rows": evaluated,
    }

    if args.json:
        print(json.dumps(output, indent=2, ensure_ascii=False))
        return

    print(f"Evaluated {len(rows)} production self-submitted YouTube rows")
    print()
    for item in evaluated:
        prod = item["prod_row"]
        meta = item["metadata"]
        chosen = item["candidates"]["chosen"]
        chosen_analysis = item["chosen_analysis"]
        print(f"- content_id={prod.get('id')} url={prod.get('url')}")
        print(f"  prod_status={prod.get('status')} prod_error={prod.get('error_message')}")
        print(
            f"  oembed_title={meta['oembed'].get('title')} "
            f"oembed_author={meta['oembed'].get('author_name')}"
        )
        print(
            f"  current_path_success={meta['current_youtube_path'].get('success')} "
            f"skip_reason={meta['current_youtube_path'].get('skip_reason')}"
        )
        print(f"  search_query={meta.get('search_query')}")
        print(f"  chosen_url={chosen.get('url') if isinstance(chosen, dict) else None}")
        if isinstance(chosen_analysis, dict):
            analysis = chosen_analysis.get("analysis", {})
            audio_probe = chosen_analysis.get("audio_probe")
            print(
                f"  chosen_analysis_type={analysis.get('content_type')} "
                f"platform={analysis.get('platform')} media_url={analysis.get('media_url')}"
            )
            print(f"  audio_probe={audio_probe}")
        print()


if __name__ == "__main__":
    main()
