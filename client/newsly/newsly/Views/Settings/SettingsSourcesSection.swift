//
//  SettingsSourcesSection.swift
//  newsly
//

import SwiftUI

struct SettingsSourcesSection: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            SectionHeader(title: "Sources")

            VStack(spacing: 0) {
                NavigationLink {
                    FeedSourcesView()
                } label: {
                    SettingsRow(
                        icon: "list.bullet.rectangle",
                        title: "Feed Sources"
                    )
                }
                .buttonStyle(.plain)

                RowDivider(leadingInset: Spacing.rowHorizontal)

                NavigationLink {
                    PodcastSourcesView()
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
