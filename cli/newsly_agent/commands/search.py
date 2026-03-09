"""Search command handlers."""

from __future__ import annotations

from argparse import Namespace

from cli.newsly_agent.client import NewslyAgentClient


def search(client: NewslyAgentClient, args: Namespace) -> dict:
    """Run provider-backed agent search."""
    data = client.request(
        "POST",
        "/api/agent/search",
        json_body={
            "query": args.query,
            "limit": args.limit,
            "include_podcasts": not args.no_podcasts,
        },
    )
    return {"command": "search", "ok": True, "data": data}
