"""Re-render the mark on transparency, for the signed-out landing.

AppMark keeps its baked field on purpose: Settings and the launch state clip it into the
rounded rect iOS gives the icon on the home screen, and it is the now-playing artwork, all
of which want an opaque square. The landing is not showing an icon chip, though -- it is
showing the brand -- so it needs the same drawing on transparency, or the field reads as a
pale box against the page.

Cutting a ground out of a finished render does not survive a change of ground. Every soft
edge in it is part mark and part cream, and distance-to-cream saturates long before that
blend finishes, so those pixels come out fully opaque while still carrying cream: a pale
halo tracing the Buddy and the brush, invisible over cream and obvious over charcoal. The
dark icon is worse as a source, since it maps the paper showing through the dry brush to a
warm cream rather than to its own ground, which cuts into a tan outline along every stroke.

So the mark is rebuilt rather than cut. The light render gives two mattes -- the brush's
ink density, and the Buddy's silhouette -- and those are painted with flat palette colours
and composited onto transparency. No pixel carries a ground, so both appearances are the
same mattes with a different ring colour, and either one drops onto any surface cleanly.

Usage: uv run python docs/brand-exploration-2026-08/build_brand_mark.py
Writes client/newsly/newsly/Assets.xcassets/BrandMark.imageset. Safe to re-run.
"""

import json
from pathlib import Path

import numpy as np
from PIL import Image

ASSETS = Path(__file__).resolve().parents[2] / "client/newsly/newsly/Assets.xcassets"
ICONS = ASSETS / "AppIcon.appiconset"
OUT = ASSETS / "BrandMark.imageset"

BASE = 220  # points; the landing renders it at 220
# The ring's colour on a dark ground, matching the dark app icon.
RING_ON_DARK = np.array([0x9D, 0xB0, 0xCC], dtype=np.float32)
# Ignore faint spatter when squaring up, or the mark shrinks to fit a stray fleck.
TRIM_LEVEL = 40


def dilate(mask: np.ndarray, radius: int) -> np.ndarray:
    out = mask.copy()
    for _ in range(radius):
        out[1:, :] |= out[:-1, :]
        out[:-1, :] |= out[1:, :]
        out[:, 1:] |= out[:, :-1]
        out[:, :-1] |= out[:, 1:]
    return out


def erode(mask: np.ndarray, radius: int) -> np.ndarray:
    return ~dilate(~mask, radius)


def blur(values: np.ndarray, radius: int) -> np.ndarray:
    out = values.astype(np.float32)
    size = 2 * radius + 1
    for _ in range(2):
        for axis in (0, 1):
            pad = [(0, 0), (0, 0)]
            pad[axis] = (radius, radius)
            sums = np.cumsum(np.pad(out, pad, mode="edge"), axis=axis)
            zero = np.zeros_like(np.take(sums, [0], axis=axis))
            sums = np.concatenate([zero, sums], axis=axis)
            high = np.take(sums, range(size, sums.shape[axis]), axis=axis)
            low = np.take(sums, range(sums.shape[axis] - size), axis=axis)
            out = (high - low) / size
    return out


