//
//  SettingsTwitterSection.swift
//  newsly
//

import SwiftUI

struct SettingsTwitterSection: View {
    let authState: AuthState
    let xConnection: XConnectionResponse?

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            SectionHeader(title: "X / Twitter")

            if case .authenticated = authState {
                NavigationLink {
                    TwitterSettingsView()
                } label: {
                    SettingsRow(
                        icon: "at",
                        title: "X / Twitter",
                        subtitle: xConnection?.settingsSubtitle
                    )
                }
                .buttonStyle(.plain)
                .settingsCard()
            }
        }
    }
}
