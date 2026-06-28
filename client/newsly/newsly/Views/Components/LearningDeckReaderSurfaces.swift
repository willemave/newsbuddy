//
//  LearningDeckReaderSurfaces.swift
//  newsly
//

import SwiftUI

extension View {
    func learningDeckReaderCircleSurface(tint: Color, isEnabled: Bool) -> some View {
        glassSurface(
            in: Circle(),
            tint: tint,
            opacity: 0.18,
            interactive: isEnabled,
            fallback: .materialStroke(strokeOpacity: 0.2)
        )
    }

    func learningDeckReaderCapsuleSurface(tint: Color, isEnabled: Bool) -> some View {
        glassSurface(
            in: Capsule(),
            tint: tint,
            opacity: 0.14,
            interactive: isEnabled,
            fallback: .tint(opacity: 0.54)
        )
    }

    func learningDeckReaderInputSurface(isFocused: Bool) -> some View {
        let tint = isFocused ? Color.brandPrimary : Color.surfaceContainerHighest
        let opacity = isFocused ? 0.14 : 0.18
        return glassSurface(
            in: RoundedRectangle(cornerRadius: 18, style: .continuous),
            tint: tint,
            opacity: opacity,
            interactive: true,
            fallback: .fillStroke(
                fill: Color.surfaceContainerHighest,
                fillOpacity: 1,
                strokeOpacity: isFocused ? 0.34 : 0.24
            )
        )
    }

    func learningDeckReaderSendSurface(isEnabled: Bool) -> some View {
        let tint = isEnabled ? Color.chatUserBubble : Color.surfaceContainer
        return glassSurface(
            in: Circle(),
            tint: tint,
            opacity: isEnabled ? 0.3 : 0.16,
            interactive: isEnabled,
            fallback: .tint(opacity: 1)
        )
    }
}
