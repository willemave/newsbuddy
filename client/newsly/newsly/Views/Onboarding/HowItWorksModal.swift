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

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
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

                VStack(spacing: 8) {
                    Text("What to expect")
                        .font(.appTitle2)
                        .foregroundColor(.onboardingText)
                        .opacity(entranceOpacity)
                        .offset(y: entranceOffset(10))
                }
                .padding(.bottom, 30)

                VStack(spacing: 10) {
                    ForEach(Array(tips.enumerated()), id: \.element.id) { index, tip in
                        tipRow(tip)
                            .opacity(entranceOpacity)
                            .offset(y: entranceOffset(16))
                            .animation(
                                stagedEntranceAnimation(index: index),
                                value: appeared
                            )
                    }
                }
                .padding(.horizontal, Spacing.appHorizontalMargin)

                Spacer()

                Button(action: onDone) {
                    Text(primaryButtonTitle)
                        .font(.appCallout.weight(.semibold))
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 14)
                        .foregroundColor(.onboardingSurface)
                        .background(primaryButtonBackground)
                }
                .buttonStyle(.plain)
                .padding(.horizontal, Spacing.appHorizontalMargin)
                .padding(.bottom, 16)
                .opacity(entranceOpacity)
                .animation(buttonEntranceAnimation, value: appeared)
                .accessibilityIdentifier("onboarding.tutorial.complete")
            }
        }
        .onAppear {
            withAnimation(reduceMotion ? nil : AppMotion.emphasized) {
                appeared = true
            }
        }
        .accessibilityIdentifier("onboarding.tutorial.screen")
    }

    private func tipRow(_ tip: TutorialTip) -> some View {
        HStack(spacing: 14) {
            Image(systemName: tip.icon)
                .font(.appBody.weight(.medium))
                .foregroundColor(tip.isFeatured ? .onboardingSurface : .onboardingText)
                .frame(width: 42, height: 42)
                .background(
                    Circle()
                        .fill(
                            tip.isFeatured
                                ? Color.onboardingText
                                : Color.onboardingText.opacity(0.08)
                        )
                )

            VStack(alignment: .leading, spacing: 4) {
                Text(tip.title)
                    .font(.appCallout.weight(.semibold))
                    .foregroundColor(.onboardingText)
                Text(tip.detail)
                    .font(.appCaption)
                    .foregroundColor(.onboardingText.opacity(0.62))
                    .fixedSize(horizontal: false, vertical: true)
            }

            Spacer()
        }
        .padding(14)
        .background(
            RoundedRectangle(cornerRadius: 18)
                .fill(
                    tip.isFeatured
                        ? Color.onboardingSurface.opacity(0.94)
                        : Color.onboardingSurface.opacity(0.82)
                )
                .overlay(
                    RoundedRectangle(cornerRadius: 18)
                        .stroke(Color.onboardingText.opacity(tip.isFeatured ? 0.14 : 0.08), lineWidth: 0.5)
                )
                .appShadow(tip.isFeatured ? .elevated : .card)
        )
    }

    private var primaryButtonTitle: String {
        feedCount > 0 ? "Open my feed" : "Start reading"
    }

    private var primaryButtonBackground: some View {
        RoundedRectangle(cornerRadius: 24)
            .fill(Color.onboardingText)
            .appShadow(.elevated)
    }

    private var entranceOpacity: Double {
        appeared || reduceMotion ? 1 : 0
    }

    private func entranceOffset(_ distance: CGFloat) -> CGFloat {
        appeared || reduceMotion ? 0 : distance
    }

    private func stagedEntranceAnimation(index: Int) -> Animation? {
        reduceMotion ? nil : AppMotion.emphasized.delay(0.12 + Double(index) * 0.06)
    }

    private var buttonEntranceAnimation: Animation? {
        reduceMotion ? nil : AppMotion.panel.delay(0.36)
    }
}
