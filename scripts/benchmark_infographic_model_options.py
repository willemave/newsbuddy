"""Generate a side-by-side HTML benchmark for additional infographic image models.

This runner reuses the proven `fluxdev_process_chain` prompt from a previous
benchmark so new model comparisons stay apples-to-apples.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import uuid4

import requests
from dotenv import load_dotenv
from PIL import Image

from app.services.image_generation import (
    RUNWARE_API_URL,
    RUNWARE_INFOGRAPHIC_HEIGHT,
    RUNWARE_INFOGRAPHIC_WIDTH,
    ImageGenerationService,
)
from app.services.vendor_costs import estimate_vendor_cost_usd

FIXTURE_RESULTS_PATH = Path(
    "outputs/image_provider_benchmark/20260418_infographic_explainer_grid_v2_merged/results.json"
)
OUTPUT_ROOT = Path("outputs/image_provider_benchmark")
PROCESS_CHAIN_PROVIDER = "fluxdev_process_chain"
EXISTING_PROVIDER = "newsly_existing"
FAL_API_BASE = "https://fal.run"
RUN_TS_FORMAT = "%Y%m%d_%H%M%S"
TARGET_CASE_IDS = [29269, 29268, 29267, 29266]
RUNWARE_BENCHMARK_NEGATIVE_PROMPT = (
    "readable text, labels, logos, watermarks, screenshots, interface, dashboard, "
    "phone screen, laptop, monitor"
)


@dataclass(frozen=True)
class CaseFixture:
    case_id: int
    case_title: str
    content_type: str
    full_prompt: str
    fluxdev_image_src: Path
    fluxdev_model: str
    fluxdev_cost: float | None
    fluxdev_elapsed: float | None
    existing_image_src: Path
    existing_model: str
    existing_cost: float | None
    existing_elapsed: float | None


@dataclass(frozen=True)
class ProviderSpec:
    key: str
    label: str
    kind: str
    model: str
    prompt_mode: str
    estimated_fixed_cost_usd: float | None = None
    payload_overrides: dict[str, Any] | None = None
    supports_negative_prompt: bool = True


@dataclass(frozen=True)
class PromptVariantSpec:
    key: str
    label: str
    suffix: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixture-results",
        default=str(FIXTURE_RESULTS_PATH),
        help="Path to an existing benchmark results.json with fluxdev/newsly baselines.",
    )
    parser.add_argument(
        "--case-id",
        dest="case_ids",
        action="append",
        type=int,
        default=None,
        help="Restrict to one or more content ids.",
    )
    parser.add_argument(
        "--case-limit",
        type=int,
        default=None,
        help="Restrict to the first N cases after filtering.",
    )
    parser.add_argument(
        "--output-dir-name",
        default=None,
        help="Override the output directory name under outputs/image_provider_benchmark.",
    )
    return parser.parse_args()


def load_fixture_cases(results_path: Path, target_case_ids: list[int] | None) -> list[CaseFixture]:
    data = json.loads(results_path.read_text())
    rows = data["results"]
    cases_by_id: dict[int, dict[str, dict[str, Any]]] = {}
    for row in rows:
        cases_by_id.setdefault(int(row["case_id"]), {})[str(row["provider"])] = row

    ordered_case_ids = target_case_ids or TARGET_CASE_IDS
    fixtures: list[CaseFixture] = []
    base_dir = results_path.parent
    for case_id in ordered_case_ids:
        providers = cases_by_id.get(case_id) or {}
        process_chain = providers.get(PROCESS_CHAIN_PROVIDER)
        existing = providers.get(EXISTING_PROVIDER)
        if process_chain is None or existing is None:
            continue
        fixtures.append(
            CaseFixture(
                case_id=case_id,
                case_title=str(process_chain["case_title"]),
                content_type=str(process_chain["content_type"]),
                full_prompt=str(process_chain["prompt_text"]),
                fluxdev_image_src=base_dir / str(process_chain["image_path"]),
                fluxdev_model=str(process_chain["model"]),
                fluxdev_cost=_to_float(process_chain.get("estimated_cost_usd")),
                fluxdev_elapsed=_to_float(process_chain.get("elapsed_seconds")),
                existing_image_src=base_dir / str(existing["image_path"]),
                existing_model=str(existing["model"]),
                existing_cost=_to_float(existing.get("estimated_cost_usd")),
                existing_elapsed=_to_float(existing.get("elapsed_seconds")),
            )
        )
    return fixtures


def _to_float(value: object) -> float | None:
    if value is None:
        return None
    if not isinstance(value, (int, float, str, bytes, bytearray)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_compact_gemini_prompt(full_prompt: str) -> str:
    title = ""
    key_facts: list[str] = []
    capture_facts = False
    for raw_line in full_prompt.splitlines():
        line = raw_line.strip()
        if line.startswith("Story title:"):
            title = line.split(":", 1)[1].strip()
        elif line == "Key facts:":
            capture_facts = True
        elif capture_facts and line.startswith("- "):
            key_facts.append(line[2:].strip())
        elif capture_facts and not line:
            break

    compact_facts = key_facts[:2]
    story_bits = "\n".join(f"- {fact}" for fact in compact_facts) or "- Explain the story visually."
    return (
        "No text. 16:9 editorial infographic. Explain the story through connected objects in a "
        "clear process chain with 3 to 5 major elements. No UI, screens, dashboards, labels, "
        "logos, or readable words.\n"
        f"Title: {title}\n"
        "Encode these facts visually:\n"
        f"{story_bits}\n"
        "Use clean hierarchy, strong negative space, and a premium editorial illustration style."
    )


def build_prompt(spec: ProviderSpec, fixture: CaseFixture) -> str:
    if spec.prompt_mode == "compact_gemini":
        return build_compact_gemini_prompt(fixture.full_prompt)
    if spec.prompt_mode == "full_plus_ideogram":
        return (
            f"{fixture.full_prompt}\n\n"
            "Keep it crisp and diagrammatic, with clear object grouping and no fake typography."
        )
    return fixture.full_prompt


def build_variant_prompt(
    spec: ProviderSpec,
    fixture: CaseFixture,
    prompt_variant: PromptVariantSpec,
) -> str:
    base_prompt = build_prompt(spec, fixture)
    return f"{base_prompt}\n\nPrompt variant:\n{prompt_variant.suffix}\n"


def ensure_output_dir(output_dir_name: str | None) -> Path:
    if output_dir_name:
        output_dir = OUTPUT_ROOT / output_dir_name
    else:
        output_dir = OUTPUT_ROOT / datetime.now(UTC).strftime(RUN_TS_FORMAT)
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def copy_baseline(
    fixture: CaseFixture,
    *,
    output_dir: Path,
    provider_key: str,
    model: str,
    image_src: Path,
    prompt_text: str,
    estimated_cost_usd: float | None,
    elapsed_seconds: float | None,
) -> dict[str, Any]:
    case_dir = output_dir / f"case_{fixture.case_id}" / provider_key
    case_dir.mkdir(parents=True, exist_ok=True)
    extension = image_src.suffix or ".png"
    image_dest = case_dir / f"{fixture.case_id}{extension}"
    shutil.copy2(image_src, image_dest)
    prompt_path = case_dir / "prompt.txt"
    prompt_path.write_text(prompt_text, encoding="utf-8")

    width, height = read_image_size(image_dest)
    return {
        "case_id": fixture.case_id,
        "case_title": fixture.case_title,
        "content_type": fixture.content_type,
        "provider": provider_key,
        "model": model,
        "success": True,
        "elapsed_seconds": elapsed_seconds,
        "estimated_cost_usd": estimated_cost_usd,
        "width": width,
        "height": height,
        "image_path": str(image_dest.relative_to(output_dir)),
        "image_url": None,
        "prompt_path": str(prompt_path.relative_to(output_dir)),
        "prompt_text": prompt_text,
        "prompt_chars": len(prompt_text),
        "token_usage": None,
        "error": None,
    }


def read_image_size(path: Path) -> tuple[int | None, int | None]:
    with Image.open(path) as image:
        return image.width, image.height


def save_image_as_png(image_bytes: bytes, destination: Path) -> tuple[int, int]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(BytesIO(image_bytes)) as image:
        converted = image.convert("RGBA") if image.mode not in ("RGB", "RGBA") else image.copy()
        converted.save(destination, "PNG", optimize=True)
        return converted.width, converted.height


def download_bytes(url: str) -> bytes:
    response = requests.get(url, timeout=180)
    response.raise_for_status()
    return response.content


def extract_fal_image_url(payload: dict[str, Any]) -> str:
    for key in ("images", "data"):
        value = payload.get(key)
        if isinstance(value, list) and value:
            first = value[0]
            if isinstance(first, dict):
                for url_key in ("url", "image_url", "imageURL"):
                    url = first.get(url_key)
                    if isinstance(url, str) and url:
                        return url
    image = payload.get("image")
    if isinstance(image, dict):
        for url_key in ("url", "image_url", "imageURL"):
            url = image.get(url_key)
            if isinstance(url, str) and url:
                return url
    for url_key in ("url", "image_url", "imageURL"):
        url = payload.get(url_key)
        if isinstance(url, str) and url:
            return url
    raise RuntimeError(f"Could not find image URL in fal response: {payload}")


def generate_fal_image(
    *,
    model: str,
    prompt: str,
    fal_key: str,
    payload_overrides: dict[str, Any] | None = None,
) -> tuple[bytes, str]:
    payload = {
        "prompt": prompt,
        "image_size": "landscape_16_9",
        "num_images": 1,
    }
    if payload_overrides:
        payload.update(payload_overrides)

    response = requests.post(
        f"{FAL_API_BASE}/{model}",
        headers={
            "Authorization": f"Key {fal_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=300,
    )
    response.raise_for_status()
    response_payload = response.json()
    image_url = extract_fal_image_url(response_payload)
    return download_bytes(image_url), image_url


def generate_google_image(
    *,
    model: str,
    prompt: str,
    service: ImageGenerationService,
) -> tuple[bytes, dict[str, int | None] | None]:
    response = requests.post(
        build_google_vertex_endpoint(
            project=resolve_google_project(service),
            location=service.google_cloud_location,
            model=model,
            action="generateContent",
        ),
        headers=build_google_auth_headers(),
        json={
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseModalities": ["IMAGE"],
                "imageConfig": {
                    "aspectRatio": "16:9",
                    "imageSize": "512",
                },
            },
        },
        timeout=300,
    )
    payload = extract_json_payload(response, "Google Gemini image generation")
    return (
        extract_google_generate_content_image_bytes(payload),
        extract_google_rest_usage_details(payload),
    )


def generate_google_imagen_image(
    *,
    model: str,
    prompt: str,
    service: ImageGenerationService,
) -> bytes:
    response = requests.post(
        build_google_vertex_endpoint(
            project=resolve_google_project(service),
            location=service.google_cloud_location,
            model=model,
            action="predict",
        ),
        headers=build_google_auth_headers(),
        json={
            "instances": [{"prompt": prompt}],
            "parameters": {
                "sampleCount": 1,
                "aspectRatio": "16:9",
                "outputOptions": {"mimeType": "image/png"},
            },
        },
        timeout=300,
    )
    payload = extract_json_payload(response, "Google Imagen generation")
    return extract_google_predict_image_bytes(payload)


def estimate_google_cost(model: str, usage: dict[str, int | None] | None) -> float | None:
    if usage is None:
        return None
    return estimate_vendor_cost_usd(provider="google", model=model, usage=usage)


def resolve_google_project(service: ImageGenerationService) -> str:
    project = service.google_cloud_project or os.getenv("GOOGLE_CLOUD_PROJECT")
    if not project:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT not configured.")
    return project


@lru_cache(maxsize=1)
def get_google_access_token() -> str:
    result = subprocess.run(
        ["gcloud", "auth", "print-access-token"],
        check=True,
        capture_output=True,
        text=True,
    )
    token = result.stdout.strip()
    if not token:
        raise RuntimeError("gcloud auth print-access-token returned an empty token.")
    return token


def build_google_auth_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {get_google_access_token()}",
        "Content-Type": "application/json",
    }


def build_google_vertex_endpoint(
    *,
    project: str,
    location: str,
    model: str,
    action: str,
) -> str:
    return (
        f"https://{location}-aiplatform.googleapis.com/v1/projects/{project}/locations/"
        f"{location}/publishers/google/models/{model}:{action}"
    )


def extract_json_payload(response: requests.Response, context: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(f"{context} returned non-JSON response: {response.text[:400]}") from exc
    if response.status_code >= 400:
        raise RuntimeError(f"{context} failed: {payload}")
    return payload


def extract_google_generate_content_image_bytes(payload: dict[str, Any]) -> bytes:
    for candidate in payload.get("candidates") or []:
        content = candidate.get("content") or {}
        for part in content.get("parts") or []:
            inline_data = part.get("inlineData") or {}
            encoded = inline_data.get("data")
            if isinstance(encoded, str) and encoded:
                return base64.b64decode(encoded)
    raise RuntimeError(f"Google Gemini image response did not include image bytes: {payload}")


def extract_google_predict_image_bytes(payload: dict[str, Any]) -> bytes:
    for prediction in payload.get("predictions") or []:
        encoded = prediction.get("bytesBase64Encoded")
        if isinstance(encoded, str) and encoded:
            return base64.b64decode(encoded)
    raise RuntimeError(f"Google Imagen response did not include image bytes: {payload}")


def extract_google_rest_usage_details(payload: dict[str, Any]) -> dict[str, int | None] | None:
    usage = payload.get("usageMetadata") or {}
    input_tokens = usage.get("promptTokenCount")
    output_tokens = usage.get("candidatesTokenCount")
    total_tokens = usage.get("totalTokenCount")
    if input_tokens is None and output_tokens is None and total_tokens is None:
        return None
    usage_details: dict[str, int | None] = {
        "input": int(input_tokens) if input_tokens is not None else None,
        "output": int(output_tokens) if output_tokens is not None else None,
        "total": int(total_tokens) if total_tokens is not None else None,
    }
    return usage_details


def extract_runware_payload(response: requests.Response) -> dict[str, Any]:
    payload = response.json()
    if response.status_code >= 400:
        errors = payload.get("errors") or []
        if errors:
            message = errors[0].get("message") or str(errors[0])
            raise RuntimeError(f"Runware error: {message}")
        response.raise_for_status()

    errors = payload.get("errors") or []
    if errors:
        message = errors[0].get("message") or str(errors[0])
        raise RuntimeError(f"Runware error: {message}")
    return payload


def generate_runware_image(
    *,
    model: str,
    prompt: str,
    runware_key: str,
    payload_overrides: dict[str, Any] | None = None,
    supports_negative_prompt: bool = True,
) -> tuple[bytes, str, float | None]:
    payload = {
        "taskType": "imageInference",
        "taskUUID": str(uuid4()),
        "includeCost": True,
        "outputType": "URL",
        "outputFormat": "PNG",
        "positivePrompt": prompt,
        "model": model,
        "numberResults": 1,
        "width": RUNWARE_INFOGRAPHIC_WIDTH,
        "height": RUNWARE_INFOGRAPHIC_HEIGHT,
    }
    if supports_negative_prompt:
        payload["negativePrompt"] = RUNWARE_BENCHMARK_NEGATIVE_PROMPT
    if payload_overrides:
        payload.update(payload_overrides)
    response = requests.post(
        RUNWARE_API_URL,
        headers={
            "Authorization": f"Bearer {runware_key}",
            "Content-Type": "application/json",
        },
        json=[payload],
        timeout=300,
    )
    payload = extract_runware_payload(response)
    raw_data = payload.get("data") or []
    if not isinstance(raw_data, list) or not raw_data:
        raise RuntimeError("Runware did not return inference data.")
    result = raw_data[0]
    if not isinstance(result, dict):
        raise RuntimeError("Runware returned an unexpected result payload.")
    image_url = result.get("imageURL") or result.get("imageUrl") or result.get("image_url")
    if not isinstance(image_url, str) or not image_url:
        raise RuntimeError("Runware did not return an image URL.")
    image_bytes = download_bytes(image_url)
    raw_cost = result.get("cost")
    cost = None
    if raw_cost is not None:
        try:
            cost = float(raw_cost)
        except (TypeError, ValueError):
            cost = None
    return image_bytes, image_url, cost


def run_generation(
    fixture: CaseFixture,
    *,
    spec: ProviderSpec,
    provider_key: str,
    provider_label: str,
    prompt_text: str,
    output_dir: Path,
    google_service: ImageGenerationService,
    fal_key: str | None,
    runware_key: str | None,
) -> dict[str, Any]:
    case_dir = output_dir / f"case_{fixture.case_id}" / provider_key
    case_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = case_dir / "prompt.txt"
    prompt_path.write_text(prompt_text, encoding="utf-8")

    start = time.perf_counter()
    usage: dict[str, int | None] | None = None
    image_url: str | None = None
    estimated_cost_usd: float | None = spec.estimated_fixed_cost_usd
    width: int | None = None
    height: int | None = None
    image_path = case_dir / f"{fixture.case_id}.png"
    error: str | None = None

    try:
        if spec.kind == "google":
            image_bytes, usage = generate_google_image(
                model=spec.model,
                prompt=prompt_text,
                service=google_service,
            )
            width, height = save_image_as_png(image_bytes, image_path)
            estimated_cost_usd = estimate_google_cost(spec.model, usage)
        elif spec.kind == "google_imagen":
            image_bytes = generate_google_imagen_image(
                model=spec.model,
                prompt=prompt_text,
                service=google_service,
            )
            width, height = save_image_as_png(image_bytes, image_path)
        elif spec.kind == "runware":
            if not runware_key:
                raise RuntimeError("RUNWARE_API_KEY not configured.")
            image_bytes, image_url, runware_cost = generate_runware_image(
                model=spec.model,
                prompt=prompt_text,
                runware_key=runware_key,
                payload_overrides=spec.payload_overrides,
                supports_negative_prompt=spec.supports_negative_prompt,
            )
            width, height = save_image_as_png(image_bytes, image_path)
            estimated_cost_usd = runware_cost if runware_cost is not None else estimated_cost_usd
        elif spec.kind == "fal":
            if not fal_key:
                raise RuntimeError("FAL_KEY not configured.")
            payload_overrides: dict[str, Any] = {}
            if spec.model == "fal-ai/ideogram/v3":
                payload_overrides = {
                    "expand_prompt": False,
                    "rendering_speed": "BALANCED",
                    "style": "DESIGN",
                }
            image_bytes, image_url = generate_fal_image(
                model=spec.model,
                prompt=prompt_text,
                fal_key=fal_key,
                payload_overrides=payload_overrides,
            )
            width, height = save_image_as_png(image_bytes, image_path)
        else:
            raise RuntimeError(f"Unsupported provider kind: {spec.kind}")
        success = True
    except Exception as exc:  # noqa: BLE001
        success = False
        error = str(exc)

    elapsed_seconds = time.perf_counter() - start
    return {
        "case_id": fixture.case_id,
        "case_title": fixture.case_title,
        "content_type": fixture.content_type,
        "provider": provider_key,
        "provider_label": provider_label,
        "model": spec.model,
        "success": success,
        "elapsed_seconds": elapsed_seconds,
        "estimated_cost_usd": estimated_cost_usd,
        "width": width,
        "height": height,
        "image_path": str(image_path.relative_to(output_dir)) if success else None,
        "image_url": image_url,
        "prompt_path": str(prompt_path.relative_to(output_dir)),
        "prompt_text": prompt_text,
        "prompt_chars": len(prompt_text),
        "token_usage": usage,
        "error": error,
    }


def write_summary(output_dir: Path, results: list[dict[str, Any]]) -> None:
    lines = [
        "# Image Model Options Benchmark",
        "",
        (
            "| Case | Provider | Model | Success | Latency (s) | Est. Cost USD | "
            "Prompt Chars | Size | Image |"
        ),
        "| --- | --- | --- | --- | ---: | ---: | ---: | --- | --- |",
    ]
    for row in results:
        size = (
            f"{row['width']}x{row['height']}"
            if row.get("width") is not None and row.get("height") is not None
            else "-"
        )
        image_path = row.get("image_path") or "-"
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row["case_id"]),
                    str(row["provider"]),
                    str(row["model"]),
                    "yes" if row["success"] else "no",
                    format_optional_float(row.get("elapsed_seconds")),
                    format_optional_float(row.get("estimated_cost_usd"), precision=4),
                    str(row.get("prompt_chars") or 0),
                    size,
                    image_path,
                ]
            )
            + " |"
        )
    (output_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def format_optional_float(value: object, *, precision: int = 2) -> str:
    if value is None:
        return "-"
    if not isinstance(value, (int, float, str, bytes, bytearray)):
        return "-"
    try:
        return f"{float(value):.{precision}f}"
    except (TypeError, ValueError):
        return "-"


def render_html(output_dir: Path, results: list[dict[str, Any]], providers: list[str]) -> None:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in results:
        grouped.setdefault(int(row["case_id"]), []).append(row)

    sections: list[str] = []
    for case_id, rows in sorted(grouped.items()):
        rows_by_provider = {str(row["provider"]): row for row in rows}
        case_title = rows[0]["case_title"]
        cards: list[str] = []
        for provider in providers:
            maybe_row = rows_by_provider.get(provider)
            if maybe_row is None:
                continue
            row = maybe_row
            provider_title = str(row.get("provider_label") or provider)
            prompt_details = ""
            if row.get("prompt_text"):
                prompt_details = (
                    "<details><summary>Prompt</summary>"
                    f"<pre>{html_escape(str(row['prompt_text']))}</pre>"
                    "</details>"
                )
            usage_html = ""
            if isinstance(row.get("token_usage"), dict) and row["token_usage"]:
                usage = row["token_usage"]
                usage_html = (
                    '<p class="meta"><strong>Tokens</strong> '
                    f"in {usage.get('input', '-')} / out {usage.get('output', '-')}</p>"
                )
            if row["success"] and row.get("image_path"):
                media_html = (
                    f'<img src="{html_escape(str(row["image_path"]))}" '
                    f'alt="{html_escape(provider)} image for case {case_id}">'
                )
            else:
                error_text = html_escape(str(row.get("error") or "Failed"))
                media_html = f'<div class="error">{error_text}</div>'
            size_text = (
                f"{row['width']}x{row['height']}"
                if row.get("width") is not None and row.get("height") is not None
                else "-"
            )
            latency_text = format_optional_float(row.get("elapsed_seconds"))
            cost_text = format_optional_float(row.get("estimated_cost_usd"), precision=4)
            prompt_chars_text = html_escape(str(row.get("prompt_chars") or 0))
            cards.append(
                '<article class="card">'
                "<header>"
                f"<h3>{html_escape(provider_title)}</h3>"
                f"<p>{html_escape(str(row['model']))}</p>"
                "</header>"
                f"{media_html}"
                '<div class="stats">'
                f"<p><strong>Latency</strong> {latency_text}s</p>"
                f"<p><strong>Est. Cost</strong> ${cost_text}</p>"
                f"<p><strong>Size</strong> {html_escape(size_text)}</p>"
                f"<p><strong>Prompt</strong> {prompt_chars_text} chars</p>"
                "</div>"
                f"{usage_html}"
                f"{prompt_details}"
                "</article>"
            )
        sections.append(
            '<section class="case">'
            f"<h2>Article #{case_id}</h2>"
            f"<h1>{html_escape(str(case_title))}</h1>"
            '<div class="grid">' + "".join(cards) + "</div></section>"
        )

    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Infographic Model Options Benchmark</title>
  <style>
    :root {{
      --bg: #f5efe7;
      --card: #fffaf4;
      --border: #e2d5c4;
      --ink: #2d2722;
      --muted: #76685b;
      --accent: #bf5b2c;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Georgia, "Iowan Old Style", serif;
      background: radial-gradient(circle at top, #fff8f1 0%, var(--bg) 60%);
      color: var(--ink);
      padding: 24px;
    }}
    main {{ max-width: 1600px; margin: 0 auto; }}
    .case {{
      background: rgba(255, 250, 244, 0.88);
      border: 1px solid var(--border);
      border-radius: 24px;
      padding: 24px;
      margin-bottom: 24px;
      box-shadow: 0 10px 30px rgba(80, 53, 31, 0.08);
    }}
    h1 {{ margin: 0 0 12px; font-size: clamp(1.8rem, 3vw, 3rem); line-height: 1.05; }}
    h2 {{
      margin: 0 0 8px;
      color: var(--accent);
      font-size: 0.95rem;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 18px;
      align-items: start;
    }}
    .card {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 20px;
      padding: 16px;
    }}
    .card header h3 {{ margin: 0; font-size: 1.65rem; }}
    .card header p {{
      margin: 4px 0 12px;
      color: var(--muted);
      font-size: 0.95rem;
      word-break: break-word;
    }}
    img {{
      width: 100%;
      aspect-ratio: 16 / 9;
      object-fit: cover;
      border-radius: 14px;
      border: 1px solid var(--border);
      background: white;
    }}
    .stats {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px 12px;
      margin: 14px 0;
      font-size: 0.95rem;
    }}
    .stats p, .meta {{ margin: 0; }}
    .error {{
      min-height: 180px;
      display: grid;
      place-items: center;
      border: 1px dashed #d7b9aa;
      border-radius: 14px;
      color: #a4451b;
      background: #fff1e9;
      padding: 16px;
      text-align: center;
    }}
    details {{
      margin-top: 12px;
      border-top: 1px solid var(--border);
      padding-top: 12px;
    }}
    summary {{
      cursor: pointer;
      color: var(--accent);
      font-weight: 700;
    }}
    pre {{
      white-space: pre-wrap;
      word-break: break-word;
      font-family: "SFMono-Regular", Menlo, monospace;
      font-size: 12px;
      color: var(--muted);
      background: #fff;
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 12px;
      margin: 12px 0 0;
    }}
  </style>
</head>
<body>
  <main>
    {"".join(sections)}
  </main>
</body>
</html>
"""
    (output_dir / "index.html").write_text(html, encoding="utf-8")


