//
//  MoreView.swift
//  newsly
//

import SwiftUI

private enum MoreRoute: Hashable {
    case search
    case recentlyRead
    case submissions
    case processing
    case settings
}

struct MoreView: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(BadgeStatsStore.self) private var badgeStatsStore

    let submissionsViewModel: SubmissionStatusViewModel
    let readStateCache: ReadStateCache
    let showsDismissButton: Bool
    let dependencyFactory: RootDependencyFactory

    init(
        submissionsViewModel: SubmissionStatusViewModel,
        readStateCache: ReadStateCache,
        showsDismissButton: Bool = false,
        dependencyFactory: RootDependencyFactory
    ) {
        self.submissionsViewModel = submissionsViewModel
        self.readStateCache = readStateCache
        self.showsDismissButton = showsDismissButton
        self.dependencyFactory = dependencyFactory
    }

    var body: some View {
        VStack(spacing: 0) {
            EditorialMastheadHeader(
                title: "More",
                titleAccessibilityIdentifier: "more.screen",
                showsDate: false,
                trailingAccessory: showsDismissButton ? AnyView(dismissButton) : nil
            )

            List {
                Section {
                    menuRow(
                        route: .search,
                        icon: "magnifyingglass",
                        title: "Search",
                        accessibilityIdentifier: "more.search"
                    )

                    menuRow(
                        route: .recentlyRead,
                        icon: "clock",
                        title: "Recently Read",
                        accessibilityIdentifier: "more.recently_read"
                    )

                    NavigationLink(value: MoreRoute.submissions) {
                        HStack(spacing: 16) {
                            minimalIcon("tray.and.arrow.up")
                            Text("Submissions")
                                .foregroundStyle(Color.onSurface)
                            Spacer()
                            if submissionsViewModel.unseenCount > 0 {
                                CountBadge(count: submissionsViewModel.unseenCount, color: .brandPrimary)
                            }
                        }
                        .frame(minHeight: RowMetrics.compactHeight)
                    }
                    .accessibilityIdentifier("more.submissions")

                    NavigationLink(value: MoreRoute.processing) {
                        HStack(spacing: 16) {
                            minimalIcon("clock.arrow.circlepath")
                            Text("Processing")
                                .foregroundStyle(Color.onSurface)
                            Spacer()
                            if badgeStatsStore.processingCount > 0 {
                                CountBadge(count: badgeStatsStore.processingCount, color: .brandPrimary)
                            }
                        }
                        .frame(minHeight: RowMetrics.compactHeight)
                    }
                }

                Section {
                    menuRow(
                        route: .settings,
                        icon: "gearshape",
                        title: "Settings",
                        accessibilityIdentifier: "more.settings"
                    )
                }
            }
            .listStyle(.insetGrouped)
            .scrollContentBackground(.hidden)
            .contentMargins(.top, 0, for: .scrollContent)
            .contentMargins(.horizontal, Spacing.appHorizontalMargin, for: .scrollContent)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(Color.surfacePrimary.ignoresSafeArea())
        .navigationTitle("")
        .navigationBarTitleDisplayMode(.inline)
        .navigationDestination(for: MoreRoute.self, destination: moreDestination)
        .accessibilityElement(children: .contain)
        .task {
            await submissionsViewModel.load()
            await badgeStatsStore.refreshStats()
        }
    }

    private var dismissButton: some View {
        Button {
            dismiss()
        } label: {
            Image(systemName: "xmark")
                .font(.appSymbol(size: 16, weight: .semibold))
                .foregroundStyle(Color.onSurfaceSecondary)
                .frame(width: 44, height: 44)
                .background(Color.surfaceTertiary)
                .clipShape(Circle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Close More")
        .accessibilityIdentifier("more.close")
    }

    private func menuRow(
        route: MoreRoute,
        icon: String,
        title: String,
        accessibilityIdentifier: String
    ) -> some View {
        NavigationLink(value: route) {
            HStack(spacing: 16) {
                minimalIcon(icon)
                Text(title)
                    .foregroundStyle(Color.onSurface)
            }
            .frame(minHeight: RowMetrics.compactHeight)
        }
        .accessibilityIdentifier(accessibilityIdentifier)
    }

    @ViewBuilder
    private func moreDestination(for route: MoreRoute) -> some View {
        switch route {
        case .search:
            SearchView(
                readStateCache: readStateCache,
                viewModel: dependencyFactory.makeSearchViewModel()
            )
        case .recentlyRead:
            RecentlyReadView(
                readStateCache: readStateCache,
                viewModel: dependencyFactory.makeContentListViewModel(
                    readStateCache: readStateCache
                )
            )
        case .submissions:
            SubmissionsView(viewModel: submissionsViewModel)
        case .processing:
            ProcessingStatsView(
                sourcesViewModel: dependencyFactory.makeScraperSettingsViewModel(
                    filterTypes: ["substack", "atom", "youtube", "podcast_rss"]
                )
            )
        case .settings:
            SettingsView()
        }
    }

    private func minimalIcon(_ name: String) -> some View {
        Image(systemName: name)
            .font(.appSymbol(size: Spacing.smallIcon, weight: .regular))
            .foregroundStyle(Color.onSurfaceSecondary)
            .frame(width: 24, height: 24)
    }
}

#Preview {
    EmptyView()
}
