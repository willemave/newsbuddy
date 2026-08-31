# Slate brand rollout — design tokens, buddy placements, dark mode

Date: 2026-08-30 · Branch: `main` · Scope: iOS client only

Outcome of the brand exploration in `docs/brand-exploration-2026-08/`. Two marks were
selected and they do different jobs:

| Asset | Source | Role |
|---|---|---|
| **Ensō · Slate** | `images_r8/r8-03-enso-slate.png` | App icon. Launch screen, Settings. Never emotes. |
| **Reader · Indigo** | `images_r8/r8-10-reader-indigo.png` | The buddy. Appears only where the app speaks to the user. |

Scheme: **Slate on warm paper**. The accent moves from amber to the icon's own slate, and
the neutral ramp moves from cool grey to warm paper.

---

## 1. Design tokens

`Shared/ReaderPalette.swift` is the single source of truth — every SwiftUI and UIKit token
in `DesignTokens.swift` reads from it. **No other file needs a color edit.** That is the whole
token change.

### Light — warm paper

| Token | Before | After | Note |
|---|---|---|---|
| `surfacePrimary` | `#f4f5f7` | `#f8f6f1` | cool grey → warm paper |
| `surfaceSecondary` | `#ffffff` | `#fffcf7` | warm white; pure white reads blue on a warm ground |
| `surfaceTertiary` | `#e8eaee` | `#edeae2` | |
| `surfaceContainer` | `#dcdee4` | `#e3dfd5` | assistant chat bubble |
| `surfaceContainerHigh` | `#cdd1d9` | `#d6d1c5` | |
| `surfaceContainerHighest` | `#bcc1cb` | `#c6c0b2` | |
| `brandPrimary` | `#99610a` | `#3f4c60` | **amber → slate**, from the icon's ring |
| `brandPrimaryStrong` | `#8f5a08` | `#333e50` | |
| `brandSecondary` | `#676c76` | `#6e6a61` | neutral, not a second hue |
| `brandTertiary` | `#6a707b` | `#757067` | |
| `onSurface` | `#1b1e24` | `#22211d` | warm near-black |
| `onSurfaceSecondary` | `#676c76` | `#6e6a61` | |
| `onSurfaceTertiary` | `#6a707b` | `#757067` | |
| `chatUserBubble` | `#efe7d8` | `#efe7d8` | **unchanged** — already warm, fits the new ground |
| `outlineVariant` | `#cdd1d9` | `#dcd7cb` | |
| `borderSubtle` | `#dcdee4` | `#e7e3da` | |
| `borderStrong` | `#aab0bb` | `#b5afa1` | |

### Dark — warm charcoal

The existing dark ramp is cool. It has to warm up with the light ramp or the two modes read
as different products. `brandPrimary` cannot simply carry over: `#3f4c60` on a dark ground is
invisible, so dark mode lifts the same hue rather than inventing a new one.

| Token | Before | After |
|---|---|---|
| `surfacePrimary` | `#131519` | `#171613` |
| `surfaceSecondary` | `#1b1e23` | `#1f1e1a` |
| `surfaceTertiary` | `#21252b` | `#262521` |
| `surfaceContainer` | `#282c33` | `#2e2c27` |
| `surfaceContainerHigh` | `#333841` | `#393731` |
| `surfaceContainerHighest` | `#40454f` | `#46433c` |
| `brandPrimary` | `#e0a33f` | `#93a7c4` — same slate hue, lifted |
| `brandPrimaryStrong` | `#c98a2c` | `#aabdd8` — brighter, since "strong" means more emphasis |
| `brandSecondary` | `#8f959f` | `#9a958a` |
| `brandTertiary` | `#818792` | `#8d887e` |
| `onSurface` | `#e5e7ec` | `#ece8e0` |
| `onSurfaceSecondary` | `#8f959f` | `#9a958a` |
| `onSurfaceTertiary` | `#818792` | `#8d887e` |
| `chatUserBubble` | `#2a2417` | `#2b2620` |
| `outlineVariant` | `#3b404a` | `#413e37` |
| `borderSubtle` | `#282c33` | `#2e2c27` |
| `borderStrong` | `#5b626d` | `#635e54` |

