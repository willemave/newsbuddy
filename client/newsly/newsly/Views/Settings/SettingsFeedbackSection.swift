//
//  SettingsFeedbackSection.swift
//  newsly
//

import SwiftUI

struct SettingsFeedbackSection: View {
    let isVisible: Bool
    let onGiveFeedback: () -> Void

    var body: some View {
        if isVisible {
            Button(action: onGiveFeedback) {
                SettingsRow(
                    icon: "bubble.left.and.bubble.right",
                    title: "Give Feedback"
                ) {
                    NavigationChevron()
                }
            }
            .buttonStyle(.plain)
            .settingsCard()
        }
    }
}
