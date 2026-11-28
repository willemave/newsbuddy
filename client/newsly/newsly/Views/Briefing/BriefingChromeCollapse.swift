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
    /// leave as one continuous motion. They come back only with the text, as
    /// the reader returns to the top.
    private var pinnedHideByLens: [String: CGFloat] = [:]
    /// Slide that takes the pinned chrome fully off the top of the screen.
    private(set) var pinnedHideDistance: CGFloat = 0
    @ObservationIgnored private let now: () -> TimeInterval
    @ObservationIgnored private var lastScrolledByLens: [String: CGFloat] = [:]
    /// While a reveal slide runs, scroll samples must not overwrite
    /// its target: an unanimated write would snap the chrome mid-slide.
    @ObservationIgnored private var slideEndsAtByLens: [String: TimeInterval] = [:]

    func pinnedHide(forLens key: String?) -> CGFloat {
        key.flatMap { pinnedHideByLens[$0] } ?? 0
    }

    func isPinnedChromeHidden(forLens key: String?) -> Bool {
        pinnedHideDistance > 0 && pinnedHide(forLens: key) >= pinnedHideDistance - 0.5
    }

    /// Feeds one scroll sample. Past the masthead the pills ride the text off
    /// screen, and they return only with it, near the top.
    func trackPinnedChrome(
        forLens key: String,
        scrolled: CGFloat,
        mastheadHeight: CGFloat
    ) {
        let hide = briefingPinnedChromeHide(
            hide: pinnedHide(forLens: key),
            lastScrolled: lastScrolledByLens[key],
            scrolled: scrolled,
            mastheadHeight: mastheadHeight,
            hideDistance: pinnedHideDistance
        )
        lastScrolledByLens[key] = scrolled
        guard !isSliding(key) else { return }
        setPinnedHide(hide, forLens: key)
    }

    /// Shows the pills at once, or with a slide when `reduceMotion` is given
    /// (a page reached by swipe or tap).
    func revealPinnedChrome(forLens key: String, reduceMotion: Bool? = nil) {
        guard let reduceMotion else {
            slideEndsAtByLens.removeValue(forKey: key)
            setPinnedHide(0, forLens: key)
            return
        }
        guard pinnedHide(forLens: key) != 0 else { return }
        let animation = AppMotion.respectingReduceMotion(
            reduceMotion,
            .smooth(duration: Self.slideDuration)
        )
        slideEndsAtByLens[key] = now() + (reduceMotion ? 0 : Self.slideDuration)
        withAnimation(animation) {
            setPinnedHide(0, forLens: key)
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

/// Most the pills can be hidden: only as far as the reader has scrolled past
/// the masthead, so near the top they always sit on the text they inset.
func briefingPinnedChromeHideCeiling(
    scrolled: CGFloat,
    mastheadHeight: CGFloat,
    hideDistance: CGFloat
) -> CGFloat {
    min(max(scrolled - mastheadHeight, 0), max(hideDistance, 0))
}

/// Hide for the pinned pills and player after one scroll sample. `scrolled`
/// is distance from the top of content, clamped to the scrollable range.
/// Downward scrolling hides by exactly the distance scrolled, so the pills
/// leave attached to the text; scrolling up only clamps to the ceiling, so
/// they come back with the text as the reader reaches the top.
func briefingPinnedChromeHide(
    hide: CGFloat,
    lastScrolled: CGFloat?,
    scrolled: CGFloat,
    mastheadHeight: CGFloat,
    hideDistance: CGFloat
) -> CGFloat {
    let ceiling = briefingPinnedChromeHideCeiling(
        scrolled: scrolled,
        mastheadHeight: mastheadHeight,
        hideDistance: hideDistance
    )
    guard let lastScrolled, scrolled > lastScrolled else { return min(hide, ceiling) }
    return min(hide + scrolled - lastScrolled, ceiling)
}

/// Slide that carries the pinned chrome from its resting place (below the
/// status bar) fully off the top of the screen.
func briefingPinnedChromeHideDistance(screenTop: CGFloat, pinnedHeight: CGFloat) -> CGFloat {
    max(screenTop, 0) + max(pinnedHeight, 0) + 4
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
