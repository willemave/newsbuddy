//
//  PressableButtonStyle.swift
//  newsly
//

import SwiftUI

struct PressableButtonStyle: ButtonStyle {
    var pressedScale: CGFloat = 0.96

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? pressedScale : 1)
            .animation(AppMotion.press, value: configuration.isPressed)
    }
}
