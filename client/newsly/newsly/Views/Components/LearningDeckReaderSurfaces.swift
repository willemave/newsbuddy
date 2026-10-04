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
            interactive: isEnabled
        )
    }

    func learningDeckReaderCapsuleSurface(tint: Color, isEnabled: Bool) -> some View {
        glassSurface(
            in: Capsule(),
            tint: tint,
            opacity: 0.14,
            interactive: isEnabled
        )
    }

    func learningDeckReaderInputSurface(isFocused: Bool) -> some View {
        let tint = isFocused ? Color.brandPrimary : Color.surfaceContainerHighest
        let opacity = isFocused ? 0.14 : 0.18
        return glassSurface(
            in: RoundedRectangle(cornerRadius: 18, style: .continuous),
            tint: tint,
            opacity: opacity,
            interactive: true
        )
    }

    func learningDeckReaderSendSurface(isEnabled: Bool) -> some View {
        let tint = isEnabled ? Color.chatUserBubble : Color.surfaceContainer
        return glassSurface(
            in: Circle(),
            tint: tint,
            opacity: isEnabled ? 0.3 : 0.16,
            interactive: isEnabled
        )
    }
}
