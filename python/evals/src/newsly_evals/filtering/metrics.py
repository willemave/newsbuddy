"""Challenge-set metrics; selection happens only on development items."""

import hashlib
import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .artifacts import LABELS, VARIANTS, digest, read_jsonl, write_json
from .judge import REFERENCE_VALIDATOR_VERSION
from .ledger import read_jev_state
from .migration import resolve_reference_source
from .provider import RESPONSE_VALIDATOR_VERSION, validate_stored_result
from .references import accepted_references

THRESHOLDS = (0.5, 0.7, 0.8, 0.9, 0.95, 0.98)


def ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def summarize(rows: list[dict[str, Any]], threshold: float) -> dict[str, Any]:
    tp = fp = tn = fn = 0
    confusion: Counter[str] = Counter()
    unavailable = uncertain = unresolved = unknown_admitted = 0
    for row in rows:
        gold, result = row["gold"], row["result"]
        choice = result["choice"] if result else "unavailable"
        confusion[f"{gold or 'unlabelled'}->{choice}"] += 1
        unavailable += result is None
        uncertain += choice == "insufficient_evidence"
        if gold not in {"match", "no_match"}:
            unresolved += 1
            unknown_admitted += not (
                result and choice == "no_match" and result["probabilities"]["no_match"] >= threshold
            )
            continue
        excluded = bool(
            result and choice == "no_match" and result["probabilities"]["no_match"] >= threshold
        )
        if gold == "match":
            fn += excluded
            tp += not excluded
        else:
            tn += excluded
            fp += not excluded
    return {
        "pairs": len(rows),
        "binary_labelled": tp + fp + tn + fn,
        "unresolved_gold": unresolved,
        "unresolved_gold_admitted": unknown_admitted,
        "unavailable": unavailable,
        "unavailable_rate": ratio(unavailable, len(rows)),
        "uncertain": uncertain,
        "uncertainty_rate": ratio(uncertain, len(rows)),
        "confusion": dict(sorted(confusion.items())),
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
        "admitted_precision": ratio(tp, tp + fp),
        "relevance_recall": ratio(tp, tp + fn),
        "exclusion_precision": ratio(tn, tn + fn),
        "exclusion_recall": ratio(tn, tn + fp),
        "excluded_fraction": ratio(tn + fn, tp + fp + tn + fn),
    }


def grouped(rows: list[dict[str, Any]], threshold: float, field: str) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row[field])].append(row)
    metrics = {key: summarize(group, threshold) for key, group in sorted(groups.items())}
    macro = {}
    for metric in ("admitted_precision", "relevance_recall", "exclusion_precision"):
        defined = [m[metric] for m in metrics.values() if m[metric] is not None]
        macro[metric] = sum(defined) / len(defined) if defined else None
        macro[metric + "_defined_groups"] = len(defined)
        if metric == "admitted_precision":
            macro[metric] = (
                sum(m[metric] or 0 for m in metrics.values()) / len(metrics) if metrics else None
            )
            macro[metric + "_profiles_in_denominator"] = len(metrics)
    return {"groups": metrics, "macro": macro}


def confidence_intervals(
    rows_by_variant: dict[str, list[dict[str, Any]]],
    selected: str,
    threshold: float,
    draws: int = 500,
) -> dict[str, Any]:
    """Same sampled story clusters for every variant and every metric difference."""
    clusters: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for variant, rows in rows_by_variant.items():
        clusters[variant] = defaultdict(list)
        for row in rows:
            clusters[variant][str(row["story_group"])].append(row)
    keys = sorted(clusters[selected])
    if not keys:
        return {}
    rng = random.Random(20261017)
    values: dict[str, list[float]] = defaultdict(list)
    for _ in range(draws):
        sampled = rng.choices(keys, k=len(keys))
        metrics = {
            variant: summarize([row for key in sampled for row in group[key]], threshold)
            for variant, group in clusters.items()
        }
        for metric in ("admitted_precision", "relevance_recall", "exclusion_precision"):
            chosen = metrics[selected][metric]
            if chosen is None:
                continue
            values[metric].append(chosen)
            for variant in VARIANTS:
                comparator = metrics[variant][metric]
                if variant != selected and comparator is not None:
                    values[f"{metric}_delta_vs_{variant}"].append(chosen - comparator)
    intervals = {}
    for name, draws_values in values.items():
        ordered = sorted(draws_values)
        intervals[name] = {
            "low": ordered[int(0.025 * (len(ordered) - 1))],
            "high": ordered[int(0.975 * (len(ordered) - 1))],
            "defined_draws": len(ordered),
        }
    return {
        "method": "paired story-cluster percentile bootstrap",
        "draws": draws,
        "seed": 20261017,
        "intervals_95_percent": intervals,
    }


