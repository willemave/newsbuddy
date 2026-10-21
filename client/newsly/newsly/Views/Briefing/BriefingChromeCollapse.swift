import Observation
import SwiftUI

/// Per-lens chrome collapse driven directly by scroll position, so the
/// masthead tracks the finger 1:1 instead of snapping at a
/// threshold. This remains view-owned interaction state rather than feature
/// state, so per-frame geometry writes only invalidate the small chrome views
/// that read it — never the pager.
@MainActor
@Observable
final class BriefingChromeCollapseModel {
    private var collapseByLens: [String: CGFloat] = [:]

    func collapse(forLens key: String?) -> CGFloat {
        key.flatMap { collapseByLens[$0] } ?? 0
    }

    func setCollapse(_ value: CGFloat, forLens key: String) {
        guard collapseByLens[key] != value else { return }
        collapseByLens[key] = value
    }

    func resetCollapse(forLens key: String) {
        collapseByLens.removeValue(forKey: key)
        revealPinnedChrome(forLens: key)
    }

    // MARK: Pinned chrome quick return

    /// Lenses whose pills and player are slid off screen. A discrete state,
    /// changed a few times per gesture, so the slide is one animation rather
    /// than a per-frame offset that trailed the text by a frame.
    private(set) var pinnedHiddenLensKeys: Set<String> = []
    @ObservationIgnored private var lastScrolledByLens: [String: CGFloat] = [:]
    @ObservationIgnored private var travelByLens: [String: CGFloat] = [:]

    func isPinnedChromeHidden(forLens key: String?) -> Bool {
        key.map { pinnedHiddenLensKeys.contains($0) } ?? false
    }

    /// Feeds one scroll sample. Scrolling down with text under the chrome
    /// slides it away; any deliberate upward scroll brings it straight back.
    /// The masthead is separate: it tracks the offset, so it returns only at
    /// the top.
    func trackPinnedChrome(
        forLens key: String,
        scrolled: CGFloat,
        mastheadHeight: CGFloat,
        pinnedHeight: CGFloat
    ) {
        let step = briefingPinnedChromeStep(
            hidden: isPinnedChromeHidden(forLens: key),
            travel: travelByLens[key] ?? 0,
            lastScrolled: lastScrolledByLens[key],
            scrolled: scrolled,
            mastheadHeight: mastheadHeight,
            pinnedHeight: pinnedHeight
        )
        lastScrolledByLens[key] = scrolled
        travelByLens[key] = step.travel
        setPinnedHidden(step.hidden, forLens: key)
    }

    func revealPinnedChrome(forLens key: String) {
        travelByLens[key] = 0
        setPinnedHidden(false, forLens: key)
    }

    private func setPinnedHidden(_ hidden: Bool, forLens key: String) {
        guard hidden != pinnedHiddenLensKeys.contains(key) else { return }
        if hidden {
            pinnedHiddenLensKeys.insert(key)
        } else {
            pinnedHiddenLensKeys.remove(key)
        }
    }
}

struct BriefingPinnedChromeStep: Equatable {
    var hidden: Bool
    /// Signed distance scrolled since the last change of direction.
    var travel: CGFloat
}

/// Downward travel that hides the chrome, and upward travel that brings it
/// back. Reveal is shorter so the pills answer the moment the reader reverses.
let briefingPinnedChromeHideDistance: CGFloat = 24
let briefingPinnedChromeRevealDistance: CGFloat = 8

/// Quick-return step for the pinned pills and player. `scrolled` is distance
/// from the top of content, clamped to the scrollable range, so a bounce at
/// the end does not read as reversing. The chrome is always shown until the
/// reader is past both the masthead and the chrome itself, so it never leaves
/// an empty band where it was insetting the text.
func briefingPinnedChromeStep(
    hidden: Bool,
    travel: CGFloat,
    lastScrolled: CGFloat?,
    scrolled: CGFloat,
    mastheadHeight: CGFloat,
    pinnedHeight: CGFloat
) -> BriefingPinnedChromeStep {
    guard scrolled > mastheadHeight + max(pinnedHeight, 0) else {
        return BriefingPinnedChromeStep(hidden: false, travel: 0)
    }
    guard let lastScrolled else {
        return BriefingPinnedChromeStep(hidden: hidden, travel: 0)
    }
    let delta = scrolled - lastScrolled
    guard delta != 0 else { return BriefingPinnedChromeStep(hidden: hidden, travel: travel) }
    let sameDirection = (delta > 0) == (travel > 0) && travel != 0
    let nextTravel = sameDirection ? travel + delta : delta
    if nextTravel >= briefingPinnedChromeHideDistance {
        return BriefingPinnedChromeStep(hidden: true, travel: nextTravel)
    }
    if nextTravel <= -briefingPinnedChromeRevealDistance {
        return BriefingPinnedChromeStep(hidden: false, travel: nextTravel)
    }
    return BriefingPinnedChromeStep(hidden: hidden, travel: nextTravel)
}

/// Slides the whole header fully off the top of the screen, through the
/// status bar like the text, and back. The masthead has already collapsed to
/// nothing by then, so this carries off the pills and player. Offset leaves
/// layout alone, so the measured chrome height and every page inset stay put.
private struct BriefingPinnedChromeHideModifier: ViewModifier {
    var model: BriefingChromeCollapseModel
    var lensKey: String?
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    /// Distance from the top of the screen to the header's bottom edge.
    @State private var travelOut: CGFloat = 0

