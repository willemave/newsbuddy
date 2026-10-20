//
//  FloatingBackButton.swift
//  newsly
//

import SwiftUI

struct FloatingBackButton: View {
    enum Style {
        case imageOverlay
        case surface
    }

    let style: Style
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: "chevron.left")
                .font(.appSymbol(size: 20, weight: .semibold))
                .foregroundStyle(foregroundStyle)
                .frame(width: DetailDesign.floatingBackButtonSize, height: DetailDesign.floatingBackButtonSize)
                .glassEffect(glass.interactive(), in: .circle)
                // Glass is not hit-testable content; without this only the
                // chevron's pixels would take the tap.
                .contentShape(Circle())
                .animation(AppMotion.subtle, value: style)
        }
        .buttonStyle(.plain)
        .textSelection(.disabled)
        .accessibilityLabel("Back")
        .accessibilityIdentifier("navigation.back")
    }

    private var foregroundStyle: Color {
        switch style {
        case .imageOverlay:
            .white
        case .surface:
            .onSurface
        }
    }

    /// Over hero artwork the glass is darkened so the white chevron holds up
    /// on bright images; over the reading surface it uses the shared chrome glass.
    private var glass: Glass {
        switch style {
        case .imageOverlay:
            .regular.tint(Color.black.opacity(0.28))
        case .surface:
            .floatingChrome
        }
    }
}