def select_development(rows_by_variant: dict[str, list[dict[str, Any]]]) -> dict[str, Any] | None:
    candidates = []
    for variant, rows in rows_by_variant.items():
        subset = [
            row
            for row in rows
            if row["split"] == "development"
            and row["age_slice"] == "recent"
            and row["result"] is not None
        ]
        for threshold in THRESHOLDS:
            micro = summarize(subset, threshold)
            profiles = grouped(subset, threshold, "profile_id")
            recall = micro["relevance_recall"]
            macro_precision = profiles["macro"]["admitted_precision"]
            gated = {
                name: metrics
                for name, metrics in profiles["groups"].items()
                if metrics["tp"] + metrics["fn"] >= 20
            }
            gates_pass = all(metrics["relevance_recall"] >= 0.95 for metrics in gated.values())
            if recall is not None and recall >= 0.95 and macro_precision is not None and gates_pass:
                candidates.append(
                    (macro_precision, -VARIANTS.index(variant), threshold, variant, micro, profiles)
                )
    if not candidates:
        return None
    macro_precision, _, threshold, variant, micro, profiles = max(candidates, key=lambda x: x[:3])
    return {
        "variant": variant,
        "threshold": threshold,
        "development_macro_profile_precision": macro_precision,
        "development_precision": micro["admitted_precision"],
        "development_recall": micro["relevance_recall"],
        "development_profile_metrics": profiles,
        "rule": "Recent development only: micro recall >=.95 and every profile with >=20 "
        "positive references recall >=.95; maximize macro profile precision. "
        "Ties prefer prose, bullets, structured, checklist, then higher threshold.",
    }


def all_curves(
    rows_by_variant: dict[str, list[dict[str, Any]]], splits: tuple[str, ...]
) -> dict[str, Any]:
    operational: dict[str, Any] = {}
    completed: dict[str, Any] = {}
    fixed_groups: dict[str, Any] = {}
    for variant, rows in rows_by_variant.items():
        operational[variant], completed[variant], fixed_groups[variant] = {}, {}, {}
        for split in splits:
            operational[variant][split], completed[variant][split] = {}, {}
            fixed_groups[variant][split] = {}
            for age in sorted({r["age_slice"] for r in rows}):
                subset = [r for r in rows if r["split"] == split and r["age_slice"] == age]
                operational[variant][split][age] = {
                    str(t): summarize(subset, t) for t in THRESHOLDS
                }
                completed[variant][split][age] = {
                    str(t): summarize([r for r in subset if r["result"] is not None], t)
                    for t in THRESHOLDS
                }
                fixed_groups[variant][split][age] = {
                    "by_profile": grouped(subset, 0.9, "profile_id"),
                    "by_aggregator": grouped(subset, 0.9, "aggregator"),
                }
    return {
        "fixed_curves": operational,
        "completed_only_curves": completed,
        "fixed_0_9_subgroups": fixed_groups,
    }


