import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import func

from app.core.db import get_db
from app.core.deps import require_admin
from app.core.logging import get_logger
from app.core.settings import get_settings
from app.models.schema import LlmUsageRecord
from app.templates import templates

router = APIRouter(prefix="/admin")

# Get logs directory
settings = get_settings()
LOGS_DIR = settings.logs_dir
ERRORS_DIR = LOGS_DIR / "errors"
STRUCTURED_DIR = LOGS_DIR / "structured"
STRUCTURED_FILTER_FIELDS = (
    "request_id",
    "content_id",
    "task_id",
    "session_id",
    "message_id",
    "job_name",
    "component",
    "operation",
    "event_name",
    "status",
)

# Logger
logger = get_logger(__name__)


@router.get("/logs", response_class=HTMLResponse)
async def list_logs(request: Request, _: None = Depends(require_admin)):
    """List all log files with recent error logs."""
    log_files = []
    recent_errors = []
    structured_filters = _get_structured_filters(request)

    # Get all log files from errors directory
    if ERRORS_DIR.exists():
        for file_path in ERRORS_DIR.glob("*.log"):
            stat = file_path.stat()
            log_files.append(
                {
                    "filename": f"errors/{file_path.name}",
                    "size": f"{stat.st_size / 1024:.1f} KB",
                    "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                    "modified_timestamp": stat.st_mtime,
                }
            )

        # Get all JSONL error files
        for file_path in ERRORS_DIR.glob("*.jsonl"):
            stat = file_path.stat()
            log_files.append(
                {
                    "filename": f"errors/{file_path.name}",
                    "size": f"{stat.st_size / 1024:.1f} KB",
                    "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                    "modified_timestamp": stat.st_mtime,
                }
            )

    # Also check root logs directory for any remaining log files
    if LOGS_DIR.exists():
        for file_path in LOGS_DIR.glob("*.log"):
            if file_path.is_file():  # Skip directories
                stat = file_path.stat()
                log_files.append(
                    {
                        "filename": file_path.name,
                        "size": f"{stat.st_size / 1024:.1f} KB",
                        "modified": datetime.fromtimestamp(stat.st_mtime).strftime(
                            "%Y-%m-%d %H:%M:%S"
                        ),
                        "modified_timestamp": stat.st_mtime,
                    }
                )

    # Include structured JSONL logs for turn traces and context diagnostics
    if STRUCTURED_DIR.exists():
        for file_path in STRUCTURED_DIR.glob("*.jsonl"):
            stat = file_path.stat()
            log_files.append(
                {
                    "filename": f"structured/{file_path.name}",
                    "size": f"{stat.st_size / 1024:.1f} KB",
                    "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                    "modified_timestamp": stat.st_mtime,
                }
            )

    # Sort by modified time, newest first
    log_files.sort(key=lambda x: x["modified_timestamp"], reverse=True)

    # Remove timestamp from final output
    for log in log_files:
        log.pop("modified_timestamp", None)

    # Get recent errors from the most recent error files
    recent_errors = _get_recent_errors(limit=10)
    recent_structured = _get_recent_structured_events(limit=20, filters=structured_filters)

    return templates.TemplateResponse(
        request,
        "logs_list.html",
        {
            "request": request,
            "log_files": log_files,
            "recent_errors": recent_errors,
            "recent_structured": recent_structured,
            "structured_filters": structured_filters,
        },
    )


@router.get("/logs/{filename:path}", response_class=HTMLResponse)
async def view_log(request: Request, filename: str, _: None = Depends(require_admin)):
    """View specific log file content."""
    file_path = LOGS_DIR / filename
    structured_filters = _get_structured_filters(request)

    # Security check - ensure file is in logs directory
    if not file_path.exists() or not str(file_path.resolve()).startswith(str(LOGS_DIR.resolve())):
        raise HTTPException(status_code=404, detail="Log file not found")

    try:
        # Handle JSONL files differently
        if file_path.suffix == ".jsonl":
            content = _format_jsonl_content(file_path, filters=structured_filters)
        else:
            with open(file_path, encoding="utf-8") as f:
                content = f.read()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading log file: {str(e)}") from e

    return templates.TemplateResponse(
        request,
        "log_detail.html",
        {
            "request": request,
            "filename": filename,
            "content": content,
            "structured_filters": structured_filters,
        },
    )


