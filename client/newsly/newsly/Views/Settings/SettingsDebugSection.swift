//
//  SettingsDebugSection.swift
//  newsly
//

import SwiftUI

struct SettingsDebugSection: View {
    let onOpenDebugMenu: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            SectionHeader(title: "Debug")

            Button(action: onOpenDebugMenu) {
                SettingsRow(
                    icon: "ladybug",
                    iconColor: .statusDestructive,
                    title: "Debug Menu"
                ) {
                    EmptyView()
                }
            }
            .buttonStyle(.plain)
            .settingsCard()
        }
    }
}
