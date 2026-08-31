"""Round 5: variations on the three shortlisted marks.

Ensō & Book (r4-01), Bookmark Buddy (r4-07) and Reader (r4-21), each in eight color schemes
with light form variation. Each family stays on the model that produced the original, since
the rendering character the user picked is partly the model's.

This is a convergence round, not an exploration one: the form stays recognizable so the
palettes can be compared directly.
"""

# model key -> (provider, model id, display label)
MODELS = {
    "seedream-pro": ("runware", "bytedance:seedream@5.0-pro", "Seedream 5.0 Pro"),
    "nano-banana-pro": ("runware", "google:4@2", "Nano Banana Pro"),
    # Same model as nano-banana-pro (Nano Banana Pro is Gemini 3 Pro Image), billed through
    # OpenRouter instead. Used for the last five bookmarks after Runware credits ran out.
    "nano-banana-pro-or": ("openrouter", "google/gemini-3-pro-image", "Nano Banana Pro"),
    "gpt-image-2": ("openrouter", "openai/gpt-5.4-image-2", "GPT Image 2"),
}

STYLE_BASE = (
    "A warm illustrated app icon mark. This is a flat 2D graphic ILLUSTRATION — absolutely not "
    "a photograph, not a photorealistic product shot, not a 3D render. Editorial illustration "
    "with gentle depth: mostly flat color shapes with soft subtle shading, a fine grain texture "
    "over the fills, and clean crisp edges. Simple, contained and iconic enough to work as an "
    "app icon at small size. The mood is calm, quiet and reassuring. Follow the stated color "
    "palette exactly and introduce no other hues — in particular do not add orange unless the "
    "palette names it. No text, no letters, no words, no numbers. Composed flat-on and "
    "front-facing like a logo, NOT in three-quarter perspective and not sitting in a scene. "
    "Centered on a plain pale flat field with generous space around it. "
)

ENSO = (
    "A soft hand-brushed ink ring, slightly imperfect with visible dry-brush texture, cradling "
    "a small open book resting in the gap at the bottom of the ring. "
)
BOOKMARK = (
    "A rounded bookmark ribbon with a notched swallow-tail bottom, with two small dot eyes and "
    "a tiny gentle smile near the top, soft shading giving it a little weight. "
)
READER = (
    "A soft rounded blob character wearing a pair of round spectacles, with two small dot eyes "
    "visible behind the lenses. No mouth, no limbs, no arms. "
)