@router.get("/logs/{filename:path}/download")
async def download_log(filename: str, _: None = Depends(require_admin)):
    """Download a log file."""
    file_path = LOGS_DIR / filename

    # Security check
    if not file_path.exists() or not str(file_path.resolve()).startswith(str(LOGS_DIR.resolve())):
        raise HTTPException(status_code=404, detail="Log file not found")

    return FileResponse(path=str(file_path), filename=file_path.name, media_type="text/plain")


@router.get("/errors", response_class=HTMLResponse)
async def errors_dashboard(
    request: Request,
    _: None = Depends(require_admin),
    hours: int = 24,
    min_errors: int = 1,
    component: str | None = None,
):
    """Analyze recent error logs and present an HTML dashboard (no LLM calls)."""
    logs_dir = LOGS_DIR
    errors = _an_get_recent_logs(logs_dir, hours)

    # Optional component filter
    if component:
        errors = [e for e in errors if e.get("component", "").lower() == component.lower()]

    grouped = _an_group_errors(errors)
    if min_errors > 1:
        grouped = {k: v for k, v in grouped.items() if len(v) >= min_errors}

    # Flatten for helpers
    flat_errors: list[dict[str, Any]] = [e for lst in grouped.values() for e in lst]
    affected_files, file_counts = _an_extract_file_references(flat_errors)
    summary = _an_generate_summary_report(errors, grouped)

    # A long, copyable markdown prompt (rendered as Markdown in template)
    prompt = _an_generate_llm_prompt(grouped, hours)

    # Build simple category view model
    categories: list[dict[str, Any]] = []
    for key, items in grouped.items():
        sample = items[0] if items else {}
        categories.append(
            {
                "key": key,
                "count": len(items),
                "sample_message": (sample.get("error_message") or sample.get("message") or "")[
                    0:300
                ],
            }
        )

    # Recent sample errors (limit 20)
    recent_samples = []
    for e in flat_errors[:20]:
        recent_samples.append(
            {
                "timestamp": e.get("timestamp", "N/A"),
                "component": e.get("component", "unknown"),
                "error_type": e.get("error_type", "unknown"),
                "message": (e.get("error_message") or e.get("message") or "")[0:400],
                "file_link": f"errors/{e.get('source_file')}" if e.get("source_file") else None,
            }
        )

    return templates.TemplateResponse(
        request,
        "admin_errors.html",
        {
            "request": request,
            "hours": hours,
            "min_errors": min_errors,
            "component": component or "",
            "total_errors": len(errors),
            "category_count": len(grouped),
            "categories": categories,
            "affected_files": affected_files,
            "file_counts": file_counts,
            "recent_samples": recent_samples,
            "summary_markdown": summary,
            "llm_prompt_markdown": prompt,
        },
    )


@router.get("/llm-usage", response_class=HTMLResponse)
async def llm_usage_dashboard(
    request: Request,
    _: None = Depends(require_admin),
):
    """Show recent persisted LLM usage rows with lightweight filtering."""
    provider = request.query_params.get("provider")
    model = request.query_params.get("model")
    feature = request.query_params.get("feature")
    start_date = request.query_params.get("start_date")
    end_date = request.query_params.get("end_date")
    raw_limit = request.query_params.get("limit")
    try:
        limit = max(1, min(int(raw_limit or "100"), 500))
    except ValueError:
        limit = 100
    records, totals = _get_llm_usage_rows(
        provider=provider,
        model=model,
        feature=feature,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
    )
    return templates.TemplateResponse(
        request,
        "llm_usage_list.html",
        {
            "request": request,
            "records": records,
            "totals": totals,
            "filters": {
                "provider": provider or "",
                "model": model or "",
                "feature": feature or "",
                "start_date": start_date or "",
                "end_date": end_date or "",
                "limit": limit,
            },
        },
    )


