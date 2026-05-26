//
//  GlassCard.swift
//  newsly
//

import SwiftUI

struct GlassCardModifier: ViewModifier {
    var cornerRadius: CGFloat = 40

    func body(content: Content) -> some View {
        content
            .background(.ultraThinMaterial.opacity(0.8))
            .clipShape(RoundedRectangle(cornerRadius: cornerRadius))
            .overlay(
                RoundedRectangle(cornerRadius: cornerRadius)
                    .stroke(Color.onboardingText.opacity(0.15), lineWidth: 0.5)
            )
    }
}

extension View {
    func glassCard(cornerRadius: CGFloat = 40) -> some View {
        modifier(GlassCardModifier(cornerRadius: cornerRadius))
    }
}