def html_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def main() -> None:
    args = parse_args()
    load_dotenv(os.getenv("NEWSLY_ENV_FILE", ".env"), override=False)

    target_case_ids = args.case_ids or TARGET_CASE_IDS
    fixture_cases = load_fixture_cases(Path(args.fixture_results), target_case_ids)
    if args.case_limit is not None:
        fixture_cases = fixture_cases[: args.case_limit]
    if not fixture_cases:
        raise SystemExit("No matching fixture cases were found.")

    output_dir = ensure_output_dir(args.output_dir_name)
    google_service = ImageGenerationService()
    fal_key = os.getenv("FAL_KEY")
    runware_key = os.getenv("RUNWARE_API_KEY")

    prompt_variants = [
        PromptVariantSpec(
            key="process_chain",
            label="process_chain",
            suffix=(
                "Emphasize a clean left-to-right explainer chain with 3 to 5 editorial objects, "
                "where each object visibly transforms into or causes the next."
            ),
        ),
        PromptVariantSpec(
            key="artifact_network",
            label="artifact_network",
            suffix=(
                "Emphasize an information-dense artifact network with one dominant central object "
                "and 3 to 4 supporting objects grouped around it, using connectors and spatial "
                "hierarchy instead of text."
            ),
        ),
    ]

    providers = [
        ProviderSpec(
            key=EXISTING_PROVIDER,
            label="newsly_existing",
            kind="baseline",
            model="gemini-3.1-flash-image-preview",
            prompt_mode="full",
        ),
        ProviderSpec(
            key="fluxdev",
            label="fluxdev",
            kind="runware",
            model="runware:101@1",
            prompt_mode="full",
            estimated_fixed_cost_usd=0.0038,
        ),
        ProviderSpec(
            key="flux_krea",
            label="flux_krea",
            kind="runware",
            model="runware:107@1",
            prompt_mode="full",
            estimated_fixed_cost_usd=0.0086,
        ),
        ProviderSpec(
            key="flux11_pro",
            label="flux11_pro",
            kind="runware",
            model="bfl:2@1",
            prompt_mode="full",
            estimated_fixed_cost_usd=0.03,
            payload_overrides={
                "providerSettings": {
                    "bfl": {
                        "promptUpsampling": True,
                        "safetyTolerance": 2,
                    }
                }
            },
            supports_negative_prompt=False,
        ),
        ProviderSpec(
            key="flux2_klein_9b",
            label="flux2_klein_9b",
            kind="runware",
            model="runware:400@2",
            prompt_mode="full",
            estimated_fixed_cost_usd=0.0008,
        ),
        ProviderSpec(
            key="flux2_dev",
            label="flux2_dev",
            kind="runware",
            model="runware:400@1",
            prompt_mode="full",
            estimated_fixed_cost_usd=0.012,
        ),
    ]

    results: list[dict[str, Any]] = []
    provider_order = [EXISTING_PROVIDER]
    for prompt_variant in prompt_variants:
        for spec in providers[1:]:
            provider_order.append(f"{spec.key}_{prompt_variant.key}")
    for fixture in fixture_cases:
        results.append(
            copy_baseline(
                fixture,
                output_dir=output_dir,
                provider_key=EXISTING_PROVIDER,
                model=fixture.existing_model,
                image_src=fixture.existing_image_src,
                prompt_text=fixture.full_prompt,
                estimated_cost_usd=fixture.existing_cost,
                elapsed_seconds=fixture.existing_elapsed,
            )
        )
        for prompt_variant in prompt_variants:
            for spec in providers[1:]:
                prompt_text = build_variant_prompt(spec, fixture, prompt_variant)
                provider_key = f"{spec.key}_{prompt_variant.key}"
                provider_label = f"{spec.label} · {prompt_variant.label}"
                results.append(
                    run_generation(
                        fixture,
                        spec=spec,
                        provider_key=provider_key,
                        provider_label=provider_label,
                        prompt_text=prompt_text,
                        output_dir=output_dir,
                        google_service=google_service,
                        fal_key=fal_key,
                        runware_key=runware_key,
                    )
                )

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "fixture_results_path": str(Path(args.fixture_results)),
        "providers": provider_order,
        "results": results,
    }
    (output_dir / "results.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    write_summary(output_dir, results)
    render_html(output_dir, results, provider_order)
    print(output_dir)


if __name__ == "__main__":
    main()
