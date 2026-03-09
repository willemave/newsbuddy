"""Digest command handlers."""

from __future__ import annotations

from argparse import Namespace

from cli.newsly_agent.client import NewslyAgentClient


def generate_digest(client: NewslyAgentClient, args: Namespace) -> dict:
    """Queue arbitrary-window agent digest generation."""
    data = client.request(
        "POST",
        "/api/agent/digests",
        json_body={
            "start_at": args.start_at,
            "end_at": args.end_at,
            "form": args.form,
        },
    )
    return {"command": "digest.generate", "ok": True, "data": data}


def list_digests(client: NewslyAgentClient, args: Namespace) -> dict:
    """List existing daily digest rows using the stable mobile-facing API."""
    params: dict[str, object] = {"limit": args.limit, "read_filter": "unread"}
    if args.cursor:
        params["cursor"] = args.cursor
    data = client.request("GET", "/api/content/daily-digests", params=params)
    return {"command": "digest.list", "ok": True, "data": data}
