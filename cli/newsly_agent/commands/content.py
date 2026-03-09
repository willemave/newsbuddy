"""Content command handlers."""

from __future__ import annotations

from argparse import Namespace

from cli.newsly_agent.client import NewslyAgentClient


def list_content(client: NewslyAgentClient, args: Namespace) -> dict:
    """List content cards from the server."""
    params: dict[str, object] = {"limit": args.limit}
    if args.cursor:
        params["cursor"] = args.cursor
    data = client.request("GET", "/api/content/", params=params)
    return {"command": "content.list", "ok": True, "data": data}


def get_content(client: NewslyAgentClient, args: Namespace) -> dict:
    """Fetch one content item by ID."""
    data = client.request("GET", f"/api/content/{args.content_id}")
    return {"command": "content.get", "ok": True, "data": data}


def submit_content(client: NewslyAgentClient, args: Namespace) -> dict:
    """Submit content through the existing async ingestion endpoint."""
    payload = {"url": args.url}
    if args.note:
        payload["note"] = args.note
    if args.crawl_links:
        payload["crawl_links"] = True
    if args.subscribe_to_feed:
        payload["subscribe_to_feed"] = True
    data = client.request("POST", "/api/content/submit", json_body=payload)
    return {"command": "content.submit", "ok": True, "data": data}


def summarize_content(client: NewslyAgentClient, args: Namespace) -> dict:
    """Queue persistent async summarization for a URL via submission semantics."""
    payload = {"url": args.url}
    if args.note:
        payload["note"] = args.note
    data = client.request("POST", "/api/content/submit", json_body=payload)
    return {"command": "content.summarize", "ok": True, "data": data}
