"""Round 2 concepts: single-glyph reduction, Japanese sensibility without Japanese motifs.

Brief: one simple geometric mark, readable at 16px, warm and relatable. Restraint and
negative space are the Japanese inheritance; torii gates and origami cranes are not.
"""

# model key -> (provider, model id, display label)
MODELS = {
    "seedream-pro": ("runware", "bytedance:seedream@5.0-pro", "Seedream 5.0 Pro"),
    "nano-banana-pro": ("runware", "google:4@2", "Nano Banana Pro"),
    "recraft-pro": ("runware", "recraft:v4.1-pro@0", "Recraft V4.1 Pro"),
    "ideogram-4": ("runware", "ideogram:4@0", "Ideogram 4.0"),
    "gpt-image-2": ("openrouter", "openai/gpt-5.4-image-2", "GPT Image 2"),
}

# Models that reject arbitrary dimensions and need the 2048x2048 square.
FIXED_SQUARE_MODELS = {"recraft:v4.1-pro@0", "ideogram:4@0"}

STYLE_BASE = (
    "Ultra-minimal flat vector logo mark for a mobile app. Extreme reduction: ONE simple "
    "geometric glyph built from basic shapes — circles, squares, arcs, straight lines. It must "
    "read instantly at 16 pixels. Absolutely no text, no letters, no words, no numbers. No "
    "scene, no illustration, no background decoration — only the mark, centered on a plain "
    "solid pale field. Flat solid color fills: no gradients, no drop shadows, no 3D, no "
    "photographic texture. Restrained palette of one warm accent plus a soft neutral on warm "
    "off-white. Design sensibility: Japanese restraint and generous negative space (ma), but "
    "strictly NO literal Japanese motifs — no torii gates, no daruma dolls, no origami cranes, "
    "no cherry blossoms, no kanji, no paper lanterns, no wave patterns. Universal, relatable, "
    "quietly friendly and warm. The kind of confident, ownable mark a major tech brand would "
    "put on a phone home screen. "
)

