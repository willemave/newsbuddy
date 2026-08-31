//
//  SettingsSourcesSection.swift
//  newsly
//

import SwiftUI

struct SettingsSourcesSection: View {
    @Environment(RootDependencyFactory.self) private var dependencyFactory

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            SectionHeader(title: "Sources")

            VStack(spacing: 0) {
                NavigationLink {
                    FeedSourcesView(
                        viewModel: dependencyFactory.makeScraperSettingsViewModel(
                            filterTypes: ["substack", "atom", "youtube"]
                        )
                    )
                } label: {
                    SettingsRow(
                        icon: "list.bullet.rectangle",
                        title: "Feed Sources"
                    )
                }
                .buttonStyle(.plain)

                RowDivider(leadingInset: Spacing.rowHorizontal)

                NavigationLink {
                    PodcastSourcesView(
                        viewModel: dependencyFactory.makeScraperSettingsViewModel(
                            filterTypes: ["podcast_rss"]
                        )
                    )
                } label: {
                    SettingsRow(
                        icon: "waveform",
                        title: "Podcast Sources"
                    )
                }
                .buttonStyle(.plain)
            }
            .settingsCard()
        }
    }
}
