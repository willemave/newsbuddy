import SwiftUI

/// Caught-up state for a lens page with nothing unread.
struct BriefingLensEmptyView: View {
    let lensTitle: String
    let topContentInset: CGFloat
    let screenEdgeInsets: EdgeInsets
    let scrollToTopRequest: Int
    let shouldScrollToTop: Bool
    let onRefresh: () async -> Void

    private static let topAnchor = "briefing.lens_empty.top"

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    Text("YOU'RE CAUGHT UP")
                        .kicker()

                Text("Nothing unread here.")
                    .font(.appTitle)
                    .foregroundStyle(Color.onSurface)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 12)

                Text("New items in \(lensTitle) will appear here as your briefing updates.")
                    .font(.appBody)
                    .foregroundStyle(Color.onSurfaceSecondary)
                    .lineSpacing(5)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 14)

                Text("Pull down to check again.")
                    .font(.appCaption)
                    .foregroundStyle(Color.onSurfaceSecondary)
                    .padding(.top, 24)
                }
                .id(Self.topAnchor)
                .padding(.horizontal, Spacing.appHorizontalMargin)
                .padding(.top, 28)
                .frame(maxWidth: 680, alignment: .leading)
                .frame(maxWidth: .infinity, alignment: .center)
            }
            .contentMargins(.top, topContentInset)
            .contentMargins(.bottom, 40 + screenEdgeInsets.bottom)
            // The bar supplies the status-bar part of the top inset.
            .systemTopScrollEdge(height: screenEdgeInsets.top)
            .refreshable {
                await onRefresh()
            }
            .scrollsToTopOnRequest(
                scrollToTopRequest,
                anchor: Self.topAnchor,
                using: proxy,
                isEnabled: shouldScrollToTop
            )
        }
        .accessibilityIdentifier("briefing.lens_empty")
    }

}
