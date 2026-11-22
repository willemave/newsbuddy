import Observation
import QuartzCore
import SwiftUI

/// Per-lens chrome collapse driven directly by scroll position, so the
/// header tracks the finger 1:1 instead of snapping at a threshold. This
/// remains view-owned interaction state rather than feature state, so
/// per-frame geometry writes only invalidate the small chrome modifiers that
/// read it — never the pager or the header's content.
@MainActor
@Observable
final class BriefingChromeCollapseModel {
    private var collapseByLens: [String: CGFloat] = [:]

    init(now: @escaping () -> TimeInterval = CACurrentMediaTime) {
        self.now = now
    }

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

    // MARK: Pinned chrome

    /// How far the pills and player have slid up past the collapsed masthead.
    /// Scrolling down carries them off with the text, so masthead and pills
    /// leave as one continuous motion; an upward scroll brings them back.
    private var pinnedHideByLens: [String: CGFloat] = [:]
    /// Slide that takes the pinned chrome fully off the top of the screen.
    private(set) var pinnedHideDistance: CGFloat = 0
    @ObservationIgnored private let now: () -> TimeInterval
    @ObservationIgnored private var lastScrolledByLens: [String: CGFloat] = [:]
    @ObservationIgnored private var upwardTravelByLens: [String: CGFloat] = [:]
    @ObservationIgnored private var movingDownByLens: [String: Bool] = [:]
    @ObservationIgnored private var ceilingByLens: [String: CGFloat] = [:]
    /// While a reveal or settle slide runs, scroll samples must not overwrite
    /// its target: an unanimated write would snap the chrome mid-slide.
    @ObservationIgnored private var slideEndsAtByLens: [String: TimeInterval] = [:]

    func pinnedHide(forLens key: String?) -> CGFloat {
        key.flatMap { pinnedHideByLens[$0] } ?? 0
    }

    func isPinnedChromeHidden(forLens key: String?) -> Bool {
        pinnedHideDistance > 0 && pinnedHide(forLens: key) >= pinnedHideDistance - 0.5
    }

    /// Feeds one scroll sample. Past the masthead the pills ride the text off
    /// screen; any deliberate upward scroll slides them straight back. The
    /// masthead is separate: it tracks the offset, so it returns only at the
    /// top.
    func trackPinnedChrome(
        forLens key: String,
        scrolled: CGFloat,
        mastheadHeight: CGFloat,
        reduceMotion: Bool
    ) {
        let lastScrolled = lastScrolledByLens[key]
        let step = briefingPinnedChromeStep(
            hide: pinnedHide(forLens: key),
            upwardTravel: upwardTravelByLens[key] ?? 0,
            lastScrolled: lastScrolled,
            scrolled: scrolled,
            mastheadHeight: mastheadHeight,
            hideDistance: pinnedHideDistance
        )
        lastScrolledByLens[key] = scrolled
        upwardTravelByLens[key] = step.upwardTravel
        ceilingByLens[key] = briefingPinnedChromeHideCeiling(
            scrolled: scrolled,
            mastheadHeight: mastheadHeight,
            hideDistance: pinnedHideDistance
        )
        if let lastScrolled, scrolled != lastScrolled {
            movingDownByLens[key] = scrolled > lastScrolled
        }
        if step.revealed {
            slidePinnedChrome(to: 0, forLens: key, reduceMotion: reduceMotion)
        } else if !isSliding(key) {
            setPinnedHide(step.hide, forLens: key)
        }
    }

    /// Called when scrolling comes to rest, so the pills never park half off
    /// screen: they finish leaving if the reader was heading down with room
    /// to hide them, and come back otherwise.
    func settlePinnedChrome(forLens key: String, reduceMotion: Bool) {
        guard let target = briefingPinnedChromeSettleTarget(
            hide: pinnedHide(forLens: key),
            ceiling: ceilingByLens[key] ?? 0,
            hideDistance: pinnedHideDistance,
            movingDown: movingDownByLens[key] ?? false
        ) else { return }
        slidePinnedChrome(to: target, forLens: key, reduceMotion: reduceMotion)
    }

    func revealPinnedChrome(forLens key: String, reduceMotion: Bool? = nil) {
        upwardTravelByLens[key] = 0
        guard let reduceMotion else {
            slideEndsAtByLens.removeValue(forKey: key)
            setPinnedHide(0, forLens: key)
            return
        }
        slidePinnedChrome(to: 0, forLens: key, reduceMotion: reduceMotion)
    }

    private func slidePinnedChrome(to value: CGFloat, forLens key: String, reduceMotion: Bool) {
        guard pinnedHide(forLens: key) != value else { return }
        let animation = AppMotion.respectingReduceMotion(
            reduceMotion,
            .smooth(duration: Self.slideDuration)
        )
        slideEndsAtByLens[key] = now() + (reduceMotion ? 0 : Self.slideDuration)
        withAnimation(animation) {
            setPinnedHide(value, forLens: key)
        }
    }

