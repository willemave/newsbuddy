//
//  HowItWorksModal.swift
//  newsly
//
//  Created by Assistant on 1/17/26.
//

import SwiftUI

struct HowItWorksModal: View {
    let feedCount: Int
    let onDone: () -> Void

    @State private var appeared = false

    private var tips: [(icon: String, title: String, detail: String)] {
        var items: [(String, String, String)] = []

        if feedCount > 0 {
            let noun = feedCount == 1 ? "source is" : "sources are"
            items.append((
                "arrow.trianglehead.2.clockwise",
                "Processing",
                "Your \(feedCount) \(noun) being ingested now. New items will appear shortly."
            ))
        }

        items.append(contentsOf: [
            (
                "bolt.fill",
                "Start with Fast News",
                "Read a few quick summaries to see how the system works."
            ),
            (
                "books.vertical.fill",
                "Save to Knowledge",
                "Tap the bookshelf icon on any article to save it for future reference."
            ),
            (
                "brain.head.profile.fill",
                "Chat with your knowledge",
                "Use the brain icon to ask questions across everything you've saved."
            ),
            (
                "square.and.arrow.up.fill",
                "Share links to add feeds",
                "Share any podcast, newsletter, or article with Newsbuddy from your browser to add it."
            ),
        ])

        return items
    }

    var body: some View {
        ZStack {
            WatercolorBackground(energy: 0.08)

            VStack(spacing: 0) {
                Spacer()

                // Heading
                VStack(spacing: 12) {
                    Text("What to expect")
                        .font(.title2.bold())
                        .foregroundColor(.watercolorSlate)
                        .opacity(appeared ? 1 : 0)
                        .offset(y: appeared ? 0 : 10)

                    Text("A few things to know before you dive in.")
                        .font(.callout)
                        .foregroundColor(.watercolorSlate.opacity(0.6))
                        .opacity(appeared ? 1 : 0)
                        .offset(y: appeared ? 0 : 10)
                }
                .padding(.bottom, 40)

                // Tip cards
                VStack(spacing: 10) {
                    ForEach(Array(tips.enumerated()), id: \.offset) { index, tip in
                        tipRow(icon: tip.icon, title: tip.title, detail: tip.detail)
                            .opacity(appeared ? 1 : 0)
                            .offset(y: appeared ? 0 : 16)
                            .animation(
                                .easeOut(duration: 0.5).delay(0.15 + Double(index) * 0.08),
                                value: appeared
                            )
                    }
                }
                .padding(.horizontal, 24)

                Spacer()

                // CTA
                Button(action: onDone) {
                    Text("Let's go")
                        .font(.callout.weight(.semibold))
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 14)
                        .foregroundColor(.watercolorBase)
                        .background(Color.watercolorSlate)
                        .clipShape(RoundedRectangle(cornerRadius: 24))
                }
                .buttonStyle(.plain)
                .padding(.horizontal, 24)
                .padding(.bottom, 16)
                .opacity(appeared ? 1 : 0)
                .animation(.easeOut(duration: 0.4).delay(0.6), value: appeared)
                .accessibilityIdentifier("onboarding.tutorial.complete")
            }
        }
        .onAppear {
            withAnimation(.easeOut(duration: 0.6)) {
                appeared = true
            }
        }
        .accessibilityIdentifier("onboarding.tutorial.screen")
    }

    private func tipRow(icon: String, title: String, detail: String) -> some View {
        HStack(spacing: 14) {
            Image(systemName: icon)
                .font(.body.weight(.medium))
                .foregroundColor(.watercolorSlate)
                .frame(width: 40, height: 40)
                .background(Color.watercolorSlate.opacity(0.08))
                .clipShape(Circle())

            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.callout.weight(.semibold))
                    .foregroundColor(.watercolorSlate)
                Text(detail)
                    .font(.caption)
                    .foregroundColor(.watercolorSlate.opacity(0.55))
            }

            Spacer()
        }
        .padding(14)
        .background(Color.watercolorSlate.opacity(0.06))
        .clipShape(RoundedRectangle(cornerRadius: 14))
    }
}
