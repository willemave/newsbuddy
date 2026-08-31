"""Round 4: calm, zen, reading and knowledge — with a distinct palette per concept.

Two corrections from round 3:

1. Paper folding is no longer the driving idea. Round 3 made every concept an origami
   variation. The soft illustrated texture stays; the folding goes.
2. Every prior round came out orange because the shared style base asked for "one warm
   accent". The palette is now specified per concept and deliberately varied — sage, slate,
   plum, indigo, teal, moss, lavender — with orange dropped as a key color.

Carried forward: the Ensō & Card brush-ring direction and its warm-grey-plus-blush palette,
Peek Curl's small peeking face, and the cute bookmark from round 2's Ribbon Eyes.

Recraft V4.1 Pro is retired after producing the weakest tiles in all three prior rounds.
"""

# model key -> (provider, model id, display label)
MODELS = {
    "seedream-pro": ("runware", "bytedance:seedream@5.0-pro", "Seedream 5.0 Pro"),
    "nano-banana-pro": ("runware", "google:4@2", "Nano Banana Pro"),
    "ideogram-4": ("runware", "ideogram:4@0", "Ideogram 4.0"),
    "gpt-image-2": ("openrouter", "openai/gpt-5.4-image-2", "GPT Image 2"),
}

STYLE_BASE = (
    "A warm illustrated app icon mark. This is a flat 2D graphic ILLUSTRATION — absolutely not "
    "a photograph, not a photorealistic product shot, not a 3D render. Editorial illustration "
    "with gentle depth: mostly flat color shapes with soft subtle shading, a fine grain texture "
    "over the fills, and clean crisp edges. Simple, contained and iconic enough to work as an "
    "app icon at small size, but with real craft and warmth. The mood is calm, quiet and "
    "reassuring. Follow the stated color palette exactly and introduce no other hues — in "
    "particular do not add orange. No text, no letters, no words, no numbers. No literal "
    "Japanese motifs — no torii gates, no daruma dolls, no paper cranes, no cherry blossoms, no "
    "kanji, no paper lanterns. Composed flat-on and front-facing like a logo, NOT in "
    "three-quarter perspective and not sitting in a scene. Centered on a plain pale flat field "
    "with generous space around it. "
)

