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

    /// How far the pills and player have slid up out of view. Read only by the
    /// header's hide modifier, so per-frame writes never reach the pages.
    private var pinnedHideByLens: [String: CGFloat] = [:]
    /// Changes only when a lens crosses the half-hidden mark; gates hit
    /// testing on the hidden header. Pages must not read it: anything that
    /// changes a page's layout mid-scroll re-lays out the whole text page.
    private(set) var pinnedHiddenLensKeys: Set<String> = []
    @ObservationIgnored private var pinnedHeightByLens: [String: CGFloat] = [:]
    @ObservationIgnored private var mastheadHeightByLens: [String: CGFloat] = [:]
    @ObservationIgnored private var lastScrolledByLens: [String: CGFloat] = [:]
    @ObservationIgnored private var upwardTravelByLens: [String: CGFloat] = [:]

    func pinnedHide(forLens key: String?) -> CGFloat {
        key.flatMap { pinnedHideByLens[$0] } ?? 0
    }

    func isPinnedChromeHidden(forLens key: String?) -> Bool {
        key.map { pinnedHiddenLensKeys.contains($0) } ?? false
    }

    /// Feeds one scroll sample. Past the masthead the pinned chrome rides the
    /// scroll off screen; any deliberate upward scroll brings it straight back
    /// (animated with `reveal`), while the masthead itself only returns at the
    /// top because it tracks the offset directly.
    func trackPinnedChrome(
        forLens key: String,
        scrolled: CGFloat,
        mastheadHeight: CGFloat,
        pinnedHeight: CGFloat,
        reveal: Animation?
    ) {
        pinnedHeightByLens[key] = pinnedHeight
        mastheadHeightByLens[key] = mastheadHeight
        let step = briefingPinnedChromeStep(
            hide: pinnedHide(forLens: key),
            upwardTravel: upwardTravelByLens[key] ?? 0,
            lastScrolled: lastScrolledByLens[key],
            scrolled: scrolled,
            mastheadHeight: mastheadHeight,
            pinnedHeight: pinnedHeight
        )
        lastScrolledByLens[key] = scrolled
        upwardTravelByLens[key] = step.upwardTravel
        if step.revealed {
            withAnimation(reveal) { setPinnedHide(step.hide, forLens: key, height: pinnedHeight) }
        } else {
            setPinnedHide(step.hide, forLens: key, height: pinnedHeight)
        }
    }

    /// Called when scrolling comes to rest: a part-hidden chrome finishes
    /// whichever way it is closer to.
    func settlePinnedChrome(forLens key: String, animation: Animation?) {
        guard let height = pinnedHeightByLens[key],
              let scrolled = lastScrolledByLens[key],
              let target = briefingPinnedChromeSettleTarget(
                  hide: pinnedHide(forLens: key),
                  scrolled: scrolled,
                  mastheadHeight: mastheadHeightByLens[key] ?? 0,
                  pinnedHeight: height
              ) else { return }
        withAnimation(animation) { setPinnedHide(target, forLens: key, height: height) }
    }

    func revealPinnedChrome(forLens key: String) {
        upwardTravelByLens[key] = 0
        setPinnedHide(0, forLens: key, height: pinnedHeightByLens[key] ?? 0)
    }

    private func setPinnedHide(_ hide: CGFloat, forLens key: String, height: CGFloat) {
        if pinnedHideByLens[key] ?? 0 != hide {
            pinnedHideByLens[key] = hide
        }
        let hidden = height > 0 && hide >= height / 2
        if hidden != pinnedHiddenLensKeys.contains(key) {
            if hidden {
                pinnedHiddenLensKeys.insert(key)
            } else {
                pinnedHiddenLensKeys.remove(key)
            }
        }
    }
}

struct BriefingPinnedChromeStep: Equatable {
    var hide: CGFloat
    var upwardTravel: CGFloat
    /// True when this sample snapped the chrome back into view.
    var revealed: Bool
}

/// Upward travel that counts as "scrolling back", so finger jitter at rest
/// does not flash the chrome back in.
let briefingPinnedChromeRevealDistance: CGFloat = 8

