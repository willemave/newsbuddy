"""Shared Runware image inference client and retry policy."""

from collections.abc import Callable
from typing import Any, cast
from uuid import uuid4

import requests

from app.core.logging import get_logger
from app.core.model_defaults import RUNWARE_INFOGRAPHIC_MODEL_SPEC
from app.services.prompt_library import load_prompt

logger = get_logger(__name__)

DEFAULT_RUNWARE_INFOGRAPHIC_MODEL = RUNWARE_INFOGRAPHIC_MODEL_SPEC
RUNWARE_API_URL = "https://api.runware.ai/v1"
RUNWARE_INFOGRAPHIC_WIDTH = 1024
RUNWARE_INFOGRAPHIC_HEIGHT = 576
RUNWARE_SEEDREAM_INFOGRAPHIC_WIDTH = 2848
RUNWARE_SEEDREAM_INFOGRAPHIC_HEIGHT = 1600
RUNWARE_INFOGRAPHIC_NEGATIVE_PROMPT = load_prompt("images/infographic#runware_negative")
RUNWARE_LEARNING_DECK_THUMBNAIL_SIZE = 1024
RUNWARE_INLINE_RETRY_ATTEMPTS = 2


class RunwareGenerationError(RuntimeError):
    """Structured Runware failure that can drive local retries and fallback."""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        parameter: str | None = None,
        status_code: int | None = None,
        task_uuid: str | None = None,
        retryable: bool = True,
        fallback_allowed: bool = True,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.parameter = parameter
        self.status_code = status_code
        self.task_uuid = task_uuid
        self.retryable = retryable
        self.fallback_allowed = fallback_allowed


def generate_runware_image(
    *,
    api_key: str | None,
    models: list[str],
    prompt: str,
    content_id: int | None,
    item_id: int,
    task_id: int | None,
    user_id: int | None,
    feature: str,
    operation: str,
    image_type: str,
    width: int,
    height: int,
    negative_prompt: str | None,
    download_file: Callable[[str], bytes],
    record_usage: Callable[..., object],
) -> tuple[bytes, str]:
    if not api_key:
        raise ValueError("RUNWARE_API_KEY not configured for image generation.")

    last_error: RunwareGenerationError | None = None
    for model in models:
        request_width, request_height, request_negative_prompt = _resolve_request_options(
            model=model,
            image_type=image_type,
            width=width,
            height=height,
            negative_prompt=negative_prompt,
        )
        for attempt in range(RUNWARE_INLINE_RETRY_ATTEMPTS):
            task_uuid = str(uuid4())
            try:
                payload = _post_inference(
                    api_key=api_key,
                    prompt=prompt,
                    model=model,
                    task_uuid=task_uuid,
                    width=request_width,
                    height=request_height,
                    negative_prompt=request_negative_prompt,
                )
                data = payload.get("data") or []
                if not data:
                    raise RunwareGenerationError(
                        "Runware did not return inference data.",
                        task_uuid=task_uuid,
                        retryable=False,
                        fallback_allowed=True,
                    )
                result = cast(dict[str, Any], data[0])
                image_url = (
                    result.get("imageURL") or result.get("imageUrl") or result.get("image_url")
                )
                if not isinstance(image_url, str) or not image_url:
                    raise RunwareGenerationError(
                        "Runware did not return an image URL.",
                        task_uuid=task_uuid,
                        retryable=False,
                        fallback_allowed=True,
                    )

                image_bytes = download_file(image_url)
                record_usage(
                    provider="runware",
                    model=model,
                    feature=feature,
                    operation=operation,
                    source="queue",
                    usage={"request_count": 1},
                    task_id=task_id,
                    content_id=content_id,
                    user_id=user_id,
                    metadata={
                        "image_type": image_type,
                        "provider": "runware",
                        "response_cost_usd": result.get("cost"),
                        "image_url": image_url,
                        "task_uuid": task_uuid,
                        "inline_attempt": attempt + 1,
                        "width": request_width,
                        "height": request_height,
                    },
                )
                return image_bytes, model
            except RunwareGenerationError as exc:
                last_error = exc
                logger.warning(
                    "Runware image attempt failed for %s",
                    item_id,
                    extra={
                        "component": "image_generation",
                        "operation": operation,
                        "item_id": item_id,
                        "user_id": user_id,
                        "context_data": {
                            "model": model,
                            "attempt": attempt + 1,
                            "task_uuid": exc.task_uuid or task_uuid,
                            "status_code": exc.status_code,
                            "code": exc.code,
                            "parameter": exc.parameter,
                            "retryable": exc.retryable,
                            "error_message": str(exc),
                            "image_type": image_type,
                        },
                    },
                )
                if exc.retryable and attempt + 1 < RUNWARE_INLINE_RETRY_ATTEMPTS:
                    continue
                break

    if last_error is not None:
        raise last_error
    raise RuntimeError("No Runware image generation models configured.")


