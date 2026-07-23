//
//  LaneStatusRow.swift
//  newsly
//
//  Extracted from OnboardingFlowView for reuse in DiscoveryPersonalizeSheet.
//

import SwiftUI

struct LaneStatusRow: View {
    let lane: OnboardingDiscoveryLaneStatus
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        HStack(alignment: .center, spacing: 12) {
            statusIndicator

            VStack(alignment: .leading, spacing: 6) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(lane.name)
                        .font(.appCallout.weight(isCompleted ? .regular : .medium))
                        .foregroundColor(.onboardingText.opacity(isCompleted ? 0.7 : 0.95))
                        .lineLimit(1)

                    Spacer(minLength: 0)

                    if showsCountBadge {
                        Text("\(lane.completedQueries)/\(lane.queryCount)")
                            .font(.appCaption2.weight(.semibold))
                            .monospacedDigit()
                            .foregroundColor(.onboardingText.opacity(0.55))
                            .padding(.horizontal, 7)
                            .padding(.vertical, 3)
                            .background(
                                Capsule(style: .continuous)
                                    .fill(Color.onboardingText.opacity(0.07))
                            )
                            .transition(.opacity)
                    }
                }

                if showsProgressBar {
                    OnboardingProgressBar(progress: laneProgress)
                        .frame(height: 3)
                        .transition(.opacity)
                } else if !isCompleted {
                    Text(statusLabel)
                        .font(.appCaption)
                        .foregroundColor(.onboardingText.opacity(0.55))
                        .transition(.opacity)
                }
            }
        }
        .padding(.vertical, 4)
        .opacity(rowOpacity)
        .animation(
            AppMotion.respectingReduceMotion(reduceMotion, AppMotion.panel),
            value: lane.status
        )
        .animation(
            AppMotion.respectingReduceMotion(reduceMotion, AppMotion.panel),
            value: lane.completedQueries
        )
    }

    @ViewBuilder
    private var statusIndicator: some View {
        ZStack {
            Circle()
                .fill(indicatorBackground)
                .frame(width: 26, height: 26)

            switch lane.status {
            case "completed":
                Image(systemName: "checkmark")
                    .font(.appSymbol(size: 11, weight: .bold))
                    .foregroundColor(.statusSuccess)
                    .transition(.scale(scale: 0.4).combined(with: .opacity))
            case "failed":
                Image(systemName: "exclamationmark")
                    .font(.appSymbol(size: 11, weight: .bold))
                    .foregroundColor(.statusDestructive)
            case "processing":
                LanePulsingDot()
            default:
                Circle()
                    .strokeBorder(
                        Color.onboardingText.opacity(0.28),
                        style: StrokeStyle(lineWidth: 1.2, dash: [2, 2.5])
                    )
                    .frame(width: 12, height: 12)
            }
        }
    }

    private var indicatorBackground: Color {
        switch lane.status {
        case "completed": return Color.statusSuccess.opacity(0.16)
        case "failed": return Color.statusDestructive.opacity(0.14)
        case "processing": return Color.statusProcessing.opacity(0.14)
        default: return Color.onboardingText.opacity(0.05)
        }
    }

    private var rowOpacity: Double {
        switch lane.status {
        case "completed": return 0.88
        case "failed", "processing": return 1.0
        default: return 0.6
        }
    }

    private var isCompleted: Bool { lane.status == "completed" }

    private var showsCountBadge: Bool {
        lane.queryCount > 0 && !isCompleted && lane.status != "failed"
    }

    private var showsProgressBar: Bool {
        lane.queryCount > 0 && !isCompleted && lane.status != "failed"
    }

    private var laneProgress: Double {
        guard lane.queryCount > 0 else { return 0 }
        return min(1, Double(lane.completedQueries) / Double(lane.queryCount))
    }

    private var statusLabel: String {
        switch lane.status {
        case "processing":
            return lane.queryCount > 0 ? "Searching" : "Searching..."
        case "completed":
            return "Done"
        case "failed":
            return "Couldn't reach this source"
        default:
            return "Queued"
        }
    }
}

private struct OnboardingProgressBar: View {
    let progress: Double

    var body: some View {
        GeometryReader { geo in
            let fillWidth = max(0, min(geo.size.width, geo.size.width * CGFloat(progress)))

            ZStack(alignment: .leading) {
                Capsule(style: .continuous)
                    .fill(Color.onboardingText.opacity(0.08))

                Capsule(style: .continuous)
                    .fill(Color.statusProcessing.opacity(0.9))
                    .frame(width: fillWidth)
            }
        }
    }
}

private struct LanePulsingDot: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var isPulsing = false

    var body: some View {
        ZStack {
            Circle()
                .fill(Color.statusProcessing.opacity(0.45))
                .frame(width: 14, height: 14)
                .scaleEffect(reduceMotion ? 1.0 : (isPulsing ? 1.6 : 0.85))
                .opacity(reduceMotion ? 0.55 : (isPulsing ? 0 : 0.65))

            Circle()
                .fill(Color.statusProcessing)
                .frame(width: 7, height: 7)
        }
        .onAppear {
            restartPulse()
        }
        .onChange(of: reduceMotion) { _, _ in
            restartPulse()
        }
    }

    private func restartPulse() {
        guard !reduceMotion else {
            isPulsing = false
            return
        }
        isPulsing = false
        withAnimation(AppMotion.lanePulse) {
            isPulsing = true
        }
    }
}
