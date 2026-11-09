mod agent;
mod artifacts;
mod browser;
mod finalizer;
mod handler;
mod source;

/// Every file the E2B template installs as the Deck Kit. Template revisions and private draft
/// fingerprints both hash this list, so a kit change cannot reuse an older template or draft.
pub(crate) const DECK_KIT_FILES: [(&str, &[u8]); 15] = [
    (
        "rust/assets/deckkit/README.md",
        include_bytes!("../../../../assets/deckkit/README.md"),
    ),
    (
        "rust/assets/deckkit/bin/deckkit",
        include_bytes!("../../../../assets/deckkit/bin/deckkit"),
    ),
    (
        "rust/assets/deckkit/bin/inline.mjs",
        include_bytes!("../../../../assets/deckkit/bin/inline.mjs"),
    ),
    (
        "rust/assets/deckkit/bin/look.mjs",
        include_bytes!("../../../../assets/deckkit/bin/look.mjs"),
    ),
    (
        "rust/assets/deckkit/bin/math.mjs",
        include_bytes!("../../../../assets/deckkit/bin/math.mjs"),
    ),
    (
        "rust/assets/deckkit/bin/mermaid.mjs",
        include_bytes!("../../../../assets/deckkit/bin/mermaid.mjs"),
    ),
    (
        "rust/assets/deckkit/bin/svg.mjs",
        include_bytes!("../../../../assets/deckkit/bin/svg.mjs"),
    ),
    (
        "rust/assets/deckkit/deck.mplstyle",
        include_bytes!("../../../../assets/deckkit/deck.mplstyle"),
    ),
    (
        "rust/assets/deckkit/install.sh",
        include_bytes!("../../../../assets/deckkit/install.sh"),
    ),
    (
        "rust/assets/deckkit/kit-init.js",
        include_bytes!("../../../../assets/deckkit/kit-init.js"),
    ),
    (
        "rust/assets/deckkit/kit.css",
        include_bytes!("../../../../assets/deckkit/kit.css"),
    ),
    (
        "rust/assets/deckkit/package-lock.json",
        include_bytes!("../../../../assets/deckkit/package-lock.json"),
    ),
    (
        "rust/assets/deckkit/package.json",
        include_bytes!("../../../../assets/deckkit/package.json"),
    ),
    (
        "rust/assets/deckkit/skeleton.html",
        include_bytes!("../../../../assets/deckkit/skeleton.html"),
    ),
    (
        "rust/assets/deckkit/theme.mjs",
        include_bytes!("../../../../assets/deckkit/theme.mjs"),
    ),
];

#[cfg(test)]
mod deck_kit_tests;

pub use handler::{LearningDeckTaskBuildError, LearningDeckTaskExecutor};
