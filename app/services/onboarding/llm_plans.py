"""LLM-backed profile, voice, and discovery planning for onboarding."""

from __future__ import annotations

import time
from typing import Any

from app.core.logging import get_logger
from app.models.api.onboarding import (
    OnboardingAudioDiscoverRequest,
    OnboardingAudioLanePreviewResponse,
    OnboardingFastDiscoverRequest,
    OnboardingProfileRequest,
    OnboardingProfileResponse,
    OnboardingVoiceParseRequest,
    OnboardingVoiceParseResponse,
)
from app.services.exa_client import ExaSearchResult
from app.services.llm_agents import get_basic_agent
from app.services.onboarding.audio_plan_heuristics import (
    _fallback_audio_lane_plan,
    _normalize_audio_lane_plan_with_metadata,
)
from app.services.onboarding.audio_plan_preview import serialize_audio_lane_preview
from app.services.onboarding.config import (
    AUDIO_PLAN_FALLBACK_MODELS,
    AUDIO_PLAN_MODEL,
    AUDIO_PLAN_SYSTEM_PROMPT,
    AUDIO_PLAN_TIMEOUT_SECONDS,
    DISCOVERY_FALLBACK_MODELS,
    DISCOVERY_PROMPT_MAX_WEB_RESULTS,
    FAST_DISCOVER_MODEL,
    FAST_DISCOVER_SYSTEM_PROMPT,
    ONBOARDING_PRIMARY_MODEL,
    PROFILE_EXA_RESULTS,
    PROFILE_MODEL,
    PROFILE_SYSTEM_PROMPT,
    PROFILE_TIMEOUT_SECONDS,
    VOICE_PARSE_MODEL,
    VOICE_PARSE_SYSTEM_PROMPT,
    VOICE_PARSE_TIMEOUT_SECONDS,
)
from app.services.onboarding.internal_models import (
    _AudioPlanOutput,
    _DiscoverOutput,
    _DiscoveryWebResult,
    _ProfileOutput,
    _VoiceParseOutput,
)
from app.services.onboarding.model_routing import candidate_models, onboarding_model_settings
from app.services.onboarding.query_heuristics import (
    _build_profile_fallback_summary,
    _build_profile_queries,
    _merge_topics,
    _prompt_snippet,
)
from app.services.onboarding.search import _run_exa_queries
from app.services.prompt_library import render_prompt

logger = get_logger(__name__)


def _duration_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000, 2)


def build_onboarding_profile(request: OnboardingProfileRequest) -> OnboardingProfileResponse:
    """Build a quick profile from name + interest topics using Exa + LLM.

    Args:
        request: OnboardingProfileRequest payload.

    Returns:
        OnboardingProfileResponse with summary and inferred topics.
    """
    queries = _build_profile_queries(request)
    results = _run_exa_queries(
        queries, num_results=PROFILE_EXA_RESULTS, request_timeout_seconds=PROFILE_TIMEOUT_SECONDS
    )

    if not results:
        fallback_summary = _build_profile_fallback_summary(
            request.first_name, request.interest_topics
        )
        return OnboardingProfileResponse(
            profile_summary=fallback_summary,
            inferred_topics=_merge_topics(request.interest_topics),
            candidate_sources=[],
        )

    try:
        prompt = _format_profile_prompt(request, results)
        agent = get_basic_agent(PROFILE_MODEL, _ProfileOutput, PROFILE_SYSTEM_PROMPT)
        result = agent.run_sync(
            prompt,
            model_settings=onboarding_model_settings(PROFILE_MODEL, PROFILE_TIMEOUT_SECONDS),
        )
        output = _get_agent_output(result)
        merged_topics = _merge_topics(output.inferred_topics, request.interest_topics)
        return OnboardingProfileResponse(
            profile_summary=output.profile_summary,
            inferred_topics=merged_topics,
            candidate_sources=output.candidate_sources,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Onboarding profile build failed",
            extra={
                "component": "onboarding",
                "operation": "profile_build",
                "context_data": {"error": str(exc)},
            },
        )
        raise


