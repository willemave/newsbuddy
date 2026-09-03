"""Derive the Buddy's blinking frame from the Buddy mark itself.

The mark is a raster, so the onboarding guide blinks by swapping to a second frame rather
than by animating parts of the artwork. That frame is generated here instead of drawn by
hand: each pupil is filled with the lens interior colour sampled from the ring around it,
then a closed lid is stroked in the pupil's own colour, so the blink stays in the artwork's
palette even if the mark is recoloured later.

Usage: uv run python docs/brand-exploration-2026-08/build_blink_mark.py
Writes client/newsly/newsly/Assets.xcassets/BuddyMarkBlink.imageset.
"""

import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ASSETS = Path(__file__).resolve().parents[2] / "client/newsly/newsly/Assets.xcassets"
SOURCE = ASSETS / "BuddyMark.imageset" / "buddymark-light@3x.png"
OUT = ASSETS / "BuddyMarkBlink.imageset"

BASE = 64  # points; the imageset ships 1x/2x/3x like BuddyMark
SUPERSAMPLE = 4

# Measured off the mark at 192px: the gold rims, and the pupils centred inside them.
LENS_OUTER_RADIUS = 22.0
PUPILS = ((72.1, 69.7), (117.1, 69.7))
PUPIL_RADIUS = 5.4
LID_HALF_WIDTH = 10.5
LID_THICKNESS = 4.4


def soft_mask(size: int, draw) -> np.ndarray:
    """Rasterise a shape supersampled, then average down for clean edges."""
    big = Image.new("L", (size * SUPERSAMPLE, size * SUPERSAMPLE), 0)
    draw(ImageDraw.Draw(big), SUPERSAMPLE)
    return np.array(big.resize((size, size), Image.LANCZOS)).astype(np.float32) / 255.0


def build_frame(source: Image.Image) -> Image.Image:
    arr = np.array(source.convert("RGBA")).astype(np.float32)
    size = arr.shape[0]
    yy, xx = np.mgrid[0:size, 0:size]

    for cx, cy in PUPILS:
        distance = np.hypot(xx - cx, yy - cy)
        pupil = distance < PUPIL_RADIUS
        surround = (distance > PUPIL_RADIUS + 1.5) & (
            distance < LENS_OUTER_RADIUS * 0.62
        )
        lens_colour = arr[surround][:, :3].mean(axis=0)
        # A shade under the pupil so the closed lid still reads at 54pt.
        lid_colour = arr[pupil][:, :3].mean(axis=0) * 0.88

        cover = soft_mask(
            size,
            lambda d, s, cx=cx, cy=cy: d.ellipse(
                [
                    (cx - PUPIL_RADIUS - 1.2) * s,
                    (cy - PUPIL_RADIUS - 1.2) * s,
                    (cx + PUPIL_RADIUS + 1.2) * s,
                    (cy + PUPIL_RADIUS + 1.2) * s,
                ],
                fill=255,
            ),
        )[..., None]
        arr[:, :, :3] = arr[:, :, :3] * (1 - cover) + lens_colour * cover

        lid = soft_mask(
            size,
            lambda d, s, cx=cx, cy=cy: d.rounded_rectangle(
                [
                    (cx - LID_HALF_WIDTH) * s,
                    (cy - LID_THICKNESS / 2) * s,
                    (cx + LID_HALF_WIDTH) * s,
                    (cy + LID_THICKNESS / 2) * s,
                ],
                radius=LID_THICKNESS / 2 * s,
                fill=255,
            ),
        )[..., None]
        arr[:, :, :3] = arr[:, :, :3] * (1 - lid) + lid_colour * lid

    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGBA")


def main() -> None:
    OUT.mkdir(exist_ok=True)
    frame = build_frame(Image.open(SOURCE))

    images = []
    for scale in (1, 2, 3):
        suffix = "" if scale == 1 else f"@{scale}x"
        name = f"buddymark-blink{suffix}.png"
        frame.resize((BASE * scale, BASE * scale), Image.LANCZOS).save(OUT / name)
        images.append({"filename": name, "idiom": "universal", "scale": f"{scale}x"})

    (OUT / "Contents.json").write_text(
        json.dumps(
            {"images": images, "info": {"author": "xcode", "version": 1}}, indent=2
        )
        + "\n"
    )
    print(f"wrote {OUT.name}: " + ", ".join(i["filename"] for i in images))


if __name__ == "__main__":
    main()
