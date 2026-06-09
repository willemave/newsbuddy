//
//  AncientScrollRevealView.swift
//  newsly
//
//  Created by Assistant on 2/10/26.
//

import SpriteKit
import SwiftUI
import UIKit

struct AncientScrollRevealView: View {
    let obfuscatedSeed: UInt64
    let showsPhysics: Bool
    let onProgressChanged: (Double) -> Void

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    @State private var physicsScene = RevealPhysicsScene(size: CGSize(width: 1, height: 1))
    @State private var lastDragPoint: CGPoint?
    @State private var lastDragTime: Date?
    @State private var cumulativeSwipeDistance: CGFloat = 0
    @State private var headingVisible = false
    @State private var hasTriggeredHaptic = false

    var body: some View {
        GeometryReader { proxy in
            let canvasSize = proxy.size

            ZStack {
                background

                SpriteView(scene: physicsScene, options: [.allowsTransparency])
                    .allowsHitTesting(false)

                edgeVignettes
                topTextOverlay
            }
            .clipped()
            .contentShape(Rectangle())
            .gesture(swipeGesture(in: canvasSize))
            .onAppear {
                configureScene(for: canvasSize)
                withAnimation(.easeOut(duration: 0.8).delay(0.3)) {
                    headingVisible = true
                }
            }
            .onChange(of: canvasSize) { _, newSize in
                configureScene(for: newSize)
            }
            .onChange(of: reduceMotion) { _, _ in
                configureScene(for: canvasSize)
            }
            .onDisappear {
                physicsScene.isPaused = true
            }
        }
    }

    private var background: some View {
        ZStack {
            LinearGradient(
                colors: [
                    Color(red: 0.05, green: 0.08, blue: 0.12),
                    Color(red: 0.08, green: 0.10, blue: 0.16),
                    Color(red: 0.10, green: 0.12, blue: 0.18),
                ],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )

            RadialGradient(
                colors: [
                    Color(red: 0.30, green: 0.36, blue: 0.44).opacity(0.22),
                    .clear,
                ],
                center: .topTrailing,
                startRadius: 40,
                endRadius: 420
            )

            RadialGradient(
                colors: [
                    Color(red: 0.22, green: 0.26, blue: 0.32).opacity(0.18),
                    .clear,
                ],
                center: .bottomLeading,
                startRadius: 30,
                endRadius: 360
            )
        }
    }

    private var edgeVignettes: some View {
        VStack(spacing: 0) {
            LinearGradient(
                colors: [
                    Color(red: 0.05, green: 0.08, blue: 0.12),
                    Color(red: 0.05, green: 0.08, blue: 0.12).opacity(0.55),
                    .clear,
                ],
                startPoint: .top,
                endPoint: .bottom
            )
            .frame(height: 140)

            Spacer()

            LinearGradient(
                colors: [
                    .clear,
                    Color(red: 0.06, green: 0.09, blue: 0.14).opacity(0.65),
                    Color(red: 0.06, green: 0.09, blue: 0.14),
                ],
                startPoint: .top,
                endPoint: .bottom
            )
            .frame(height: 100)
        }
        .allowsHitTesting(false)
    }

    private var topTextOverlay: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Newsbuddy")
                .font(.appLargeTitle)
                .tracking(0.5)
                .foregroundStyle(Color.white.opacity(0.88))

            Text("Curated updates and concise summaries,\nminus the noise.")
                .font(.appSubheadline.weight(.regular))
                .foregroundStyle(Color.white.opacity(0.50))
                .lineSpacing(3)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 24)
        .padding(.top, 16)
        .opacity(headingVisible ? 1 : 0)
        .offset(y: headingVisible ? 0 : 8)
        .allowsHitTesting(false)
    }

    private func swipeGesture(in canvasSize: CGSize) -> some Gesture {
        DragGesture(minimumDistance: 0, coordinateSpace: .local)
            .onChanged { value in
                handleSwipe(value: value, canvasSize: canvasSize)
            }
            .onEnded { _ in
                lastDragPoint = nil
                lastDragTime = nil
            }
    }

    private func handleSwipe(value: DragGesture.Value, canvasSize: CGSize) {
        guard canvasSize.width > 0, canvasSize.height > 0 else { return }
        guard showsPhysics, !reduceMotion else { return }

        if !hasTriggeredHaptic {
            hasTriggeredHaptic = true
            let generator = UIImpactFeedbackGenerator(style: .light)
            generator.impactOccurred()
        }

        let clampedPoint = CGPoint(
            x: min(max(0, value.location.x), canvasSize.width),
            y: min(max(0, value.location.y), canvasSize.height)
        )

        var velocity = CGVector.zero
        if let lastPoint = lastDragPoint, let lastTime = lastDragTime {
            let dt = max(0.001, value.time.timeIntervalSince(lastTime))
            let dx = clampedPoint.x - lastPoint.x
            let dy = clampedPoint.y - lastPoint.y
            velocity = CGVector(dx: dx / dt, dy: dy / dt)
            cumulativeSwipeDistance += hypot(dx, dy)

            onProgressChanged(min(1, Double(cumulativeSwipeDistance / 900)))
        }

        physicsScene.applySwipe(
            at: CGPoint(x: clampedPoint.x, y: canvasSize.height - clampedPoint.y),
            velocity: CGVector(dx: velocity.dx, dy: -velocity.dy)
        )

        lastDragPoint = clampedPoint
        lastDragTime = value.time
    }

    private func configureScene(for canvasSize: CGSize) {
        guard canvasSize.width > 0, canvasSize.height > 0 else { return }
        physicsScene.isPaused = false
        physicsScene.configure(seed: obfuscatedSeed, isEnabled: showsPhysics && !reduceMotion)
        physicsScene.updateBounds(to: canvasSize)
    }
}
