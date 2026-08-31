"""Extract a small color kit from each generated logo and emit palettes.json."""

import colorsys
import json
from pathlib import Path
from typing import cast

from PIL import Image

ROOT = Path(__file__).parent
IMAGES = ROOT / "images"


def rel_luminance(rgb: tuple[int, int, int]) -> float:
    def chan(c: float) -> float:
        c = c / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (chan(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    la, lb = rel_luminance(a), rel_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def hex_of(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def darken(rgb: tuple[int, int, int], factor: float) -> tuple[int, int, int]:
    h, lightness, s = colorsys.rgb_to_hls(*(c / 255 for c in rgb))
    r, g, b = colorsys.hls_to_rgb(h, max(0.0, lightness * factor), min(1.0, s * 1.15))
    return (int(r * 255), int(g * 255), int(b * 255))


def dist2(a: tuple[int, int, int], b: tuple[int, int, int]) -> int:
    return sum((x - y) ** 2 for x, y in zip(a, b, strict=True))


def extract(path: Path) -> dict[str, str | list[str]]:
    img = Image.open(path).convert("RGB").resize((240, 240))
    pixels = cast(list[tuple[int, int, int]], list(img.get_flattened_data()))

    # Background = most common coarsely-bucketed color (logo-on-plain-field images).
    buckets: dict[tuple[int, int, int], int] = {}
    for px in pixels:
        key = (px[0] // 16 * 16 + 8, px[1] // 16 * 16 + 8, px[2] // 16 * 16 + 8)
        buckets[key] = buckets.get(key, 0) + 1
    bg = max(buckets.items(), key=lambda kv: kv[1])[0]

    # Quantize only the foreground (pixels far enough from the background).
    fg = [px for px in pixels if dist2(px, bg) > 2500]
    if not fg:
        fg = pixels
    fg_img = Image.new("RGB", (len(fg), 1))
    fg_img.putdata(fg)
    quant = fg_img.quantize(colors=6, method=Image.Quantize.MEDIANCUT).convert("RGB")
    counts: dict[tuple[int, int, int], int] = {}
    quantized_pixels = cast(
        list[tuple[int, int, int]], list(quant.get_flattened_data())
    )
    for px in quantized_pixels:
        counts[px] = counts.get(px, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])

    distinct: list[tuple[tuple[int, int, int], int]] = []
    for rgb, n in ranked:
        if n < len(fg) * 0.01:
            continue
        if dist2(rgb, bg) < 2000:
            continue
        if any(dist2(rgb, seen) < 1600 for seen, _ in distinct):
            continue
        distinct.append((rgb, n))
    swatches = [rgb for rgb, _ in distinct[:4]]

    # Accent = most saturated non-neutral swatch; ink = darkest.
    def sat(rgb: tuple[int, int, int]) -> float:
        return colorsys.rgb_to_hls(*(c / 255 for c in rgb))[2]

    accent = max(swatches, key=sat) if swatches else bg
    ink = min(swatches, key=rel_luminance) if swatches else (60, 55, 50)
    if contrast(ink, bg) < 4.5:
        ink = darken(ink, 0.45)
        if contrast(ink, bg) < 4.5:
            ink = darken(ink, 0.6)
    accent_strong = accent if contrast(accent, bg) >= 2.5 else darken(accent, 0.7)

    return {
        "bg": hex_of(bg),
        "swatches": [hex_of(s) for s in swatches],
        "accent": hex_of(accent),
        "accent_strong": hex_of(accent_strong),
        "ink": hex_of(ink),
    }


def main() -> None:
    # Each image set gets its own palette file; the site switches between them.
    sets = {
        "v1": (IMAGES, "PALETTES"),
        "v2": (ROOT / "images_v2", "PALETTES_V2"),
        "r2": (ROOT / "images_r2", "PALETTES_R2"),
        "r3": (ROOT / "images_r3", "PALETTES_R3"),
        "r4": (ROOT / "images_r4", "PALETTES_R4"),
        "r5": (ROOT / "images_r5", "PALETTES_R5"),
        "r6": (ROOT / "images_r6", "PALETTES_R6"),
        "r7": (ROOT / "images_r7", "PALETTES_R7"),
        "r8": (ROOT / "images_r8", "PALETTES_R8"),
    }
    for name, (folder, global_name) in sets.items():
        if not folder.is_dir():
            continue
        result: dict[str, dict[str, str | list[str]]] = {}
        for path in sorted(folder.glob("*.png")):
            result[path.stem] = extract(path)
            print(name, path.stem, result[path.stem])
        suffix = "" if name == "v1" else f"_{name}"
        (ROOT / f"palettes{suffix}.json").write_text(json.dumps(result, indent=2))
        (ROOT / f"palettes{suffix}.js").write_text(
            f"window.{global_name} = " + json.dumps(result, indent=2) + ";\n"
        )


if __name__ == "__main__":
    main()
