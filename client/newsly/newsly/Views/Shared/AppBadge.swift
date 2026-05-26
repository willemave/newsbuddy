//
//  AppBadge.swift
//  newsly
//
//  Unified badge components for counts and text labels.
//

import SwiftUI

/// Numeric count badge (e.g. unread count in More tab).
struct CountBadge: View {
    let count: Int
    var color: Color = .secondary

    var body: some View {
        Text("\(count)")
            .font(.system(size: 14, weight: .medium))
            .foregroundStyle(color)
            .monospacedDigit()
    }
}

/// Text badge with colored background capsule (e.g. "New", "Submitted", status labels).
struct TextBadge: View {
    let text: String
    var color: Color = .secondary
    var style: Style = .filled

    enum Style {
        case filled   // colored background, colored text
        case outlined // subtle background, colored text
    }

    var body: some View {
        Text(text.uppercased())
            .font(.chipLabel)
            .foregroundStyle(color)
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(color.opacity(style == .filled ? 0.15 : 0.08))
            .clipShape(Capsule())
    }
}

#Preview {
    VStack(spacing: 16) {
        HStack(spacing: 12) {
            CountBadge(count: 5, color: .brandTertiary)
            CountBadge(count: 12, color: .brandPrimary)
        }
        HStack(spacing: 12) {
            TextBadge(text: "New", color: .brandTertiary)
            TextBadge(text: "Processing", color: .brandPrimary)
            TextBadge(text: "Failed", color: .statusDestructive)
            TextBadge(text: "Submitted", color: .brandSecondary, style: .outlined)
        }
    }
    .padding()
}