# (id, model key, title, one-line rationale, prompt)
CONCEPTS = [
    # --- Seedream 5.0 Pro ---
    (
        "r2-01-two-circles",
        "seedream-pro",
        "Two Circles",
        "Companionship as pure geometry: a big form and a small one, side by side. The most "
        "literal reading of 'buddy' with zero illustration.",
        "Two overlapping circles of different sizes sitting side by side, the larger in a soft "
        "neutral and the smaller in a warm accent. Nothing else at all.",
    ),
    (
        "r2-02-folded-corner",
        "seedream-pro",
        "Folded Corner",
        "A page without drawing a newspaper. One rounded square, one corner turned. Reads as "
        "'something to read' at any size.",
        "A single rounded square with exactly one corner folded back to reveal a lighter "
        "underside. Perfectly flat, geometric, no other detail.",
    ),
    (
        "r2-03-horizon",
        "seedream-pro",
        "Horizon",
        "The daily ritual reduced to a half-disc on a line. Morning, arrival, a fresh start.",
        "A half-circle resting on a single horizontal bar, like a sun on a horizon. Two shapes "
        "only. Warm accent half-circle, neutral bar.",
    ),
    (
        "r2-04-three-bars",
        "seedream-pro",
        "Three Bars",
        "A story compressed to three lines of decreasing width — the universal glyph for text, "
        "made warm by soft rounded caps.",
        "Three stacked horizontal bars with fully rounded caps, each shorter than the one above, "
        "left aligned. The top bar in a warm accent, the rest neutral.",
    ),
    (
        "r2-05-soft-swoosh",
        "seedream-pro",
        "Soft Swoosh",
        "A messenger reduced to one confident stroke — motion and delivery without a bird or a "
        "plane.",
        "One single confident curved stroke that tapers from thick to thin, like a paper plane's "
        "path reduced to a single mark. Warm accent on off-white.",
    ),
    # --- Nano Banana Pro ---
    (
        "r2-06-quote",
        "nano-banana-pro",
        "The Quote",
        "One oversized quotation mark as the entire logo. Instantly says 'someone is telling you "
        "something'.",
        "A single oversized quotation mark glyph as the entire mark, drawn as two soft rounded "
        "geometric shapes. Warm accent color. Bold and confident.",
    ),
    (
        "r2-07-notch-disc",
        "nano-banana-pro",
        "Notch Disc",
        "A circle with one bite taken out of its lower edge — a speech bubble, stripped to its "
        "single defining feature.",
        "A perfect solid circle with one small triangular notch cut out of its lower edge, the "
        "single cue of a speech bubble tail. One flat warm color.",
    ),
    (
        "r2-08-split-disc",
        "nano-banana-pro",
        "Split Disc",
        "One circle, one soft diagonal split, two tones. Two sides of a story, or day and night.",
        "A single circle divided by one soft diagonal line into two flat tones: a warm accent "
        "half and a soft neutral half. Nothing else.",
    ),
    (
        "r2-09-stacked-cards",
        "nano-banana-pro",
        "Stacked Cards",
        "Your briefing as a deck — three offset rounded rectangles suggesting depth and finite "
        "length. You can finish this.",
        "Three rounded rectangles stacked with a slight offset like a small deck of cards seen "
        "flat-on. Front card in a warm accent, the two behind in neutrals.",
    ),
    (
        "r2-10-two-arcs",
        "nano-banana-pro",
        "Two Arcs",
        "Two facing arcs that read equally as an open book and an open eye. A dual meaning in "
        "four strokes.",
        "Two simple facing arcs forming a lens or almond shape, open in the middle. Thick even "
        "stroke weight, flat color, generous space around it.",
    ),
    # --- Recraft V4.1 Pro ---
    (
        "r2-11-dot-arc",
        "recraft-pro",
        "Dot & Arc",
        "A dot above an arc: a head and shoulders, or a sunrise. The smallest possible friendly "
        "presence.",
        "One small solid circle sitting above a wide shallow arc, like a minimal head and "
        "shoulders silhouette. Warm accent dot, neutral arc.",
    ),
    (
        "r2-12-lifted-quadrant",
        "recraft-pro",
        "Lifted Quadrant",
        "A square in four parts with one lifted away. Structure, and one piece surfaced for you.",
        "A square divided into four equal quadrants, with one quadrant separated and offset "
        "slightly outward. Flat geometric, one quadrant in a warm accent.",
    ),
    (
        "r2-13-ribbon",
        "recraft-pro",
        "Ribbon",
        "The bookmark notch — saved, kept, returned to. A shape everyone already knows.",
        "A simple vertical bookmark ribbon shape: a rectangle with a triangular notch cut from "
        "its bottom edge. One flat warm color, softly rounded corners.",
    ),
    (
        "r2-14-signal",
        "recraft-pro",
        "Signal",
        "Concentric arcs over a dot: broadcast, arrival, something reaching you.",
        "One solid dot with two or three concentric arcs radiating above it, evenly spaced, like "
        "a minimal broadcast signal. Warm accent dot, neutral arcs.",
    ),
    (
        "r2-15-plane",
        "recraft-pro",
        "Plane",
        "A paper plane cut to three facets — delivery, lightness, something sent to you.",
        "An extremely reduced paper plane made of exactly three flat triangular facets. Sharp "
        "clean geometry. Warm accent and neutral.",
    ),
    # --- Ideogram 4.0 ---
    (
        "r2-16-blob-buddy",
        "ideogram-4",
        "Blob Buddy",
        "One soft asymmetric shape and two dot eyes. The whole personality in three elements.",
        "One soft asymmetric rounded blob shape with exactly two tiny dot eyes. No mouth, no "
        "limbs, no other detail. Warm accent blob, dark neutral eyes.",
    ),
    (
        "r2-17-ring-fold",
        "ideogram-4",
        "Ring Fold",
        "A thick ring where one segment turns inward like paper. Continuity plus the page, in "
        "one closed form.",
        "A thick circular ring where one short segment of the ring folds inward and overlaps "
        "itself, like a strip of paper twisting. Flat, two tones.",
    ),
    (
        "r2-18-cup-page",
        "ideogram-4",
        "Cup & Page",
        "The morning ritual everyone recognises — a cup silhouette whose rim doubles as a page "
        "edge.",
        "A simple cup silhouette seen from the side, where the flat top edge extends into a thin "
        "page edge. Two shapes total, flat and geometric.",
    ),
    (
        "r2-19-bar-face",
        "ideogram-4",
        "Bar Face",
        "Three text bars where the top two also read as eyes. A double-take that rewards a "
        "second look.",
        "Three stacked horizontal rounded bars, where the top row is split into two short bars "
        "that also read as a pair of eyes. Warm accent, minimal.",
    ),
    (
        "r2-20-crescent-gap",
        "ideogram-4",
        "Crescent Gap",
        "A crescent made by subtracting one circle from another. Classic negative-space "
        "craftsmanship, no moon required.",
        "A crescent shape formed purely by one circle overlapping and subtracting from another. "
        "Single flat warm color, pure negative space geometry.",
    ),
    # --- GPT Image 2 ---
    (
        "r2-21-peek",
        "gpt-image-2",
        "Peek",
        "A small round head just cresting the top edge of a rounded square. Warmth from "
        "composition alone.",
        "A rounded square with a small circle peeking up from behind its top edge, only the top "
        "half of the circle visible. Two flat shapes, warm accent circle.",
    ),
    (
        "r2-22-wave-hand",
        "gpt-image-2",
        "Wave",
        "A greeting abstracted to a rounded shape with three notches. Friendly without a face.",
        "A soft rounded shape with three shallow notches cut into its top edge, abstractly "
        "suggesting a waving hand. Flat single warm color.",
    ),
    (
        "r2-23-ribbon-eyes",
        "gpt-image-2",
        "Ribbon Eyes",
        "The bookmark, given two dots. Utility plus a face, at the smallest possible cost.",
        "A simple bookmark ribbon shape with exactly two tiny dot eyes near its top. Flat warm "
        "accent ribbon, dark neutral dots.",
    ),
    (
        "r2-24-crease",
        "gpt-image-2",
        "Crease",
        "Two planes meeting at a single fold. Quiet, architectural, adult.",
        "Two flat planes meeting along one clean vertical crease, one plane slightly lighter "
        "than the other, forming a soft folded sheet. Two tones only.",
    ),
    (
        "r2-25-dot-trio",
        "gpt-image-2",
        "Dot Trio",
        "Three dots, one warmer and larger. Conversation, company, the typing indicator we all "
        "already read as 'someone is there'.",
        "Three solid dots arranged in a loose triangle, one noticeably larger and in a warm "
        "accent, the other two smaller and neutral. Nothing else.",
    ),
]