def score(manifest: dict[str, Any], output: Path, *, allow_partial: bool = False) -> dict[str, Any]:
    checkpoint = read_jev_state(output, manifest)
    if checkpoint["manifest_hash"] != manifest["manifest_hash"]:
        raise ValueError("Jev checkpoint manifest mismatch")
    reference_root, reference_manifest, reference_source = resolve_reference_source(
        manifest, output
    )
    judge = json.loads((reference_root / "judge-checkpoint.json").read_text())
    labels, reference_provenance = accepted_references(reference_manifest, reference_root, judge)
    loose_labels = read_jsonl(reference_root / "judge-labels.jsonl")
    if sorted(loose_labels, key=lambda r: r["pair_id"]) != sorted(
        labels, key=lambda r: r["pair_id"]
    ):
        raise ValueError("Judge labels differ from manifest-bound checkpoint")
    gold = {row["pair_id"]: row["label"] for row in labels}
    pair_ids = {p["pair_id"] for p in manifest["pairs"]}
    if len(gold) != len(labels) or not set(gold) <= pair_ids:
        raise ValueError("Duplicate/unrequested judge labels")
    if any(label not in LABELS for label in gold.values()):
        raise ValueError("Invalid gold label")
    if not allow_partial and set(gold) != pair_ids:
        raise ValueError("Reference labels incomplete; use --allow-partial for provisional report")
    # Optional human labels are a separate sensitivity analysis, never headline selection.
    human_path = reference_root / "human-labels.jsonl"
    human_bytes = human_path.read_bytes() if human_path.exists() else None
    human_count = 0
    human_cohorts: dict[str, dict[str, str]] = defaultdict(dict)
    if human_bytes is not None:
        human_rows = [json.loads(line) for line in human_bytes.splitlines() if line.strip()]
        seen = set()
        for row in human_rows:
            pair_id, label = row["pair_id"], row["human_label"]
            if row.get("manifest_hash") != reference_manifest["manifest_hash"]:
                raise ValueError("Human labels must include the frozen manifest_hash")
            if pair_id in seen or pair_id not in pair_ids or label not in LABELS:
                raise ValueError("Invalid or duplicate human adjudication")
            seen.add(pair_id)
            cohort = row.get("review_cohort", row.get("review_selection", "unspecified"))
            if cohort == "unselected":
                cohort = "unspecified"
            if not isinstance(cohort, str) or not cohort or len(cohort) > 80:
                raise ValueError("Invalid human review cohort")
            human_cohorts[cohort][pair_id] = label
            human_count += 1
    results = checkpoint["results"]
    expected = {job["job_id"]: job["input_hash"] for job in manifest["jobs"]}
    if any(
        key not in expected or result["input_hash"] != expected[key]
        for key, result in results.items()
    ):
        raise ValueError("Jev results not bound to expected job input")
    for result in results.values():
        validate_stored_result(result, manifest["model"])
    rows_by_variant = {
        variant: [
            {
                **p,
                "gold": gold.get(p["pair_id"]),
                "result": results.get(p["pair_id"] + "-" + variant),
            }
            for p in manifest["pairs"]
        ]
        for variant in VARIANTS
    }
    complete = len(gold) == len(pair_ids) and len(results) == len(expected)
    if not complete and not allow_partial:
        raise ValueError("Jev run incomplete; --allow-partial reports no selected winner")
    selection = select_development(rows_by_variant) if complete else None
    selected_details: dict[str, Any] = {}
    if complete:
        development_rows = {
            v: [r for r in rows if r["split"] == "development"]
            for v, rows in rows_by_variant.items()
        }
        frozen_selection = {
            "schema_version": 1,
            "manifest_hash": manifest["manifest_hash"],
            "development_hash": digest(development_rows),
            "reference_validator_version": REFERENCE_VALIDATOR_VERSION,
            "response_validator_version": RESPONSE_VALIDATOR_VERSION,
            "reference_source": reference_source,
            "selection": selection,
        }
        selection_path = output / "selection.json"
        if selection_path.exists() and json.loads(selection_path.read_text()) != frozen_selection:
            raise ValueError(
                "Development inputs/selection changed after holdout scoring; "
                "create a new explicitly versioned experiment"
            )
        write_json(selection_path, frozen_selection)
    # No holdout metrics are calculated before the development choice is frozen.
    if selection is not None:
        variant, threshold = selection["variant"], selection["threshold"]
        for age in sorted({p["age_slice"] for p in manifest["pairs"]}):
            test_rows = {
                v: [r for r in rows if r["split"] == "test" and r["age_slice"] == age]
                for v, rows in rows_by_variant.items()
            }
            selected_details[age] = {
                "micro": summarize(test_rows[variant], threshold),
                "by_profile": grouped(test_rows[variant], threshold, "profile_id"),
                "by_aggregator": grouped(test_rows[variant], threshold, "aggregator"),
                "bootstrap": confidence_intervals(test_rows, variant, threshold),
            }
    reported_splits = ("development", "test") if complete else ("development",)
    curve_report = all_curves(rows_by_variant, reported_splits)
    human_sensitivity = None
    if human_count:
        cohort_reports = {}
        for cohort, annotations in sorted(human_cohorts.items()):
            sensitivity_gold = {**gold, **annotations}
            sensitivity_rows = {
                variant: [{**row, "gold": sensitivity_gold.get(row["pair_id"])} for row in rows]
                for variant, rows in rows_by_variant.items()
            }
            subset_rows = {
                variant: [row for row in rows if row["pair_id"] in annotations]
                for variant, rows in sensitivity_rows.items()
            }
            selection_type = {
                "random_audit": "random",
                "random": "random",
                "profile_coverage": "coverage",
                "coverage": "coverage",
                "targeted": "targeted",
            }.get(cohort, "unspecified")
            cohort_reports[cohort] = {
                "selection_type": selection_type,
                "label_count": len(annotations),
                "human_subset_only": all_curves(subset_rows, reported_splits),
                "reference_override_sensitivity": all_curves(sensitivity_rows, reported_splits),
            }
        human_sensitivity = {
            "note": "Cohorts are separate; coverage/targeted labels do not "
            "estimate population accuracy. Headline Opus gold is unchanged.",
            "cohorts": cohort_reports,
        }
    baseline: dict[str, Any] = {}
    for split in reported_splits:
        baseline[split] = {}
        for age in sorted({p["age_slice"] for p in manifest["pairs"]}):
            rows = [
                {**r, "result": {"choice": "match", "probabilities": {"no_match": 0}}}
                for r in rows_by_variant["prose"]
                if r["split"] == split and r["age_slice"] == age
            ]
            baseline[split][age] = summarize(rows, 0.9)
    report = {
        "schema_version": 1,
        "manifest_hash": manifest["manifest_hash"],
        "population": "Enriched challenge set, not deployment prevalence/precision",
        "reference_validator_version": REFERENCE_VALIDATOR_VERSION,
        "response_validator_version": RESPONSE_VALIDATOR_VERSION,
        "provisional": not complete,
        "heldout_metrics_withheld": not complete,
        "heldout_policy": "Test metrics are withheld until the run is complete and the "
        "development selection record is frozen.",
        "reference_labels": len(gold),
        "reference_provenance": reference_provenance,
        "reference_source": reference_source,
        "human_sensitivity_labels": human_count,
        "human_labels_sha256": (
            hashlib.sha256(human_bytes).hexdigest() if human_bytes is not None else None
        ),
        "human_sensitivity": human_sensitivity,
        "completed_jobs": len(results),
        "expected_jobs": len(expected),
        "selection": selection,
        "selected_test": selected_details,
        "accept_all_baseline": baseline,
        **curve_report,
        "limitations": [
            "Model judge is provisional gold, not human ground truth.",
            "Unknown gold excluded from binary denominators, counted separately.",
            "Unavailable Jev jobs fail open operationally and stay in denominators.",
            "Bootstrap captures sample uncertainty, not systematic judge error.",
            "Test curves are descriptive; do not reselect on test results.",
        ],
    }
    write_json(output / "metrics.json", report)
    return report
