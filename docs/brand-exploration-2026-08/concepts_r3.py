"""Round 3: soft paper-craft objects with dimension, texture, and a hint of character.

Direction comes from the round-1/round-2 favorites — Ensō & Paper, Washi Bubble, Hanko Seal,
the folded-paper marks, and Ribbon Eyes. Their shared DNA is a tactile made object with soft
shading, visible creases, and a warm contained silhouette. Explicitly NOT a flat vector glyph:
round 2 reduced too far.

Recraft V4.1 Pro gets a small allocation here — in both prior rounds it over-reduced and landed
on existing marks (the Microsoft logo, the Wi-Fi glyph).
"""

# model key -> (provider, model id, display label)
MODELS = {
    "seedream-pro": ("runware", "bytedance:seedream@5.0-pro", "Seedream 5.0 Pro"),
    "nano-banana-pro": ("runware", "google:4@2", "Nano Banana Pro"),
    "recraft-pro": ("runware", "recraft:v4.1-pro@0", "Recraft V4.1 Pro"),
    "ideogram-4": ("runware", "ideogram:4@0", "Ideogram 4.0"),
    "gpt-image-2": ("openrouter", "openai/gpt-5.4-image-2", "GPT Image 2"),
}

STYLE_BASE = (
    "A warm illustrated app icon mark in a soft paper-craft style. This is a flat 2D graphic "
    "ILLUSTRATION — absolutely not a photograph, not a photorealistic product shot, not a 3D "
    "render. Editorial vector illustration with gentle depth: mostly flat color shapes, but with "
    "soft subtle shading on folds, a fine paper-grain texture over the fills, and clean crisp "
    "edges. Richer and more crafted than a bare geometric glyph, but still simple, contained, "
    "and iconic enough to work as an app icon. Warm muted calm palette: one warm accent plus "
    "soft neutrals on a warm off-white ground. Quietly friendly and approachable. No text, no "
    "letters, no words, no numbers. No literal Japanese motifs — no torii gates, no daruma "
    "dolls, no paper cranes, no cherry blossoms, no kanji, no paper lanterns. Composed flat-on "
    "and front-facing like a logo, NOT in three-quarter perspective and not sitting in a scene. "
    "The mark is centered on a plain pale flat field with generous space around it. "
)

