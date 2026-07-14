from __future__ import annotations

import json
import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.settings import Settings, get_settings
from app.models.db import BriefingLens, BriefingPendingSource, BriefingSegment
from app.services.briefing.openrouter import (
    StructuredOutputRequester,
    request_openrouter_json_schema,
    strip_json_code_fence,
)
from app.services.briefing.sources import BriefingSource, sources_for_keys
from app.services.llm_agents import get_basic_agent
from app.services.prompt_library import render_prompt
from app.services.vendor_costs import record_vendor_usage_out_of_band
from app.services.vendor_usage import record_model_usage

logger = get_logger(__name__)
MISC_LENS_KEY = "misc"
FIXED_LENS_KEYS = {"podcasts", "articles"}


class TaxonomyPlanError(ValueError):
    """Raised when a proposed briefing taxonomy is incomplete or unsafe to apply."""


class TaxonomyCategory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(..., min_length=2, max_length=64)
    title: str = Field(..., min_length=2, max_length=40)
    deck: str = Field(..., min_length=8, max_length=180)
    routing_rule: str = Field(..., min_length=20, max_length=500)
    include_lens_keys: list[str] = Field(default_factory=list)


class TaxonomyPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    categories: list[TaxonomyCategory] = Field(default_factory=list)
    operating_model: str = Field(..., min_length=10, max_length=1200)


@dataclass(frozen=True)
class TaxonomyPlannerInput:
    user_id: int
    max_categories: int
    lens_dossiers: tuple[dict[str, Any], ...]


TaxonomyPlanner = Callable[[TaxonomyPlannerInput], TaxonomyPlan]


def build_llm_taxonomy_planner(
    *,
    settings: Settings,
    task_id: int | None,
    user_id: int | None,
    structured_output_requester: StructuredOutputRequester | None = None,
) -> TaxonomyPlanner:
    def plan_taxonomy(planner_input: TaxonomyPlannerInput) -> TaxonomyPlan:
        return _plan_taxonomy_with_llm(
            planner_input,
            settings=settings,
            task_id=task_id,
            user_id=user_id,
            structured_output_requester=structured_output_requester,
        )

    return plan_taxonomy


def apply_taxonomy_if_needed(
    db: Session,
    *,
    user_id: int,
    settings: Settings | None = None,
    planner: TaxonomyPlanner | None = None,
    task_id: int | None = None,
    use_llm: bool = True,
    force: bool = False,
    structured_output_requester: StructuredOutputRequester | None = None,
) -> int:
    settings = settings or get_settings()
    if not settings.briefing_taxonomy_planner_enabled and not force:
        return 0
    active_lenses = _active_news_lenses(db, user_id=user_id)
    active_news_count = _active_news_lens_count(db, user_id=user_id)
    if not force and active_news_count <= settings.briefing_max_news_lenses:
        return 0
    if not active_lenses:
        return 0
    planner_input = collect_taxonomy_planner_input(
        db,
        user_id=user_id,
        settings=settings,
        active_lenses=active_lenses,
    )
    if planner is None:
        if not use_llm:
            return 0
        planner = build_llm_taxonomy_planner(
            settings=settings,
            task_id=task_id,
            user_id=user_id,
            structured_output_requester=structured_output_requester,
        )

    last_error: Exception | None = None
    for attempt in range(1, 3):
        try:
            plan = planner(planner_input)
            return apply_taxonomy_plan(db, user_id=user_id, plan=plan, settings=settings)
        except Exception as exc:
            last_error = exc
            logger.exception(
                "Briefing taxonomy planner failed",
                extra={
                    "component": "briefing",
                    "operation": "plan_taxonomy",
                    "item_id": user_id,
                    "task_id": task_id,
                    "context_data": {
                        "attempt": attempt,
                        "max_attempts": 2,
                        "active_news_lenses": active_news_count,
                    },
                },
            )
    assert last_error is not None
    return 0