    private func isSliding(_ key: String) -> Bool {
        guard let endsAt = slideEndsAtByLens[key] else { return false }
        guard now() < endsAt else {
            slideEndsAtByLens.removeValue(forKey: key)
            return false
        }
        return true
    }

    /// Set by the header whenever its geometry changes, scrolling or not.
    /// Chrome that was fully hidden stays fully hidden when its height
    /// changes (the player appearing, say), instead of leaving a sliver.
    func setPinnedHideDistance(_ distance: CGFloat) {
        let distance = max(distance, 0)
        guard distance != pinnedHideDistance else { return }
        let previous = pinnedHideDistance
        pinnedHideDistance = distance
        for (key, hide) in pinnedHideByLens {
            let wasHidden = previous > 0 && hide >= previous - 0.5
            setPinnedHide(wasHidden ? distance : min(hide, distance), forLens: key)
        }
    }

    private func setPinnedHide(_ hide: CGFloat, forLens key: String) {
        guard pinnedHideByLens[key] ?? 0 != hide else { return }
        pinnedHideByLens[key] = hide
    }

    static let slideDuration: TimeInterval = 0.28
}

struct BriefingPinnedChromeStep: Equatable {
    var hide: CGFloat
    /// Distance scrolled up since the reader last moved down.
    var upwardTravel: CGFloat
    /// True when this sample slides the chrome back into view.
    var revealed: Bool
}

/// Upward travel that counts as scrolling back, so finger jitter at rest does
/// not bring the chrome back in.
let briefingPinnedChromeRevealDistance: CGFloat = 8

/// Most the pills can be hidden: only as far as the reader has scrolled past
/// the masthead, so near the top they always sit on the text they inset.
func briefingPinnedChromeHideCeiling(
    scrolled: CGFloat,
    mastheadHeight: CGFloat,
    hideDistance: CGFloat
) -> CGFloat {
    min(max(scrolled - mastheadHeight, 0), max(hideDistance, 0))
}

/// Quick-return step for the pinned pills and player. `scrolled` is distance
/// from the top of content, clamped to the scrollable range, so a bounce at
/// the end does not read as reversing. Downward scrolling hides by exactly the
/// distance scrolled, so the pills leave attached to the text; upward
/// scrolling past the reveal distance brings them back in one slide.
func briefingPinnedChromeStep(
    hide: CGFloat,
    upwardTravel: CGFloat,
    lastScrolled: CGFloat?,
    scrolled: CGFloat,
    mastheadHeight: CGFloat,
    hideDistance: CGFloat
) -> BriefingPinnedChromeStep {
    let ceiling = briefingPinnedChromeHideCeiling(
        scrolled: scrolled,
        mastheadHeight: mastheadHeight,
        hideDistance: hideDistance
    )
    guard let lastScrolled else {
        return BriefingPinnedChromeStep(hide: min(hide, ceiling), upwardTravel: 0, revealed: false)
    }
    let delta = scrolled - lastScrolled
    if delta > 0 {
        return BriefingPinnedChromeStep(hide: min(hide + delta, ceiling), upwardTravel: 0, revealed: false)
    }
    let travel = upwardTravel - delta
    let held = min(hide, ceiling)
    if held > 0, travel >= briefingPinnedChromeRevealDistance {
        return BriefingPinnedChromeStep(hide: 0, upwardTravel: travel, revealed: true)
    }
    return BriefingPinnedChromeStep(hide: held, upwardTravel: travel, revealed: false)
}

/// Where part-hidden chrome finishes when scrolling stops, or nil when it is
/// already fully in or out.
func briefingPinnedChromeSettleTarget(
    hide: CGFloat,
    ceiling: CGFloat,
    hideDistance: CGFloat,
    movingDown: Bool
) -> CGFloat? {
    guard hideDistance > 0, hide > 0, hide < hideDistance else { return nil }
    return movingDown && ceiling >= hideDistance ? hideDistance : 0
}

/// Slide that carries the pinned chrome from its resting place (below the
/// status bar) fully off the top of the screen, past its fade.
func briefingPinnedChromeHideDistance(screenTop: CGFloat, pinnedHeight: CGFloat) -> CGFloat {
    max(screenTop, 0) + max(pinnedHeight, 0) + BriefingPinnedChromeBacking.fadeHeight + 4
}

