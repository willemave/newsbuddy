"""Seat the Buddy higher in the ensō, and grow him into it.

He has always plugged the ensō's mouth, so the stroke behind him was never drawn and ends
in a blunt radial cut. He therefore stays in that mouth: nudged up and very slightly
enlarged so he no longer hangs off the bottom of the ring, but never far enough to uncover
the cut, so every pixel of the ring stays the artwork's own.

The light, dark and tinted icons are independent renders at slightly different scales, so
each one's ring is fitted on its own terms and the Buddy is placed at the same position
*relative to that ring* rather than at a shared pixel offset.

This rewrites the artwork in place and is a one-shot, not a repeatable transform: a second
run would find the Buddy already moved and scale him again. Re-run it only against the
pre-move icons (git restore them first), and use --check while iterating.

Usage: uv run python docs/brand-exploration-2026-08/recentre_icon.py [--check]
Writes the three PNGs back into AppIcon.appiconset, and AppMark alongside them.
"""

import argparse
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

ASSETS = Path(__file__).resolve().parents[2] / "client/newsly/newsly/Assets.xcassets"
ICONS = ASSETS / "AppIcon.appiconset"
# The landing and settings mark is the same drawing at 64pt, so it follows the icon.
APP_MARK = ASSETS / "AppMark.imageset"
APP_MARK_BASE = 64

# Where the Buddy should sit, as a fraction of the ring's radius from the ring's centre:
# centred horizontally, low enough to stay seated in the ensō's mouth.
TARGET_U = 0.0
TARGET_V = 0.86
# A touch larger than as drawn -- enough to lift him off the bottom edge and keep the
# stroke behind him covered, without turning him into the subject.
BUDDY_SCALE = 1.25


@dataclass
class Ring:
    cx: float
    cy: float
    inner: float
    outer: float


def dilate(mask: np.ndarray, radius: int) -> np.ndarray:
    out = mask.copy()
    for _ in range(radius):
        out[1:, :] |= out[:-1, :]
        out[:-1, :] |= out[1:, :]
        out[:, 1:] |= out[:, :-1]
        out[:, :-1] |= out[:, 1:]
    return out


def blur(a: np.ndarray, radius: int) -> np.ndarray:
    out = a.astype(np.float32)
    k = 2 * radius + 1
    for _ in range(3):
        for axis in (0, 1):
            pad = [(0, 0), (0, 0)]
            pad[axis] = (radius, radius)
            c = np.cumsum(np.pad(out, pad, mode="edge"), axis=axis)
            zero = np.zeros_like(np.take(c, [0], axis=axis))
            c = np.concatenate([zero, c], axis=axis)
            hi = np.take(c, range(k, c.shape[axis]), axis=axis)
            lo = np.take(c, range(c.shape[axis] - k), axis=axis)
            out = (hi - lo) / k
    return out


