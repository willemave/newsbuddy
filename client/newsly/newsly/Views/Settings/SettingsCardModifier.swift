//
//  SettingsCardModifier.swift
//  newsly
//
//  Shared settings-section card styling.
//

import SwiftUI

enum SettingsCardShape {
    static let cornerRadius: CGFloat = 22
    static let shape = RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
}

extension View {
    /// Settings sections sit on glass, matching the sheet and the floating
    /// chrome; the clip keeps row press states inside the rounded edge.
    func settingsCard() -> some View {
        self
            .clipShape(SettingsCardShape.shape)
            .glassEffect(.regular, in: SettingsCardShape.shape)
            .padding(.horizontal, Spacing.appHorizontalMargin)
    }
}
