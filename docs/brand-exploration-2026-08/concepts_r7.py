"""Round 7: the two finalists across twelve palettes each.

Finalists: Ensō · Petrol (r6-03) and Reader × Ribbon Tail (r6-16).

Both run on Nano Banana Pro via OpenRouter — Runware is out of credits, and r6-03 was already
a Nano Banana Pro render (Seedream 5.0 Pro has no OpenRouter route). Keeping both families on
one model means the palettes are the only variable.

Palette direction from the shortlist: one deep dominant color carrying the shape, with a small
restrained highlight. No orange.
"""

MODELS = {
    "nano-banana-pro": ("openrouter", "google/gemini-3-pro-image", "Nano Banana Pro"),
}

STYLE_BASE = (
    "A warm illustrated app icon mark. This is a flat 2D graphic ILLUSTRATION — absolutely not "
    "a photograph, not a photorealistic product shot, not a 3D render. Editorial illustration "
    "with gentle depth: mostly flat color shapes with soft subtle shading, a fine grain texture "
    "over the fills, and clean crisp edges. Simple, contained and iconic enough to work as an "
    "app icon at small size. The mood is calm, quiet and reassuring. The mark uses one deep "
    "dominant color carrying most of the shape, with only a small restrained highlight in the "
    "accent color — the accent must be a minority of the mark, not half of it. Follow the "
    "stated palette exactly and introduce no other hues; do not add orange. No text, no "
    "letters, no words, no numbers. Composed flat-on and front-facing like a logo, NOT in "
    "three-quarter perspective and not sitting in a scene. Centered on a plain pale flat field "
    "with generous space around it. "
)

ENSO = (
    "A soft hand-brushed ink ring, slightly imperfect with visible dry-brush texture, thick and "
    "confident, cradling a small open book resting in the gap at the bottom of the ring. "
)
READER = (
    "A soft rounded blob character wearing a pair of round spectacles, with two small dot eyes "
    "visible behind the lenses and a tiny gentle smile. No limbs, no arms. The bottom of its "
    "body ends in a notched swallow-tail like a bookmark ribbon. "
)

# (palette slug, display name, palette clause)
PALETTES = [
    (
        "petrol",
        "Petrol",
        "deep petrol blue-green mark, pale sand highlight, on warm taupe",
    ),
    ("forest", "Forest", "deep forest green mark, warm oat highlight, on pale cream"),
    ("ink", "Ink", "near-black ink mark, warm cream highlight, on soft oat"),
    ("indigo", "Indigo", "deep indigo mark, pale gold highlight, on warm cream"),
    ("plum", "Plum", "deep plum mark, soft blush highlight, on warm cream"),
    ("moss", "Moss", "deep moss green mark, pale gold highlight, on soft oat"),
    ("olive", "Olive", "dark olive mark, warm cream highlight, on warm oat"),
    ("slate", "Slate", "deep slate blue mark, warm cream highlight, on pale ivory"),
    (
        "espresso",
        "Espresso",
        "deep warm espresso brown mark, soft blush highlight, on warm cream",
    ),
    ("oxblood", "Oxblood", "deep oxblood red mark, warm oat highlight, on pale cream"),
    ("pine", "Pine", "very deep pine green mark, pale sage highlight, on warm cream"),
    ("navy", "Navy", "deep navy mark, warm sand highlight, on soft ivory"),
]

CONCEPTS = []
for i, (slug, name, palette) in enumerate(PALETTES, start=1):
    CONCEPTS.append(
        (
            f"r7-{i:02d}-enso-{slug}",
            "nano-banana-pro",
            f"Ensō · {name}",
            f"Finalist one in {name.lower()}. Ring carries the color, the book is the highlight.",
            ENSO + f"Palette: {palette}.",
        )
    )
for i, (slug, name, palette) in enumerate(PALETTES, start=13):
    CONCEPTS.append(
        (
            f"r7-{i:02d}-reader-{slug}",
            "nano-banana-pro",
            f"Reader · {name}",
            f"Finalist two in {name.lower()}. Body carries the color, the spectacles are the "
            "highlight.",
            READER + f"Palette: {palette}.",
        )
    )
