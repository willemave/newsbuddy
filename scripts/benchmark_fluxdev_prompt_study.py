# ruff: noqa: E402
"""Run a prompt-iteration study for Runware FLUX.1 dev against Gemini baselines."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import uuid4

import requests
from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.services.prompt_library import load_prompt, render_prompt

FIXTURE_RESULTS_PATH = Path(
    "outputs/image_provider_benchmark/20260418_infographic_explainer_grid_v2_merged/results.json"
)
OUTPUT_ROOT = Path("outputs/image_provider_benchmark")
EXISTING_PROVIDER = "newsly_existing"
PROCESS_CHAIN_PROVIDER = "fluxdev_process_chain"
RUNWARE_API_URL = "https://api.runware.ai/v1"
RUNWARE_MODEL = "runware:101@1"
RUNWARE_WIDTH = 1024
RUNWARE_HEIGHT = 576
RUNWARE_NEGATIVE_PROMPT = load_prompt("scripts/image_benchmarks#fluxdev_runware_negative")
TARGET_CASE_IDS = [29269, 29268, 29267, 29266]
RUN_TS_FORMAT = "%Y%m%d_%H%M%S"

FLUXDEV_VARIANT_PROMPTS = {
    "long_gemini": "scripts/image_benchmarks#fluxdev_long_gemini",
    "long_gemini_narrative": "scripts/image_benchmarks#fluxdev_long_gemini_narrative",
    "long_gemini_process": "scripts/image_benchmarks#fluxdev_long_gemini_process",
    "long_gemini_airy": "scripts/image_benchmarks#fluxdev_long_gemini_airy",
    "long_gemini_object_system": "scripts/image_benchmarks#fluxdev_long_gemini_object_system",
    "long_gemini_story_card": "scripts/image_benchmarks#fluxdev_long_gemini_story_card",
}


@dataclass(frozen=True)
class CaseFixture:
    case_id: int
    case_title: str
    content_type: str
    existing_image_src: Path
    existing_model: str
    existing_cost: float | None
    existing_elapsed: float | None
    existing_prompt_text: str
    story_title: str
    editorial_narrative: str
    key_facts: tuple[str, ...]


@dataclass(frozen=True)
class PromptVariant:
    key: str
    label: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixture-results",
        default=str(FIXTURE_RESULTS_PATH),
        help="Existing benchmark results.json containing Gemini baselines.",
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
        "--output-dir-name",
        default=None,
        help="Override output directory name under outputs/image_provider_benchmark.",
    )
    return parser.parse_args()


def load_fixture_cases(results_path: Path, target_case_ids: list[int]) -> list[CaseFixture]:
    data = json.loads(results_path.read_text())
    rows = data["results"]
    cases_by_id: dict[int, dict[str, dict[str, Any]]] = {}
    for row in rows:
        cases_by_id.setdefault(int(row["case_id"]), {})[str(row["provider"])] = row

    base_dir = results_path.parent
    fixtures: list[CaseFixture] = []
    for case_id in target_case_ids:
        providers = cases_by_id.get(case_id) or {}
        existing = providers.get(EXISTING_PROVIDER)
        process = providers.get(PROCESS_CHAIN_PROVIDER)
        if existing is None or process is None:
            continue
        story_title, narrative, key_facts = parse_existing_prompt(str(existing["prompt_text"]))
        fixtures.append(
            CaseFixture(
                case_id=case_id,
                case_title=str(existing["case_title"]),
                content_type=str(existing["content_type"]),
                existing_image_src=base_dir / str(existing["image_path"]),
                existing_model=str(existing["model"]),
                existing_cost=to_float(existing.get("estimated_cost_usd")),
                existing_elapsed=to_float(existing.get("elapsed_seconds")),
                existing_prompt_text=str(existing["prompt_text"]),
                story_title=story_title,
                editorial_narrative=narrative,
                key_facts=key_facts,
            )
        )
    return fixtures


def to_float(value: object) -> float | None:
    if value is None:
        return None
    if not isinstance(value, (int, float, str, bytes, bytearray)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_existing_prompt(prompt_text: str) -> tuple[str, str, tuple[str, ...]]:
    story_title = ""
    narrative = ""
    key_facts: list[str] = []
    lines = prompt_text.splitlines()
    capture_facts = False
    for raw_line in lines:
        line = raw_line.strip()
        if line.startswith("Story title:"):
            story_title = line.split(":", 1)[1].strip()
            capture_facts = False
            continue
        if line.startswith("Editorial narrative:"):
            narrative = line.split(":", 1)[1].strip()
            capture_facts = False
            continue
        if line == "Key facts to encode visually:":
            capture_facts = True
            continue
        if capture_facts:
            if line.startswith("- "):
                key_facts.append(line[2:].strip())
                continue
            if not line:
                break
    return story_title, narrative, tuple(key_facts)


def build_prompt_variants() -> list[PromptVariant]:
    return [
        PromptVariant("long_gemini", "long_gemini"),
        PromptVariant("long_gemini_narrative", "long_gemini_narrative"),
        PromptVariant("long_gemini_process", "long_gemini_process"),
        PromptVariant("long_gemini_airy", "long_gemini_airy"),
        PromptVariant("long_gemini_object_system", "long_gemini_object_system"),
        PromptVariant("long_gemini_story_card", "long_gemini_story_card"),
    ]


def facts_block(facts: tuple[str, ...], *, count: int) -> str:
    selected = facts[:count] if facts else ()
    if not selected:
        return "- Explain the article through objects only."
    return "\n".join(f"- {fact}" for fact in selected)


def clamp_text(text: str, max_chars: int) -> str:
    normalized = " ".join(text.split()).strip()
    if len(normalized) <= max_chars:
        return normalized
    truncated = normalized[: max_chars - 1].rstrip(" ,;:")
    if ". " in truncated:
        truncated = truncated.rsplit(". ", 1)[0].rstrip(" ,;:")
    if truncated.endswith("."):
        return truncated
    return f"{truncated}."


def build_variant_prompt(fixture: CaseFixture, variant: PromptVariant) -> str:
    facts_3 = facts_block(fixture.key_facts, count=3)
    facts_4 = facts_block(fixture.key_facts, count=4)
    narrative = fixture.editorial_narrative or fixture.case_title
    narrative_compact = clamp_text(narrative, 650)
    narrative_tight = clamp_text(narrative, 420)
    prompt_name = FLUXDEV_VARIANT_PROMPTS.get(variant.key)
    if prompt_name is None:
        raise ValueError(f"Unknown prompt variant: {variant.key}")
    return render_prompt(
        prompt_name,
        story_title=fixture.story_title,
        facts_3=facts_3,
        facts_4=facts_4,
        narrative_compact=narrative_compact,
        narrative_tight=narrative_tight,
    )


def ensure_output_dir(output_dir_name: str | None) -> Path:
    output_dir = (
        OUTPUT_ROOT / output_dir_name
        if output_dir_name
        else OUTPUT_ROOT / datetime.now(UTC).strftime(RUN_TS_FORMAT)
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def read_image_size(path: Path) -> tuple[int | None, int | None]:
    with Image.open(path) as image:
        return image.width, image.height


def save_png(image_bytes: bytes, destination: Path) -> tuple[int, int]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(BytesIO(image_bytes)) as image:
        converted = image.convert("RGBA") if image.mode not in ("RGB", "RGBA") else image.copy()
        converted.save(destination, "PNG", optimize=True)
        return converted.width, converted.height


def copy_baseline(fixture: CaseFixture, output_dir: Path) -> dict[str, Any]:
    case_dir = output_dir / f"case_{fixture.case_id}" / EXISTING_PROVIDER
    case_dir.mkdir(parents=True, exist_ok=True)
    image_dest = case_dir / f"{fixture.case_id}.png"
    shutil.copy2(fixture.existing_image_src, image_dest)
    prompt_path = case_dir / "prompt.txt"
    prompt_path.write_text(fixture.existing_prompt_text, encoding="utf-8")
    width, height = read_image_size(image_dest)
    return {
        "case_id": fixture.case_id,
        "case_title": fixture.case_title,
        "content_type": fixture.content_type,
        "provider": EXISTING_PROVIDER,
        "provider_label": "gemini_existing",
        "model": fixture.existing_model,
        "success": True,
        "elapsed_seconds": fixture.existing_elapsed,
        "estimated_cost_usd": fixture.existing_cost,
        "width": width,
        "height": height,
        "image_path": str(image_dest.relative_to(output_dir)),
        "image_url": None,
        "prompt_path": str(prompt_path.relative_to(output_dir)),
        "prompt_text": fixture.existing_prompt_text,
        "prompt_chars": len(fixture.existing_prompt_text),
        "negative_prompt": None,
        "error": None,
    }


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


def download_bytes(url: str) -> bytes:
    response = requests.get(url, timeout=180)
    response.raise_for_status()
    return response.content


def generate_runware_image(prompt_text: str, runware_key: str) -> tuple[bytes, str, float | None]:
    response = requests.post(
        RUNWARE_API_URL,
        headers={
            "Authorization": f"Bearer {runware_key}",
            "Content-Type": "application/json",
        },
        json=[
            {
                "taskType": "imageInference",
                "taskUUID": str(uuid4()),
                "includeCost": True,
                "outputType": "URL",
                "outputFormat": "PNG",
                "positivePrompt": prompt_text,
                "negativePrompt": RUNWARE_NEGATIVE_PROMPT,
                "model": RUNWARE_MODEL,
                "numberResults": 1,
                "width": RUNWARE_WIDTH,
                "height": RUNWARE_HEIGHT,
            }
        ],
        timeout=300,
    )
    payload = extract_runware_payload(response)
    data = payload.get("data") or []
    if not data:
        raise RuntimeError("Runware did not return inference data.")
    result = data[0]
    image_url = result.get("imageURL") or result.get("imageUrl") or result.get("image_url")
    if not isinstance(image_url, str) or not image_url:
        raise RuntimeError("Runware did not return an image URL.")
    image_bytes = download_bytes(image_url)
    cost = to_float(result.get("cost"))
    return image_bytes, image_url, cost


def run_generation(
    fixture: CaseFixture,
    variant: PromptVariant,
    *,
    output_dir: Path,
    runware_key: str,
) -> dict[str, Any]:
    provider_key = f"fluxdev_{variant.key}"
    case_dir = output_dir / f"case_{fixture.case_id}" / provider_key
    case_dir.mkdir(parents=True, exist_ok=True)
    prompt_text = build_variant_prompt(fixture, variant)
    prompt_path = case_dir / "prompt.txt"
    prompt_path.write_text(prompt_text, encoding="utf-8")
    image_path = case_dir / f"{fixture.case_id}.png"

    start = time.perf_counter()
    width: int | None = None
    height: int | None = None
    image_url: str | None = None
    estimated_cost_usd: float | None = None
    error: str | None = None

    try:
        image_bytes, image_url, estimated_cost_usd = generate_runware_image(
            prompt_text,
            runware_key,
        )
        width, height = save_png(image_bytes, image_path)
        success = True
    except Exception as exc:  # noqa: BLE001
        success = False
        error = str(exc)

    return {
        "case_id": fixture.case_id,
        "case_title": fixture.case_title,
        "content_type": fixture.content_type,
        "provider": provider_key,
        "provider_label": provider_key,
        "model": RUNWARE_MODEL,
        "success": success,
        "elapsed_seconds": time.perf_counter() - start,
        "estimated_cost_usd": estimated_cost_usd,
        "width": width,
        "height": height,
        "image_path": str(image_path.relative_to(output_dir)) if success else None,
        "image_url": image_url,
        "prompt_path": str(prompt_path.relative_to(output_dir)),
        "prompt_text": prompt_text,
        "prompt_chars": len(prompt_text),
        "negative_prompt": RUNWARE_NEGATIVE_PROMPT,
        "error": error,
    }


def format_optional_float(value: object, *, precision: int = 2) -> str:
    if value is None:
        return "-"
    if not isinstance(value, (int, float, str, bytes, bytearray)):
        return "-"
    try:
        return f"{float(value):.{precision}f}"
    except (TypeError, ValueError):
        return "-"


def html_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def render_html(output_dir: Path, results: list[dict[str, Any]], provider_order: list[str]) -> None:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in results:
        grouped.setdefault(int(row["case_id"]), []).append(row)

    sections: list[str] = []
    for case_id, rows in sorted(grouped.items()):
        rows_by_provider = {str(row["provider"]): row for row in rows}
        case_title = rows[0]["case_title"]
        cards: list[str] = []
        for provider in provider_order:
            maybe_row = rows_by_provider.get(provider)
            if maybe_row is None:
                continue
            row = maybe_row
            if row["success"] and row.get("image_path"):
                media_html = (
                    f'<img src="{html_escape(str(row["image_path"]))}" '
                    f'alt="{html_escape(provider)} image for case {case_id}">'
                )
            else:
                error_text = html_escape(str(row.get("error") or "Failed"))
                media_html = f'<div class="error">{error_text}</div>'
            prompt_details = (
                "<details><summary>Prompt</summary>"
                f"<pre>{html_escape(str(row['prompt_text']))}</pre>"
                "</details>"
            )
            negative_details = ""
            if row.get("negative_prompt"):
                negative_details = (
                    "<details><summary>Negative prompt</summary>"
                    f"<pre>{html_escape(str(row['negative_prompt']))}</pre>"
                    "</details>"
                )
            size_text = (
                f"{row['width']}x{row['height']}"
                if row.get("width") is not None and row.get("height") is not None
                else "-"
            )
            cards.append(
                '<article class="card">'
                "<header>"
                f"<h3>{html_escape(str(row.get('provider_label') or provider))}</h3>"
                f"<p>{html_escape(str(row['model']))}</p>"
                "</header>"
                f"{media_html}"
                '<div class="stats">'
                f"<p><strong>Latency</strong> "
                f"{format_optional_float(row.get('elapsed_seconds'))}s</p>"
                f"<p><strong>Cost</strong> "
                f"${format_optional_float(row.get('estimated_cost_usd'), precision=4)}</p>"
                f"<p><strong>Size</strong> {html_escape(size_text)}</p>"
                f"<p><strong>Prompt</strong> "
                f"{html_escape(str(row.get('prompt_chars') or 0))} chars</p>"
                "</div>"
                f"{prompt_details}"
                f"{negative_details}"
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
  <title>FLUXDEV Prompt Study</title>
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
    main {{ max-width: 1800px; margin: 0 auto; }}
    .intro {{
      margin: 0 0 24px;
      max-width: 960px;
      color: var(--muted);
      font-size: 1rem;
      line-height: 1.5;
    }}
    .case {{
      background: rgba(255, 250, 244, 0.9);
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
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 18px;
      align-items: start;
    }}
    .card {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 20px;
      padding: 16px;
    }}
    .card header h3 {{ margin: 0; font-size: 1.35rem; }}
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
    .stats p {{ margin: 0; }}
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
    <p class="intro">
      Gemini production image on the left as the baseline, then a wide prompt-study grid on
      Runware FLUX.1 dev. The negative prompt is shared across every generated card so the
      main variable is prompt shape and prompt length.
    </p>
    {"".join(sections)}
  </main>
</body>
</html>
"""
    (output_dir / "index.html").write_text(html, encoding="utf-8")


