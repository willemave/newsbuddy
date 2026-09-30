"""Summarize Rust replay outputs without rescoring or changing assignments."""

from __future__ import annotations

import argparse
import json
import statistics
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def weighted(rows: list[dict[str, Any]], field: str) -> float | None:
    valid = [r for r in rows if r["metrics"][field] is not None]
    total = sum(r["metrics"]["new_arrival_count"] for r in valid)
    if not total:
        return None
    return sum(r["metrics"][field] * r["metrics"]["new_arrival_count"] for r in valid) / total


def summary(variant: dict[str, Any], since: int) -> dict[str, Any]:
    # Compare identical arrival periods across different checkpoint cadences.
    rows = [
        checkpoint
        for index, checkpoint in enumerate(variant["checkpoints"])
        if (
            index == 0
            and since == 0
            or index > 0
            and variant["checkpoints"][index - 1]["cutoff"] >= since
        )
        and checkpoint["metrics"]["n_window"]
    ]
    if not rows:
        return {"label": variant["label"], "checkpoints": 0}
    active = [c["metrics"] for c in rows]
    movement = [
        m["matched_movement_shared_active"]
        for m in active
        if m["matched_movement_shared_active"] is not None
    ]
    ari = [
        m["adjusted_rand_shared_active"]
        for m in active
        if m["adjusted_rand_shared_active"] is not None
    ]
    return {
        "label": variant["label"],
        "checkpoints": len(rows),
        "arrival_count_scored": sum(
            m["new_arrival_count"] for m in active if m["pre_recluster_mean_cosine"] is not None
        ),
        "arrival_cosine": weighted(rows, "pre_recluster_mean_cosine"),
        "arrival_margin": weighted(rows, "pre_recluster_mean_margin"),
        "arrival_weak_fraction": weighted(rows, "pre_recluster_weak_fraction"),
        "arrival_match_coverage": weighted(rows, "pre_recluster_match_coverage"),
        "mean_shared_movement": statistics.mean(movement) if movement else None,
        "mean_shared_ari": statistics.mean(ari) if ari else None,
        "mean_noise_fraction": statistics.mean(m["noise_fraction"] for m in active),
        "mean_cluster_count": statistics.mean(m["cluster_count"] for m in active),
        "mean_cohesion": statistics.mean(m["mean_cosine_to_center"] for m in active),
        "mean_largest_cluster_fraction": statistics.mean(
            m["largest_cluster_fraction"] for m in active
        ),
        "median_compute_ms": statistics.median(m["elapsed_ms"] for m in active),
        "max_compute_ms": max(m["elapsed_ms"] for m in active),
        "births": sum(m["births"] for m in active),
        "deaths": sum(m["deaths"] for m in active),
        "final_seen": rows[-1]["metrics"]["n_seen"],
        "final_window": rows[-1]["metrics"]["n_window"],
    }


def fmt(row: dict[str, Any], key: str, percent: bool = False) -> str:
    value = row.get(key)
    if value is None:
        return "—"
    return f"{value * 100:.1f}%" if percent else f"{value:.3f}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--since", default="2026-09-07T10:00:00+00:00")
    args = parser.parse_args()
    since = int(datetime.fromisoformat(args.since).timestamp())
    report: dict[str, Any] = {"schema_version": 1, "recent_since": args.since, "runs": {}}
    lines = [
        "# News category replay: numerical results",
        "",
        "Pre-update arrival fit is measured against the previous checkpoint's centers. "
        "Higher cosine/margin and lower weak-match rate are geometric diagnostics, "
        "not independently judged category quality.",
        "",
        "Arrival metrics are weighted by arrival count; churn/ARI and in-window diagnostics "
        "are means across eligible checkpoints. Timing excludes export, JSON parsing, "
        "provider calls and production publication.",
        "",
    ]
    for path in sorted(args.directory.glob("*-weekly.output.json")) + sorted(
        args.directory.glob("*-nightly.output.json")
    ):
        payload = json.loads(path.read_text())
        full = [summary(v, 0) for v in payload["variants"]]
        recent = [summary(v, since) for v in payload["variants"]]
        report["runs"][path.stem] = {"full": full, "recent": recent}
        lines.extend(
            [
                f"## {path.stem}",
                "",
                f"Arrival intervals beginning at or after {args.since}.",
                "",
                "| Variant | Next-batch cosine | Margin | Weak < .45 | Shared movement | "
                "ARI | Mixed | Median compute ms | New IDs |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for row in recent:
            lines.append(
                f"| {row['label']} | {fmt(row, 'arrival_cosine')} | {fmt(row, 'arrival_margin')} | "
                f"{fmt(row, 'arrival_weak_fraction', True)} | "
                f"{fmt(row, 'mean_shared_movement', True)} | "
                f"{fmt(row, 'mean_shared_ari')} | {fmt(row, 'mean_noise_fraction', True)} | "
                f"{fmt(row, 'median_compute_ms')} | {row.get('births', 0)} |"
            )
        lines.append("")
        if path.name == "all-weekly.output.json":
            variant = next(v for v in payload["variants"] if v["label"] == "warm14")
            lines.extend(
                [
                    "### Warm 14-day progression",
                    "",
                    "| Cutoff UTC | Seen | Fitting set | Cohesion | Next-batch cosine | "
                    "Shared movement | New IDs |",
                    "|---|---:|---:|---:|---:|---:|---:|",
                ]
            )
            for checkpoint in variant["checkpoints"]:
                m = checkpoint["metrics"]
                date = datetime.fromtimestamp(checkpoint["cutoff"], UTC).strftime("%Y-%m-%d")
                fit = m["pre_recluster_mean_cosine"]
                churn = m["matched_movement_shared_active"]
                fit_s = f"{fit:.3f}" if fit is not None else "—"
                churn_s = f"{churn * 100:.1f}%" if churn is not None else "—"
                lines.append(
                    f"| {date} | {m['n_seen']} | {m['n_window']} | "
                    f"{m['mean_cosine_to_center']:.3f} | {fit_s} | {churn_s} | "
                    f"{m['births']} |"
                )
            lines.append("")
    (args.directory / "comparison.json").write_text(json.dumps(report, indent=2) + "\n")
    (args.directory / "comparison.md").write_text("\n".join(lines) + "\n")
    print(args.directory / "comparison.md")


if __name__ == "__main__":
    main()
