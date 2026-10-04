//
//  SettingsSourcesSection.swift
//  newsly
//

import SwiftUI

struct SettingsSourcesSection: View {
    @Environment(RootDependencyFactory.self) private var dependencyFactory

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            SectionHeader(title: "Sources", font: .modalSupporting.weight(.semibold))

            VStack(spacing: 0) {
                NavigationLink {
                    SourcesView(
                        kind: .feed,
                        viewModel: dependencyFactory.makeScraperSettingsViewModel(
                            filterTypes: APIScraperType.articleFeedRawValues
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
                    SourcesView(
                        kind: .podcast,
                        viewModel: dependencyFactory.makeScraperSettingsViewModel(
                            filterTypes: APIScraperType.podcastRawValues
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
