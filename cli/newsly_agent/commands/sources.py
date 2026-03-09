"""Source command handlers."""

from __future__ import annotations

from argparse import Namespace

from cli.newsly_agent.client import NewslyAgentClient


def list_sources(client: NewslyAgentClient, args: Namespace) -> dict:
    """List runtime source subscriptions for the current user."""
    params: dict[str, object] = {}
    if args.type:
        params["type"] = args.type
    data = client.request("GET", "/api/scrapers/", params=params or None)
    return {"command": "sources.list", "ok": True, "data": data}


def add_source(client: NewslyAgentClient, args: Namespace) -> dict:
    """Add a source using the existing scraper subscription route."""
    data = client.request(
        "POST",
        "/api/scrapers/subscribe",
        json_body={
            "feed_url": args.feed_url,
            "feed_type": args.feed_type,
            "display_name": args.display_name,
        },
    )
    return {"command": "sources.add", "ok": True, "data": data}
