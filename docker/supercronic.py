#!/usr/bin/env python3
"""Small cron runner for containerized single-host deployments."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from contextlib import suppress
from datetime import datetime
from pathlib import Path

from croniter import croniter


def _load_crontab(path: Path) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        schedule, command = line.split(maxsplit=5)[:5], line.split(maxsplit=5)[5]
        entries.append((" ".join(schedule), command))
    return entries


async def _run_loop(schedule: str, command: str) -> None:
    while True:
        now = datetime.now()
        next_run = croniter(schedule, now).get_next(datetime)
        sleep_seconds = max(0.0, (next_run - now).total_seconds())
        await asyncio.sleep(sleep_seconds)
        process = await asyncio.create_subprocess_shell(
            command,
            cwd="/app",
            env=os.environ.copy(),
        )
        await process.wait()


async def _main(path: Path) -> None:
    tasks = [
        asyncio.create_task(_run_loop(schedule, command))
        for schedule, command in _load_crontab(path)
    ]
    stop_event = asyncio.Event()

    def _stop() -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    for signame in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(signame, _stop)

    await stop_event.wait()
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(_main(Path(sys.argv[1])))