/// Quick-return step for the pinned pills and player. `scrolled` is distance
/// from the top of content, clamped to the scrollable range. The chrome can
/// only be hidden by as much as the reader has scrolled past the masthead, so
/// near the top it always sits on the text it insets.
func briefingPinnedChromeStep(
    hide: CGFloat,
    upwardTravel: CGFloat,
    lastScrolled: CGFloat?,
    scrolled: CGFloat,
    mastheadHeight: CGFloat,
    pinnedHeight: CGFloat
) -> BriefingPinnedChromeStep {
    let limit = min(max(pinnedHeight, 0), max(scrolled - mastheadHeight, 0))
    guard let lastScrolled else {
        return BriefingPinnedChromeStep(hide: min(hide, limit), upwardTravel: 0, revealed: false)
    }
    let delta = scrolled - lastScrolled
    if delta > 0 {
        return BriefingPinnedChromeStep(hide: min(hide + delta, limit), upwardTravel: 0, revealed: false)
    }
    if delta < 0 {
        let travel = upwardTravel - delta
        if hide > 0, travel >= briefingPinnedChromeRevealDistance {
            return BriefingPinnedChromeStep(hide: 0, upwardTravel: 0, revealed: true)
        }
        return BriefingPinnedChromeStep(hide: min(hide, limit), upwardTravel: travel, revealed: false)
    }
    return BriefingPinnedChromeStep(hide: min(hide, limit), upwardTravel: upwardTravel, revealed: false)
}

/// Where a part-hidden chrome should come to rest, or nil if it already is at
/// rest. It can only finish hiding when the reader is far enough past the
/// masthead for the whole chrome to be gone.
func briefingPinnedChromeSettleTarget(
    hide: CGFloat,
    scrolled: CGFloat,
    mastheadHeight: CGFloat,
    pinnedHeight: CGFloat
) -> CGFloat? {
    guard pinnedHeight > 0, hide > 0, hide < pinnedHeight else { return nil }
    let canHideFully = scrolled - mastheadHeight >= pinnedHeight
    return hide >= pinnedHeight / 2 && canHideFully ? pinnedHeight : 0
}

/// Slides the whole header up by the lens's pinned-chrome hide amount. The
/// masthead has already collapsed to nothing by then, so this carries off the
/// pills and player. Offset leaves layout alone, so the measured chrome height
/// and every page inset stay put; the mask clips at the header's own top edge
/// so the chrome slides under the status bar rather than over the clock.
private struct BriefingPinnedChromeHideModifier: ViewModifier {
    var model: BriefingChromeCollapseModel
    var lensKey: String?

    func body(content: Content) -> some View {
        let hide = model.pinnedHide(forLens: lensKey)
        content
            .offset(y: -hide)
            .mask(alignment: .top) {
                Rectangle().padding(.bottom, -400)
            }
            // Hidden chrome must not swallow taps on the text it uncovered.
            .allowsHitTesting(!model.isPinnedChromeHidden(forLens: lensKey))
    }
}

/// Soft backing behind the pinned chrome, standing in for the system scroll
/// edge: text passing under the pills is blurred and dimmed, fading out just
/// below them. It belongs to the header, so it slides away with the pills
/// for free instead of resizing the page's scroll edge. Text only passes
/// under the chrome once the masthead has fully collapsed (before that the
/// text rides the chrome's bottom edge), so the backing fades in over the
/// last stretch of the collapse; at rest it would read as a lighter band.
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
            ZStack {
                Rectangle().fill(.ultraThinMaterial)
                Color.surfacePrimary.opacity(0.45)
            }
            .mask {
                LinearGradient(
                    stops: [
                        .init(color: .black, location: 0),
                        .init(color: .black, location: max(1 - Self.fadeHeight * 2 / max(height, 1), 0)),
                        .init(color: .black.opacity(0), location: 1),
                    ],
                    startPoint: .top,
                    endPoint: .bottom
                )
            }
            .frame(height: height)
        }
        .opacity(visibility)
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }

    /// How far the backing's fade extends below the header; hiding must
    /// travel this much further or the tail lingers as a faint band.
    static let fadeHeight: CGFloat = 18
    private static let fadeInDistance: CGFloat = 24
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
