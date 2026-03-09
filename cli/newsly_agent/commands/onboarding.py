"""Onboarding command handlers."""

from __future__ import annotations

from argparse import Namespace

from cli.newsly_agent.client import NewslyAgentClient


def run_onboarding(client: NewslyAgentClient, args: Namespace) -> dict:
    """Start simplified async onboarding."""
    data = client.request(
        "POST",
        "/api/agent/onboarding",
        json_body={
            "brief": args.brief,
            "preferences": {},
            "seed_urls": args.seed_url,
            "seed_feeds": args.seed_feed,
        },
    )
    return {"command": "onboarding.run", "ok": True, "data": data}


def get_onboarding_status(client: NewslyAgentClient, args: Namespace) -> dict:
    """Fetch onboarding status."""
    data = client.request("GET", f"/api/agent/onboarding/{args.run_id}")
    return {"command": "onboarding.status", "ok": True, "data": data}


def complete_onboarding(client: NewslyAgentClient, args: Namespace) -> dict:
    """Complete simplified onboarding selections."""
    data = client.request(
        "POST",
        f"/api/agent/onboarding/{args.run_id}/complete",
        json_body={
            "accept_all": args.accept_all,
            "source_ids": args.source_id,
            "selected_subreddits": args.subreddit,
        },
    )
    return {"command": "onboarding.complete", "ok": True, "data": data}