# (id, model key, title, one-line rationale, prompt)
CONCEPTS = [
    # ---------------- Ensō & Book — Seedream 5.0 Pro ----------------
    (
        "r5-01-enso-greyblush",
        "seedream-pro",
        "Ensō · Grey & Blush",
        "The original palette, unchanged — the baseline everything else is measured against.",
        ENSO + "Palette: warm grey ring, soft blush book, on warm cream.",
    ),
    (
        "r5-02-enso-sage",
        "seedream-pro",
        "Ensō · Sage",
        "Sage ring with an oat book. The calmest, most natural reading of the mark.",
        ENSO + "Palette: soft sage green ring, warm oat book, on pale cream.",
    ),
    (
        "r5-03-enso-indigo",
        "seedream-pro",
        "Ensō · Indigo",
        "Deep indigo ring. The most editorial and serious version — closest to a masthead.",
        ENSO + "Palette: deep indigo ring, cream book, on soft ivory.",
    ),
    (
        "r5-04-enso-charcoal",
        "seedream-pro",
        "Ensō · Charcoal",
        "Near-monochrome with a heavier ring. The highest contrast and the best small-size "
        "survival.",
        ENSO + "The brushed ring is noticeably thicker and heavier. "
        "Palette: charcoal ring, warm cream book, on soft oat.",
    ),
    (
        "r5-05-enso-teal",
        "seedream-pro",
        "Ensō · Teal",
        "Deep teal on sand. Cooler and more distinctive than the warm neutrals.",
        ENSO + "Palette: deep teal ring, warm sand book, on pale sand.",
    ),
    (
        "r5-06-enso-plum",
        "seedream-pro",
        "Ensō · Plum",
        "Plum ring with a blush book — the warmest of the cool palettes.",
        ENSO + "Palette: deep plum ring, soft blush book, on warm cream.",
    ),
    (
        "r5-07-enso-slate",
        "seedream-pro",
        "Ensō · Slate",
        "A thinner, lighter slate ring with a larger book — weight shifted toward the reading.",
        ENSO + "The brushed ring is noticeably thinner and lighter, and the open book is larger "
        "and more prominent. Palette: slate blue ring, pale grey-blue book, on ivory.",
    ),
    (
        "r5-08-enso-clay",
        "seedream-pro",
        "Ensō · Clay & Sage",
        "Two-tone: a clay ring against a sage book. The only variant where ring and book differ "
        "in hue rather than value.",
        ENSO + "Palette: muted clay pink ring, soft sage green book, on warm cream.",
    ),
    # ---------------- Bookmark Buddy — Nano Banana Pro ----------------
    (
        "r5-09-mark-rose",
        "nano-banana-pro",
        "Bookmark · Dusty Rose",
        "The original palette, unchanged.",
        BOOKMARK + "Palette: dusty rose ribbon, deep plum face details, on warm cream.",
    ),
    (
        "r5-10-mark-sage",
        "nano-banana-pro",
        "Bookmark · Sage",
        "Sage green with forest details. Quieter and less sweet than the rose.",
        BOOKMARK + "Palette: soft sage green ribbon, deep forest green face details, on warm oat.",
    ),
    (
        "r5-11-mark-teal",
        "nano-banana-pro",
        "Bookmark · Teal",
        "Deep teal with cream features — the most confident and brand-like of the set.",
        BOOKMARK + "Palette: deep teal ribbon, warm cream face details, on pale sand.",
    ),
    (
        "r5-12-mark-indigo",
        "nano-banana-pro-or",
        "Bookmark · Indigo",
        "Indigo with pale features, and closed sleeping eyes instead of the smile.",
        "A rounded bookmark ribbon with a notched swallow-tail bottom, with two simple "
        "closed-eye arcs near the top giving a calm sleeping expression and no mouth, soft "
        "shading giving it a little weight. "
        "Palette: deep indigo ribbon, pale blue face details, on soft ivory.",
    ),
    (
        "r5-13-mark-gold",
        "nano-banana-pro-or",
        "Bookmark · Soft Gold",
        "Muted gold with deep brown. Warm without tipping into orange.",
        BOOKMARK + "Palette: soft muted gold ribbon, deep warm brown face details, on cream.",
    ),
    (
        "r5-14-mark-lavender",
        "nano-banana-pro-or",
        "Bookmark · Lavender",
        "Lavender with plum, and a shorter, rounder body — the softest variant.",
        BOOKMARK + "The ribbon is shorter and rounder, closer to square than tall. "
        "Palette: soft lavender ribbon, deep plum face details, on pale ivory.",
    ),
    (
        "r5-15-mark-charcoal",
        "nano-banana-pro-or",
        "Bookmark · Charcoal",
        "Near-monochrome charcoal on oat. Reads as a serious utility mark.",
        BOOKMARK + "Palette: deep charcoal ribbon, warm oat face details, on warm oat.",
    ),
    (
        "r5-16-mark-plumblush",
        "nano-banana-pro-or",
        "Bookmark · Plum & Blush",
        "Inverted from the original: deep plum body with blush features, and a longer tail.",
        BOOKMARK + "The ribbon is taller with a deeper notched tail. "
        "Palette: deep plum ribbon, soft blush face details, on warm cream.",
    ),
    # ---------------- Reader — GPT Image 2 ----------------
    (
        "r5-17-reader-plum",
        "gpt-image-2",
        "Reader · Plum",
        "The original palette, unchanged.",
        READER + "Palette: soft plum body, charcoal spectacles, on warm oat.",
    ),
    (
        "r5-18-reader-sage",
        "gpt-image-2",
        "Reader · Sage",
        "Sage body with charcoal frames. The most neutral and least sweet.",
        READER + "Palette: soft sage green body, charcoal spectacles, on warm cream.",
    ),
    (
        "r5-19-reader-teal",
        "gpt-image-2",
        "Reader · Teal",
        "Deep teal with cream frames — highest contrast on the frames themselves.",
        READER + "Palette: deep teal body, warm cream spectacles, on pale sand.",
    ),
    (
        "r5-20-reader-grey",
        "gpt-image-2",
        "Reader · Warm Grey",
        "Warm grey body with plum frames, and a tiny smile added below the lenses.",
        "A soft rounded blob character wearing a pair of round spectacles, with two small dot "
        "eyes visible behind the lenses and a tiny gentle smile below them. No limbs, no arms. "
        "Palette: warm grey body, deep plum spectacles, on warm cream.",
    ),
    (
        "r5-21-reader-indigo",
        "gpt-image-2",
        "Reader · Indigo",
        "Indigo with soft gold frames. The richest and most premium-feeling pairing.",
        READER + "Palette: deep indigo body, soft muted gold spectacles, on soft ivory.",
    ),
    (
        "r5-22-reader-rose",
        "gpt-image-2",
        "Reader · Dusty Rose",
        "Dusty rose body, matching the bookmark family if you want one shared palette.",
        READER + "Palette: dusty rose body, charcoal spectacles, on warm cream.",
    ),
    (
        "r5-23-reader-moss",
        "gpt-image-2",
        "Reader · Moss",
        "Moss green with oat frames, and a wider, rounder body.",
        READER + "The body is wider and rounder, closer to a circle. "
        "Palette: moss green body, warm oat spectacles, on pale sand.",
    ),
    (
        "r5-24-reader-slate",
        "gpt-image-2",
        "Reader · Slate",
        "Slate blue with cream frames, and larger spectacles that dominate the face.",
        READER + "The spectacles are noticeably larger, dominating the face. "
        "Palette: slate blue body, warm cream spectacles, on ivory.",
    ),
]
