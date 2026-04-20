//
//  LaneStatusRow.swift
//  newsly
//
//  Extracted from OnboardingFlowView for reuse in DiscoveryPersonalizeSheet.
//

import SwiftUI

struct LaneStatusRow: View {
    let lane: OnboardingDiscoveryLaneStatus

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 12) {
                ZStack {
                    Circle()
                        .fill(statusColor.opacity(0.12))
                        .frame(width: 32, height: 32)
                    Image(systemName: statusIcon)
                        .font(.caption.weight(.semibold))
                        .foregroundColor(statusColor)
                }

                VStack(alignment: .leading, spacing: 2) {
                    Text(lane.name)
                        .font(.callout)
                        .foregroundColor(.watercolorSlate)
                    Text(statusLabel)
                        .font(.caption)
                        .foregroundColor(.watercolorSlate.opacity(0.62))
                }

                Spacer()

                if lane.queryCount > 0 {
                    Text("\(lane.completedQueries)/\(lane.queryCount)")
                        .font(.caption.weight(.semibold))
                        .monospacedDigit()
                        .foregroundColor(.watercolorSlate.opacity(0.68))
                }
            }

            if lane.queryCount > 0 {
                ProgressView(value: laneProgress)
                    .tint(statusColor)
                    .scaleEffect(x: 1.0, y: 0.85, anchor: .center)
            }
        }
        .padding(.vertical, 4)
        .animation(.easeInOut(duration: 0.2), value: lane.status)
        .animation(.easeInOut(duration: 0.2), value: lane.completedQueries)
    }

    private var laneProgress: Double {
        guard lane.queryCount > 0 else { return 0 }
        return min(1, Double(lane.completedQueries) / Double(lane.queryCount))
    }

    private var statusLabel: String {
        switch lane.status {
        case "processing":
            return lane.queryCount > 0 ? "Searching in progress" : "Searching..."
        case "completed":
            return "Done"
        case "failed":
            return "Failed"
        default:
            return "Queued"
        }
    }

    private var statusIcon: String {
        switch lane.status {
        case "processing": return "hourglass"
        case "completed": return "checkmark"
        case "failed": return "exclamationmark"
        default: return "circle"
        }
    }

    private var statusColor: Color {
        switch lane.status {
        case "processing": return .watercolorSlate
        case "completed": return .watercolorPaleEmerald
        case "failed": return .watercolorDiffusedPeach
        default: return .watercolorSlate.opacity(0.4)
        }
    }
}