def parse_onboarding_voice(request: OnboardingVoiceParseRequest) -> OnboardingVoiceParseResponse:
    """Parse a voice transcript into onboarding fields.

    Args:
        request: OnboardingVoiceParseRequest payload.

    Returns:
        OnboardingVoiceParseResponse with extracted fields.
    """
    started_at = time.perf_counter()
    transcript = request.transcript.strip()
    if not transcript:
        logger.info(
            "Onboarding voice parse skipped empty transcript",
            extra={
                "component": "onboarding",
                "operation": "voice_parse",
                "status": "empty",
                "duration_ms": _duration_ms(started_at),
                "context_data": {"locale": request.locale},
            },
        )
        return OnboardingVoiceParseResponse(
            first_name=None,
            interest_topics=[],
            confidence=0,
            missing_fields=["first_name", "interest_topics"],
        )

    try:
        logger.info(
            "Onboarding voice parse started",
            extra={
                "component": "onboarding",
                "operation": "voice_parse",
                "status": "started",
                "context_data": {
                    "locale": request.locale,
                    "transcript_chars": len(transcript),
                },
            },
        )
        prompt = _format_voice_parse_prompt(transcript, request.locale)
        agent = get_basic_agent(VOICE_PARSE_MODEL, _VoiceParseOutput, VOICE_PARSE_SYSTEM_PROMPT)
        llm_started_at = time.perf_counter()
        result = agent.run_sync(
            prompt,
            model_settings=onboarding_model_settings(
                VOICE_PARSE_MODEL, VOICE_PARSE_TIMEOUT_SECONDS
            ),
        )
        llm_duration_ms = _duration_ms(llm_started_at)
        output = _get_agent_output(result)
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Onboarding voice parse failed",
            extra={
                "component": "onboarding",
                "operation": "voice_parse",
                "duration_ms": _duration_ms(started_at),
                "context_data": {
                    "error": str(exc),
                    "locale": request.locale,
                    "transcript_chars": len(transcript),
                },
            },
        )
        return OnboardingVoiceParseResponse(
            first_name=None,
            interest_topics=[],
            confidence=0,
            missing_fields=["first_name", "interest_topics"],
        )

    first_name = (output.first_name or "").strip() or None
    topics = _merge_topics(output.interest_topics)
    missing_fields: list[str] = []
    if not first_name:
        missing_fields.append("first_name")
    if not topics:
        missing_fields.append("interest_topics")

    logger.info(
        "Onboarding voice parse completed",
        extra={
            "component": "onboarding",
            "operation": "voice_parse",
            "status": "completed",
            "duration_ms": _duration_ms(started_at),
            "context_data": {
                "locale": request.locale,
                "transcript_chars": len(transcript),
                "llm_duration_ms": llm_duration_ms,
                "topic_count": len(topics),
                "has_first_name": bool(first_name),
                "missing_fields": missing_fields,
            },
        },
    )
    return OnboardingVoiceParseResponse(
        first_name=first_name,
        interest_topics=topics,
        confidence=output.confidence,
        missing_fields=missing_fields,
    )


async def preview_audio_lane_plan(
    request: OnboardingAudioDiscoverRequest,
) -> OnboardingAudioLanePreviewResponse:
    """Preview generated audio discovery lanes for admin debugging.

    Args:
        request: OnboardingAudioDiscoverRequest payload.

    Returns:
        OnboardingAudioLanePreviewResponse with generated lanes and fallback metadata.
    """
    transcript = request.transcript.strip()
    if not transcript:
        raise ValueError("Transcript is required")

    plan, used_fallback, fallback_reason = await _build_audio_lane_plan_with_metadata(
        transcript, request.locale
    )
    return OnboardingAudioLanePreviewResponse(
        topic_summary=plan.topic_summary,
        inferred_topics=plan.inferred_topics,
        lanes=[serialize_audio_lane_preview(lane) for lane in plan.lanes],
        used_fallback=used_fallback,
        fallback_reason=fallback_reason,
    )


def _format_profile_prompt(request: OnboardingProfileRequest, hits: list[ExaSearchResult]) -> str:
    lines: list[str] = []
    for idx, item in enumerate(hits[:10], start=1):
        lines.append(f"{idx}. {item.title}\nurl: {item.url}\nsummary: {item.snippet or ''}")
    return render_prompt(
        "onboarding/profile#user",
        first_name=request.first_name,
        interest_topics=", ".join(request.interest_topics),
        web_results="\n".join(lines),
    )


def _format_voice_parse_prompt(transcript: str, locale: str | None) -> str:
    return render_prompt(
        "onboarding/voice_parse#user",
        locale=locale or "unknown",
        transcript=transcript,
    )


async def _build_audio_lane_plan(transcript: str, locale: str | None) -> _AudioPlanOutput:
    plan, _, _ = await _build_audio_lane_plan_with_metadata(transcript, locale)
    return plan


async def _build_audio_lane_plan_with_metadata(
    transcript: str, locale: str | None
) -> tuple[_AudioPlanOutput, bool, str | None]:
    try:
        prompt = _format_audio_plan_prompt(transcript, locale)
        output = await _run_audio_plan_with_fallback(
            prompt=prompt,
            timeout_seconds=AUDIO_PLAN_TIMEOUT_SECONDS,
        )
        normalized_plan, used_fallback = _normalize_audio_lane_plan_with_metadata(
            output, transcript
        )
        fallback_reason = "Generated lanes were empty or invalid." if used_fallback else None
        return normalized_plan, used_fallback, fallback_reason
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Onboarding audio lane plan failed",
            extra={
                "component": "onboarding",
                "operation": "audio_plan",
                "context_data": {"error": str(exc)},
            },
        )
        return _fallback_audio_lane_plan(transcript), True, str(exc)


