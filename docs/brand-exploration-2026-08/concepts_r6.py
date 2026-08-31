"""Round 6: darker palettes, and hybrids between the four shortlisted marks.

Shortlist carried in: Ensō · Charcoal (r5-04), Ensō · Teal (r5-05), Bookmark · Sage (r5-10),
Reader · Warm Grey (r5-20).

Two halves:
  A. Ten palette variations pushing darker, each with a single restrained accent highlight.
  B. Fourteen hybrids crossing the ensō ring, the open book, the bookmark ribbon and the
     blob reader — the question being whether any pair fuses into something better than
     either parent.

Each family stays on the model that produced its original where possible.
"""

# model key -> (provider, model id, display label)
#
# Runware credits are exhausted, so this round runs entirely through OpenRouter. Nano Banana
# Pro is unaffected (it is Gemini 3 Pro Image). Seedream 5.0 Pro is Runware-only and has no
# OpenRouter route, so the ensō concepts it previously rendered fall back to Nano Banana Pro —
# expect the dry-brush texture to read differently until Runware is topped up.
MODELS = {
    "seedream-pro": ("openrouter", "google/gemini-3-pro-image", "Nano Banana Pro (Seedream sub)"),
    "nano-banana-pro": ("openrouter", "google/gemini-3-pro-image", "Nano Banana Pro"),
    "gpt-image-2": ("openrouter", "openai/gpt-5.4-image-2", "GPT Image 2"),
}

STYLE_BASE = (
    "A warm illustrated app icon mark. This is a flat 2D graphic ILLUSTRATION — absolutely not "
    "a photograph, not a photorealistic product shot, not a 3D render. Editorial illustration "
    "with gentle depth: mostly flat color shapes with soft subtle shading, a fine grain texture "
    "over the fills, and clean crisp edges. Simple, contained and iconic enough to work as an "
    "app icon at small size. The mood is calm, quiet and reassuring. The mark uses one deep "
    "dominant color carrying most of the shape, with only a small restrained highlight in the "
    "accent color — the accent should be a minority of the mark, not half of it. Follow the "
    "stated palette exactly and introduce no other hues; do not add orange. No text, no "
    "letters, no words, no numbers. Composed flat-on and front-facing like a logo, NOT in "
    "three-quarter perspective and not sitting in a scene. Centered on a plain pale flat field "
    "with generous space around it. "
)

ENSO = (
    "A soft hand-brushed ink ring, slightly imperfect with visible dry-brush texture, thick and "
    "confident, "
)
BLOB = (
    "A soft rounded blob character wearing a pair of round spectacles, with two small dot eyes "
    "visible behind the lenses. No limbs, no arms. "
)

