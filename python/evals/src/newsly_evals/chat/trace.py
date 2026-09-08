"""Read server-authored, turn-scoped tool summaries from the public transcript.

These summaries expose tool names/counts, not call order, arguments or raw results.
"""

import re
from typing import Any


def tool_evidence(turn: dict[str, Any]) -> dict[str, Any]:
    source_id = turn.get("message_id")
    messages = turn.get("messages", [])
    scoped = [
        message
        for message in messages
        if source_id is not None and message.get("source_message_id") == source_id
    ]
    summaries = [
        message["content"]
        for message in scoped
        if message.get("role") == "tool" and message.get("display_type") == "process_summary"
    ]
    counts: dict[str, int] = {}
    complete = bool(scoped)
    for summary in summaries:
        if summary == "Prepared intermediate context before writing the final answer.":
            continue
        lines = summary.splitlines()
        header = re.fullmatch(r"Executed (\d+) tool calls?:", lines[0]) if lines else None
        subtotal = 0
        for line in lines[1:]:
            match = re.fullmatch(r"• ([a-zA-Z0-9_]+)(?: x([1-9][0-9]*))?", line)
            if not match:
                complete = False
                continue
            name, count = match[1], int(match[2] or 1)
            counts[name] = counts.get(name, 0) + count
            subtotal += count
        if header is None or subtotal != int(header[1]):
            complete = False
    return {
        "complete": complete,
        "counts": counts,
        "summaries": summaries,
        "scope": "Public transcript names/counts only; arguments, results and order unavailable",
    }
