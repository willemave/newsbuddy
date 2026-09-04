//
//  SettingsLibrarySection.swift
//  newsly
//

import SwiftUI

struct SettingsLibrarySection: View {
    @Environment(BadgeStatsStore.self) private var badgeStatsStore
    @Environment(ReadStateCache.self) private var readStateCache
    @Environment(RootDependencyFactory.self) private var dependencyFactory
    @Environment(SubmissionStatusViewModel.self) private var submissionsViewModel

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            SectionHeader(title: "Library")

            VStack(spacing: 0) {
                libraryLink(
                    icon: "magnifyingglass",
                    title: "Search",
                    accessibilityIdentifier: "settings.search"
                ) {
                    SearchView(
                        readStateCache: readStateCache,
                        viewModel: dependencyFactory.makeSearchViewModel()
                    )
                }

                divider

                libraryLink(
                    icon: "clock",
                    title: "Recently Read",
                    accessibilityIdentifier: "settings.recently_read"
                ) {
                    RecentlyReadView(
                        readStateCache: readStateCache,
                        viewModel: dependencyFactory.makeContentListViewModel(
                            readStateCache: readStateCache
                        )
                    )
                }

                divider

                NavigationLink {
                    SubmissionsView(viewModel: submissionsViewModel)
                } label: {
                    SettingsRow(icon: "tray.and.arrow.up", title: "Submissions") {
                        HStack(spacing: 8) {
                            if submissionsViewModel.unseenCount > 0 {
                                CountBadge(
                                    count: submissionsViewModel.unseenCount,
                                    color: .brandPrimary
                                )
                            }
                            NavigationChevron()
                        }
                    }
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("settings.submissions")

                divider

                NavigationLink {
                    ProcessingStatsView(
                        sourcesViewModel: dependencyFactory.makeScraperSettingsViewModel(
                            filterTypes: ["substack", "atom", "youtube", "podcast_rss"]
                        )
                    )
                } label: {
                    SettingsRow(icon: "clock.arrow.circlepath", title: "Processing") {
                        HStack(spacing: 8) {
                            if badgeStatsStore.processingCount > 0 {
                                CountBadge(
                                    count: badgeStatsStore.processingCount,
                                    color: .brandPrimary
                                )
                            }
                            NavigationChevron()
                        }
                    }
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("settings.processing")
            }
            .settingsCard()
        }
        .task {
            await submissionsViewModel.load()
            await badgeStatsStore.refreshStats()
        }
    }

    private var divider: some View {
        RowDivider(leadingInset: Spacing.rowHorizontal)
    }

    private func libraryLink<Destination: View>(
        icon: String,
        title: String,
        accessibilityIdentifier: String,
        @ViewBuilder destination: () -> Destination
    ) -> some View {
        NavigationLink(destination: destination) {
            SettingsRow(icon: icon, title: title)
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier(accessibilityIdentifier)
    }
}
