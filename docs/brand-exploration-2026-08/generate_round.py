"""Generate a concept round defined in concepts_<round>.py.

Usage: uv run python docs/brand-exploration-2026-08/generate_round.py r3 [concept_id ...]
Also emits concepts_<round>.js so the explainer site can render the round.
"""

import argparse
import importlib
import json
import sys
import time
from pathlib import Path

from generate_logos import gen_openrouter, gen_runware

ROOT = Path(__file__).parent

# The only square dimension every model in the round registries accepts.
SQUARE = 2048


def write_site_data(round_name: str, concepts: list, models: dict) -> None:
    """Emit concept metadata the site needs to render this round."""
    payload = [
        {"id": cid, "title": title, "desc": desc, "model": models[model_key][2]}
        for cid, model_key, title, desc, _ in concepts
    ]
    global_name = f"CONCEPTS_{round_name.upper()}"
    (ROOT / f"concepts_{round_name}.js").write_text(
        f"window.{global_name} = " + json.dumps(payload, indent=2) + ";\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("round", help="round name, e.g. r3 (loads concepts_r3.py)")
    parser.add_argument(
        "ids", nargs="*", help="concept ids to regenerate (default: all pending)"
    )
    args = parser.parse_args()

    module = importlib.import_module(f"concepts_{args.round}")
    concepts, models, style = module.CONCEPTS, module.MODELS, module.STYLE_BASE
    # Optional {concept id: reference image} — those concepts are edited, not generated fresh.
    refs: dict[str, Path] = getattr(module, "REFS", {})

    out_dir = ROOT / f"images_{args.round}"
    out_dir.mkdir(exist_ok=True)
    write_site_data(args.round, concepts, models)

    only = set(args.ids)
    failures = []
    for cid, model_key, _title, _desc, concept in concepts:
        if only and cid not in only:
            continue
        out = out_dir / f"{cid}.png"
        if out.exists() and not only:
            print(f"skip {cid} (exists)")
            continue
        provider, model, label = models[model_key]
        prompt = style + concept + " Square 1:1 composition."
        print(f"gen {cid} via {label} ...")
        try:
            t = time.time()
            if provider == "runware":
                gen_runware(prompt, out, model, size=SQUARE)
            else:
                gen_openrouter(prompt, out, model, reference=refs.get(cid))
            print(f"  ok in {time.time() - t:.0f}s -> {out.name}")
        except Exception as exc:  # noqa: BLE001
            failures.append((cid, str(exc)[:300]))
            print(f"  FAIL {cid}: {exc}")

    if failures:
        print("\nFailures:")
        for cid, msg in failures:
            print(f"  {cid}: {msg}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
