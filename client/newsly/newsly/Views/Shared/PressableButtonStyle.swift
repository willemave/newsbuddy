//
//  PressableButtonStyle.swift
//  newsly
//

import SwiftUI

struct PressableButtonStyle: ButtonStyle {
    var pressedScale: CGFloat = 0.94
    var response: Double = 0.25
    var dampingFraction: Double = 0.65

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? pressedScale : 1)
            .animation(
                .spring(response: response, dampingFraction: dampingFraction),
                value: configuration.isPressed
            )
    }
}
