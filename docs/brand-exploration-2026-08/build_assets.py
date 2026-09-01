"""Turn the two chosen brand renders into shippable app assets.

The generated PNGs have a flat cream field baked in. The buddy has to sit inside small
circular buttons on varying surfaces, so it needs real alpha plus a lightened variant for
dark mode — indigo at #383061 is unreadable on a warm charcoal ground.

The ensō keeps its ground: an app icon is a filled square everywhere it appears, so the
Settings row and the launch screen both want the untouched render.

Usage: uv run python docs/brand-exploration-2026-08/build_assets.py
Writes into build_assets/ for copying into Assets.xcassets.
"""

import colorsys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).parent
OUT = ROOT / "build_assets"

BUDDY_SRC = ROOT / "images_r8" / "r8-10-reader-indigo.png"
ENSO_SRC = ROOT / "images_r8" / "r8-03-enso-slate.png"

# Body indigo and spectacle gold as rendered, sampled from the source.
BUDDY_BODY = (0x38, 0x30, 0x61)
BUDDY_GOLD = (0xD3, 0xBC, 0x78)
# Dark-mode body: same hue, lifted until it reads on the warm dark ground.
BUDDY_BODY_DARK = (0x8B, 0x7F, 0xD0)


def cut_background(img: Image.Image, tolerance: int = 60) -> Image.Image:
    """Replace the flat corner-sampled field with alpha.

    Distance is measured against the corner color, and the resulting alpha ramps across the
    tolerance band so anti-aliased edges and brush texture survive as partial alpha rather
    than a hard cut.
    """
    rgba = np.array(img.convert("RGBA")).astype(np.float32)
    h, w = rgba.shape[:2]
    corners = np.concatenate(
        [
            rgba[:8, :8, :3].reshape(-1, 3),
            rgba[:8, w - 8 :, :3].reshape(-1, 3),
            rgba[h - 8 :, :8, :3].reshape(-1, 3),
            rgba[h - 8 :, w - 8 :, :3].reshape(-1, 3),
        ]
    )
    bg = corners.mean(axis=0)

    dist = np.sqrt(((rgba[:, :, :3] - bg) ** 2).sum(axis=2))
    alpha = np.clip((dist - tolerance * 0.35) / (tolerance * 0.65), 0.0, 1.0)
    rgba[:, :, 3] = alpha * 255.0
    return Image.fromarray(rgba.astype(np.uint8), "RGBA")


def recolor(
    img: Image.Image,
    source: tuple[int, int, int],
    target: tuple[int, int, int],
    protect: tuple[int, int, int],
    radius: int = 110,
) -> Image.Image:
    """Shift one color family to a new hue/lightness, preserving shading.

    Each matched pixel keeps its lightness offset from the source color, so folds and the
    darker side panel survive the swap instead of flattening to one flat fill.
    """
    rgba = np.array(img).astype(np.float32)
    rgb = rgba[:, :, :3]

    d_src = np.sqrt(((rgb - np.array(source, dtype=np.float32)) ** 2).sum(axis=2))
    d_pro = np.sqrt(((rgb - np.array(protect, dtype=np.float32)) ** 2).sum(axis=2))
    mask = (d_src < radius) & (d_src < d_pro) & (rgba[:, :, 3] > 8)

    _s_h, s_l, s_s = colorsys.rgb_to_hls(*[c / 255 for c in source])
    t_h, t_l, t_s = colorsys.rgb_to_hls(*[c / 255 for c in target])

    idx = np.argwhere(mask)
    for y, x in idx:
        r, g, b = rgb[y, x] / 255.0
        _h, lightness, s = colorsys.rgb_to_hls(r, g, b)
        new_l = float(np.clip(t_l + (lightness - s_l), 0.0, 1.0))
        new_s = float(np.clip(t_s + (s - s_s) * 0.5, 0.0, 1.0))
        nr, ng, nb = colorsys.hls_to_rgb(t_h, new_l, new_s)
        rgba[y, x, :3] = (nr * 255, ng * 255, nb * 255)
    return Image.fromarray(rgba.astype(np.uint8), "RGBA")