def write_summary(output_dir: Path, results: list[dict[str, Any]]) -> None:
    lines = [
        "# FLUXDEV Prompt Study",
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
                    str(row.get("image_path") or "-"),
                ]
            )
            + " |"
        )
    (output_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    target_case_ids = args.case_ids or TARGET_CASE_IDS
    fixture_cases = load_fixture_cases(Path(args.fixture_results), target_case_ids)
    if not fixture_cases:
        raise SystemExit("No matching fixture cases were found.")

    runware_key = os.getenv("RUNWARE_API_KEY")
    if not runware_key:
        raise SystemExit("RUNWARE_API_KEY is required.")

    output_dir = ensure_output_dir(args.output_dir_name)
    variants = build_prompt_variants()
    provider_order = [EXISTING_PROVIDER] + [f"fluxdev_{variant.key}" for variant in variants]
    results: list[dict[str, Any]] = []
    for fixture in fixture_cases:
        results.append(copy_baseline(fixture, output_dir))
        for variant in variants:
            results.append(
                run_generation(
                    fixture,
                    variant,
                    output_dir=output_dir,
                    runware_key=runware_key,
                )
            )

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "fixture_results_path": str(Path(args.fixture_results)),
        "providers": provider_order,
        "results": results,
        "negative_prompt": RUNWARE_NEGATIVE_PROMPT,
    }
    (output_dir / "results.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    write_summary(output_dir, results)
    render_html(output_dir, results, provider_order)
    print(output_dir)


if __name__ == "__main__":
    main()
