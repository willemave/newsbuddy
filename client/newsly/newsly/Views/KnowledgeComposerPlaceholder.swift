//
//  KnowledgeComposerPlaceholder.swift
//  newsly
//

import SwiftUI

/// Knowledge composer placeholder that rolls through example asks while the field is idle.
/// It inherits the composer font so it sits exactly where typed text begins.
struct KnowledgeComposerPlaceholder: View {
    private static let prompts = [
        "Ask anything…",
        "Find new news sources…",
        "Find new podcasts…",
        "Find new newsletters…",
    ]
    private static let holdDuration: Duration = .seconds(3)

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    let isRotating: Bool

    @State private var index = 0

    var body: some View {
        ZStack(alignment: .leading) {
            Text(Self.prompts[index])
                .lineLimit(1)
                .transition(reduceMotion ? .opacity : .push(from: .bottom))
                .id(index)
        }
        .foregroundStyle(Color.onSurfaceTertiary)
        .allowsHitTesting(false)
        .accessibilityHidden(true)
        .task(id: isRotating) { await rotate() }
    }

    private func rotate() async {
        guard isRotating else { return }
        do {
            while true {
                try await Task.sleep(for: Self.holdDuration)
                withAnimation(AppMotion.emphasized) {
                    index = (index + 1) % Self.prompts.count
                }
            }
        } catch {
            // Cancelled: the composer gained focus or text, or the screen went away.
        }
    }
}