def _format_audio_plan_prompt(transcript: str, locale: str | None) -> str:
    return render_prompt(
        "onboarding/audio_plan#user", locale=locale or "unknown", transcript=transcript
    )


def _run_discover_output_with_fallback(
    *,
    prompt: str,
    timeout_seconds: int,
    operation: str,
    item_id: str | None = None,
) -> _DiscoverOutput:
    last_error: Exception | None = None
    models = candidate_models(FAST_DISCOVER_MODEL, DISCOVERY_FALLBACK_MODELS)

    for attempt_index, model_spec in enumerate(models, start=1):
        try:
            agent = get_basic_agent(model_spec, _DiscoverOutput, FAST_DISCOVER_SYSTEM_PROMPT)
            result = agent.run_sync(
                prompt,
                model_settings=onboarding_model_settings(model_spec, timeout_seconds),
            )
            output = _get_agent_output(result)
            if attempt_index > 1:
                logger.warning(
                    "Onboarding discovery succeeded on fallback model",
                    extra={
                        "component": "onboarding",
                        "operation": operation,
                        "item_id": item_id,
                        "context_data": {
                            "model": model_spec,
                            "attempt": attempt_index,
                            "models_tried": models,
                        },
                    },
                )
            return output
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logger.warning(
                "Onboarding discovery model attempt failed",
                extra={
                    "component": "onboarding",
                    "operation": operation,
                    "item_id": item_id,
                    "context_data": {
                        "model": model_spec,
                        "attempt": attempt_index,
                        "models_tried": models,
                        "error": str(exc),
                    },
                },
            )

    if last_error:
        raise last_error
    raise RuntimeError("No discovery models configured")


async def _run_audio_plan_with_fallback(
    *,
    prompt: str,
    timeout_seconds: int,
) -> _AudioPlanOutput:
    last_error: Exception | None = None
    models = candidate_models(AUDIO_PLAN_MODEL, AUDIO_PLAN_FALLBACK_MODELS)

    for attempt_index, model_spec in enumerate(models, start=1):
        try:
            agent = get_basic_agent(model_spec, _AudioPlanOutput, AUDIO_PLAN_SYSTEM_PROMPT)
            model_settings = onboarding_model_settings(model_spec, timeout_seconds)
            if hasattr(agent, "run"):
                result = await agent.run(prompt, model_settings=model_settings)
            else:
                result = agent.run_sync(prompt, model_settings=model_settings)
            output = _get_agent_output(result)
            if attempt_index > 1:
                logger.warning(
                    "Onboarding audio plan succeeded on fallback model",
                    extra={
                        "component": "onboarding",
                        "operation": "audio_plan",
                        "context_data": {
                            "model": model_spec,
                            "attempt": attempt_index,
                            "models_tried": models,
                        },
                    },
                )
            return output
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logger.warning(
                "Onboarding audio plan model attempt failed",
                extra={
                    "component": "onboarding",
                    "operation": "audio_plan",
                    "context_data": {
                        "model": model_spec,
                        "attempt": attempt_index,
                        "models_tried": models,
                        "error": str(exc),
                    },
                },
            )

    if last_error:
        raise last_error
    raise RuntimeError("No audio plan models configured")


def _get_agent_output(result: Any) -> Any:
    if hasattr(result, "output"):
        return result.output
    if hasattr(result, "data"):
        return result.data
    raise AttributeError("Agent result missing output")


def _normalize_audio_lane_plan(plan: _AudioPlanOutput, transcript: str) -> _AudioPlanOutput:
    normalized_plan, _ = _normalize_audio_lane_plan_with_metadata(plan, transcript)
    return normalized_plan


def _format_discovery_prompt(
    request: OnboardingFastDiscoverRequest,
    results: list[_DiscoveryWebResult],
) -> str:
    web_lines: list[str] = []
    for idx, item in enumerate(results[:DISCOVERY_PROMPT_MAX_WEB_RESULTS], start=1):
        lane_name = getattr(item, "lane_name", None)
        query = getattr(item, "query", None)
        lane_context = f" | lane: {lane_name}" if lane_name else ""
        query_context = f" | query: {query}" if query else ""
        web_lines.append(
            f"{idx}. {item.title}{lane_context}{query_context}\n"
            f"url: {item.url}\n"
            f"summary: {_prompt_snippet(item.snippet)}"
        )

    return render_prompt(
        "onboarding/fast_discover#user",
        profile_summary=request.profile_summary,
        topics=", ".join(request.inferred_topics),
        web_results="\n".join(web_lines),
    )


__all__ = [
    "AUDIO_PLAN_FALLBACK_MODELS",
    "AUDIO_PLAN_MODEL",
    "FAST_DISCOVER_MODEL",
    "ONBOARDING_PRIMARY_MODEL",
    "PROFILE_MODEL",
    "VOICE_PARSE_MODEL",
    "build_onboarding_profile",
    "parse_onboarding_voice",
    "preview_audio_lane_plan",
]
