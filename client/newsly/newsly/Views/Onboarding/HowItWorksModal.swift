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

    private struct TutorialTip: Identifiable {
        let id: String
        let icon: String
        let title: String
        let detail: String
        let isFeatured: Bool
    }

    private var tips: [TutorialTip] {
        var items: [TutorialTip] = []

        if feedCount > 0 {
            let noun = feedCount == 1 ? "source is" : "sources are"
            items.append(
                TutorialTip(
                    id: "processing",
                    icon: "arrow.trianglehead.2.clockwise",
                    title: "Your feed is warming up",
                    detail: "Your \(feedCount) \(noun) being ingested now. New items will appear shortly.",
                    isFeatured: true
                )
            )
        }

        items.append(
            contentsOf: [
                TutorialTip(
                    id: "fast-news",
                    icon: "bolt.fill",
                    title: "Start with Fast News",
                    detail: "Read a few quick summaries first to get a feel for the rhythm of the app.",
                    isFeatured: false
                ),
                TutorialTip(
                    id: "knowledge",
                    icon: "books.vertical.fill",
                    title: "Save to Knowledge",
                    detail: "Tap the bookshelf on any article to keep it around for later questions.",
                    isFeatured: false
                ),
                TutorialTip(
                    id: "share",
                    icon: "square.and.arrow.up.fill",
                    title: "Keep adding great inputs",
                    detail: "Share any newsletter, podcast, or article to Newsbuddy from Safari or another app.",
                    isFeatured: false
                ),
            ]
        )

        return items
    }

    var body: some View {
        ZStack {
            WatercolorBackground(energy: 0.08)

            VStack(spacing: 0) {
                Spacer()

                VStack(spacing: 14) {
                    Text("LAST STEP")
                        .font(.editorialMeta)
                        .tracking(1.8)
                        .foregroundColor(.watercolorSlate.opacity(0.58))
                        .opacity(appeared ? 1 : 0)
                        .offset(y: appeared ? 0 : 10)

                    Text("What to expect")
                        .font(.title2.bold())
                        .foregroundColor(.watercolorSlate)
                        .opacity(appeared ? 1 : 0)
                        .offset(y: appeared ? 0 : 10)

                    Text("A quick handoff before you dive in.")
                        .font(.callout)
                        .foregroundColor(.watercolorSlate.opacity(0.68))
                        .opacity(appeared ? 1 : 0)
                        .offset(y: appeared ? 0 : 10)

                    if feedCount > 0 {
                        Text("\(feedCount) sources on deck")
                            .font(.caption.weight(.semibold))
                            .monospacedDigit()
                            .foregroundColor(.watercolorSlate)
                            .padding(.horizontal, 12)
                            .padding(.vertical, 8)
                            .background(
                                Capsule()
                                    .fill(Color.watercolorBase.opacity(0.84))
                                    .shadow(color: .black.opacity(0.05), radius: 12, x: 0, y: 8)
                            )
                            .opacity(appeared ? 1 : 0)
                            .offset(y: appeared ? 0 : 10)
                    }
                }
                .padding(.bottom, 36)

                VStack(spacing: 10) {
                    ForEach(Array(tips.enumerated()), id: \.element.id) { index, tip in
                        tipRow(tip)
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

                Button(action: onDone) {
                    Text(primaryButtonTitle)
                        .font(.callout.weight(.semibold))
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 14)
                        .foregroundColor(.watercolorBase)
                        .background(primaryButtonBackground)
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

    private func tipRow(_ tip: TutorialTip) -> some View {
        HStack(spacing: 14) {
            Image(systemName: tip.icon)
                .font(.body.weight(.medium))
                .foregroundColor(tip.isFeatured ? .watercolorBase : .watercolorSlate)
                .frame(width: 42, height: 42)
                .background(
                    Circle()
                        .fill(
                            tip.isFeatured
                                ? Color.watercolorSlate
                                : Color.watercolorSlate.opacity(0.08)
                        )
                )

            VStack(alignment: .leading, spacing: 4) {
                Text(tip.title)
                    .font(.callout.weight(.semibold))
                    .foregroundColor(.watercolorSlate)
                Text(tip.detail)
                    .font(.caption)
                    .foregroundColor(.watercolorSlate.opacity(0.62))
                    .fixedSize(horizontal: false, vertical: true)
            }

            Spacer()
        }
        .padding(14)
        .background(
            RoundedRectangle(cornerRadius: 18)
                .fill(
                    tip.isFeatured
                        ? Color.watercolorBase.opacity(0.94)
                        : Color.watercolorBase.opacity(0.82)
                )
                .overlay(
                    RoundedRectangle(cornerRadius: 18)
                        .stroke(Color.watercolorSlate.opacity(tip.isFeatured ? 0.14 : 0.08), lineWidth: 0.5)
                )
                .shadow(color: .black.opacity(tip.isFeatured ? 0.08 : 0.04), radius: 14, x: 0, y: 10)
        )
    }

    private var primaryButtonTitle: String {
        feedCount > 0 ? "Open my feed" : "Start reading"
    }

    private var primaryButtonBackground: some View {
        RoundedRectangle(cornerRadius: 24)
            .fill(Color.watercolorSlate)
            .shadow(color: .black.opacity(0.10), radius: 18, x: 0, y: 12)
    }
}
