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

    // Controls inside the opaque chat sheet use flat fills: glass rims read as
    // extra borders there.
    func learningDeckChatFieldSurface(isFocused: Bool) -> some View {
        background(
            isFocused ? Color.brandPrimary.opacity(0.08) : Color.surfaceSecondary,
            in: RoundedRectangle(cornerRadius: 22, style: .continuous)
        )
    }

    func learningDeckChatSendSurface(isEnabled: Bool) -> some View {
        background(
            isEnabled ? Color.chatUserBubble : Color.surfaceSecondary,
            in: Circle()
        )
    }
}