@router.post("/errors/reset")
async def reset_error_logs(_: None = Depends(require_admin)):
    """Reset all error logs by deleting all log files in the errors directory.

    Returns:
        Redirect to errors dashboard

    Raises:
        HTTPException: If there's an error during deletion
    """
    deleted_files_count = 0
    errors = []

    if not ERRORS_DIR.exists():
        raise HTTPException(status_code=404, detail="Errors directory not found")

    try:
        # Delete all .log files from errors directory
        for file_path in ERRORS_DIR.glob("*.log"):
            try:
                file_path.unlink()
                deleted_files_count += 1
            except Exception as e:
                errors.append(f"Failed to delete {file_path.name}: {str(e)}")

        # Delete all .jsonl files from errors directory
        for file_path in ERRORS_DIR.glob("*.jsonl"):
            try:
                file_path.unlink()
                deleted_files_count += 1
            except Exception as e:
                errors.append(f"Failed to delete {file_path.name}: {str(e)}")

        # Delete all .log files from root logs directory
        for file_path in LOGS_DIR.glob("*.log"):
            if file_path.is_file():  # Skip directories
                try:
                    file_path.unlink()
                    deleted_files_count += 1
                except Exception as e:
                    errors.append(f"Failed to delete {file_path.name}: {str(e)}")

        logger.info("Reset error logs: deleted %s files", deleted_files_count)

        # Redirect back to errors page
        return RedirectResponse(url="/admin/errors", status_code=303)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error resetting logs: {str(e)}") from e


# ===== Helpers adapted from scripts/analyze_logs_for_fixes.py (trimmed for server use) =====


