"""One-off brand exploration: generate Newsbuddy logo concepts via Runware + OpenRouter.

Usage: uv run python docs/brand-exploration-2026-08/generate_logos.py [concept_id ...]
Run with no args to generate all pending concepts (skips files that already exist).
"""

import argparse
import base64
import json
import sys
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import requests

ROOT = Path(__file__).parent
IMAGES = ROOT / "images"
IMAGES.mkdir(exist_ok=True)

RUNWARE_URL = "https://api.runware.ai/v1"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

STYLE_BASE = (
    "Minimal flat vector app logo mark, Japanese-inspired design aesthetic, abstract and "
    "geometric, generous negative space, soft pastel colors from a restrained palette "
    "(at most two hues plus warm off-white paper tones), clean edges, no text, no letters, "
    "no words, centered on a plain solid pale background, suitable as an iOS app icon. "
    "Friendly and warm 'news buddy' personality: approachable, calm, a little cute but not "
    "childish. "
)

CONCEPTS = [
    # (id, provider, palette hint, concept prompt)
    (
        "01-enso-paper",
        "runware",
        "sumi ink grey + sakura pink",
        (
            "A single enso brush circle rendered as a smooth pastel ink stroke, with a tiny folded "
            "newspaper page tucked into the circle's opening. Sakura pink and warm grey on cream."
        ),
    ),
    (
        "02-origami-crane",
        "runware",
        "matcha green + cream",
        (
            "An abstract origami paper crane folded from a newspaper page, extremely simplified into "
            "5-6 geometric facets. Soft matcha green and paper cream tones."
        ),
    ),
    (
        "03-hinomaru-fold",
        "runware",
        "persimmon + warm ivory",
        (
            "A hinomaru-style circle sun, but one quadrant gently peels back like a turning newspaper "
            "page revealing a paper texture underneath. Muted persimmon orange on warm ivory."
        ),
    ),
    (
        "04-mochi-buddy",
        "openrouter",
        "pastel peach + charcoal dots",
        (
            "A round kawaii mochi blob character with two tiny dot eyes, peacefully holding an open "
            "folded paper. Ultra-minimal, chubby silhouette. Pastel peach body, soft charcoal details."
        ),
    ),
    (
        "05-kamon-news",
        "runware",
        "indigo + paper white",
        (
            "A Japanese kamon family-crest style emblem: a circular seal formed from three overlapping "
            "folded newspaper pages arranged in rotational symmetry. Soft muted indigo on paper white."
        ),
    ),
    (
        "06-seigaiha-n",
        "runware",
        "pastel blue + cream",
        (
            "Seigaiha overlapping wave-crest pattern where the front wave curls into the shape of a "
            "folded broadsheet page. Layered pastel blues on cream."
        ),
    ),
    (
        "07-torii-bubble",
        "openrouter",
        "vermilion pastel + fog grey",
        (
            "An abstract torii gate whose crossbeam doubles as the top of a rounded speech bubble. "
            "Extremely reduced geometry. Soft washed vermilion and fog grey."
        ),
    ),
    (
        "08-daruma-reader",
        "openrouter",
        "dusty red + cream",
        (
            "A minimal daruma figure with a serene single-line smile, its belly marked with a tiny "
            "abstract folded-paper glyph instead of kanji. Dusty pastel red and cream."
        ),
    ),
    (
        "09-washi-bubble",
        "runware",
        "lavender grey + blush",
        (
            "A speech bubble folded from washi paper, shown as an abstract origami fold with one soft "
            "crease and a subtle paper-grain feel. Pale lavender grey with a blush accent."
        ),
    ),
    (
        "10-sumie-bird",
        "openrouter",
        "ink + pale gold",
        (
            "A tiny bird drawn with two or three sumi-e brush strokes, carrying a small folded paper "
            "in its beak. Soft ink grey strokes with one pale gold accent dot."
        ),
    ),
    (
        "11-hanko-seal",
        "runware",
        "coral red + ivory",
        (
            "A round hanko stamp seal impression containing an abstract pinwheel of folded paper "
            "corners, slightly imperfect stamped texture. Soft coral red on ivory."
        ),
    ),
    (
        "12-lantern-glow",
        "openrouter",
        "warm amber + dusk mauve",
        (
            "A minimal chochin paper lantern glowing gently, its rib lines doubling as lines of text "
            "on a page. Warm pastel amber glow against a soft dusk mauve."
        ),
    ),
    (
        "13-shiba-buddy",
        "openrouter",
        "toasted cream + cocoa",
        (
            "An extremely minimal shiba inu face made of three or four rounded geometric shapes, with "
            "a folded newspaper resting on its head like a hat. Toasted cream and light cocoa."
        ),
    ),
    (
        "14-wave-page",
        "runware",
        "seafoam + sand",
        (
            "A single stylized Hokusai-style wave whose foam curl transforms into fluttering pages of "
            "a newspaper. Very abstract, four shapes maximum. Pastel seafoam and sand."
        ),
    ),
    (
        "15-tsuki-fold",
        "openrouter",
        "moon yellow + night blue pastel",
        (
            "A crescent moon formed by a curled sheet of paper, with a tiny sleeping face implied by "
            "two closed-eye arcs. Pale moon yellow on a soft pastel night blue."
        ),
    ),
    (
        "16-asanoha-burst",
        "runware",
        "sage + blush pink",
        (
            "A hemp-leaf asanoha geometric star pattern where the center facets fold outward like an "
            "opening newspaper. Flat, crisp, radially symmetric. Sage green and blush pink on cream."
        ),
    ),
]