# (id, model key, title, one-line rationale, prompt)
CONCEPTS = [
    # ============ A. Darker palettes, restrained accents ============
    (
        "r6-01-enso-ink",
        "seedream-pro",
        "Ensō · Ink",
        "The deepest version of the charcoal you liked — near-black ring, one cream book.",
        ENSO + "cradling a small open book resting in the gap at the bottom of the ring. "
        "Palette: near-black ink ring, warm cream book, on soft oat.",
    ),
    (
        "r6-02-enso-forest",
        "seedream-pro",
        "Ensō · Forest",
        "Deep forest green with a pale gold book. Darker and richer than the sage family.",
        ENSO + "cradling a small open book resting in the gap at the bottom of the ring. "
        "Palette: deep forest green ring, pale gold book, on warm cream.",
    ),
    (
        "r6-03-enso-petrol",
        "seedream-pro",
        "Ensō · Petrol",
        "The teal you liked, pushed deeper, on a warmer darker ground.",
        ENSO + "cradling a small open book resting in the gap at the bottom of the ring. "
        "Palette: deep petrol blue-green ring, pale sand book, on warm taupe.",
    ),
    (
        "r6-04-mark-forest",
        "nano-banana-pro",
        "Bookmark · Forest",
        "The sage bookmark taken darker — deep forest body, pale sage features only.",
        "A rounded bookmark ribbon with a notched swallow-tail bottom and squared shoulders, "
        "with two small dot eyes and a tiny gentle smile near the top. "
        "Palette: deep forest green ribbon, pale sage face details, on warm oat.",
    ),
    (
        "r6-05-mark-olive",
        "nano-banana-pro",
        "Bookmark · Olive",
        "Dark olive with cream features. Squared shoulders to pull it away from reading as a "
        "ghost.",
        "A rounded bookmark ribbon with a notched swallow-tail bottom and clearly squared "
        "shoulders at the top corners, with two small dot eyes and a tiny gentle smile. "
        "Palette: dark olive ribbon, warm cream face details, on warm oat.",
    ),
    (
        "r6-06-mark-petrol",
        "nano-banana-pro",
        "Bookmark · Petrol",
        "Deep petrol with a pale gold face — the richest of the bookmark palettes.",
        "A rounded bookmark ribbon with a notched swallow-tail bottom and squared shoulders, "
        "with two small dot eyes and a tiny gentle smile near the top. "
        "Palette: deep petrol blue-green ribbon, pale gold face details, on soft sand.",
    ),
    (
        "r6-07-reader-taupe",
        "gpt-image-2",
        "Reader · Deep Taupe",
        "The warm grey reader pushed darker, with cream frames as the only highlight.",
        BLOB + "A tiny gentle smile below the lenses. "
        "Palette: deep warm taupe body, warm cream spectacles, on warm oat.",
    ),
    (
        "r6-08-reader-moss",
        "gpt-image-2",
        "Reader · Deep Moss",
        "Deep moss with pale gold frames. The most natural and least synthetic pairing.",
        BLOB + "A tiny gentle smile below the lenses. "
        "Palette: deep moss green body, pale gold spectacles, on warm cream.",
    ),
    (
        "r6-09-reader-slate",
        "gpt-image-2",
        "Reader · Deep Slate",
        "Deep slate with cream frames — the coolest of the darker readers.",
        BLOB + "A tiny gentle smile below the lenses. "
        "Palette: deep slate blue body, warm cream spectacles, on soft oat.",
    ),
    (
        "r6-10-reader-ink",
        "gpt-image-2",
        "Reader · Ink",
        "Near-black body with a single blush accent on the frames. Maximum contrast, minimum "
        "color.",
        BLOB + "A tiny gentle smile below the lenses. "
        "Palette: near-black body, soft blush spectacles, on warm cream.",
    ),
    # ============ B. Hybrids ============
    (
        "r6-11-enso-bookmark",
        "seedream-pro",
        "Ensō × Bookmark",
        "The ring cradling a bookmark ribbon instead of a book. Keeps the ensō, swaps the "
        "contents.",
        ENSO + "cradling a small bookmark ribbon with a notched tail resting in the gap at the "
        "bottom of the ring. "
        "Palette: near-black ink ring, deep sage green bookmark, on warm oat.",
    ),
    (
        "r6-12-enso-blob",
        "seedream-pro",
        "Ensō × Reader",
        "The blob reader sitting inside the brushed ring — the two strongest marks, combined "
        "most literally.",
        ENSO + "with a small soft rounded blob character wearing round spectacles sitting "
        "inside the ring. "
        "Palette: deep petrol ink ring, warm taupe blob, cream spectacles, on soft oat.",
    ),
    (
        "r6-13-enso-blob-sleep",
        "seedream-pro",
        "Ensō × Sleeping Reader",
        "The same pairing, asleep — the ring becomes a bed rather than a frame.",
        ENSO + "with a small soft rounded blob curled up asleep inside the ring, its eyes two "
        "simple closed arcs. "
        "Palette: deep forest green ink ring, pale oat blob, on warm cream.",
    ),
    (
        "r6-14-ribbon-ring",
        "nano-banana-pro",
        "Ribbon Ring",
        "The ring itself made from a bookmark ribbon, curved almost closed with the notched tail "
        "at the opening.",
        "A bookmark ribbon curved around into an almost-closed circle, its notched swallow-tail "
        "end visible at the opening of the ring. One continuous form. "
        "Palette: deep petrol ribbon, pale gold inner edge, on warm oat.",
    ),
    (
        "r6-15-mark-glasses",
        "nano-banana-pro",
        "Bookmark × Spectacles",
        "The bookmark character given the reader's round glasses. Both personalities in one shape.",
        "A rounded bookmark ribbon with a notched swallow-tail bottom and squared shoulders, "
        "wearing a pair of round spectacles with two small dot eyes behind the lenses. "
        "Palette: deep forest green ribbon, warm cream spectacles, on warm oat.",
    ),
    (
        "r6-16-blob-tail",
        "nano-banana-pro",
        "Reader × Ribbon Tail",
        "The blob reader whose lower body tapers into a bookmark notch — a character that is "
        "also a bookmark.",
        BLOB + "The bottom of its body ends in a notched swallow-tail like a bookmark ribbon. "
        "Palette: deep sage green body, warm cream spectacles, on warm oat.",
    ),
    (
        "r6-17-blob-enso-specs",
        "gpt-image-2",
        "Reader × Ensō Lenses",
        "The blob's two lenses drawn as tiny brushed ensō rings. The most subtle of the hybrids.",
        "A soft rounded blob character wearing round spectacles whose two lenses are drawn as "
        "small hand-brushed ink rings with dry-brush texture, two dot eyes behind them. "
        "Palette: deep warm taupe body, near-black brushed lenses, on warm cream.",
    ),
    (
        "r6-18-blob-book",
        "gpt-image-2",
        "Reader × Book",
        "The blob reader with a small open book at its base — reading, made explicit.",
        BLOB + "A small open book rests at the base of its body, held against it. "
        "Palette: deep moss green body, warm cream spectacles and book, on soft oat.",
    ),
    (
        "r6-19-mark-book",
        "nano-banana-pro",
        "Bookmark × Book",
        "A bookmark ribbon rising out of a small closed book. The clearest reading-object hybrid.",
        "A small closed book seen flat-on with a bookmark ribbon emerging from its top edge, "
        "the ribbon carrying two small dot eyes and a tiny smile. "
        "Palette: deep petrol book, pale sage ribbon, on warm oat.",
    ),
    (
        "r6-20-enso-face",
        "seedream-pro",
        "Ensō × Face",
        "The brushed ring with the book given two dot eyes — the calmest way to add a character.",
        ENSO + "cradling a small open book in the gap at the bottom, the book carrying two tiny "
        "dot eyes and a small smile. "
        "Palette: near-black ink ring, warm cream book, deep taupe face details, on soft oat.",
    ),
    (
        "r6-21-mark-enso-tail",
        "seedream-pro",
        "Bookmark × Brushstroke",
        "A bookmark whose tail dissolves into a dry brush stroke — the ensō texture applied to "
        "the bookmark.",
        "A bookmark ribbon with squared shoulders and two small dot eyes, whose lower tail "
        "dissolves downward into a loose dry-brush ink stroke with visible texture. "
        "Palette: deep forest green ribbon and brushstroke, pale sage eyes, on warm cream.",
    ),
    (
        "r6-22-enso-character",
        "gpt-image-2",
        "Ensō as Character",
        "The ring itself becomes the buddy — two dot eyes and spectacles sitting on the "
        "brushstroke, nothing inside.",
        "A thick hand-brushed ink ring with dry-brush texture, with two small dot eyes and a "
        "pair of round spectacles resting directly on the ring itself. The centre is empty. "
        "Palette: deep slate ink ring, warm cream spectacles, on warm oat.",
    ),
    (
        "r6-23-enso-cradle",
        "gpt-image-2",
        "Ensō Cradle",
        "A brushed arc rather than a full ring, cradling the blob reader from below like a "
        "hammock.",
        "A single thick hand-brushed ink arc, like the bottom half of a ring, cradling a small "
        "soft blob character wearing round spectacles that rests in its curve. "
        "Palette: near-black brushed arc, deep sage blob, cream spectacles, on warm cream.",
    ),
    (
        "r6-24-blob-in-mark",
        "gpt-image-2",
        "Bookmark Window",
        "A bookmark ribbon with a rounded opening cut into it, the blob reader peeking through.",
        "A deep-colored bookmark ribbon with a notched tail and a round opening cut through its "
        "upper half, with a small blob character wearing spectacles visible through the opening. "
        "Palette: deep petrol ribbon, pale oat blob, on warm cream.",
    ),
]
