import SwiftUI

struct EditorialCardButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? 0.96 : 1.0)
            .animation(AppMotion.press, value: configuration.isPressed)
    }
}