/// Moves the whole header with the scroll: up by the collapsed masthead, then
/// on by the pinned chrome's hide, through the status bar like the text.
/// Render-only offsets, so steady scrolling never re-lays out the header, and
/// the measured chrome height and every page inset stay put. The two travels
/// are separate offsets so per-frame masthead writes never interrupt an
/// animated pill slide.
private struct BriefingHeaderScrollOffsetModifier: ViewModifier {
    var model: BriefingChromeCollapseModel
    var lensKey: String?
    var mastheadHeight: CGFloat
    @Environment(\.displayScale) private var displayScale

    func body(content: Content) -> some View {
        let shrink = min(max(model.collapse(forLens: lensKey), 0), max(mastheadHeight, 0))
        let hide = model.pinnedHide(forLens: lensKey)
        let hidden = model.isPinnedChromeHidden(forLens: lensKey)
        content
            .offset(y: -pixelAligned(shrink))
            .offset(y: -pixelAligned(hide))
            // Hidden chrome must not swallow taps on the text it uncovered.
            .allowsHitTesting(!hidden)
            .accessibilityHidden(hidden)
    }

    /// Keeps the pills' text from shimmering while it moves.
    private func pixelAligned(_ value: CGFloat) -> CGFloat {
        let scale = max(displayScale, 1)
        return (value * scale).rounded() / scale
    }
}

/// The masthead fades and is cut off at its top edge as the header moves up
/// past it, so it appears to slide under the status bar line.
private struct BriefingCollapsingMastheadModifier: ViewModifier {
    var model: BriefingChromeCollapseModel
    var lensKey: String?
    @Binding var naturalHeight: CGFloat

    func body(content: Content) -> some View {
        let shrink = min(max(model.collapse(forLens: lensKey), 0), naturalHeight)
        let progress = naturalHeight > 0 ? shrink / naturalHeight : 0
        content
            .onGeometryChange(for: CGFloat.self) { proxy in
                proxy.size.height
            } action: { _, height in
                guard height > 0, abs(height - naturalHeight) > 0.5 else { return }
                naturalHeight = height
            }
            .opacity(1 - progress)
            .clipShape(BriefingTopInsetClip(inset: shrink))
            .allowsHitTesting(shrink < 1)
    }
}

private struct BriefingTopInsetClip: Shape {
    var inset: CGFloat

    var animatableData: CGFloat {
        get { inset }
        set { inset = newValue }
    }

    func path(in rect: CGRect) -> Path {
        Path(CGRect(x: rect.minX, y: rect.minY + inset, width: rect.width, height: max(rect.height - inset, 0)))
    }
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
        // The gradient has no inputs, so per-frame collapse changes only
        // update this opacity and never rebuild the gradient.
        BriefingPinnedChromeFade()
            .opacity(visibility)
            .allowsHitTesting(false)
            .accessibilityHidden(true)
    }

    /// How far the fade extends below the header.
    static let fadeHeight: CGFloat = 20
    private static let fadeInDistance: CGFloat = 24
}

private struct BriefingPinnedChromeFade: View {
    var body: some View {
        GeometryReader { proxy in
            let fadeHeight = BriefingPinnedChromeBacking.fadeHeight
            let height = proxy.size.height + fadeHeight
            LinearGradient(
                stops: [
                    .init(color: Color.surfacePrimary.opacity(0.6), location: 0),
                    .init(color: Color.surfacePrimary.opacity(0.45), location: max(1 - fadeHeight * 2 / max(height, 1), 0)),
                    .init(color: Color.surfacePrimary.opacity(0), location: 1),
                ],
                startPoint: .top,
                endPoint: .bottom
            )
            .frame(height: height)
        }
    }
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
    func briefingHeaderScrollOffset(
        model: BriefingChromeCollapseModel,
        lensKey: String?,
        mastheadHeight: CGFloat
    ) -> some View {
        modifier(BriefingHeaderScrollOffsetModifier(model: model, lensKey: lensKey, mastheadHeight: mastheadHeight))
    }

    func briefingCollapsingMasthead(
        model: BriefingChromeCollapseModel,
        lensKey: String?,
        naturalHeight: Binding<CGFloat>
    ) -> some View {
        modifier(BriefingCollapsingMastheadModifier(model: model, lensKey: lensKey, naturalHeight: naturalHeight))
    }

    /// Reports the header's fully expanded height. The header keeps its layout
    /// height while it scrolls away, so only the minimized player's give-back
    /// needs adding.
    func measureBriefingExpandedChromeHeight(
        additionalShrink: CGFloat = 0,
        expandedHeight: Binding<CGFloat>
    ) -> some View {
        onGeometryChange(for: CGFloat.self) { proxy in
            proxy.size.height + max(additionalShrink, 0)
        } action: { _, height in
            guard height > 0, abs(height - expandedHeight.wrappedValue) > 0.5 else { return }
            expandedHeight.wrappedValue = height
        }
    }
}
