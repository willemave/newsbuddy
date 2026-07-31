//
//  SettingsTwitterSection.swift
//  newsly
//

import SwiftUI

struct SettingsTwitterSection: View {
    let authState: AuthState
    let xConnection: XConnectionResponse?

    var body: some View {
        if case .authenticated = authState {
            VStack(alignment: .leading, spacing: 0) {
                SectionHeader(title: "Connections")

                NavigationLink {
                    TwitterSettingsView()
                } label: {
                    SettingsRow(
                        icon: "at",
                        title: "X / Twitter",
                        subtitle: xConnection?.settingsSubtitle ?? "Not connected"
                    )
                }
                .buttonStyle(.plain)
                .settingsCard()
            }
        }
    }
}
