//
//  SettingsReadStatusSection.swift
//  newsly
//

import SwiftUI

struct SettingsReadStatusSection: View {
    let isProcessing: Bool
    let onMarkAll: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            SectionHeader(title: "Actions")

            Button(action: onMarkAll) {
                SettingsRow(
                    icon: "checkmark.circle",
                    title: "Mark All As Read"
                ) {
                    if isProcessing {
                        ProgressView()
                    } else {
                        EmptyView()
                    }
                }
            }
            .buttonStyle(.plain)
            .disabled(isProcessing)
            .settingsCard()
        }
    }
}
