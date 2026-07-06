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
                .background(backgroundStyle, in: Circle())
                .overlay {
                    Circle()
                        .stroke(strokeStyle, lineWidth: 1)
                }
                .appShadow(shadowStyle)
        }
        .buttonStyle(.plain)
        .textSelection(.disabled)
        .accessibilityLabel("Back")
    }

    private var foregroundStyle: Color {
        switch style {
        case .imageOverlay:
            .white
        case .surface:
            .onSurface
        }
    }

    private var backgroundStyle: Color {
        switch style {
        case .imageOverlay:
            Color(red: 0.07, green: 0.06, blue: 0.05).opacity(0.42)
        case .surface:
            Color.surfacePrimary.opacity(0.72)
        }
    }

    private var strokeStyle: Color {
        switch style {
        case .imageOverlay:
            .white.opacity(0.22)
        case .surface:
            .outlineVariant.opacity(0.16)
        }
    }

    private var shadowStyle: ShadowStyle {
        switch style {
        case .imageOverlay:
            .floating
        case .surface:
            .none
        }
    }
}