### Contrast verification

Measured against each mode's `surfacePrimary`; all pass WCAG AA for body text.

| | light | dark |
|---|---|---|
| `onSurface` | 14.92 | 14.81 |
| `onSurfaceSecondary` | 4.99 | 6.07 |
| `onSurfaceTertiary` | 4.55 | 5.13 |
| `brandPrimary` | 8.05 | 7.38 |
| `brandPrimaryStrong` | 9.99 | 9.46 |

**Known constraint.** `brandPrimary` against `onSurface` is only 1.85 (light) / 2.01 (dark).
Inline story links in the Briefing column therefore depend on underline **and** semibold
weight, not hue alone, to separate from body prose. Both are already applied
(`BriefingAttributedTextBuilder`), so this is acceptable — but any future change that drops the
underline breaks link legibility.

---

## 2. Assets

Built by `docs/brand-exploration-2026-08/build_assets.py` into `build_assets/`.

- **`AppIcon`** — `AppIconSource-1024.png`, the untouched ensō render including its cream
  field. App icons are filled squares; no alpha.
- **`AppMark`** (1x/2x/3x, 72pt) — same render, for the Settings row and launch screen.
  Keeps its field, because that is what an app icon looks like wherever it is shown.
- **`BuddyMark`** / **`BuddyMarkDark`** (1x/2x/3x, 64pt) — background cut to real alpha so the
  buddy can sit inside small circular buttons on any surface. The dark variant lifts the body
  from `#383061` to `#8b7fd0`; at `#383061` the buddy disappears on a warm charcoal ground.
  Spectacle gold is protected during the recolor and stays put.

Asset catalog entries use `Contents.json` `appearances` so `Image("BuddyMark")` resolves the
dark variant automatically — no `@Environment(\.colorScheme)` branching in view code.

---

## 3. Buddy placements

Four, in order of how much they change existing behaviour:

1. **Launch screen** — the ensō app icon, centered on `surfacePrimary`. Replaces whatever the
   app currently shows while restoring session.
2. **Settings** — the ensō app icon in a header row alongside the app name and version.
3. **Chat composer, leading slot** — the buddy replaces the `plus` glyph on the existing menu
   button in `ChatComposerDock`. The menu wiring (Chat History / Model / Council / Deep
   Research) is untouched; only the label changes. This is the buddy's home in the chat.
4. **Detail screens** — the buddy replaces the `sparkles` glyph in `DetailActionBar`, which is
   the action that opens chat about the article. Same action, a face instead of a symbol.

Deliberately **not** doing: an avatar beside every assistant message. The real chat has no
avatar column, and adding one is a layout change rather than a re-skin.

---

## 4. Onboarding introduction

The buddy introduces itself once, on first run, before the source picker. It should read as a
greeting rather than a feature tour: one screen, the buddy, three short lines, one button.

Copy:

> **Hello. I'm your buddy.**
> I read across your sources every morning and write you one briefing.
> When something needs a second look, ask me — I'll show my working.
>
> `Nice to meet you`

Rationale: the character earns its place by explaining what the app does in the first person,
which is also the clearest one-sentence description of the product. It appears large here and
small everywhere else, so the first meeting is the only time it gets the full frame.

---

## 5. Order of work

1. Palette (`ReaderPalette.swift`) — one file, affects everything, verify in both modes first.
2. Assets into `Assets.xcassets` with `appearances` variants.
3. Composer + detail-bar buddy swaps (smallest, most contained view edits).
4. Settings header row and launch screen.
5. Onboarding greeting step.
6. Build, then screenshot every touched surface in light and dark.

## 6. Risks

- The amber→slate switch touches every screen at once; there is no partial rollout. Mitigated
  by the palette being one file and fully revertible.
- Dark mode has not been visually audited recently. Step 6 must screenshot dark explicitly,
  not assume it follows.
- The ensō is a raster brush texture. It is fine at icon sizes but cannot be scaled into a
  large hero or recolored cleanly; a vector redraw is still outstanding before any marketing use.
