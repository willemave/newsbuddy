//
//  PulseModifier.swift
//  newsly
//
//  Shared lifecycle-safe opacity pulse modifier.
//

import SwiftUI

/// Repeats an opacity pulse while `isActive` is true and Reduce Motion is off.
/// Resets on disappear so views inside lazy containers restart their pulse cleanly,
/// and switches to a `nil` animation on stop so `.repeatForever` never runs on exit.
struct PulseModifier: ViewModifier {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    let isActive: Bool
    let minOpacity: Double
    let animation: Animation

    @State private var isVisible = false
    @State private var isPulsing = false

    private var shouldPulse: Bool {
        isVisible && isActive && !reduceMotion
    }

    func body(content: Content) -> some View {
        content
            .opacity(isPulsing ? minOpacity : 1)
            .animation(isPulsing ? animation : nil, value: isPulsing)
            .onAppear {
                isVisible = true
                syncPulse()
            }
            .onChange(of: isActive) { _, _ in
                syncPulse()
            }
            .onChange(of: reduceMotion) { _, _ in
                syncPulse()
            }
            .onDisappear {
                isVisible = false
                isPulsing = false
            }
    }

    private func syncPulse() {
        isPulsing = shouldPulse
    }
}

extension View {
    func pulse(
        isActive: Bool = true,
        minOpacity: Double = 0.5,
        animation: Animation
    ) -> some View {
        modifier(
            PulseModifier(
                isActive: isActive,
                minOpacity: minOpacity,
                animation: animation
            )
        )
    }
}