def _resolve_request_options(
    *,
    model: str,
    image_type: str,
    width: int,
    height: int,
    negative_prompt: str | None,
) -> tuple[int, int, str | None]:
    if image_type == "infographic" and model == DEFAULT_RUNWARE_INFOGRAPHIC_MODEL:
        return RUNWARE_SEEDREAM_INFOGRAPHIC_WIDTH, RUNWARE_SEEDREAM_INFOGRAPHIC_HEIGHT, None
    return width, height, negative_prompt


def _post_inference(
    *,
    api_key: str,
    prompt: str,
    model: str,
    task_uuid: str,
    width: int,
    height: int,
    negative_prompt: str | None,
) -> dict[str, Any]:
    request = _build_inference_request(
        prompt=prompt,
        model=model,
        task_uuid=task_uuid,
        width=width,
        height=height,
        negative_prompt=negative_prompt,
    )
    try:
        response = requests.post(
            RUNWARE_API_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=[request],
            timeout=180,
        )
    except requests.RequestException as exc:
        raise RunwareGenerationError(
            f"Runware request failed: {exc}",
            task_uuid=task_uuid,
            retryable=True,
            fallback_allowed=True,
        ) from exc

    try:
        payload = cast(dict[str, Any], response.json())
    except ValueError as exc:
        raise RunwareGenerationError(
            "Runware returned a non-JSON response.",
            status_code=response.status_code,
            task_uuid=task_uuid,
            retryable=response.status_code >= 500,
            fallback_allowed=response.status_code >= 400,
        ) from exc

    errors = payload.get("errors") or []
    if response.status_code >= 400 or errors:
        raise _build_generation_error(
            errors[0] if errors else None,
            status_code=response.status_code,
            task_uuid=task_uuid,
        )
    return payload


def _build_inference_request(
    *,
    prompt: str,
    model: str,
    task_uuid: str,
    width: int,
    height: int,
    negative_prompt: str | None,
) -> dict[str, Any]:
    request: dict[str, Any] = {
        "taskType": "imageInference",
        "taskUUID": task_uuid,
        "includeCost": True,
        "outputType": "URL",
        "outputFormat": "PNG",
        "positivePrompt": prompt,
        "model": model,
        "numberResults": 1,
        "width": width,
        "height": height,
    }
    if negative_prompt:
        request["negativePrompt"] = negative_prompt
    return request


def build_runware_infographic_request(*, prompt: str, model: str, task_uuid: str) -> dict[str, Any]:
    width, height, negative_prompt = _resolve_request_options(
        model=model,
        image_type="infographic",
        width=RUNWARE_INFOGRAPHIC_WIDTH,
        height=RUNWARE_INFOGRAPHIC_HEIGHT,
        negative_prompt=RUNWARE_INFOGRAPHIC_NEGATIVE_PROMPT,
    )
    return _build_inference_request(
        prompt=prompt,
        model=model,
        task_uuid=task_uuid,
        width=width,
        height=height,
        negative_prompt=negative_prompt,
    )


def _build_generation_error(
    error: dict[str, Any] | None,
    *,
    status_code: int | None,
    task_uuid: str,
) -> RunwareGenerationError:
    error = error or {}
    message = str(error.get("message") or "Runware request failed.")
    code = error.get("code")
    parameter = error.get("parameter")
    retryable = bool(
        (status_code or 0) >= 500
        or status_code == 429
        or parameter == "taskUUID"
        or "taskuuid" in message.lower()
    )
    fallback_allowed = bool((status_code or 0) >= 400 or parameter == "taskUUID")
    return RunwareGenerationError(
        f"Runware error: {message}",
        code=str(code) if code is not None else None,
        parameter=str(parameter) if parameter is not None else None,
        status_code=status_code,
        task_uuid=task_uuid,
        retryable=retryable,
        fallback_allowed=fallback_allowed,
    )