def sample(arr: np.ndarray, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    h, w = arr.shape[:2]
    x0, y0 = np.floor(xs).astype(int), np.floor(ys).astype(int)
    fx, fy = (xs - x0)[..., None], (ys - y0)[..., None]
    x0c, x1c = np.clip(x0, 0, w - 1), np.clip(x0 + 1, 0, w - 1)
    y0c, y1c = np.clip(y0, 0, h - 1), np.clip(y0 + 1, 0, h - 1)
    top = arr[y0c, x0c] * (1 - fx) + arr[y0c, x1c] * fx
    bot = arr[y1c, x0c] * (1 - fx) + arr[y1c, x1c] * fx
    return top * (1 - fy) + bot * fy


def polar(shape, cx: float, cy: float):
    yy, xx = np.mgrid[0 : shape[0], 0 : shape[1]].astype(np.float32)
    dx, dy = xx - cx, yy - cy
    return np.degrees(np.arctan2(dy, dx)), np.hypot(dx, dy)


def fit_ring(ring_mask: np.ndarray) -> Ring:
    """Fit the ring's inner edge to a circle, then measure its band."""
    h, w = ring_mask.shape
    cx, cy = w / 2.0, h / 2.0
    for _ in range(3):
        ang, rad = polar(ring_mask.shape, cx, cy)
        pts = []
        for a in np.arange(-180, 180, 3.0):
            # Skip the ensō's mouth, where there is no stroke to measure.
            if 55 <= a <= 120:
                continue
            m = ring_mask & (ang >= a) & (ang < a + 3.0)
            if m.sum() < 40:
                continue
            r = np.percentile(rad[m], 2)
            pts.append(
                (
                    cx + r * np.cos(np.deg2rad(a + 1.5)),
                    cy + r * np.sin(np.deg2rad(a + 1.5)),
                )
            )
        p = np.array(pts)
        # Algebraic circle fit: x^2 + y^2 + Ax + By + C = 0
        acc = np.column_stack([p[:, 0], p[:, 1], np.ones(len(p))])
        rhs = -(p[:, 0] ** 2 + p[:, 1] ** 2)
        sol, *_ = np.linalg.lstsq(acc, rhs, rcond=None)
        cx, cy = -sol[0] / 2, -sol[1] / 2
        inner = float(np.sqrt(cx**2 + cy**2 - sol[2]))

    ang, rad = polar(ring_mask.shape, cx, cy)
    band = ring_mask & (rad > inner * 0.9)
    outer = float(np.percentile(rad[band], 99.5))

    return Ring(cx, cy, inner, outer)


def spread(values: np.ndarray, core: np.ndarray, steps: int = 10) -> np.ndarray:
    """Grow interior values outward so the Buddy's rim carries no old background."""
    out, known = values.copy(), core.copy()
    for _ in range(steps):
        acc = np.zeros_like(out)
        cnt = np.zeros(out.shape[:2], np.float32)
        for ax, sh in ((0, 1), (0, -1), (1, 1), (1, -1)):
            acc += np.roll(np.where(known[..., None], out, 0.0), sh, axis=ax)
            cnt += np.roll(known.astype(np.float32), sh, axis=ax)
        # Take the frontier from the neighbourhood actually being averaged. Deriving it
        # from a dilation instead can admit a pixel with no known neighbour at all, which
        # averages to black and shows up as a speck of pure void.
        nxt = (cnt > 0) & ~known
        out = np.where(nxt[..., None], acc / np.maximum(cnt, 1.0)[..., None], out)
        known |= nxt
    return out


def feather(core: np.ndarray) -> np.ndarray:
    """A soft coverage ramp across the Buddy's outline.

    Eroded one pixel first, because the outermost ring of source pixels is blended with the
    ground he was drawn on and would carry it along. The ramp is then the blur alone,
    rescaled so its half-covered contour sits back on the original edge -- gating it with a
    dilated copy of the mask, as this once did, cuts the smooth ramp along a jagged boundary
    and leaves a row of teeth around him.
    """
    inner = ~dilate(~core, 1)
    return np.clip(blur(inner.astype(np.float32), 2) * 1.9 - 0.45, 0.0, 1.0)


def solid_blob(mask: np.ndarray, seed_radius: int = 10) -> np.ndarray:
    """Keep only the one large blob in `mask`, dropping speckle.

    The dark render's brush carries warm tan flecks that answer the same colour test as the
    spectacles, so the Buddy is re-grown from a core that only he is thick enough to survive.
    """
    seed = ~dilate(~mask, seed_radius)
    for _ in range(seed_radius * 5):
        grown = dilate(seed, 1) & mask
        if (grown == seed).all():
            break
        seed = grown
    return seed


def buddy_from_colour(rgb: np.ndarray) -> np.ndarray:
    """The clay body and gold spectacles, as a boolean core."""
    r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    clay = (r > 100) & (r < 225) & (r > g + 35) & (b < 160) & (g > b - 25)
    gold = (r > 175) & (g > 135) & (b < 150) & (r > b + 55)
    return solid_blob(clay | gold)


def fit_buddy(core: np.ndarray, alpha: np.ndarray, ring: Ring) -> np.ndarray:
    """Locate the tinted render's Buddy by fitting the light render's silhouette to it.

    The tinted icon is a separate render at a different scale and carries no colour to
    select on, but the two silhouettes are the same drawing, so a scale-and-shift fit
    against the zones where only the Buddy can be is enough to isolate him.
    """
    _, rad = polar(alpha.shape, ring.cx, ring.cy)
    yy, xx = np.mgrid[0 : alpha.shape[0], 0 : alpha.shape[1]]
    zone = (
        ((rad < ring.inner * 0.98) | (yy > ring.cy + ring.outer * 1.03))
        & (xx > ring.cx - 160)
        & (xx < ring.cx + 160)
        & (yy > ring.cy)
    )
    target = alpha & zone
    src = Image.fromarray((core * 255).astype(np.uint8))
    ys, xs = np.nonzero(core)
    bx, by = (xs.min() + xs.max()) / 2, (ys.min() + ys.max()) / 2
    h, w = alpha.shape

    best = None
    for scale in np.arange(0.94, 1.30, 0.02):
        big = (
            np.array(src.resize((int(w * scale), int(h * scale)), Image.LANCZOS)) > 128
        )
        oy, ox = int(by * scale - by), int(bx * scale - bx)
        if big.shape[0] < oy + h or big.shape[1] < ox + w or oy < 0 or ox < 0:
            continue
        placed = big[oy : oy + h, ox : ox + w]
        for dy in range(-30, 60, 2):
            for dx in range(-30, 31, 2):
                moved = np.roll(np.roll(placed, dy, 0), dx, 1)
                iou = (moved & target).sum() / max(((moved & zone) | target).sum(), 1)
                if best is None or iou > best[0]:
                    best = (iou, scale, dx, dy, moved)
    iou, scale, dx, dy, mask = best
    print(
        f"    fitted tinted Buddy: scale {scale:.2f} offset ({dx},{dy}) IoU {iou:.3f}"
    )
    return dilate(mask, 8) & alpha


def place(
    cov: np.ndarray,
    colour: np.ndarray,
    now: tuple[float, float],
    want: tuple[float, float],
) -> tuple[np.ndarray, np.ndarray]:
    """Move the Buddy from `now` to `want`, scaling him about his own centre."""
    height, width = cov.shape
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    src_x = (xx - want[0]) / BUDDY_SCALE + now[0]
    src_y = (yy - want[1]) / BUDDY_SCALE + now[1]
    moved_cov = sample(cov[..., None], src_x, src_y)
    return moved_cov, sample(colour, src_x, src_y)


def recentre(name: str, check_dir: Path | None, light_core: np.ndarray) -> None:
    path = ICONS / name
    src = Image.open(path)
    tinted = src.mode == "RGBA"
    print(f"  {name}")

    if tinted:
        arr = np.array(src).astype(np.float32)
        alpha = arr[:, :, 3] > 128
        ring = fit_ring(alpha & ~dilate(light_core, 40))
        core = fit_buddy(light_core, alpha, ring)
        ring = fit_ring(alpha & ~dilate(core, 4))
        values = arr[:, :, 3:4]
        ground = np.zeros((1, 1, 1), np.float32)
        keep_ring = alpha
    else:
        rgb = np.array(src.convert("RGB")).astype(np.float32)
        core = buddy_from_colour(rgb)
        keep_ring = rgb[:, :, 2] > rgb[:, :, 0] + 8
        ring = fit_ring(keep_ring & ~dilate(core, 30))
        values = rgb
        ground = rgb[2:3, 2:3].mean(axis=(0, 1)).reshape(1, 1, 3)

    print(
        f"    ring centre ({ring.cx:.0f},{ring.cy:.0f}) "
        f"inner {ring.inner:.0f} outer {ring.outer:.0f}"
    )

    cov = feather(core)
    # Clear the Buddy and the shadow he casts on the ground, but keep the ring.
    near = dilate(core, 70)
    lit = np.abs(values - ground).max(axis=2) > 2.0
    hole = (dilate(core, 3) | (near & lit & ~keep_ring))[..., None]
    plate = np.where(hole, 0.0, values - ground)

    ys, xs = np.nonzero(core)
    now = ((xs.min() + xs.max()) / 2, (ys.min() + ys.max()) / 2)
    want = (ring.cx + TARGET_U * ring.inner, ring.cy + TARGET_V * ring.inner)
    print(
        f"    Buddy {now[0]:.0f},{now[1]:.0f} -> {want[0]:.0f},{want[1]:.0f}"
        f"  scale {BUDDY_SCALE:.2f}"
    )

    colour = spread(values, cov > 0.8)
    moved_cov, moved_col = place(cov, colour, now, want)
    out = (ground + plate) * (1 - moved_cov) + moved_col * moved_cov

    if tinted:
        a = np.clip(out[:, :, 0], 0, 255)
        rgba = np.zeros((*a.shape, 4), np.uint8)
        rgba[:, :, :3] = np.where(a[..., None] > 0, 253, 0)
        rgba[:, :, 3] = a.astype(np.uint8)
        image = Image.fromarray(rgba, "RGBA")
    else:
        image = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGB")

    image.save(check_dir / name if check_dir else path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="write to a scratch dir instead of the app"
    )
    args = parser.parse_args()
    out = Path("icon_check") if args.check else None
    if out:
        out.mkdir(exist_ok=True)
    # The tinted render carries no colour to select on, so it borrows the light render's
    # silhouette. Read that before anything is rewritten in place.
    light_core = buddy_from_colour(
        np.array(Image.open(ICONS / "AppIcon-Light.png").convert("RGB")).astype(
            np.float32
        )
    )

    print("recentring:")
    for name in ("AppIcon-Light.png", "AppIcon-Dark.png", "AppIcon-Tinted.png"):
        recentre(name, out, light_core)

    print("  AppMark (follows the icon)")
    mark_dir = out if out else APP_MARK
    for icon, stem in (("AppIcon-Light.png", "light"), ("AppIcon-Dark.png", "dark")):
        source = Image.open((out or ICONS) / icon).convert("RGB")
        for scale in (1, 2, 3):
            size = APP_MARK_BASE * scale
            suffix = "" if scale == 1 else f"@{scale}x"
            source.resize((size, size), Image.LANCZOS).save(
                mark_dir / f"appmark-{stem}{suffix}.png"
            )
    print("done")


if __name__ == "__main__":
    main()