# (id, model key, title, one-line rationale, prompt incl. its own palette)
CONCEPTS = [
    # --- Seedream 5.0 Pro: strongest at soft brush texture ---
    (
        "r4-01-enso-book",
        "seedream-pro",
        "Ensō & Book",
        "Your favorite mark, with the folded card swapped for a small open book — the same "
        "brushed ring and the same muted palette you liked.",
        "A soft hand-brushed ink ring, slightly imperfect with visible dry-brush texture, "
        "cradling a small open book resting in the gap of the ring. "
        "Palette: warm grey ring, soft blush book, on warm cream.",
    ),
    (
        "r4-02-enso-cat",
        "seedream-pro",
        "Ensō & Cat",
        "The brush ring with a sleeping cat curled inside it. Calm, companionable, and a genuine "
        "buddy without being a mascot.",
        "A soft hand-brushed ink ring with a small cat curled up asleep inside it, the cat "
        "reduced to one simple rounded silhouette with a closed-eye curve. "
        "Palette: sage green ring, soft oat cat, on pale cream.",
    ),
    (
        "r4-03-enso-breath",
        "seedream-pro",
        "Ensō & Breath",
        "A brushed ring with a single dot resting in its opening. The most reduced, most "
        "meditative version of the direction.",
        "A soft hand-brushed ink ring with an open gap, and one small solid dot resting in the "
        "gap. Calm and meditative. "
        "Palette: slate blue ring, deeper slate dot, on ivory.",
    ),
    (
        "r4-04-ripple",
        "seedream-pro",
        "Ripple",
        "Concentric rings on still water: one story, spreading. Calm and universally legible.",
        "Three or four concentric rings spreading outward on still water from a single small "
        "point, drawn with soft brushed edges. "
        "Palette: dusty blue rings of varying depth, on pale sand.",
    ),
    (
        "r4-05-cairn",
        "seedream-pro",
        "Cairn",
        "Three balanced stones. Stillness, patience, and things stacked in the right order.",
        "Three smooth rounded stones stacked and balanced on top of each other, largest at the "
        "bottom, each softly shaded with a subtle stone grain. "
        "Palette: slate grey stones with one moss green stone, on warm oat.",
    ),
    (
        "r4-06-open-book",
        "seedream-pro",
        "Open Book",
        "The book itself, abstracted to two gentle curved planes. Quiet, adult, unmistakable.",
        "An open book seen from the front, abstracted into two soft curved planes meeting at a "
        "central spine, with a gentle shadow in the gutter. "
        "Palette: deep indigo covers, cream pages.",
    ),
    # --- Nano Banana Pro: bold contained icon composition ---
    (
        "r4-07-bookmark-buddy",
        "nano-banana-pro",
        "Bookmark Buddy",
        "The cute bookmark you asked for: a ribbon with two dot eyes and a small smile, with real "
        "weight rather than a flat glyph.",
        "A rounded bookmark ribbon with a notched tail, with two small dot eyes and a tiny gentle "
        "smile near the top, soft shading giving it a little weight. "
        "Palette: dusty rose ribbon, deep plum face details, on warm cream.",
    ),
    (
        "r4-08-bookmark-sleep",
        "nano-banana-pro",
        "Sleeping Bookmark",
        "The same bookmark, asleep between readings. Serene rather than cute — the evening mood.",
        "A rounded bookmark ribbon with a notched tail, with two simple closed-eye arcs giving it "
        "a calm sleeping expression. Soft shading. "
        "Palette: lavender grey ribbon, soft blush cheeks, deep plum eyes, on pale ivory.",
    ),
    (
        "r4-09-owl",
        "nano-banana-pro",
        "Owl",
        "Knowledge's oldest symbol, reduced to a circle, two eyes and a beak. Warm without being "
        "childish.",
        "A small owl abstracted to one soft rounded body shape with two large calm circular eyes "
        "and a tiny triangular beak. No feather detail, no branch. "
        "Palette: deep teal body, sand-colored eyes, on pale sand.",
    ),
    (
        "r4-10-moon-calm",
        "nano-banana-pro",
        "Calm Moon",
        "A crescent with one closed eye. The end-of-day briefing, and a natural dark-mode mark.",
        "A simple crescent moon shape with one calm closed-eye arc on its face, softly shaded. "
        "Palette: deep plum crescent, pale cream ground, soft mauve shading.",
    ),
    (
        "r4-11-lamp",
        "nano-banana-pro",
        "Reading Lamp",
        "A lamp and the pool of light beneath it. The most direct image of sitting down to read.",
        "A simple reading lamp with a rounded shade casting a soft triangular pool of light "
        "beneath it, everything reduced to two or three flat shapes. "
        "Palette: soft muted gold light, deep navy lamp, on pale ivory.",
    ),
    (
        "r4-12-book-stack",
        "nano-banana-pro",
        "Book Stack",
        "Three books seen edge-on. Accumulated knowledge, and a finite pile you can finish.",
        "Three books stacked flat on top of one another seen from the side, each a simple "
        "rounded bar with a visible page edge. "
        "Palette: clay pink, sage green and soft oat books, on warm cream.",
    ),
    # --- Ideogram 4.0 ---
    (
        "r4-13-glasses",
        "ideogram-4",
        "Spectacles",
        "Two circles and a bridge. Reading itself, in the fewest possible shapes.",
        "A pair of round reading spectacles reduced to two circles joined by a simple bridge, "
        "with short arms. Even stroke weight, softly shaded. "
        "Palette: charcoal frames, on warm oat.",
    ),
    (
        "r4-14-sprout",
        "ideogram-4",
        "Sprout",
        "Two soft leaves on a stem. Knowledge as something that grows a little each day.",
        "A small sprout with exactly two soft rounded leaves on a short stem, calm and simple. "
        "Palette: deep forest green leaves, soft moss stem, on warm oat.",
    ),
    (
        "r4-15-cup",
        "ideogram-4",
        "Cup",
        "The calm ritual around reading. Warm and universally understood.",
        "A simple rounded cup seen from the front with a small handle and two soft curls of "
        "steam rising above it. "
        "Palette: muted teal cup, pale grey steam, on soft cream.",
    ),
    (
        "r4-16-key",
        "ideogram-4",
        "Key",
        "Knowledge unlocks. A confident, slightly editorial mark with real history behind it.",
        "A simple key with a large rounded bow at the top and a short simple bit at the bottom, "
        "flat and softly shaded. "
        "Palette: soft olive key, on warm cream.",
    ),
    (
        "r4-17-window",
        "ideogram-4",
        "Window",
        "A window onto the world with a calm horizon inside it — the news as a view, not a feed.",
        "A softly rounded square window frame with a simple horizon line and a small sun disc "
        "visible inside it. Calm and still. "
        "Palette: dusty blue frame, pale sand interior, soft grey horizon.",
    ),
    (
        "r4-18-thread",
        "ideogram-4",
        "Thread",
        "A single line followed to its end point. A story you can trace, and the least literal "
        "mark in the set.",
        "One single smooth curving thread line that travels gently across the mark and ends in a "
        "small solid dot. Even stroke weight, calm and unhurried. "
        "Palette: deep plum thread and dot, on warm oat.",
    ),
    # --- GPT Image 2: warmth and character ---
    (
        "r4-19-cat-curl",
        "gpt-image-2",
        "Curled Cat",
        "A cat curled into an almost perfect circle. Calm, warm, and a naturally great icon "
        "silhouette.",
        "A cat curled into a soft near-circular sleeping shape, tail wrapped around, reduced to "
        "one continuous silhouette with a single closed-eye curve. "
        "Palette: warm grey cat, dusty rose nose and inner ear, on pale cream.",
    ),
    (
        "r4-20-bird-perch",
        "gpt-image-2",
        "Perched Bird",
        "A small bird sitting calmly on a line. Quiet company rather than a messenger in flight.",
        "A small round bird perched calmly on a single horizontal line, reduced to one soft "
        "rounded body, a small beak and one dot eye. "
        "Palette: sage green bird, soft charcoal line, on warm cream.",
    ),
    (
        "r4-21-buddy-glasses",
        "gpt-image-2",
        "Reader",
        "A soft round character in round glasses. The buddy, characterised purely by the fact "
        "that it reads.",
        "A soft rounded blob character wearing a pair of round spectacles, with two small dot "
        "eyes visible behind the lenses. No mouth, no limbs. "
        "Palette: soft plum body, charcoal spectacles, on warm oat.",
    ),
    (
        "r4-22-candle",
        "gpt-image-2",
        "Candle",
        "A small steady flame. Calm, warm and a little old-world — good against a dark UI.",
        "A short rounded candle with one small calm teardrop flame above it, softly shaded. "
        "Palette: soft muted gold flame, deep forest green candle, on pale cream.",
    ),
    (
        "r4-23-nest",
        "gpt-image-2",
        "Nest",
        "A nest holding one egg — something gathered and kept safe for you.",
        "A soft rounded nest shape holding exactly one small egg, reduced to two simple forms "
        "with gentle shading. "
        "Palette: moss green nest, pale sand egg, on warm cream.",
    ),
    (
        "r4-24-calm-eye",
        "gpt-image-2",
        "Calm Eye",
        "Attention itself: two soft arcs and a dot. Reading, watching, noticing.",
        "A calm open eye formed from two soft facing arcs with a single solid dot at the center. "
        "Even stroke weight, serene. "
        "Palette: slate blue arcs, deeper slate dot, on ivory.",
    ),
]
