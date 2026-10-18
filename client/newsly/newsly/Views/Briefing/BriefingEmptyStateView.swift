import SwiftUI

struct BriefingEmptyStateView: View {
    @Environment(RootDependencyFactory.self) private var dependencyFactory
    let refreshPhase: BriefingViewModel.RefreshPhase
    let onRefresh: () async -> Void

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                EditorialMastheadHeader(
                    title: "Briefing",
                    titleAccessibilityIdentifier: "briefing.screen"
                )

                VStack(alignment: .leading, spacing: 0) {
                    Text("CURRENT EDITION")
                        .kicker()
                        .padding(.bottom, 12)

                    Text("Your next edition is taking shape.")
                        .font(.appTitle)
                        .foregroundStyle(Color.onSurface)
                        .fixedSize(horizontal: false, vertical: true)

                    Text(
                        "Newsbuddy is checking your sources and grouping related stories. "
                            + "The first readable category will appear here when there is enough to brief you on."
                    )
                        .font(.appBody)
                        .foregroundStyle(Color.onSurfaceSecondary)
                        .lineSpacing(5)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.top, 14)

                    statusRow
                        .padding(.top, 28)

                    Text("Pull down to check for a new edition.")
                        .font(.appCaption)
                        .foregroundStyle(Color.onSurfaceSecondary)
                        .padding(.top, 12)

                    NavigationLink("All News") {
                        NewsFilterNewsView(service: dependencyFactory.newsFilterService, filtered: false)
                    }
                    .font(.appCallout.weight(.semibold))
                    .foregroundStyle(Color.brandPrimary)
                    .padding(.top, 20)
                    .accessibilityIdentifier("briefing.empty.all_news")

                    NavigationLink("Edit News Filter") {
                        NewsFilterView(service: dependencyFactory.newsFilterService)
                    }
                    .font(.appCallout.weight(.semibold))
                    .foregroundStyle(Color.brandPrimary)
                    .padding(.top, 12)
                    .accessibilityIdentifier("briefing.empty.news_filter")

                    NavigationLink {
                        SettingsView()
                    } label: {
                        Text("Manage sources")
                            .font(.appCallout.weight(.semibold))
                            .foregroundStyle(Color.brandPrimary)
                            .frame(minHeight: 44)
                    }
                    .buttonStyle(.plain)
                    .padding(.top, 20)
                    .accessibilityIdentifier("briefing.empty.manage_sources")
                }
                .padding(.horizontal, Spacing.appHorizontalMargin)
                .padding(.bottom, 48)
                .frame(maxWidth: 680, alignment: .leading)
                .frame(maxWidth: .infinity, alignment: .center)
            }
        }
        .refreshable {
            await onRefresh()
        }
        .accessibilityIdentifier("briefing.empty")
    }

    private var statusRow: some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            statusIndicator

            Text(statusText)
                .font(.appCallout.weight(.medium))
                .foregroundStyle(statusColor)
                .fixedSize(horizontal: false, vertical: true)
        }
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("briefing.empty.status")
    }

    @ViewBuilder
    private var statusIndicator: some View {
        switch refreshPhase {
        case .requesting, .waitingForCompletion:
            ProgressView()
                .controlSize(.small)
        case .failed:
            Image(systemName: "exclamationmark.circle.fill")
                .font(.appSymbol(size: 14, weight: .semibold))
                .foregroundStyle(Color.statusDestructive)
        case .idle:
            Circle()
                .fill(Color.brandPrimary)
                .frame(width: 8, height: 8)
        }
    }

    private var statusText: String {
        switch refreshPhase {
        case .requesting, .waitingForCompletion:
            "Checking your sources…"
        case .failed(let message):
            message
        case .idle:
            "Waiting for enough related stories"
        }
    }

    private var statusColor: Color {
        if case .failed = refreshPhase {
            return .statusDestructive
        }
        return .onSurfaceSecondary
    }
}