def trim(img: Image.Image, pad_ratio: float = 0.04) -> Image.Image:
    """Crop to the mark's alpha bounds, then re-pad to a centered square."""
    bbox = img.split()[3].getbbox()
    if bbox is None:
        return img
    cropped = img.crop(bbox)
    side = max(cropped.size)
    pad = int(side * pad_ratio)
    canvas = Image.new("RGBA", (side + pad * 2, side + pad * 2), (0, 0, 0, 0))
    canvas.paste(
        cropped,
        ((canvas.width - cropped.width) // 2, (canvas.height - cropped.height) // 2),
    )
    return canvas


def write_scales(img: Image.Image, name: str, base: int) -> None:
    OUT.mkdir(exist_ok=True)
    for scale in (1, 2, 3):
        size = base * scale
        suffix = "" if scale == 1 else f"@{scale}x"
        img.resize((size, size), Image.Resampling.LANCZOS).save(
            OUT / f"{name}{suffix}.png"
        )


def main() -> None:
    OUT.mkdir(exist_ok=True)

    buddy = trim(cut_background(Image.open(BUDDY_SRC)))
    write_scales(buddy, "BuddyMark", 64)

    buddy_dark = recolor(buddy, BUDDY_BODY, BUDDY_BODY_DARK, protect=BUDDY_GOLD)
    write_scales(buddy_dark, "BuddyMarkDark", 64)

    # The ensō keeps its field — an app icon is a filled square wherever it is shown.
    enso = Image.open(ENSO_SRC).convert("RGB")
    enso.resize((1024, 1024), Image.Resampling.LANCZOS).save(OUT / "AppIcon-Light.png")
    for scale in (1, 2, 3):
        size = 72 * scale
        suffix = "" if scale == 1 else f"@{scale}x"
        enso.resize((size, size), Image.Resampling.LANCZOS).save(
            OUT / f"AppMark{suffix}.png"
        )

    # Dark icon: same drawing, lifted ring on a warm charcoal ground. Classifying by
    # blueness separates the slate ring from the cream book without a hand-made mask.
    cut = cut_background(Image.open(ENSO_SRC))
    arr = np.array(cut).astype(np.float32)
    rgb, alpha = arr[:, :, :3], arr[:, :, 3:4] / 255.0
    is_ring = (rgb[:, :, 2] > rgb[:, :, 0] + 6)[:, :, None]

    ring_dark = np.array([0x9D, 0xB0, 0xCC], dtype=np.float32)
    book_dark = np.array([0xE6, 0xD9, 0xBA], dtype=np.float32)
    # Keep each pixel's shading by scaling the target with its own relative brightness.
    mean_level = max(float(rgb.mean()), 1.0)
    shade = (rgb.mean(axis=2, keepdims=True) / mean_level).clip(0.55, 1.25)
    recolored = np.where(is_ring, ring_dark * shade, book_dark * shade).clip(0, 255)

    ground = np.array([0x19, 0x15, 0x10], dtype=np.float32)
    composited = recolored * alpha + ground * (1.0 - alpha)
    Image.fromarray(composited.astype(np.uint8), "RGB").resize(
        (1024, 1024), Image.Resampling.LANCZOS
    ).save(OUT / "AppIcon-Dark.png")

    # Tinted icon (iOS 18+ tinted home screens): grayscale on alpha; the system paints
    # the backdrop and tint. Ring maps light, book mid-gray, so hierarchy survives tinting.
    tint_rgba = np.zeros_like(arr)
    tint_gray = np.where(is_ring, 235.0, 165.0)
    tint_rgba[:, :, :3] = tint_gray
    tint_rgba[:, :, 3:4] = alpha * 255.0
    Image.fromarray(tint_rgba.astype(np.uint8), "RGBA").resize(
        (1024, 1024), Image.Resampling.LANCZOS
    ).save(OUT / "AppIcon-Tinted.png")

    dark_mark = Image.open(OUT / "AppIcon-Dark.png")
    for scale in (1, 2, 3):
        size = 72 * scale
        suffix = "" if scale == 1 else f"@{scale}x"
        dark_mark.resize((size, size), Image.Resampling.LANCZOS).save(
            OUT / f"AppMarkDark{suffix}.png"
        )

    print("wrote:")
    for p in sorted(OUT.iterdir()):
        print(f"  {p.name}  {Image.open(p).size}")


if __name__ == "__main__":
    main()