def collect_taxonomy_planner_input(
    db: Session,
    *,
    user_id: int,
    settings: Settings,
    active_lenses: list[BriefingLens] | None = None,
) -> TaxonomyPlannerInput:
    lenses = (
        active_lenses if active_lenses is not None else _active_news_lenses(db, user_id=user_id)
    )
    source_keys_by_lens_id = _active_source_keys_by_lens_id(db, user_id=user_id)
    sample_keys = list(
        dict.fromkeys(
            key
            for lens in lenses
            for key in source_keys_by_lens_id.get(int(lens.id or 0), [])[
                : settings.briefing_taxonomy_sources_per_lens
            ]
        )
    )
    source_map = (
        sources_for_keys(db, user_id=user_id, source_keys=sample_keys) if sample_keys else {}
    )
    dossiers: list[dict[str, Any]] = []
    for lens in lenses:
        lens_id = int(lens.id or 0)
        source_keys = source_keys_by_lens_id.get(lens_id, [])
        samples = [
            _source_sample(source_map[key])
            for key in source_keys[: settings.briefing_taxonomy_sources_per_lens]
            if key in source_map
        ]
        dossiers.append(
            {
                "key": str(lens.key),
                "title": str(lens.title),
                "deck": str(lens.deck or ""),
                "routing_rule": str(lens.routing_rule or ""),
                "position": int(lens.position or 0),
                "source_count": len(source_keys),
                "sample_sources": samples,
            }
        )
    return TaxonomyPlannerInput(
        user_id=user_id,
        max_categories=_planner_category_limit(db, user_id=user_id, settings=settings),
        lens_dossiers=tuple(dossiers),
    )


def apply_taxonomy_plan(
    db: Session,
    *,
    user_id: int,
    plan: TaxonomyPlan,
    settings: Settings | None = None,
) -> int:
    settings = settings or get_settings()
    active_lenses = _active_news_lenses(db, user_id=user_id)
    lenses_by_key = {str(lens.key): lens for lens in active_lenses}
    normalized_plan = _validated_plan(
        plan,
        input_lens_keys=set(lenses_by_key),
        max_categories=_planner_category_limit(db, user_id=user_id, settings=settings),
    )
    source_counts_by_lens_id = _source_counts_by_lens_id(db, user_id=user_id)
    changed = 0
    now = datetime.now(UTC).replace(tzinfo=None)

    for index, category in enumerate(normalized_plan.categories, start=2):
        included_lenses = [lenses_by_key[key] for key in category.include_lens_keys]
        winner = _get_or_create_taxonomy_winner(
            db,
            user_id=user_id,
            category=category,
            included_lenses=included_lenses,
            position=index,
        )
        if winner.id is None:
            db.flush()
        if winner.id is None:
            raise TaxonomyPlanError("taxonomy winner lens was not persisted")
        if _update_winner_fields(winner, category=category, position=index):
            changed += 1
        included_ids = {int(lens.id) for lens in included_lenses if lens.id is not None}
        if winner.id is not None:
            included_ids.discard(int(winner.id))
        loser_keys = [str(lens.key) for lens in included_lenses if lens is not winner]
        if included_ids:
            db.query(BriefingSegment).filter(BriefingSegment.user_id == user_id).filter(
                BriefingSegment.lens_id.in_(included_ids)
            ).update({BriefingSegment.lens_id: int(winner.id)}, synchronize_session=False)
        if loser_keys:
            db.query(BriefingPendingSource).filter(BriefingPendingSource.user_id == user_id).filter(
                BriefingPendingSource.lens_key.in_(loser_keys)
            ).update({BriefingPendingSource.lens_key: str(winner.key)}, synchronize_session=False)
        for loser in included_lenses:
            if loser is winner:
                continue
            if loser.status != "merged":
                changed += 1
            loser.status = "merged"
            loser.retired_at = now
        _update_merged_centroid(
            winner,
            included_lenses=included_lenses,
            source_counts_by_lens_id=source_counts_by_lens_id,
            settings=settings,
        )

    if changed:
        db.flush()
    return changed


def _plan_taxonomy_with_llm(
    planner_input: TaxonomyPlannerInput,
    *,
    settings: Settings,
    task_id: int | None,
    user_id: int | None,
    structured_output_requester: StructuredOutputRequester | None = None,
) -> TaxonomyPlan:
    started_at = time.perf_counter()
    model_spec = settings.briefing_taxonomy_model or settings.briefing_model
    user_prompt = render_prompt(
        "briefing/taxonomy#user",
        taxonomy_payload_json=json.dumps(
            {
                "target_category_count": min(10, planner_input.max_categories),
                "max_category_count": planner_input.max_categories,
                "lens_dossiers": list(planner_input.lens_dossiers),
            },
            ensure_ascii=False,
            indent=2,
        ),
    )
    system_prompt = render_prompt("briefing/taxonomy#system")
    if model_spec.startswith("openrouter:"):
        return _plan_taxonomy_with_openrouter(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model_spec=model_spec,
            timeout_seconds=settings.briefing_taxonomy_llm_timeout_seconds,
            settings=settings,
            task_id=task_id,
            user_id=user_id,
            lens_count=len(planner_input.lens_dossiers),
            generation_started_at=started_at,
            structured_output_requester=structured_output_requester,
        )

    agent = get_basic_agent(model_spec, TaxonomyPlan, system_prompt)
    result = agent.run_sync(
        user_prompt,
        model_settings={"timeout": settings.briefing_taxonomy_llm_timeout_seconds},
    )
    record_model_usage(
        "briefing_taxonomy_planning",
        result,
        model_spec=model_spec,
        persist={
            "feature": "briefing_taxonomy_planning",
            "operation": "briefing.plan_taxonomy",
            "source": "queue" if task_id else "api",
            "task_id": task_id,
            "user_id": user_id,
            "metadata": {
                "lens_count": len(planner_input.lens_dossiers),
                "generation_ms": round((time.perf_counter() - started_at) * 1000),
            },
        },
    )
    return result.output


