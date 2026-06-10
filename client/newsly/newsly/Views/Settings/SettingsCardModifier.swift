//
//  SettingsCardModifier.swift
//  newsly
//
//  Shared settings-section card styling.
//

import SwiftUI

extension View {
    func settingsCard() -> some View {
        self
            .background(Color.surfaceSecondary)
            .clipShape(RoundedRectangle(cornerRadius: 14))
            .padding(.horizontal, Spacing.appHorizontalMargin)
    }
}