def read_render(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Return the render and the flat ground it was painted on."""
    arr = np.array(Image.open(path).convert("RGB")).astype(np.float32)
    height, width = arr.shape[:2]
    corners = np.concatenate(
        [
            arr[:8, :8].reshape(-1, 3),
            arr[:8, width - 8 :].reshape(-1, 3),
            arr[height - 8 :, :8].reshape(-1, 3),
            arr[height - 8 :, width - 8 :].reshape(-1, 3),
        ]
    )
    return arr, corners.mean(axis=0)


def fill_holes(mask: np.ndarray) -> np.ndarray:
    """Close gaps enclosed by the mask.

    The brightest specular on the spectacles is barely warmer than it is bright, so it fails
    the colour test and punches a hole clean through the Buddy. Anything the outside cannot
    reach belongs to him.
    """
    outside = np.zeros_like(mask)
    outside[0, :] = outside[-1, :] = True
    outside[:, 0] = outside[:, -1] = True
    outside &= ~mask
    for _ in range(400):
        grown = dilate(outside, 8) & ~mask
        if (grown == outside).all():
            break
        outside = grown
    return ~outside


def buddy_matte(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The Buddy's silhouette and his colours, with the cream driven off his rim.

    Warmth alone also selects the paper flecks in the brush, so the mask is eroded until
    only he is thick enough to survive and then regrown into his own outline. His interior
    colour is then pushed outward over that outline, because the rim as painted is part
    clay and part cream and would keep the cream wherever the mark is later placed.
    """
    red, green, blue = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    warm = (red > blue + 35) & (red > green + 20)

    solid = erode(warm, 8)
    for _ in range(40):
        grown = dilate(solid, 1) & warm
        if (grown == solid).all():
            break
        solid = grown

    depth = 4
    interior = erode(solid, depth)
    colour, known = rgb.copy(), interior.copy()
    for _ in range(depth * 2 + 2):
        total = np.zeros_like(colour)
        count = np.zeros(colour.shape[:2], np.float32)
        for axis, shift in ((0, 1), (0, -1), (1, 1), (1, -1)):
            total += np.roll(np.where(known[..., None], colour, 0.0), shift, axis=axis)
            count += np.roll(known.astype(np.float32), shift, axis=axis)
        # Take the frontier from the neighbourhood actually being averaged. Deriving it
        # from a dilation instead can admit a pixel with no known neighbour at all, which
        # averages to black and shows up as a speck of pure void.
        nxt = (count > 0) & ~known
        colour = np.where(
            nxt[..., None], total / np.maximum(count, 1.0)[..., None], colour
        )
        known |= nxt

    # Soften his outline with the blur alone. Anything that gates the ramp with a dilated
    # copy of the mask cuts it along that mask's own blocky contour and leaves a comb of
    # notches around him -- square-kernel dilation is not a circle.
    ramp = blur(fill_holes(solid).astype(np.float32), 2)
    return np.clip(ramp * 1.9 - 0.45, 0.0, 1.0), colour


def ink_density(rgb: np.ndarray, ground: np.ndarray, buddy: np.ndarray) -> np.ndarray:
    """How much ink the brush laid down, 0 where the paper shows through, 1 where solid.

    Measured against how dark the stroke goes at full strength, so a half-covered pixel
    matts as half rather than saturating to opaque the way a distance threshold does.
    """
    luminance = rgb.mean(axis=2)
    ground_level = float(ground.mean())

    ring = (rgb[:, :, 2] > rgb[:, :, 0] + 6) & ~dilate(buddy, 3)
    full = float(np.percentile(luminance[ring], 5)) if ring.any() else 0.0

    density = (ground_level - luminance) / max(ground_level - full, 1.0)
    # Ink is blue-grey; the Buddy's cast shadow is a neutral warm darkening of the paper.
    # Without this the shadow matts as faint ring colour and haloes him all over again.
    is_ink = np.clip((rgb[:, :, 2] - rgb[:, :, 0]) / 6.0, 0.0, 1.0)
    return np.clip(np.where(dilate(buddy, 2), 0.0, density * is_ink), 0.0, 1.0)


def compose(
    ring_alpha: np.ndarray,
    ring_colour: np.ndarray,
    buddy_alpha: np.ndarray,
    buddy_colour: np.ndarray,
) -> Image.Image:
    """Buddy over ring, over nothing."""
    ring_a, buddy_a = ring_alpha[..., None], buddy_alpha[..., None]
    alpha = buddy_a + ring_a * (1.0 - buddy_a)
    colour = buddy_colour * buddy_a + ring_colour * ring_a * (1.0 - buddy_a)
    rgba = np.dstack(
        [np.clip(colour / np.maximum(alpha, 1e-4), 0, 255), alpha[:, :, 0] * 255.0]
    )
    return Image.fromarray(rgba.astype(np.uint8), "RGBA")


def square_to_content(image: Image.Image, bounds) -> Image.Image:
    """Crop to the drawing and re-centre it, so the mark fills the frame it is given."""
    cropped = image.crop(bounds)
    side = max(cropped.size)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(cropped, ((side - cropped.width) // 2, (side - cropped.height) // 2))
    return canvas


def main() -> None:
    OUT.mkdir(exist_ok=True)
    render, ground = read_render(ICONS / "AppIcon-Light.png")

    buddy_alpha, buddy_colour = buddy_matte(render)
    ring_alpha = ink_density(render, ground, buddy_alpha > 0.5)
    # The stroke as painted, taken from where the brush ran solid. Density tops out below
    # 1 because the ink is measured against its own darkest reading, so this cannot ask for
    # fully covered pixels.
    solid_ring = ring_alpha > 0.6
    ring_on_light = np.percentile(render[solid_ring], 20, axis=0).astype(np.float32)

    marks = {
        "light": compose(ring_alpha, ring_on_light, buddy_alpha, buddy_colour),
        "dark": compose(ring_alpha, RING_ON_DARK, buddy_alpha, buddy_colour),
    }
    bounds = (
        marks["light"]
        .split()[3]
        .point(lambda v: 255 if v > TRIM_LEVEL else 0)
        .getbbox()
    )

    images = []
    for appearance, mark in marks.items():
        squared = square_to_content(mark, bounds)
        for scale in (1, 2, 3):
            suffix = "" if scale == 1 else f"@{scale}x"
            name = f"brandmark-{appearance}{suffix}.png"
            squared.resize((BASE * scale, BASE * scale), Image.LANCZOS).save(OUT / name)
            entry = {"filename": name, "idiom": "universal", "scale": f"{scale}x"}
            if appearance == "dark":
                entry["appearances"] = [{"appearance": "luminosity", "value": "dark"}]
            images.append(entry)

    (OUT / "Contents.json").write_text(
        json.dumps(
            {"images": images, "info": {"author": "xcode", "version": 1}}, indent=2
        )
        + "\n"
    )
    print(f"wrote {OUT.name}: {len(images)} images at {BASE}pt")


if __name__ == "__main__":
    main()