# (id, model key, title, one-line rationale, prompt)
CONCEPTS = [
    # --- Seedream 5.0 Pro: strongest at soft paper texture ---
    (
        "r3-01-enso-card",
        "seedream-pro",
        "Ensō & Card",
        "The direct descendant of your favorite: a soft brushed ring cradling a small folded "
        "card in its opening.",
        "A soft hand-brushed ink ring, slightly imperfect with visible brush texture, cradling a "
        "small folded card resting in the gap of the ring. Warm grey ring, blush accent card.",
    ),
    (
        "r3-02-folded-pouch",
        "seedream-pro",
        "Folded Pouch",
        "One sheet folded into a small pouch. It reads as something that holds what's been "
        "gathered for you.",
        "A single sheet of soft paper folded into a small rounded pouch, two clean creases "
        "visible, a gentle shadow beneath. Warm blush paper, soft neutral shading.",
    ),
    (
        "r3-03-paper-bubble",
        "seedream-pro",
        "Paper Bubble",
        "The Washi Bubble idea with more body — a speech bubble that is unmistakably a folded "
        "object rather than a drawn shape.",
        "A speech bubble folded from soft paper, built from three gently shaded planes with one "
        "crisp crease and a small folded tail. Pale lavender-grey with a blush accent plane.",
    ),
    (
        "r3-04-curled-sheet",
        "seedream-pro",
        "Curled Sheet",
        "A page mid-curl. Motion and paper-ness without drawing a newspaper.",
        "A single sheet of paper curling softly into an open scroll, seen from the side, with a "
        "warm shaded underside and a soft cast shadow. Cream paper, warm terracotta underside.",
    ),
    (
        "r3-05-two-discs",
        "seedream-pro",
        "Two Discs",
        "The 'buddy' pairing, but as two real paper discs with depth rather than two flat circles.",
        "Two soft paper discs of slightly different sizes overlapping, each with a visible edge "
        "thickness and a soft shadow between them. One warm terracotta, one soft neutral.",
    ),
    (
        "r3-06-crease-stone",
        "seedream-pro",
        "Crease Stone",
        "A soft pebble form with one fold. Calm, tactile, ownable, almost edible.",
        "A soft rounded pebble-like form with one clean crease running across it and a gentle "
        "gradient of shading, fine tactile grain. Warm clay accent color, soft shadow beneath.",
    ),
    # --- Nano Banana Pro: best at bold contained icon composition ---
    (
        "r3-07-seal-fold",
        "nano-banana-pro",
        "Seal & Fold",
        "The Hanko Seal direction refined: a stamped disc with a folded corner pressed into it.",
        "A round hand-stamped ink seal with soft imperfect ink bleed at the edges, containing a "
        "single pressed folded-corner impression at its center. Warm coral ink on ivory.",
    ),
    (
        "r3-08-pouch-face",
        "nano-banana-pro",
        "Pouch Face",
        "The folded pouch, given two dots. Object plus personality at the smallest possible cost.",
        "A folded paper pouch with soft creases and gentle shading, with exactly two tiny dark "
        "dot eyes near the top fold. No mouth. Warm peach paper, soft shadow beneath.",
    ),
    (
        "r3-09-sleeping-seal",
        "nano-banana-pro",
        "Sleeping Seal",
        "A stamped disc holding two closed-eye arcs. Serene rather than cute — good for an "
        "evening briefing.",
        "A round stamped ink disc with soft edges, containing two simple closed-eye arcs pressed "
        "into it, giving a calm sleeping expression. Dusty rose ink, soft ivory ground.",
    ),
    (
        "r3-10-paper-boat",
        "nano-banana-pro",
        "Paper Boat",
        "Universally recognized, warm, and about carrying something to you. Folded paper without "
        "any Japanese reference.",
        "A small folded paper boat, soft and rounded rather than sharp, with visible folds, "
        "gentle shading and a soft shadow beneath. Cream paper with a warm terracotta sail fold.",
    ),
    (
        "r3-11-bookmark-face",
        "nano-banana-pro",
        "Bookmark Face",
        "Ribbon Eyes with more body — a real paper bookmark with weight, a folded tip, and a "
        "calm face.",
        "A soft paper bookmark ribbon with a gently folded tip and visible paper thickness, with "
        "two small dark dot eyes near the top. Warm coral paper, soft shadow.",
    ),
    (
        "r3-12-envelope-lift",
        "nano-banana-pro",
        "Envelope Lift",
        "Something addressed to you, with one corner already lifted. Warm and slightly "
        "conspiratorial.",
        "A soft rounded paper envelope seen flat-on with one corner lifted and curling upward, "
        "revealing a warm accent interior. Soft shading and a gentle cast shadow.",
    ),
    # --- GPT Image 2: warmth and character ---
    (
        "r3-13-peek-curl",
        "gpt-image-2",
        "Peek Curl",
        "Two dot eyes peeking over a curled page edge. The buddy is literally in the paper.",
        "A softly curling sheet of paper with two tiny dark dot eyes peeking over the top of the "
        "curl. Gentle shading, soft grain, warm cream and peach tones.",
    ),
    (
        "r3-14-bag-buddy",
        "gpt-image-2",
        "Bag Buddy",
        "A soft paper bag with a calm face — a made object with a personality, not a mascot.",
        "A small soft paper bag form with a gently crumpled top edge and two tiny dot eyes, "
        "quietly calm. Visible paper grain and soft shading. Warm toasted cream and cocoa.",
    ),
    (
        "r3-15-fold-nest",
        "gpt-image-2",
        "Fold Nest",
        "A cupped paper form holding a small card — the briefing, held for you.",
        "A soft cupped paper form, like a shallow folded nest, holding one small folded card "
        "upright in its center. Gentle shading, soft shadow. Warm sand and blush.",
    ),
    (
        "r3-16-ribbon-loop",
        "gpt-image-2",
        "Ribbon Loop",
        "A single soft loop of paper ribbon. Continuity and return, with real dimension.",
        "A soft paper ribbon looping over itself exactly once, with visible ribbon thickness, "
        "soft shading where it overlaps, and a gentle shadow. Warm coral ribbon on ivory.",
    ),
    (
        "r3-17-stack-cards",
        "gpt-image-2",
        "Card Stack",
        "A small stack with real edges and shadow — a finite, finishable briefing.",
        "A small stack of three soft paper cards with slightly offset rounded corners, visible "
        "edge thickness, and a soft shadow beneath. Top card warm terracotta, others neutral.",
    ),
    # --- Ideogram 4.0 ---
    (
        "r3-18-wax-seal",
        "ideogram-4",
        "Wax Seal",
        "A pressed seal with genuine physical depth. Feels authored and trustworthy.",
        "A soft round wax seal with a pressed impression of a simple folded shape in its center, "
        "slightly irregular edges, soft dimensional shading. Warm terracotta wax on ivory.",
    ),
    (
        "r3-19-stamped-square",
        "ideogram-4",
        "Stamped Square",
        "A hand-stamped rounded square with ink texture and imperfection — the opposite of a "
        "sterile glyph.",
        "An imperfect hand-stamped rounded square with visible ink texture and slightly uneven "
        "edges, containing one soft rounded fold shape. Warm coral ink on warm off-white.",
    ),
    (
        "r3-20-shelter-fold",
        "ideogram-4",
        "Shelter Fold",
        "A folded sheet that reads as a small shelter. Calm, safe, somewhere to sit with the news.",
        "A sheet of paper folded once and stood up like a small tent or shelter, with a shaded "
        "inner face and a soft shadow beneath. Warm sand paper, terracotta interior.",
    ),
    (
        "r3-21-cup-fold",
        "ideogram-4",
        "Cup Fold",
        "A folded paper cup holding a card — the morning ritual, made rather than drawn.",
        "A small folded paper cup with visible diagonal folds, holding one small folded card "
        "upright inside it. Soft shading, gentle shadow. Cream paper, warm accent card.",
    ),
    (
        "r3-22-open-fold",
        "ideogram-4",
        "Open Fold",
        "Two soft planes opening toward you — an object mid-reveal.",
        "A sheet of paper folded down the middle and standing slightly open toward the viewer, "
        "the two planes softly shaded at different values. Warm blush and cream.",
    ),
    # --- Recraft V4.1 Pro: limited allocation, has over-reduced in prior rounds ---
    (
        "r3-23-disc-crease",
        "recraft-pro",
        "Disc Crease",
        "A soft disc with one diagonal fold. The simplest possible tactile object.",
        "A soft rounded paper disc with a single diagonal crease across it, one half catching "
        "slightly more light, with a soft shadow beneath. Warm terracotta and cream.",
    ),
    (
        "r3-24-paper-tag",
        "ideogram-4",
        "Paper Tag",
        "A tag with a punched hole — labelled, filed, kept. Physical and familiar.",
        "A soft rounded paper tag with one punched hole near the top and a gently folded corner, "
        "visible paper thickness and grain, soft shadow. Warm sand and coral.",
    ),
    (
        "r3-25-nested-fold",
        "recraft-pro",
        "Nested Fold",
        "Two folded planes nested together — companionship expressed as craft.",
        "Two soft folded paper planes nested one inside the other, each gently shaded, with a "
        "soft shadow beneath. Warm coral outer plane, cream inner plane.",
    ),
]
