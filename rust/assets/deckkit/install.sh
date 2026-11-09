#!/usr/bin/env bash
# Install the Deck Kit into /opt/deckkit (run as root at image build time).
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
DEST=/opt/deckkit
mkdir -p "$DEST/fonts"
cp -r "$SRC"/kit.css "$SRC"/kit-init.js "$SRC"/deck.mplstyle "$SRC"/skeleton.html "$SRC"/README.md "$SRC"/theme.mjs \
  "$SRC"/package.json "$SRC"/package-lock.json "$SRC"/bin "$DEST"/
cd "$DEST"
npm ci --no-audit --no-fund >/dev/null
F=node_modules/@fontsource
cp $F/newsreader/files/newsreader-latin-500-normal.woff2 fonts/newsreader-500.woff2
cp $F/newsreader/files/newsreader-latin-500-italic.woff2 fonts/newsreader-500-italic.woff2
cp $F/inter/files/inter-latin-400-normal.woff2 fonts/inter-400.woff2
cp $F/inter/files/inter-latin-600-normal.woff2 fonts/inter-600.woff2
cp $F/jetbrains-mono/files/jetbrains-mono-latin-400-normal.woff2 fonts/jetbrains-mono-400.woff2
cp $F/jetbrains-mono/files/jetbrains-mono-latin-600-normal.woff2 fonts/jetbrains-mono-600.woff2
chmod +x bin/deckkit && ln -sf "$DEST/bin/deckkit" /usr/local/bin/deckkit
chmod -R a+rX "$DEST"
deckkit help >/dev/null
