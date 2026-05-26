//
//  WatercolorBackground.swift
//  newsly
//

import SwiftUI

struct WatercolorBackground: View {
    var energy: Double = 0.15

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        if reduceMotion {
            staticBackground
        } else {
            animatedBackground
        }
    }

    // MARK: - Animated

    private var animatedBackground: some View {
        TimelineView(.animation(minimumInterval: 1.0 / 30.0)) { timeline in
            let t = timeline.date.timeIntervalSinceReferenceDate
            Canvas { context, size in
                // Base fill
                context.fill(
                    Rectangle().path(in: CGRect(origin: .zero, size: size)),
                    with: .color(Color.onboardingSurface)
                )
                drawBlobs(context: &context, size: size, time: t)
            }
            .blur(radius: 60)
            .drawingGroup()
        }
        .ignoresSafeArea()
    }

    // MARK: - Static (Reduce Motion)

    private var staticBackground: some View {
        ZStack {
            Color.onboardingSurface
            GeometryReader { geo in
                let cx = geo.size.width / 2
                let cy = geo.size.height / 2
                let r: CGFloat = 180

                Circle()
                    .fill(
                        RadialGradient(
                            colors: [Color.onboardingAmbientPrimary.opacity(0.5), .clear],
                            center: .center, startRadius: 0, endRadius: r
                        )
                    )
                    .frame(width: r * 2, height: r * 2)
                    .position(x: cx - 60, y: cy - 80)

                Circle()
                    .fill(
                        RadialGradient(
                            colors: [Color.onboardingAmbientTertiary.opacity(0.45), .clear],
                            center: .center, startRadius: 0, endRadius: r
                        )
                    )
                    .frame(width: r * 2, height: r * 2)
                    .position(x: cx + 80, y: cy - 40)

                Circle()
                    .fill(
                        RadialGradient(
                            colors: [Color.onboardingSelectionAccent.opacity(0.4), .clear],
                            center: .center, startRadius: 0, endRadius: r
                        )
                    )
                    .frame(width: r * 2, height: r * 2)
                    .position(x: cx - 40, y: cy + 100)

                Circle()
                    .fill(
                        RadialGradient(
                            colors: [Color.onboardingAmbientQuaternary.opacity(0.4), .clear],
                            center: .center, startRadius: 0, endRadius: r
                        )
                    )
                    .frame(width: r * 2, height: r * 2)
                    .position(x: cx + 60, y: cy + 60)
            }
        }
        .ignoresSafeArea()
    }

    // MARK: - Blob Drawing

    private func drawBlobs(context: inout GraphicsContext, size: CGSize, time: Double) {
        let cx = size.width / 2
        let cy = size.height / 2
        let e = min(1, max(0, energy))

        let blobs: [(color: Color, baseRadius: CGFloat, xPhase: Double, yPhase: Double, baseSpeed: Double, drift: Double)] = [
            (.onboardingAmbientPrimary,     220, 0,    0,    0.18, 100),
            (.onboardingAmbientTertiary, 190, 1.8,  2.5,  0.22, 120),
            (.onboardingSelectionAccent,   180, 3.2,  1.2,  0.20, 110),
            (.onboardingAmbientQuaternary,       200, 4.8,  3.8,  0.25, 130),
        ]

        for blob in blobs {
            let speed = blob.baseSpeed * (1.0 + e * 1.5)
            let driftRange = blob.drift + e * 60

            let dx = sin(time * speed + blob.xPhase) * driftRange
            let dy = cos(time * speed * 0.7 + blob.yPhase) * driftRange * 0.8

            let breathe = sin(time * speed * 1.5 + blob.xPhase) * 10 * e
            let r = blob.baseRadius + CGFloat(e) * 40 + CGFloat(breathe)

            let blobCenter = CGPoint(x: cx + dx, y: cy + dy)
            let rect = CGRect(
                x: blobCenter.x - r,
                y: blobCenter.y - r,
                width: r * 2,
                height: r * 2
            )

            let opacity = 0.7 + e * 0.25

            context.fill(
                Ellipse().path(in: rect),
                with: .color(blob.color.opacity(opacity))
            )
        }
    }

    // MARK: - Title Oscillation

    /// Returns a subtle vertical offset (~+/-3pt) synced with blob 0's sine phase.
    static func titleOscillation(time: Double) -> CGFloat {
        CGFloat(sin(time * 0.18) * 3.0)
    }

    /// Returns a shadow color that cycles through the blob palette over time.
    static func titleGlowColor(time: Double) -> Color {
        // Cycle through 4 colors smoothly using sine phases
        let blue = (sin(time * 0.12) + 1) / 2       // 0..1
        let peach = (sin(time * 0.15 + 1.8) + 1) / 2
        let green = (sin(time * 0.13 + 3.2) + 1) / 2
        let sky = (sin(time * 0.17 + 4.8) + 1) / 2

        // Pick the dominant color
        let maxVal = max(blue, peach, green, sky)
        if maxVal == blue { return .onboardingAmbientPrimary }
        if maxVal == peach { return .onboardingAmbientTertiary }
        if maxVal == green { return .onboardingSelectionAccent }
        return .onboardingAmbientQuaternary
    }
}
