"""Opt-in Instruments trace capture for iOS performance verification."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.models.db import NewsItem

pytestmark = [pytest.mark.integration, pytest.mark.ios_e2e, pytest.mark.ios_performance]

REPO_ROOT = Path(__file__).resolve().parents[2]
TRACE_TEMPLATES = ("SwiftUI", "Time Profiler")
TRACE_SECONDS = int(os.environ.get("NEWSLY_PERF_TRACE_SECONDS", "25"))
XCTRACE_SAVE_TIMEOUT_SECONDS = int(
    os.environ.get("NEWSLY_PERF_XCTRACE_SAVE_TIMEOUT_SECONDS", "180")
)
STRICT_XCTRACE = os.environ.get("NEWSLY_PERF_STRICT_XCTRACE") == "1"
TRACE_OUTPUT_DIR = Path(
    os.environ.get("NEWSLY_PERF_TRACE_OUTPUT_DIR", REPO_ROOT / "tmp" / "ios-performance-traces")
).resolve()
TRACE_PROCESS_NAME = os.environ.get("NEWSLY_PERF_PROCESS_NAME", "newsly")
PERF_NOW = datetime(2026, 6, 6, 16, 41, tzinfo=UTC)


def _utc_naive(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(tzinfo=None)


def _trace_slug(value: str) -> str:
    return value.lower().replace(" ", "-")


def _remove_existing_output(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _skip_if_simulator_template_is_unsupported(stderr: str) -> None:
    if "not supported on the Simulator" in stderr or "not supported on this platform" in stderr:
        pytest.skip(
            "The requested xctrace template is not supported on this simulator runtime; "
            "capture this template on a physical device."
        )


def _trace_export_error(trace_path: Path) -> str | None:
    if not trace_path.exists():
        return f"xctrace did not create {trace_path}"

    result = subprocess.run(
        ["xcrun", "xctrace", "export", "--input", str(trace_path), "--toc"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return (
            f"xctrace created {trace_path}, but export --toc failed "
            f"with exit code {result.returncode}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )

    return _trace_toc_error(trace_path=trace_path, toc_xml=result.stdout)


def _trace_toc_error(*, trace_path: Path, toc_xml: str) -> str | None:
    try:
        root = ET.fromstring(toc_xml)
    except ET.ParseError as error:
        return f"xctrace export --toc returned invalid XML for {trace_path}: {error}"

    duration_text = root.findtext("./run/info/summary/duration")
    try:
        duration = float(duration_text or "0")
    except ValueError:
        return f"xctrace export --toc returned invalid duration for {trace_path}: {duration_text}"

    if duration <= 0:
        return f"xctrace export --toc reported an empty trace for {trace_path}"

    process_names = {
        value
        for process in root.findall(".//process")
        for key in ("name", "process-name", "display-name")
        if (value := process.attrib.get(key))
    }
    if TRACE_PROCESS_NAME not in process_names:
        return (
            f"xctrace export --toc for {trace_path} does not include "
            f"the expected process {TRACE_PROCESS_NAME!r}; "
            f"found={sorted(process_names)!r}"
        )

    return None


def _assert_trace_exportable(trace_path: Path) -> None:
    if error := _trace_export_error(trace_path):
        pytest.fail(error)


def _record_xctrace(
    *,
    template: str,
    trace_name: str,
    interaction: Callable[[], None],
) -> Path:
    if shutil.which("xcrun") is None:
        pytest.skip("xctrace is not available; install Xcode command line tools.")

    output_path = TRACE_OUTPUT_DIR / f"{trace_name}-{_trace_slug(template)}.trace"
    TRACE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    _remove_existing_output(output_path)

    command = [
        "xcrun",
        "xctrace",
        "record",
        "--template",
        template,
        "--time-limit",
        f"{TRACE_SECONDS}s",
        "--attach",
        TRACE_PROCESS_NAME,
        "--output",
        str(output_path),
        "--no-prompt",
    ]
    if simulator_id := os.environ.get("NEWSLY_MAESTRO_SIMULATOR_ID"):
        command[5:5] = ["--device", simulator_id]

    process = subprocess.Popen(
        command,
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    time.sleep(2.0)
    if process.poll() is not None:
        stdout, stderr = process.communicate()
        _skip_if_simulator_template_is_unsupported(stderr)
        pytest.fail(
            "xctrace exited before the measured interaction started\n"
            f"command={' '.join(command)}\n"
            f"stdout:\n{stdout}\n"
            f"stderr:\n{stderr}"
        )

    interaction()

    try:
        stdout, stderr = process.communicate(timeout=XCTRACE_SAVE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=10)
        export_error = _trace_export_error(output_path)
        message = (
            "xctrace did not finish after the measured interaction and time limit\n"
            f"command={' '.join(command)}\n"
            f"stdout:\n{stdout}\n"
            f"stderr:\n{stderr}\n"
            f"trace_export:\n{export_error or 'export --toc succeeded'}"
        )
        if STRICT_XCTRACE:
            pytest.fail(message)
        pytest.skip(message)

    if process.returncode != 0:
        _skip_if_simulator_template_is_unsupported(stderr)
        pytest.fail(
            f"xctrace failed\ncommand={' '.join(command)}\nstdout:\n{stdout}\nstderr:\n{stderr}"
        )
    _assert_trace_exportable(output_path)
    return output_path


def _seed_fast_read_items(db_session, *, user_id: int, count: int = 200) -> list[NewsItem]:
    items: list[NewsItem] = []
    for index in range(count):
        published_at = PERF_NOW - timedelta(minutes=index * 5)
        title = f"Performance Trace Fast Read {index + 1:03d}"
        key_points = [
            "The item has enough body text to exercise row layout.",
            "Rows stay deterministic while the trace scrolls through the feed.",
        ]
        item = NewsItem(
            ingest_key=f"ios-perf-fast-read-{index + 1:03d}",
            visibility_scope="user",
            owner_user_id=user_id,
            platform="hackernews",
            source_type="hackernews",
            source_label="Hacker News",
            source_external_id=f"ios-perf-fast-read-{index + 1:03d}",
            canonical_item_url=f"https://news.ycombinator.com/item?id=90{index + 1:03d}",
            canonical_story_url=f"https://example.com/perf-fast-read/{index + 1:03d}",
            article_url=f"https://example.com/perf-fast-read/{index + 1:03d}",
            article_domain="example.com",
            discussion_url=f"https://news.ycombinator.com/item?id=90{index + 1:03d}",
            article_title=title,
            summary_title=title,
            summary_key_points=key_points,
            summary_text=(
                "A deterministic performance fixture used to measure Fast Read "
                "scrolling, day grouping, row invalidation, and pagination behavior."
            ),
            raw_metadata={
                "discussion_url": f"https://news.ycombinator.com/item?id=90{index + 1:03d}",
                "summary": {
                    "article_url": f"https://example.com/perf-fast-read/{index + 1:03d}",
                    "summary": (
                        "A deterministic performance fixture used to measure Fast Read "
                        "scrolling, day grouping, row invalidation, and pagination behavior."
                    ),
                    "key_points": key_points,
                },
                "comment_count": 12 + (index % 40),
            },
            status="ready",
            published_at=_utc_naive(published_at),
            ingested_at=_utc_naive(published_at + timedelta(minutes=2)),
            processed_at=_utc_naive(published_at + timedelta(minutes=4)),
            created_at=_utc_naive(published_at + timedelta(minutes=2)),
            updated_at=_utc_naive(published_at + timedelta(minutes=4)),
        )
        db_session.add(item)
        items.append(item)

    db_session.commit()
    for item in items:
        db_session.refresh(item)
    return items


def _long_form_metadata(index: int) -> dict:
    return {
        "summary_kind": "long_structured",
        "summary_version": 1,
        "summary": {
            "title": f"Performance Trace Long Read {index}",
            "overview": (
                "A deterministic long-form article fixture used to capture detail "
                "opening, scrolling, and swipe gesture performance."
            ),
            "bullet_points": [
                {
                    "text": "Stable text keeps the card layout deterministic while tracing.",
                    "category": "performance",
                },
                {
                    "text": (
                        "Predictable detail sections exercise summary rendering during gestures."
                    ),
                    "category": "detail",
                },
                {
                    "text": "The fixture avoids network variance while the trace records UI work.",
                    "category": "measurement",
                },
            ],
            "quotes": [],
            "topics": ["Performance", "SwiftUI"],
            "questions": ["Does detail rendering avoid repeated metadata decoding during drags?"],
            "counter_arguments": [
                "Simulator traces still need manual validation against device traces."
            ],
            "classification": "to_read",
            "full_markdown": (
                "# Performance Trace Long Read\n\n"
                "The article gives Instruments enough deterministic text to render the "
                "summary stack during the measured drag. Modernized SwiftUI state and "
                "detail orchestration should avoid re-decoding metadata during gestures."
            ),
        },
        "image_generated_at": "2026-01-01T00:00:00Z",
    }


def _seed_long_form_items(content_factory, status_entry_factory, test_user) -> list:
    items = []
    for index in range(3):
        published_at = PERF_NOW - timedelta(hours=index)
        content = content_factory(
            content_type="article",
            url=f"https://example.com/perf-long-read/{index + 1}",
            title=f"Performance Trace Long Read {index + 1}",
            source="Performance Fixtures",
            status="completed",
            classification="to_read",
            publication_date=_utc_naive(published_at),
            processed_at=_utc_naive(published_at + timedelta(minutes=8)),
            created_at=_utc_naive(published_at + timedelta(minutes=3)),
            updated_at=_utc_naive(published_at + timedelta(minutes=8)),
            content_metadata=_long_form_metadata(index + 1),
        )
        status_entry_factory(user=test_user, content=content, status="inbox")
        items.append(content)
    return items


@pytest.mark.parametrize("template", TRACE_TEMPLATES)
def test_fast_read_scroll_instruments_trace(
    template: str,
    run_ios_flow,
    db_session,
    test_user,
) -> None:
    """Capture Fast Read scrolling with 200 deterministic news rows."""
    items = _seed_fast_read_items(db_session, user_id=test_user.id)
    run_ios_flow(
        "perf_fast_read_setup.yaml",
        extra_env={"FIRST_NEWS_ITEM_ID": str(items[0].id)},
    )

    trace = _record_xctrace(
        template=template,
        trace_name="fast-read-scroll",
        interaction=lambda: run_ios_flow("perf_fast_read_scroll.yaml"),
    )

    assert trace.exists()


@pytest.mark.parametrize("template", TRACE_TEMPLATES)
def test_detail_open_drag_instruments_trace(
    template: str,
    run_ios_flow,
    content_factory,
    status_entry_factory,
    test_user,
) -> None:
    """Capture opening a long-form detail screen and dragging it."""
    items = _seed_long_form_items(content_factory, status_entry_factory, test_user)
    run_ios_flow(
        "perf_detail_open_setup.yaml",
        extra_env={"CONTENT_ID": str(items[0].id)},
    )

    trace = _record_xctrace(
        template=template,
        trace_name="detail-open-drag",
        interaction=lambda: run_ios_flow(
            "perf_detail_open_drag.yaml",
            extra_env={"CONTENT_ID": str(items[0].id)},
        ),
    )

    assert trace.exists()
