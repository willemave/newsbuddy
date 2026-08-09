import SwiftUI

private struct ScrollToTopOnRequestModifier<AnchorID: Hashable>: ViewModifier {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    let request: Int
    let anchorID: AnchorID
    let proxy: ScrollViewProxy
    let isEnabled: Bool

    func body(content: Content) -> some View {
        content.onChange(of: request) { oldValue, newValue in
            guard isEnabled, newValue > oldValue else { return }
            withAnimation(AppMotion.respectingReduceMotion(reduceMotion, AppMotion.subtle)) {
                proxy.scrollTo(anchorID, anchor: .top)
            }
        }
    }
}

extension View {
    func scrollsToTopOnRequest<AnchorID: Hashable>(
        _ request: Int,
        anchor anchorID: AnchorID,
        using proxy: ScrollViewProxy,
        isEnabled: Bool = true
    ) -> some View {
        modifier(
            ScrollToTopOnRequestModifier(
                request: request,
                anchorID: anchorID,
                proxy: proxy,
                isEnabled: isEnabled
            )
        )
    }
}
