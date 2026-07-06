//
//  SkeletonViews.swift
//  newsly
//
//  Shared redacted loading placeholders for list and card surfaces.
//

import SwiftUI

struct SkeletonRow: View {
    enum Style {
        case compact
        case regular
        case summary

        var height: CGFloat {
            switch self {
            case .compact:
                return 64
            case .regular:
                return 84
            case .summary:
                return 18
            }
        }

        var iconSize: CGFloat {
            switch self {
            case .compact:
                return 36
            case .regular:
                return 48
            case .summary:
                return 0
            }
        }
    }

    let style: Style

    init(style: Style = .regular) {
        self.style = style
    }

    var body: some View {
        Group {
            if style == .summary {
                skeletonBlock(width: nil, height: 12, radius: 6)
                    .padding(.horizontal, Spacing.appHorizontalMargin)
            } else {
                HStack(spacing: 12) {
                    skeletonBlock(width: style.iconSize, height: style.iconSize, radius: CornerRadius.nestedControl)

                    VStack(alignment: .leading, spacing: 8) {
                        skeletonBlock(width: nil, height: 12, radius: 6)
                        skeletonBlock(width: 180, height: 10, radius: 5)
                        if style == .regular {
                            skeletonBlock(width: 120, height: 10, radius: 5)
                        }
                    }
                }
                .padding(.horizontal, Spacing.appHorizontalMargin)
            }
        }
        .frame(height: style.height)
        .skeletonPulse()
        .accessibilityHidden(true)
    }

    private func skeletonBlock(width: CGFloat?, height: CGFloat, radius: CGFloat) -> some View {
        RoundedRectangle(cornerRadius: radius, style: .continuous)
            .fill(Color.onSurface.opacity(0.10))
            .frame(width: width, height: height)
    }
}

struct SkeletonCard: View {
    let showsImage: Bool

    init(showsImage: Bool = true) {
        self.showsImage = showsImage
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            if showsImage {
                RoundedRectangle(cornerRadius: CornerRadius.control, style: .continuous)
                    .fill(Color.onSurface.opacity(0.08))
                    .frame(height: 180)
            }

            VStack(alignment: .leading, spacing: 10) {
                skeletonLine(width: nil, height: 16)
                skeletonLine(width: nil, height: 16)
                skeletonLine(width: 180, height: 16)
            }

            VStack(alignment: .leading, spacing: 8) {
                skeletonLine(width: nil, height: 10)
                skeletonLine(width: 220, height: 10)
                skeletonLine(width: 150, height: 10)
            }
        }
        .padding(16)
        .background(Color.surfaceSecondary)
        .clipShape(RoundedRectangle(cornerRadius: CornerRadius.card, style: .continuous))
        .appShadow(.editorialCard)
        .skeletonPulse()
        .accessibilityHidden(true)
    }

    private func skeletonLine(width: CGFloat?, height: CGFloat) -> some View {
        RoundedRectangle(cornerRadius: height / 2, style: .continuous)
            .fill(Color.onSurface.opacity(0.10))
            .frame(width: width, height: height)
    }
}

struct SkeletonFeedList: View {
    let kind: Kind
    var count = 4

    enum Kind {
        case shortForm
        case longForm
    }

    var body: some View {
        LazyVStack(spacing: kind == .longForm ? CardMetrics.cardSpacing : 0) {
            EditorialMastheadHeader(title: kind == .longForm ? "Long Read" : "Fast Read")

            ForEach(0..<count, id: \.self) { _ in
                switch kind {
                case .shortForm:
                    SkeletonRow(style: .regular)
                case .longForm:
                    SkeletonCard()
                        .padding(.horizontal, Spacing.appHorizontalMargin)
                }
            }
        }
        .padding(.bottom, 96)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .screenContainer()
        .topScreenEdgeFade()
    }
}

struct ContentDetailSkeletonView: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Rectangle()
                .fill(Color.surfaceTertiary)
                .frame(height: 260)
                .overlay(alignment: .bottomLeading) {
                    VStack(alignment: .leading, spacing: 10) {
                        skeletonLine(width: 260, height: 18)
                        skeletonLine(width: 190, height: 18)
                        skeletonLine(width: 150, height: 10)
                    }
                    .padding(.horizontal, Spacing.appHorizontalMargin)
                    .padding(.bottom, 24)
                    .skeletonPulse()
                }

            VStack(alignment: .leading, spacing: 14) {
                ForEach(0..<5, id: \.self) { index in
                    SkeletonRow(style: index == 0 ? .compact : .summary)
                }
            }
            .padding(.top, 18)
        }
        .screenContainer()
    }

    private func skeletonLine(width: CGFloat, height: CGFloat) -> some View {
        RoundedRectangle(cornerRadius: height / 2, style: .continuous)
            .fill(Color.white.opacity(0.32))
            .frame(width: width, height: height)
    }
}

private struct SkeletonPulseModifier: ViewModifier {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var isDimmed = false

    func body(content: Content) -> some View {
        content
            .opacity(isDimmed ? 0.62 : 1)
            .redacted(reason: .placeholder)
            .animation(
                reduceMotion
                    ? nil
                    : AppMotion.subtle.repeatForever(autoreverses: true),
                value: isDimmed
            )
            .onAppear {
                guard !reduceMotion else { return }
                isDimmed = true
            }
    }
}

private extension View {
    func skeletonPulse() -> some View {
        modifier(SkeletonPulseModifier())
    }
}

#Preview {
    VStack(spacing: 24) {
        SkeletonCard()
        SkeletonRow()
    }
    .padding()
    .screenContainer()
}
