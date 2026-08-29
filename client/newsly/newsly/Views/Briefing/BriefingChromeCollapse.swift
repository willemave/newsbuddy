import Observation
import SwiftUI

/// Per-lens chrome collapse driven directly by scroll position, so the
/// masthead and category strip track the finger 1:1 instead of snapping at a
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
    let categoryStripHeight: CGFloat
    let keepsCategoryStripOpen: Bool
    @Binding var expandedHeight: CGFloat

    func body(content: Content) -> some View {
        let collapse = model.collapse(forLens: lensKey)
        let mastheadShrink = min(max(collapse, 0), mastheadHeight)
        let categoryShrink = keepsCategoryStripOpen
            ? 0
            : min(max(collapse - mastheadHeight, 0), categoryStripHeight)
        let totalShrink = mastheadShrink + categoryShrink

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
        categoryStripHeight: CGFloat,
        keepsCategoryStripOpen: Bool,
        expandedHeight: Binding<CGFloat>
    ) -> some View {
        modifier(
            BriefingExpandedChromeHeightModifier(
                model: model,
                lensKey: lensKey,
                mastheadHeight: mastheadHeight,
                categoryStripHeight: categoryStripHeight,
                keepsCategoryStripOpen: keepsCategoryStripOpen,
                expandedHeight: expandedHeight
            )
        )
    }
}