def _plan_taxonomy_with_openrouter(
    *,
    system_prompt: str,
    user_prompt: str,
    model_spec: str,
    timeout_seconds: int,
    settings: Settings,
    task_id: int | None,
    user_id: int | None,
    lens_count: int,
    generation_started_at: float,
    structured_output_requester: StructuredOutputRequester | None = None,
) -> TaxonomyPlan:
    response = request_openrouter_json_schema(
        model_spec=model_spec,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        schema_name="TaxonomyPlan",
        schema=TaxonomyPlan.model_json_schema(),
        timeout_seconds=timeout_seconds,
        settings=settings,
        requester=structured_output_requester,
    )
    output = TaxonomyPlan.model_validate_json(strip_json_code_fence(response.content))
    record_vendor_usage_out_of_band(
        provider="openrouter",
        model=model_spec,
        feature="briefing_taxonomy_planning",
        operation="briefing.plan_taxonomy",
        source="queue" if task_id else "api",
        usage=response.usage,
        task_id=task_id,
        user_id=user_id,
        metadata={
            "lens_count": lens_count,
            "generation_ms": round((time.perf_counter() - generation_started_at) * 1000),
        },
    )
    return output


def _validated_plan(
    plan: TaxonomyPlan,
    *,
    input_lens_keys: set[str],
    max_categories: int,
) -> TaxonomyPlan:
    if not plan.categories:
        raise TaxonomyPlanError("Taxonomy plan has no categories")
    if len(plan.categories) > max_categories:
        raise TaxonomyPlanError(
            f"Taxonomy plan has {len(plan.categories)} categories, max is {max_categories}"
        )
    normalized_categories: list[TaxonomyCategory] = []
    seen_lens_keys: list[str] = []
    seen_category_keys: set[str] = set()
    for category in plan.categories:
        key = _normalize_lens_key(category.key)
        if key in FIXED_LENS_KEYS or key == MISC_LENS_KEY:
            raise TaxonomyPlanError(f"Taxonomy category key is reserved: {key}")
        if key in seen_category_keys:
            raise TaxonomyPlanError(f"Duplicate taxonomy category key: {key}")
        seen_category_keys.add(key)
        lens_keys = [str(value) for value in category.include_lens_keys]
        if not lens_keys:
            raise TaxonomyPlanError(f"Taxonomy category {key} has no included lenses")
        seen_lens_keys.extend(lens_keys)
        normalized_categories.append(
            TaxonomyCategory(
                key=key,
                title=" ".join(category.title.split()).strip()[:40],
                deck=" ".join(category.deck.split()).strip(),
                routing_rule=" ".join(category.routing_rule.split()).strip(),
                include_lens_keys=lens_keys,
            )
        )
    seen_set = set(seen_lens_keys)
    duplicates = sorted(key for key in seen_set if seen_lens_keys.count(key) > 1)
    missing = sorted(input_lens_keys - seen_set)
    extra = sorted(seen_set - input_lens_keys)
    if duplicates:
        raise TaxonomyPlanError(f"Taxonomy plan duplicates lens keys: {duplicates}")
    if missing:
        raise TaxonomyPlanError(f"Taxonomy plan is missing lens keys: {missing}")
    if extra:
        raise TaxonomyPlanError(f"Taxonomy plan has unknown lens keys: {extra}")
    return TaxonomyPlan(categories=normalized_categories, operating_model=plan.operating_model)


def _get_or_create_taxonomy_winner(
    db: Session,
    *,
    user_id: int,
    category: TaxonomyCategory,
    included_lenses: list[BriefingLens],
    position: int,
) -> BriefingLens:
    existing = (
        db.query(BriefingLens)
        .filter(BriefingLens.user_id == user_id, BriefingLens.key == category.key)
        .first()
    )
    if existing is not None:
        existing.status = "active"
        existing.retired_at = None
        return existing
    winner = BriefingLens(
        user_id=user_id,
        key=category.key,
        tier="news",
        title=category.title,
        deck=category.deck,
        routing_rule=category.routing_rule,
        position=position,
        status="active",
    )
    db.add(winner)
    db.flush()
    return winner


