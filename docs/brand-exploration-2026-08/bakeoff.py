"""Model bake-off: render the same two concepts across candidate image models.

Usage: uv run python docs/brand-exploration-2026-08/bakeoff.py
Writes to images_bakeoff/<concept>__<model-slug>.png
"""

import base64
import json
import time
from pathlib import Path
from uuid import uuid4

import requests
from generate_logos import CONCEPTS, ENV, OPENROUTER_URL, RUNWARE_URL, STYLE_BASE

ROOT = Path(__file__).parent
OUT = ROOT / "images_bakeoff"
OUT.mkdir(exist_ok=True)

# (slug, provider, model id) — the candidates missed in the first pass, plus baselines.
MODELS = [
    ("seedream5-pro", "runware", "bytedance:seedream@5.0-pro"),
    ("nano-banana-2", "runware", "google:4@3"),
    ("nano-banana-pro", "runware", "google:4@2"),
    ("recraft-v41-pro", "runware", "recraft:v4.1-pro@0"),
    ("ideogram-4", "runware", "ideogram:4@0"),
    ("gemini-3-pro-image", "openrouter", "google/gemini-3-pro-image"),
]

# Two stylistically opposite concepts: geometric precision vs. character warmth.
TEST_IDS = ["03-hinomaru-fold", "04-mochi-buddy"]


# Recraft and Ideogram only accept a fixed set of dimensions; 2048 is the shared square.
FIXED_SQUARE_MODELS = {"recraft:v4.1-pro@0", "ideogram:4@0"}


def gen_runware(prompt: str, model: str, out: Path) -> float | None:
    size = 2048 if model in FIXED_SQUARE_MODELS else 1024
    req: dict[str, str | bool | int] = {
        "taskType": "imageInference",
        "taskUUID": str(uuid4()),
        "includeCost": True,
        "outputType": "URL",
        "outputFormat": "PNG",
        "positivePrompt": prompt,
        "model": model,
        "numberResults": 1,
        "width": size,
        "height": size,
    }
    r = requests.post(
        RUNWARE_URL,
        headers={"Authorization": f"Bearer {ENV['RUNWARE_API_KEY']}"},
        json=[req],
        timeout=300,
    )
    payload = r.json()
    if r.status_code >= 400 or payload.get("errors"):
        raise RuntimeError(f"{r.status_code}: {json.dumps(payload.get('errors'))[:300]}")
    result = payload["data"][0]
    out.write_bytes(requests.get(result["imageURL"], timeout=120).content)
    return result.get("cost")


def gen_openrouter(prompt: str, model: str, out: Path) -> float | None:
    r = requests.post(
        OPENROUTER_URL,
        headers={"Authorization": f"Bearer {ENV['OPENROUTER_API_KEY']}"},
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "modalities": ["image", "text"],
        },
        timeout=600,
    )
    payload = r.json()
    if r.status_code >= 400 or "error" in payload:
        raise RuntimeError(f"{r.status_code}: {json.dumps(payload)[:300]}")
    images = payload["choices"][0]["message"].get("images") or []
    if not images:
        raise RuntimeError("no images returned")
    out.write_bytes(base64.b64decode(images[0]["image_url"]["url"].split(",", 1)[1]))
    return None


def main() -> None:
    by_id = {c[0]: c for c in CONCEPTS}
    failures = []
    for cid in TEST_IDS:
        _, _, palette, concept = by_id[cid]
        prompt = STYLE_BASE + concept + f" Color palette: {palette}. Square 1:1 composition."
        for slug, provider, model in MODELS:
            out = OUT / f"{cid}__{slug}.png"
            if out.exists():
                print(f"skip {out.name}")
                continue
            print(f"gen {out.name} ...")
            try:
                t = time.time()
                cost = (gen_runware if provider == "runware" else gen_openrouter)(
                    prompt, model, out
                )
                print(f"  ok {time.time() - t:.0f}s cost={cost}")
            except Exception as exc:  # noqa: BLE001
                failures.append((out.name, str(exc)[:200]))
                print(f"  FAIL {exc}")
    if failures:
        print("\nFailures:")
        for name, msg in failures:
            print(f"  {name}: {msg}")


if __name__ == "__main__":
    main()
