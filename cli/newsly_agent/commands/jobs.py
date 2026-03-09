"""Job command handlers."""

from __future__ import annotations

from argparse import Namespace

from cli.newsly_agent.client import NewslyAgentClient


def get_job(client: NewslyAgentClient, args: Namespace) -> dict:
    """Fetch one async job by ID."""
    data = client.request("GET", f"/api/jobs/{args.job_id}")
    return {"command": "jobs.get", "ok": True, "data": data}