def _update_winner_fields(
    winner: BriefingLens,
    *,
    category: TaxonomyCategory,
    position: int,
) -> bool:
    changed = False
    updates = {
        "tier": "news",
        "title": category.title,
        "deck": category.deck,
        "routing_rule": category.routing_rule,
        "position": position,
        "status": "active",
        "retired_at": None,
    }
    for field_name, value in updates.items():
        if getattr(winner, field_name) != value:
            setattr(winner, field_name, value)
            changed = True
    return changed


def _update_merged_centroid(
    winner: BriefingLens,
    *,
    included_lenses: list[BriefingLens],
    source_counts_by_lens_id: dict[int, int],
    settings: Settings,
) -> None:
    vectors: list[tuple[list[float], int]] = []
    for lens in included_lenses:
        centroid = lens.centroid
        if not isinstance(centroid, list):
            continue
        vector = [float(value) for value in centroid]
        if not vector:
            continue
        weight = int(getattr(lens, "centroid_weight", 0) or 0)
        if weight <= 0 and lens.id is not None:
            weight = source_counts_by_lens_id.get(int(lens.id), 0)
        vectors.append((vector, max(weight, 1)))
    if not vectors:
        return
    width = len(vectors[0][0])
    compatible = [(vector, weight) for vector, weight in vectors if len(vector) == width]
    if not compatible:
        return
    total_weight = sum(weight for _vector, weight in compatible)
    merged = [0.0] * width
    for vector, weight in compatible:
        for index, value in enumerate(vector):
            merged[index] += value * weight
    winner.centroid = [value / total_weight for value in merged]
    winner.centroid_weight = min(total_weight, settings.briefing_centroid_max_weight)
    winner.centroid_model = settings.briefing_category_embedding_model


def _active_news_lenses(db: Session, *, user_id: int) -> list[BriefingLens]:
    return (
        db.query(BriefingLens)
        .filter(BriefingLens.user_id == user_id)
        .filter(BriefingLens.tier == "news")
        .filter(BriefingLens.status == "active")
        .filter(~BriefingLens.key.in_([MISC_LENS_KEY]))
        .order_by(BriefingLens.position.asc(), BriefingLens.id.asc())
        .all()
    )


def _active_news_lens_count(db: Session, *, user_id: int) -> int:
    return int(
        db.query(BriefingLens.id)
        .filter(BriefingLens.user_id == user_id)
        .filter(BriefingLens.tier == "news")
        .filter(BriefingLens.status == "active")
        .count()
    )


def _planner_category_limit(db: Session, *, user_id: int, settings: Settings) -> int:
    misc_is_active = (
        db.query(BriefingLens.id)
        .filter(BriefingLens.user_id == user_id)
        .filter(BriefingLens.key == MISC_LENS_KEY)
        .filter(BriefingLens.status == "active")
        .first()
        is not None
    )
    if misc_is_active:
        return max(1, settings.briefing_max_news_lenses - 1)
    return settings.briefing_max_news_lenses


def _active_source_keys_by_lens_id(db: Session, *, user_id: int) -> dict[int, list[str]]:
    segments = (
        db.query(BriefingSegment)
        .filter(BriefingSegment.user_id == user_id)
        .filter(BriefingSegment.status.in_(("active", "degraded")))
        .all()
    )
    keys_by_lens_id: dict[int, list[str]] = {}
    seen_by_lens_id: dict[int, set[str]] = {}
    for segment in segments:
        if segment.lens_id is None:
            continue
        lens_id = int(segment.lens_id)
        keys = keys_by_lens_id.setdefault(lens_id, [])
        seen = seen_by_lens_id.setdefault(lens_id, set())
        for raw_key in segment.source_keys or []:
            key = str(raw_key)
            if not key.startswith("news:") or key in seen:
                continue
            keys.append(key)
            seen.add(key)
    return keys_by_lens_id


def _source_counts_by_lens_id(db: Session, *, user_id: int) -> dict[int, int]:
    return {
        lens_id: len(keys)
        for lens_id, keys in _active_source_keys_by_lens_id(db, user_id=user_id).items()
    }


def _source_sample(source: BriefingSource) -> dict[str, object]:
    return {
        "source_key": source.source_key,
        "title": source.title,
        "summary": source.summary,
        "key_points": source.key_points[:3],
        "published_at": source.published_at.isoformat() if source.published_at else None,
    }


def _normalize_lens_key(value: str) -> str:
    slug = value.strip().lower()
    slug = slug.removeprefix("news-")
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")
    if not slug:
        slug = "updates"
    return f"news-{slug}"[:64].rstrip("-")


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        return -1.0
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return -1.0
    return numerator / (left_norm * right_norm)