def _an_parse_jsonl_file(file_path: Path) -> list[dict[str, Any]]:
    out = []
    try:
        with open(file_path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except Exception:
        pass
    return out


def _an_parse_log_file(file_path: Path) -> list[dict[str, Any]]:
    out = []
    try:
        import re

        with open(file_path, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    low = line.lower()
                    # Treat as error only on strong signals, not "Errors: 0"
                    strong = (
                        (" - error - " in low)
                        or (" exception" in low)
                        or ("traceback" in low)
                        or re.search(r"\berror\b", low)
                    )
                    plural_noise = re.search(r"\berrors\s*:\s*\d+", low)
                    if strong and not plural_noise:
                        out.append(
                            {
                                "timestamp": datetime.now(UTC)
                                .isoformat()
                                .replace("+00:00", "Z"),
                                "error_message": line.strip(),
                                "file": str(file_path),
                            }
                        )
    except Exception:
        pass
    return out


def _an_get_recent_logs(logs_dir: Path, hours: int = 24) -> list[dict[str, Any]]:
    cutoff = datetime.now(UTC) - timedelta(hours=hours)
    all_errors: list[dict[str, Any]] = []

    errors_dir = logs_dir / "errors"
    if errors_dir.exists():
        # JSONL files
        for fp in sorted(errors_dir.glob("*.jsonl"), reverse=True):
            try:
                # Filter by filename timestamp if possible (name like x_y_YYYYmmddHHMMSS.jsonl)
                parts = fp.stem.split("_")
                if len(parts) >= 2:
                    ts = parts[-2] + parts[-1]
                    dt = datetime.strptime(ts, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
                    if dt < cutoff:
                        continue
            except Exception:
                pass
            for e in _an_parse_jsonl_file(fp):
                ts = e.get("timestamp")
                try:
                    if ts:
                        if "Z" in ts:
                            t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        else:
                            t = datetime.fromisoformat(ts)
                        if t.tzinfo is None:
                            t = t.replace(tzinfo=UTC)
                        if t > cutoff:
                            e["source_file"] = fp.name
                            all_errors.append(e)
                except Exception as ex:
                    e["source_file"] = fp.name
                    e["timestamp_parse_error"] = str(ex)
                    all_errors.append(e)

        # Specific llm_json_errors.log
        llm = errors_dir / "llm_json_errors.log"
        if llm.exists():
            for e in _an_parse_log_file(llm):
                ts = e.get("timestamp")
                try:
                    if ts:
                        t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        if t.tzinfo is None:
                            t = t.replace(tzinfo=UTC)
                        if t > cutoff:
                            e["source_file"] = llm.name
                            all_errors.append(e)
                except Exception as ex:
                    e["source_file"] = llm.name
                    e["timestamp_parse_error"] = str(ex)
                    all_errors.append(e)

    # Root .log files
    for fp in logs_dir.glob("*.log"):
        for e in _an_parse_log_file(fp):
            ts = e.get("timestamp")
            try:
                if ts:
                    t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    if t.tzinfo is None:
                        t = t.replace(tzinfo=UTC)
                    if t > cutoff:
                        e["source_file"] = fp.name
                        all_errors.append(e)
            except Exception:
                e["source_file"] = fp.name
                all_errors.append(e)

    return all_errors


def _an_group_errors(errors: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in errors:
        et = (e.get("error_type") or "unknown").lower()
        comp = (e.get("component") or "unknown").lower()
        msg = e.get("error_message") or ""
        op = e.get("operation") or ""

        if "config file not found" in msg.lower() or (
            "config" in msg.lower() and "not found" in msg.lower()
        ):
            key = "config_missing_errors"
        elif "BrowserType.launch" in msg or "playwright" in msg.lower():
            key = "playwright_browser_errors"
        elif "crawl4ai" in msg.lower():
            key = "crawl4ai_extraction_errors"
        elif "json" in et or "json" in msg.lower():
            key = "json_parsing_errors"
        elif "validation error" in msg.lower() or "validationerror" in et:
            key = "pydantic_validation_errors"
        elif "api error" in msg or "api" in op.lower():
            key = "api_errors"
        elif "httpexception" in et or "status_code" in str(e.get("http_details", {})):
            key = "http_errors"
        elif "timeout" in msg.lower() or "timeouterror" in et:
            key = "timeout_errors"
        elif "connection" in msg.lower() or "connectionerror" in et:
            key = "connection_errors"
        elif "pdf" in comp or "pdf" in op.lower():
            key = "pdf_processing_errors"
        elif "llm" in comp or "openai" in msg.lower() or "google" in msg.lower():
            key = "llm_service_errors"
        elif "database" in msg.lower() or "sqlalchemy" in et:
            key = "database_errors"
        elif "queue" in comp or "worker" in comp:
            key = "queue_worker_errors"
        else:
            key = f"{comp}_{et}".replace(" ", "_")

        grouped[key].append(e)

    return dict(sorted(grouped.items(), key=lambda kv: len(kv[1]), reverse=True))


def _an_extract_file_references(errors: list[dict[str, Any]]) -> tuple[list[str], dict[str, int]]:
    files = set()
    counts: dict[str, int] = defaultdict(int)
    for e in errors:
        st = e.get("stack_trace", "")
        for line in st.split("\n"):
            if 'File "' in line:
                try:
                    path = line.split('File "')[1].split('"')[0]
                    if "/app/" in path:
                        rel = "app/" + path.split("/app/")[1]
                    elif "/news_app/" in path:
                        rel = path.split("/news_app/")[1]
                    else:
                        continue
                    files.add(rel)
                    counts[rel] += 1
                except Exception:
                    continue
    sorted_files = sorted(counts.items(), key=lambda x: x[1], reverse=True)
    return [f for f, _ in sorted_files], counts


def _an_generate_summary_report(
    errors: list[dict[str, Any]], grouped: dict[str, list[dict[str, Any]]]
) -> str:
    total = len(errors)
    types = len(grouped)
    top = list(grouped.items())[:3]
    lines = [
        "## Quick Summary",
        "",
        f"- **Total Errors**: {total}",
        f"- **Error Types**: {types}",
        "- **Top 3 Error Types**:",
    ]
    for t, lst in top:
        pct = (len(lst) / total * 100) if total else 0.0
        lines.append(f"  - {t.replace('_', ' ').title()}: {len(lst)} ({pct:.1f}%)")
    return "\n".join(lines)


def _an_generate_llm_prompt(grouped: dict[str, list[dict[str, Any]]], hours: int) -> str:
    """Generate a comprehensive LLM prompt similar to the CLI script output.

    Includes: summary stats, per-category breakdown with top patterns, excerpts,
    affected files, detected patterns, and a structured fix request.
    """
    from collections import defaultdict as _dd

    # Flatten errors
    all_errors: list[dict[str, Any]] = [e for lst in grouped.values() for e in lst]
    total_errors = len(all_errors)
    category_count = len(grouped)

    lines: list[str] = []
    lines.append("# Error Analysis and Fix Request")
    lines.append("")
    lines.append(f"I need help fixing errors that occurred in the last {hours} hours.")
    lines.append("")

    # Summary stats
    lines.append("## Summary Statistics")
    lines.append(f"- **Total Errors**: {total_errors}")
    lines.append(f"- **Error Categories**: {category_count}")
    lines.append(f"- **Time Period**: Last {hours} hours")
    lines.append("")

    # Category summary
    lines.append("## Error Summary by Category")
    if not grouped:
        lines.append("- No errors found in the selected window.")
    else:
        for key, errors in grouped.items():
            lines.append(f"- {key.replace('_', ' ').title()}: {len(errors)} occurrences")
    lines.append("")

    # Detailed per-category breakdown with top patterns and context
    lines.append("## Detailed Breakdown")
    for cat_idx, (key, errors) in enumerate(grouped.items(), 1):
        lines.append("")
        lines.append(f"### {cat_idx}. {key.replace('_', ' ').title()} ({len(errors)} occurrences)")

        # Group by leading message snippet to find patterns
        patterns: dict[str, list[dict[str, Any]]] = _dd(list)
        for e in errors:
            msg = (e.get("error_message") or e.get("message") or "").strip()
            snippet = msg[:400] if msg else "(no message)"
            patterns[snippet].append(e)

        # Show top 5 patterns
        for i, (snippet, plist) in enumerate(
            list(sorted(patterns.items(), key=lambda kv: len(kv[1]), reverse=True))[:5], 1
        ):
            lines.append(f"- Pattern {i} ({len(plist)}x):")
            lines.append("  ```")
            lines.append(f"  {snippet}")
            lines.append("  ```")

            # Sample context from the first instance
            sample = plist[0]
            ctx_items: list[str] = []
            if sample.get("component"):
                ctx_items.append(f"component: {sample['component']}")
            if sample.get("operation"):
                ctx_items.append(f"operation: {sample['operation']}")
            if sample.get("item_id"):
                ctx_items.append(f"item_id: {sample['item_id']}")
            http = sample.get("http_details") or {}
            if http:
                if http.get("status_code"):
                    ctx_items.append(f"http_status: {http['status_code']}")
                if http.get("method"):
                    ctx_items.append(f"method: {http['method']}")
                if http.get("url"):
                    ctx_items.append(f"url: {http['url']}")
            if ctx_items:
                lines.append("  - Context:")
                for c in ctx_items[:8]:
                    lines.append(f"    - {c}")

            # Stack trace excerpt
            st = sample.get("stack_trace", "")
            if st:
                rel_lines = []
                for ln in st.split("\n"):
                    if "/app/" in ln or " app/" in ln or 'File "' in ln:
                        rel_lines.append(ln.strip())
                if rel_lines:
                    lines.append("  - Stack trace excerpt:")
                    lines.append("    ```python")
                    for ln in rel_lines[:8]:
                        lines.append(f"    {ln}")
                    if len(rel_lines) > 8:
                        lines.append("    # ... (truncated)")
                    lines.append("    ```")

            # Include a few raw log snippets to ground the LLM
            lines.append("  - Sample log entries:")
            for j, entry in enumerate(plist[:3], 1):
                ts = entry.get("timestamp", "N/A")
                src = entry.get("source_file", "unknown")
                et = entry.get("error_type", entry.get("type", "unknown"))
                msg = (
                    (entry.get("error_message") or entry.get("message") or "")
                    .strip()
                    .replace("\n", " ")
                )
                if len(msg) > 400:
                    msg = msg[:400] + "..."
                lines.append(f"    {j}. [{ts}] ({src}) {et}: {msg}")

                # Add exception/traceback if present: last exception line and first TB line
                tb = (entry.get("stack_trace") or "").strip()
                if tb:
                    tb_lines = [ln for ln in tb.split("\n") if ln.strip()]
                    head = None
                    tail = None
                    for ln in tb_lines:
                        if "Traceback (most recent call last)" in ln:
                            head = ln
                            break
                    if tb_lines:
                        tail = tb_lines[-1]
                    lines.append(
                        "    " + ("      ↳ " + head if head else "      ↳ traceback present")
                    )
                    if tail and tail != head:
                        lines.append(f"        {tail}")

    lines.append("")

    # Most affected files (reuse helper)
    affected_files, file_counts = _an_extract_file_references(all_errors)
    if affected_files:
        lines.append("## Most Affected Files")
        for f in affected_files[:12]:
            lines.append(f"- `{f}` ({file_counts.get(f, 0)} errors)")
        if len(affected_files) > 12:
            lines.append(f"- ... and {len(affected_files) - 12} more files")
        lines.append("")

    # Detected patterns -> recommendations
    lines.append("## Error Patterns Detected")
    detected: list[str] = []
    keys = set(grouped.keys())
    if any("json" in k for k in keys):
        detected.append("- JSON parsing failures — add schema validation and fallbacks.")
    if any("timeout" in k for k in keys):
        detected.append("- Timeouts — increase timeouts, retries, or circuit breakers.")
    if any("database" in k or "sqlalchemy" in k for k in keys):
        detected.append("- Database errors — connection pool sizing, query fixes.")
    if any("http" in k for k in keys):
        detected.append("- HTTP errors — handle 4xx/5xx with backoff and clearer messages.")
    if any("llm" in k for k in keys):
        detected.append("- LLM service errors — handle rate limits, retries, and fallbacks.")
    if not detected:
        detected.append(
            "- General logging issues — entries lack structured fields. Improve logging."
        )
    lines.extend(detected)
    lines.append("")

    # Structured request for the LLM
    lines.append("## Fix Request")
    lines.append("Please propose fixes with:")
    lines.append("1. Critical fixes first (showstoppers), with code diffs.")
    lines.append("2. Root-cause analysis for top categories and patterns.")
    lines.append("3. Specific code changes (file paths, line ranges, before/after snippets).")
    lines.append("4. Preventive measures (validation, retries, backoff, monitoring, alerts).")
    lines.append("5. Testing strategy (unit/integration), plus quick verification steps.")

    return "\n".join(lines)


def _get_recent_errors(limit: int = 10) -> list[dict[str, Any]]:
    """Get the most recent errors from error log files."""
    errors = []

    if not ERRORS_DIR.exists():
        return errors

    # Get all JSONL error files
    error_files = list(ERRORS_DIR.glob("*.jsonl"))

    # Sort by modification time, newest first
    error_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)

    # Read errors from files until we have enough
    for file_path in error_files:
        if len(errors) >= limit:
            break

        try:
            with open(file_path, encoding="utf-8") as f:
                for line in f:
                    if len(errors) >= limit:
                        break
                    try:
                        error_data = json.loads(line.strip())
                        # Format the error for display
                        errors.append(
                            {
                                "timestamp": error_data.get("timestamp", "Unknown"),
                                "level": error_data.get("level", "ERROR"),
                                "source": error_data.get("source", file_path.stem),
                                "message": error_data.get("message", "No message"),
                                "file": file_path.name,
                            }
                        )
                    except json.JSONDecodeError:
                        continue
        except Exception:
            continue

    # Also check for regular .log files
    log_files = list(ERRORS_DIR.glob("*.log"))
    log_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)

    for file_path in log_files:
        if len(errors) >= limit:
            break

        try:
            with open(file_path, encoding="utf-8") as f:
                lines = f.readlines()
                # Get last few lines (reverse order)
                for line in reversed(lines[-5:]):
                    if len(errors) >= limit:
                        break
                    if line.strip():
                        errors.append(
                            {
                                "timestamp": "See file",
                                "level": "ERROR",
                                "source": file_path.stem,
                                "message": line.strip()[:200]
                                + ("..." if len(line.strip()) > 200 else ""),
                                "file": file_path.name,
                            }
                        )
        except Exception:
            continue

    return errors


def _get_recent_structured_events(
    limit: int = 20,
    max_files: int = 10,
    filters: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Get recent structured events from JSONL logs for quick live debugging."""

    events: list[dict[str, Any]] = []
    if not STRUCTURED_DIR.exists():
        return events

    structured_files = list(STRUCTURED_DIR.glob("*.jsonl"))
    structured_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)

    for file_path in structured_files[: max(1, max_files)]:
        try:
            with open(file_path, encoding="utf-8") as f:
                lines = f.readlines()
        except Exception:
            continue

        for line in reversed(lines):
            if len(events) >= limit:
                break
            if not line.strip():
                continue
            try:
                data = json.loads(line.strip())
            except json.JSONDecodeError:
                continue
            if not _matches_structured_filters(data, filters):
                continue

            timestamp = str(data.get("timestamp", "Unknown"))
            message = str(data.get("message", "")).strip()
            events.append(
                {
                    "timestamp": timestamp,
                    "level": str(data.get("level", "INFO")),
                    "component": str(data.get("component") or "unknown"),
                    "operation": str(data.get("operation") or ""),
                    "event_name": str(data.get("event_name") or ""),
                    "status": str(data.get("status") or ""),
                    "item_id": data.get("item_id"),
                    "message": message[:240] + ("..." if len(message) > 240 else ""),
                    "file": file_path.name,
                }
            )

        if len(events) >= limit:
            break

    return events


def _format_jsonl_content(file_path: Path, filters: dict[str, str] | None = None) -> str:
    """Format JSONL file content for display."""
    formatted_lines = []

    try:
        with open(file_path, encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                try:
                    data = json.loads(line.strip())
                    if not _matches_structured_filters(data, filters):
                        continue
                    # Pretty format the JSON
                    formatted = json.dumps(data, indent=2, ensure_ascii=False)
                    formatted_lines.append(f"=== Entry {i} ===")
                    formatted_lines.append(formatted)
                    formatted_lines.append("")  # Empty line for separation
                except json.JSONDecodeError:
                    formatted_lines.append(f"=== Entry {i} (Invalid JSON) ===")
                    formatted_lines.append(line.strip())
                    formatted_lines.append("")
    except Exception as e:
        return f"Error reading JSONL file: {str(e)}"

    return "\n".join(formatted_lines)


def _get_structured_filters(request: Request) -> dict[str, str]:
    """Collect supported structured log filters from the request."""
    return {
        field: value
        for field in STRUCTURED_FILTER_FIELDS
        if (value := request.query_params.get(field))
    }


def _matches_structured_filters(
    entry: dict[str, Any],
    filters: dict[str, str] | None,
) -> bool:
    """Return True when a structured log entry matches the active filters."""
    if not filters:
        return True
    for key, expected in filters.items():
        value = entry.get(key)
        if value is None:
            return False
        if str(value) != expected:
            return False
    return True


def _get_llm_usage_rows(
    *,
    provider: str | None,
    model: str | None,
    feature: str | None,
    start_date: str | None,
    end_date: str | None,
    limit: int = 100,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Query persisted LLM usage records and aggregate simple totals."""
    with get_db() as db:
        query = db.query(LlmUsageRecord).order_by(LlmUsageRecord.created_at.desc())
        if provider:
            query = query.filter(LlmUsageRecord.provider == provider)
        if model:
            query = query.filter(LlmUsageRecord.model == model)
        if feature:
            query = query.filter(LlmUsageRecord.feature == feature)

        start_dt = _parse_date_filter(start_date, end_of_day=False)
        end_dt = _parse_date_filter(end_date, end_of_day=True)
        if start_dt:
            query = query.filter(LlmUsageRecord.created_at >= start_dt)
        if end_dt:
            query = query.filter(LlmUsageRecord.created_at <= end_dt)

        rows = query.limit(limit).all()
        totals_query = db.query(
            func.coalesce(func.sum(LlmUsageRecord.input_tokens), 0),
            func.coalesce(func.sum(LlmUsageRecord.output_tokens), 0),
            func.coalesce(func.sum(LlmUsageRecord.total_tokens), 0),
            func.coalesce(func.sum(LlmUsageRecord.cost_usd), 0.0),
            func.count(LlmUsageRecord.id),
        )
        if provider:
            totals_query = totals_query.filter(LlmUsageRecord.provider == provider)
        if model:
            totals_query = totals_query.filter(LlmUsageRecord.model == model)
        if feature:
            totals_query = totals_query.filter(LlmUsageRecord.feature == feature)
        if start_dt:
            totals_query = totals_query.filter(LlmUsageRecord.created_at >= start_dt)
        if end_dt:
            totals_query = totals_query.filter(LlmUsageRecord.created_at <= end_dt)
        (
            total_input_tokens,
            total_output_tokens,
            total_tokens,
            total_cost_usd,
            total_rows,
        ) = totals_query.one()

    records = [
        {
            "id": row.id,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "provider": row.provider,
            "model": row.model,
            "feature": row.feature,
            "operation": row.operation,
            "source": row.source,
            "request_id": row.request_id,
            "task_id": row.task_id,
            "content_id": row.content_id,
            "session_id": row.session_id,
            "message_id": row.message_id,
            "user_id": row.user_id,
            "input_tokens": row.input_tokens,
            "output_tokens": row.output_tokens,
            "total_tokens": row.total_tokens,
            "cost_usd": row.cost_usd,
            "pricing_version": row.pricing_version,
        }
        for row in rows
    ]
    return records, {
        "row_count": int(total_rows or 0),
        "input_tokens": int(total_input_tokens or 0),
        "output_tokens": int(total_output_tokens or 0),
        "total_tokens": int(total_tokens or 0),
        "cost_usd": round(float(total_cost_usd or 0.0), 8),
    }


def _parse_date_filter(value: str | None, *, end_of_day: bool) -> datetime | None:
    """Parse simple YYYY-MM-DD filters into UTC datetimes."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    if end_of_day:
        parsed = parsed.replace(hour=23, minute=59, second=59, microsecond=999999)
    else:
        parsed = parsed.replace(hour=0, minute=0, second=0, microsecond=0)
    return parsed.astimezone(UTC)