def load_env() -> dict[str, str]:
    env = {}
    for line in (ROOT.parent.parent / ".env").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k] = v.strip().strip('"')
    return env


ENV = load_env()


DEFAULT_RUNWARE_MODEL = "bytedance:seedream@5.0-lite"
DEFAULT_OPENROUTER_MODEL = "openai/gpt-5.4-image-2"


def gen_runware(
    prompt: str, out: Path, model: str = DEFAULT_RUNWARE_MODEL, size: int = 2048
) -> None:
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
        raise RuntimeError(
            f"runware {r.status_code}: {json.dumps(payload.get('errors'))[:500]}"
        )
    url = payload["data"][0]["imageURL"]
    out.write_bytes(requests.get(url, timeout=120).content)
    print(f"  cost: ${payload['data'][0].get('cost')}")


def gen_openrouter(
    prompt: str,
    out: Path,
    model: str = DEFAULT_OPENROUTER_MODEL,
    reference: Path | list[Path] | None = None,
) -> None:
    """Generate an image, optionally editing from one or more references.

    Text-only prompts drift: re-describing an existing mark reliably produces a different
    mark. Passing the original as `reference` keeps the silhouette and lets the prompt
    change only what it names. Pass a list to composite two existing marks — the prompt
    then refers to them by order ("the first image", "the second image").
    """
    if reference is None:
        content: str | list[dict[str, object]] = prompt
    else:
        refs = reference if isinstance(reference, list) else [reference]
        content = [{"type": "text", "text": prompt}]
        for ref in refs:
            ref_b64 = base64.b64encode(ref.read_bytes()).decode()
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{ref_b64}"},
                }
            )
    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "modalities": ["image", "text"],
    }
    r = requests.post(
        OPENROUTER_URL,
        headers={"Authorization": f"Bearer {ENV['OPENROUTER_API_KEY']}"},
        json=body,
        timeout=600,
    )
    payload = r.json()
    if r.status_code >= 400 or "error" in payload:
        raise RuntimeError(f"openrouter {r.status_code}: {json.dumps(payload)[:500]}")
    images = payload["choices"][0]["message"].get("images") or []
    if not images:
        raise RuntimeError(
            f"openrouter returned no images: {json.dumps(payload)[:500]}"
        )
    data_url = images[0]["image_url"]["url"]
    b64 = data_url.split(",", 1)[1]
    out.write_bytes(base64.b64decode(b64))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "ids", nargs="*", help="concept ids to regenerate (default: all pending)"
    )
    parser.add_argument(
        "--runware-model",
        help="route every concept through this Runware model instead of the per-concept provider",
    )
    parser.add_argument(
        "--out", default="images", help="output directory (default: images)"
    )
    args = parser.parse_args()

    out_dir = ROOT / args.out
    out_dir.mkdir(exist_ok=True)
    only = set(args.ids)
    failures = []
    for cid, provider, palette, concept in CONCEPTS:
        if only and cid not in only:
            continue
        out = out_dir / f"{cid}.png"
        if out.exists() and not only:
            print(f"skip {cid} (exists)")
            continue
        prompt = (
            STYLE_BASE + concept + f" Color palette: {palette}. Square 1:1 composition."
        )
        label = args.runware_model or provider
        print(f"gen {cid} via {label} ...")
        try:
            t = time.time()
            if args.runware_model:
                gen_runware(prompt, out, args.runware_model)
            elif provider == "runware":
                gen_runware(prompt, out)
            else:
                gen_openrouter(prompt, out)
            print(f"  ok in {time.time() - t:.0f}s -> {out.name}")
        except Exception as exc:  # noqa: BLE001
            failures.append((cid, str(exc)[:300]))
            print(f"  FAIL {cid}: {exc}")
    if failures:
        print("\nFailures:")
        for cid, msg in failures:
            print(f"  {cid}: {msg}")
        sys.exit(1)


if __name__ == "__main__":
    main()
