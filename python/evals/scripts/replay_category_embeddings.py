#!/usr/bin/env python3
"""Offline experiment reproducing the pre-Jev embedding assignment baseline.

This is not the current production matcher. Historical source snapshot:
test-results/jev-embedding-comparison-20261018/code/00-semantic_lenses.rs
SHA-256: bff04684ec3d2233ba295e6dc8b90e27312fc43075a9b92ebfe11df18ec9d7bf
Current routing uses Jev decisions; production parity must use the Rust eval driver.

Categories are frozen: this does not create/merge/retire lenses or model eligibility.
Vectors are used as supplied; normalize provider/profile vectors before replay, but
never renormalize durable centroids. Weight zero denotes a profile fallback without
a stored centroid; the first accepted story replaces it. Batch order is significant.
"""

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any


def checked_vector(value: Any, width: int | None = None) -> list[float]:
    if not isinstance(value, list) or not value or (width is not None and len(value) != width):
        raise ValueError("vectors must be nonempty and have identical dimensions")
    if any(
        isinstance(item, bool) or not isinstance(item, (float, int)) or not math.isfinite(item)
        for item in value
    ):
        raise ValueError("vectors must contain finite numbers")
    vector = [float(item) for item in value]
    norm = math.sqrt(sum(item * item for item in vector))
    if not math.isfinite(norm) or norm <= 1e-12:
        raise ValueError("vectors must have a finite nonzero norm above 1e-12")
    return vector


def normalize_vector(value: list[float]) -> list[float]:
    """Match provider normalization, only for fresh embeddings, never centroids."""
    vector = checked_vector(value)
    norm = math.sqrt(sum(item * item for item in vector))
    return [item / norm for item in vector]


def mean_vector(vectors: list[list[float]]) -> list[float]:
    """Arithmetic class mean; retain magnitude for subsequent weighted updates."""
    if not vectors:
        raise ValueError("a mean requires at least one vector")
    checked = [checked_vector(vector, len(vectors[0])) for vector in vectors]
    return [sum(values) / len(checked) for values in zip(*checked, strict=True)]


def cosine(left: list[float], right: list[float]) -> float:
    """Historical Rust cosine behavior, including -1 for an internally degenerate centroid."""
    if not left or len(left) != len(right):
        return -1.0
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm <= sys.float_info.epsilon or right_norm <= sys.float_info.epsilon:
        return -1.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)


def integer(value: Any, name: str, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


def replay(data: dict[str, Any]) -> dict[str, Any]:
    categories, stories = data.get("categories"), data.get("stories")
    if not isinstance(categories, list) or len(categories) > 10 or not isinstance(stories, list):
        raise ValueError("categories must contain at most ten entries; stories must be a list")
    threshold = data.get("category_similarity", data.get("threshold", 0.55))
    absorb = data.get("absorb_similarity", 0.45)
    for name, value in (("category_similarity", threshold), ("absorb_similarity", absorb)):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not -1 <= value <= 1
        ):
            raise ValueError(f"{name} must be finite and within [-1,1]")
    at_cap = data.get("at_category_cap", len(categories) == 10)
    if not isinstance(at_cap, bool):
        raise ValueError("at_category_cap must be boolean")
    batch_size = integer(data.get("batch_size", 40), "batch_size", 1)
    max_weight = integer(data.get("max_weight", 32), "max_weight", 1)
    update = data.get("update_centroids", True)
    if not isinstance(update, bool):
        raise ValueError("update_centroids must be boolean")
    width = None
    for rows in (categories, stories):
        seen = set()
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("each entry must be an object")
            identifier = row.get("id")
            if not isinstance(identifier, str) or not identifier or identifier in seen:
                raise ValueError("IDs must be nonempty strings unique within each collection")
            seen.add(identifier)
            vector = checked_vector(row.get("vector"), width)
            width = len(vector)
    working = [
        {
            "id": row["id"],
            "vector": list(row["vector"]),
            "weight": integer(row.get("weight", 0), "weight", 0),
        }
        for row in categories
    ]
    assignments = []

    def update_centroid(index: int, vector: list[float]) -> None:
        if not update:
            return
        category = working[index]
        weight = category["weight"]
        if weight == 0:
            category.update(vector=list(vector), weight=1)
            return
        capped = min(max(weight, 1), max_weight)
        category["vector"] = [
            (current * capped + following) / (capped + 1)
            for current, following in zip(category["vector"], vector, strict=True)
        ]
        category["weight"] = min(weight + 1, max_weight)

    for offset in range(0, len(stories), batch_size):
        frozen = [list(category["vector"]) for category in working]
        pending = []
        batch_rows = []
        for story in stories[offset : offset + batch_size]:
            scores = [cosine(story["vector"], vector) for vector in frozen]
            best, best_score = None, -1.0
            for index, score in enumerate(scores):
                if score > best_score:  # Exact ties retain the first category in input order.
                    best, best_score = index, score
            ranked = sorted(range(len(scores)), key=lambda index: -scores[index])
            runner_up = ranked[1] if best is not None and len(ranked) > 1 else None
            row = {
                "id": story["id"],
                "category": "other",
                "best_category": working[best]["id"] if best is not None else None,
                "cosine": best_score if best is not None else None,
                "runner_up": {"category": working[runner_up]["id"], "cosine": scores[runner_up]}
                if runner_up is not None
                else None,
                "margin": best_score - scores[runner_up] if runner_up is not None else None,
                "stage": "other",
                "batch": offset // batch_size,
            }
            if best is not None and best_score >= threshold:
                row.update(category=working[best]["id"], stage="primary")
                update_centroid(best, story["vector"])
            else:
                pending.append((story, row, best, best_score))
            batch_rows.append(row)
        # Finish the historical primary pass before updating capped absorption stories.
        if at_cap:
            for story, row, best, score in pending:
                if best is not None and score >= absorb:
                    row.update(category=working[best]["id"], stage="capped_absorption")
                    update_centroid(best, story["vector"])
        assignments.extend(batch_rows)
    return {
        "assignments": assignments,
        "final_centroids": working,
        "settings": {
            "threshold": threshold,
            "absorb_threshold": absorb,
            "at_category_cap": at_cap,
            "max_weight": max_weight,
            "batch_size": batch_size,
            "update_centroids": update,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = replay(json.loads(args.input.read_text()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")


if __name__ == "__main__":
    main()
