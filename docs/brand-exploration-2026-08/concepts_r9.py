"""Round 9: put the buddy inside the ensō, replacing the open book.

The shipped icon is a slate brush ring cradling a small cream book. The book is the
weakest element — it reads as generic at small sizes and duplicates what the reader
already says. Swapping it for the indigo buddy unifies the two marks: one icon that is
both the editorial ring and the character.

Every concept is a two-reference edit — the ensō first, the buddy second — so the model
composites existing artwork instead of redrawing either from a description. Round 7
proved that re-describing a mark loses it.
"""

from pathlib import Path

ROOT = Path(__file__).parent
ENSO_SRC = ROOT / "images_r8" / "r8-03-enso-slate.png"
BUDDY_SRC = ROOT / "images_r8" / "r8-10-reader-indigo.png"

MODELS = {
    "nano-banana-pro": ("openrouter", "google/gemini-3-pro-image", "Nano Banana Pro"),
}

STYLE_BASE = (
    "You are given two images. The FIRST is an app icon: a slate blue hand-brushed ink "
    "ring with dry-brush texture, cradling a small cream open book in the gap at the "
    "bottom of the ring, on a warm cream field. The SECOND is a character mark: an "
    "indigo rounded figure with an arched top, a deep V notch at the bottom, and round "
    "gold spectacles. "
    "Produce a new version of the FIRST image in which the open book is replaced by the "
    "character from the SECOND image. Keep the brushed ring EXACTLY as it is — same "
    "slate color, same dry-brush texture, same imperfect sweep, same position and size, "
    "same warm cream field. Do not redraw or restyle the ring. Keep the character's "
    "indigo body, gold spectacles and silhouette recognizably intact. Flat 2D "
    "illustration, no photograph, no 3D render, no text, no added decoration. Square "
    "1:1, centered, generous space around the mark. "
)

# (id, model key, title, one-line rationale, prompt)
CONCEPTS = [
    (
        "r9-01-nestled",
        "nano-banana-pro",
        "Nestled",
        (
            "Straight swap: the buddy sits in the ring's gap exactly where the book was, "
            "at the same scale."
        ),
        STYLE_BASE
        + (
            "Place the character in the gap at the bottom of the ring, at the same scale "
            "the book occupied, overlapping the ring's lower stroke the same way."
        ),
    ),
    (
        "r9-02-centered",
        "nano-banana-pro",
        "Centered",
        (
            "The buddy floats in the ring's empty middle instead of the gap, so the ring "
            "frames it rather than cradling it."
        ),
        STYLE_BASE
        + (
            "Place the character centered inside the open middle of the ring, not "
            "touching the stroke, sized to leave clear space on all sides."
        ),
    ),
    (
        "r9-03-peeking",
        "nano-banana-pro",
        "Peeking",
        (
            "Only the buddy's top half clears the ring's lower stroke — the character "
            "peeks over it."
        ),
        STYLE_BASE
        + (
            "Place the character so that only its upper half and spectacles rise above "
            "the ring's lower stroke, as if peeking over it, the rest hidden behind."
        ),
    ),
    (
        "r9-04-large",
        "nano-banana-pro",
        "Large",
        (
            "The buddy dominates: bigger than the book was, filling most of the ring's "
            "interior for maximum recognition at 60px."
        ),
        STYLE_BASE
        + (
            "Place the character centered and large, filling most of the ring's interior "
            "so it is the dominant element, with the ring as a close frame around it."
        ),
    ),
    (
        "r9-05-seated",
        "nano-banana-pro",
        "Seated",
        (
            "The buddy sits low in the ring's bowl, its notched tail overlapping the "
            "stroke the way the book's pages did."
        ),
        STYLE_BASE
        + (
            "Place the character seated low in the bowl of the ring so its notched "
            "bottom rests on and slightly overlaps the ring's lower stroke."
        ),
    ),
    (
        "r9-06-gap-break",
        "nano-banana-pro",
        "Gap Break",
        (
            "The buddy occupies the ensō's natural opening, so the character completes "
            "the circle rather than sitting inside it."
        ),
        STYLE_BASE
        + (
            "Place the character in the ensō's open gap where the brush stroke does not "
            "close, so the character visually completes the circle at that break."
        ),
    ),
]

REFS: dict[str, list[Path]] = {cid: [ENSO_SRC, BUDDY_SRC] for cid, *_ in CONCEPTS}
