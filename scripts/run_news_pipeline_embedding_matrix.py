"""Run a fixed news pipeline eval case across embedding-model / threshold variants."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models.news_pipeline_eval_models import (
    NewsPipelineEvalRunConfig,
    NewsPipelineEvalRunResult,
    NewsPipelineEvalSuiteResult,
)
from app.services.news_pipeline_eval import load_eval_cases, run_eval_case, write_eval_artifact
from app.services.news_pipeline_eval_report import write_news_pipeline_eval_html_report

from app.core.logging import get_logger, setup_logging
from app.core.settings import get_settings
from app.services.news_embeddings import get_news_embedding_model

logger = get_logger(__name__)


@dataclass(frozen=True)
class ThresholdSpec:
    label: str
    primary: float
    secondary: float


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one or more eval cases across embedding model / threshold variants"
    )
    parser.add_argument(
        "--input",
        action="append",
        dest="inputs",
        required=True,
        help="Path to an eval case JSON file (repeatable)",
    )
    parser.add_argument(
        "--embedding-model",
        action="append",
        dest="embedding_models",
        required=True,
        help="Embedding model id to evaluate (repeatable)",
    )
    parser.add_argument(
        "--threshold",
        action="append",
        dest="thresholds",
        required=True,
        help="Threshold spec in label:primary:secondary format (repeatable)",
    )
    parser.add_argument(
        "--allow-summary-generation",
        action="store_true",
        help="Allow snapshot cases to generate new summaries for incomplete items",
    )
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=200,
        help="News candidate cap override for the comparison runs",
    )
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        default=Path(".tmp/news_pipeline_eval"),
        help="Directory for per-case JSON artifacts",
    )
    parser.add_argument(
        "--html-report",
        type=Path,
        required=True,
        help="Path to the generated comparison HTML report",
    )
    parser.add_argument(
        "--title",
        type=str,
        default="News Pipeline Embedding Comparison",
        help="HTML report title",
    )
    return parser.parse_args(argv)


def _parse_thresholds(raw_specs: list[str]) -> list[ThresholdSpec]:
    specs: list[ThresholdSpec] = []
    for raw_spec in raw_specs:
        label, primary_raw, secondary_raw = raw_spec.split(":", 2)
        specs.append(
            ThresholdSpec(
                label=label.strip(),
                primary=float(primary_raw),
                secondary=float(secondary_raw),
            )
        )
    return specs


def _slugify(value: str) -> str:
    return "".join(char if char.isalnum() else "-" for char in value.casefold()).strip("-")


@contextmanager
def _temporary_news_settings(
    *,
    embedding_model: str,
    primary_threshold: float,
    secondary_threshold: float,
    max_candidates: int,
) -> Iterator[None]:
    previous = {
        "NEWS_EMBEDDING_MODEL": os.environ.get("NEWS_EMBEDDING_MODEL"),
        "NEWS_DIGEST_PRIMARY_SIMILARITY_THRESHOLD": os.environ.get(
            "NEWS_DIGEST_PRIMARY_SIMILARITY_THRESHOLD"
        ),
        "NEWS_DIGEST_SECONDARY_SIMILARITY_THRESHOLD": os.environ.get(
            "NEWS_DIGEST_SECONDARY_SIMILARITY_THRESHOLD"
        ),
        "NEWS_DIGEST_MAX_CANDIDATES": os.environ.get("NEWS_DIGEST_MAX_CANDIDATES"),
    }
    os.environ["NEWS_EMBEDDING_MODEL"] = embedding_model
    os.environ["NEWS_DIGEST_PRIMARY_SIMILARITY_THRESHOLD"] = str(primary_threshold)
    os.environ["NEWS_DIGEST_SECONDARY_SIMILARITY_THRESHOLD"] = str(secondary_threshold)
    os.environ["NEWS_DIGEST_MAX_CANDIDATES"] = str(max_candidates)
    get_settings.cache_clear()
    get_news_embedding_model.cache_clear()
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear()
        get_news_embedding_model.cache_clear()


def _build_variant_result(
    *,
    case,
    embedding_model: str,
    threshold: ThresholdSpec,
    max_candidates: int,
    allow_summary_generation: bool,
) -> NewsPipelineEvalRunResult:
    variant_case_id = f"{case.case_id}__{_slugify(embedding_model)}__{threshold.label}"
    variant_case = case.model_copy(
        update={
            "case_id": variant_case_id,
            "description": (
                f"{case.description or case.case_id} | "
                f"embedding={embedding_model} | "
                f"threshold={threshold.label} ({threshold.primary:.2f}/{threshold.secondary:.2f})"
            ),
        }
    )
    run_config = NewsPipelineEvalRunConfig(
        label=threshold.label,
        base_case_id=case.case_id,
        embedding_model=embedding_model,
        primary_similarity_threshold=threshold.primary,
        secondary_similarity_threshold=threshold.secondary,
        max_candidates=max_candidates,
    )
    with _temporary_news_settings(
        embedding_model=embedding_model,
        primary_threshold=threshold.primary,
        secondary_threshold=threshold.secondary,
        max_candidates=max_candidates,
    ):
        try:
            result = run_eval_case(
                case=variant_case,
                allow_summary_generation=allow_summary_generation,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Embedding comparison run failed",
                extra={
                    "component": "news_pipeline_eval",
                    "operation": "embedding_matrix",
                    "context_data": {
                        "case_id": case.case_id,
                        "embedding_model": embedding_model,
                        "threshold_label": threshold.label,
                    },
                },
            )
            return NewsPipelineEvalRunResult(
                case_id=variant_case_id,
                mode=case.mode,
                run_config=run_config,
                failures=[str(exc)],
                passed=False,
            )
    return result.model_copy(update={"run_config": run_config})


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    args = _parse_args(argv)
    cases = load_eval_cases([Path(raw_path) for raw_path in args.inputs])
    thresholds = _parse_thresholds(args.thresholds)
    results: list[NewsPipelineEvalRunResult] = []
    rendered_cases = []
    for case in cases:
        for embedding_model in args.embedding_models:
            for threshold in thresholds:
                result = _build_variant_result(
                    case=case,
                    embedding_model=embedding_model,
                    threshold=threshold,
                    max_candidates=args.max_candidates,
                    allow_summary_generation=args.allow_summary_generation,
                )
                results.append(result)
                rendered_cases.append(
                    case.model_copy(
                        update={
                            "case_id": result.case_id,
                            "description": (
                                f"{case.description or case.case_id} | "
                                f"embedding={embedding_model} | "
                                f"threshold={threshold.label}"
                            ),
                        }
                    )
                )
                write_eval_artifact(result, artifacts_dir=args.artifacts_dir)

    suite = NewsPipelineEvalSuiteResult(
        case_count=len(results),
        passed=all(result.passed for result in results),
        results=results,
    )
    output_path = write_news_pipeline_eval_html_report(
        cases=rendered_cases,
        suite=suite,
        output_path=args.html_report,
        report_title=args.title,
    )
    logger.info(
        "Completed embedding model matrix eval",
        extra={
            "component": "news_pipeline_eval",
            "operation": "embedding_matrix",
            "context_data": {
                "case_count": len(results),
                "output": str(output_path.resolve()),
            },
        },
    )
    print(output_path.resolve())
    return 0 if suite.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