    func body(content: Content) -> some View {
        let hidden = model.isPinnedChromeHidden(forLens: lensKey)
        content
            .onGeometryChange(for: CGFloat.self) { proxy in
                proxy.frame(in: .global).maxY
            } action: { _, maxY in
                travelOut = maxY
            }
            .offset(y: hidden ? -(travelOut + Self.overshoot) : 0)
            .animation(
                AppMotion.respectingReduceMotion(reduceMotion, .smooth(duration: 0.32)),
                value: hidden
            )
            // Hidden chrome must not swallow taps on the text it uncovered.
            .allowsHitTesting(!hidden)
            .accessibilityHidden(hidden)
    }

    /// Clears the backing's fade below the header too.
    private static let overshoot: CGFloat = BriefingPinnedChromeBacking.fadeHeight + 4
}

/// Light fade behind the pinned chrome: text passing under the pills dims a
/// little, then clears just below them. Translucent, never a solid band. It
/// belongs to the header, so it slides away with the pills. Text only passes
/// under the chrome once the masthead has fully collapsed (before that it
/// rides the chrome's bottom edge), so it fades in over the end of that.
struct BriefingPinnedChromeBacking: View {
    var model: BriefingChromeCollapseModel
    var lensKey: String?
    var mastheadHeight: CGFloat

    private var visibility: CGFloat {
        guard lensKey != nil, mastheadHeight > 0 else { return 0 }
        let remaining = mastheadHeight - model.collapse(forLens: lensKey)
        return min(max(1 - remaining / Self.fadeInDistance, 0), 1)
    }

    var body: some View {
        GeometryReader { proxy in
            let height = proxy.size.height + Self.fadeHeight
            LinearGradient(
                stops: [
                    .init(color: Color.surfacePrimary.opacity(0.6), location: 0),
                    .init(color: Color.surfacePrimary.opacity(0.45), location: max(1 - Self.fadeHeight * 2 / max(height, 1), 0)),
                    .init(color: Color.surfacePrimary.opacity(0), location: 1),
                ],
                startPoint: .top,
                endPoint: .bottom
            )
            .frame(height: height)
        }
        .opacity(visibility)
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }

    /// How far the fade extends below the header.
    static let fadeHeight: CGFloat = 20
    private static let fadeInDistance: CGFloat = 24
}

/// The same light fade under the status bar, so text scrolling up there no
/// longer runs straight into the clock.
struct BriefingStatusBarFade: View {
    var height: CGFloat

    var body: some View {
        LinearGradient(
            colors: [Color.surfacePrimary.opacity(0.6), Color.surfacePrimary.opacity(0)],
            startPoint: .top,
            endPoint: .bottom
        )
        .frame(height: height + 12)
        .frame(maxHeight: .infinity, alignment: .top)
        .ignoresSafeArea(edges: .top)
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }
}

extension View {
    func briefingPinnedChromeHide(
        model: BriefingChromeCollapseModel,
        lensKey: String?
    ) -> some View {
        modifier(BriefingPinnedChromeHideModifier(model: model, lensKey: lensKey))
    }
}

/// Hosts one collapsible piece of the briefing header. The content keeps its
/// natural height (measured out through `naturalHeight`); the slot shrinks the
/// visible window from the top so the piece slides up and out, clipped, in
/// lockstep with the scroll offset `shrink` derives from.
struct BriefingCollapsibleChromeSlot<Content: View>: View {
    var model: BriefingChromeCollapseModel
    var lensKey: String?
    var shrink: (CGFloat) -> CGFloat
    @Binding var naturalHeight: CGFloat
    @ViewBuilder var content: () -> Content

    var body: some View {
        let collapse = model.collapse(forLens: lensKey)
        let shrinkAmount = min(max(shrink(collapse), 0), naturalHeight)
        let progress = naturalHeight > 0 ? shrinkAmount / naturalHeight : 0

        content()
            // Keep the content at its ideal height so the shrinking frame
            // clips it instead of re-laying-out text every frame.
            .fixedSize(horizontal: false, vertical: true)
            .onGeometryChange(for: CGFloat.self) { proxy in
                proxy.size.height
            } action: { _, height in
                guard height > 0, abs(height - naturalHeight) > 0.5 else { return }
                naturalHeight = height
            }
            .opacity(1 - progress)
            .frame(
                height: shrinkAmount > 0 ? naturalHeight - shrinkAmount : nil,
                alignment: .bottom
            )
            .clipped()
            .allowsHitTesting(shrinkAmount < 1)
    }
}

private struct BriefingExpandedChromeHeightModifier: ViewModifier {
    var model: BriefingChromeCollapseModel
    var lensKey: String?
    let mastheadHeight: CGFloat
    /// Height given up by pinned chrome that is not scroll-driven (the
    /// minimized player), added back so the reported height stays expanded.
    let additionalShrink: CGFloat
    @Binding var expandedHeight: CGFloat

    func body(content: Content) -> some View {
        let collapse = model.collapse(forLens: lensKey)
        let mastheadShrink = min(max(collapse, 0), mastheadHeight)
        let totalShrink = mastheadShrink + max(additionalShrink, 0)

        content
            .onGeometryChange(for: CGFloat.self) { proxy in
                proxy.size.height + totalShrink
            } action: { _, height in
                guard height > 0, abs(height - expandedHeight) > 0.5 else { return }
                expandedHeight = height
            }
    }
}

extension View {
    func measureBriefingExpandedChromeHeight(
        model: BriefingChromeCollapseModel,
        lensKey: String?,
        mastheadHeight: CGFloat,
        additionalShrink: CGFloat = 0,
        expandedHeight: Binding<CGFloat>
    ) -> some View {
        modifier(
            BriefingExpandedChromeHeightModifier(
                model: model,
                lensKey: lensKey,
                mastheadHeight: mastheadHeight,
                additionalShrink: additionalShrink,
                expandedHeight: expandedHeight
            )
        )
    }
}
