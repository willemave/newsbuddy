//
//  AppChrome.swift
//  newsly
//
//  Created by Assistant on 3/20/26.
//

import SwiftUI
import UIKit

private struct PersistentBottomChromeInsetKey: EnvironmentKey {
    static let defaultValue: CGFloat = 0
}

extension EnvironmentValues {
    var persistentBottomChromeInset: CGFloat {
        get { self[PersistentBottomChromeInsetKey.self] }
        set { self[PersistentBottomChromeInsetKey.self] = newValue }
    }
}

enum AppChrome {
    static func configure(textSize: DynamicTypeSize = .large) {
        let chromeAccent = UIColor.appChromeAccent
        let unselected = UIColor.appOnSurfaceTertiary
        let surface = UIColor.appSurfacePrimary
        let traitCollection = UITraitCollection(
            preferredContentSizeCategory: textSize.uiContentSizeCategory
        )

        let itemAppearance = UITabBarItemAppearance()
        itemAppearance.selected.iconColor = chromeAccent
        itemAppearance.selected.titleTextAttributes = [
            .foregroundColor: chromeAccent,
            .font: UIFontMetrics(forTextStyle: .caption2).scaledFont(
                for: UIFont.appSans(size: 10, weight: .medium),
                compatibleWith: traitCollection
            )
        ]
        itemAppearance.normal.iconColor = unselected
        itemAppearance.normal.titleTextAttributes = [
            .foregroundColor: unselected,
            .font: UIFontMetrics(forTextStyle: .caption2).scaledFont(
                for: UIFont.appSans(size: 10, weight: .medium),
                compatibleWith: traitCollection
            )
        ]

        let tabAppearance = UITabBarAppearance()
        tabAppearance.configureWithTransparentBackground()
        tabAppearance.backgroundColor = surface.withAlphaComponent(0.92)
        tabAppearance.shadowColor = ReaderPalette.selectedUIColor(\.outlineVariant)
        tabAppearance.stackedLayoutAppearance = itemAppearance
        tabAppearance.inlineLayoutAppearance = itemAppearance
        tabAppearance.compactInlineLayoutAppearance = itemAppearance
        UITabBar.appearance().standardAppearance = tabAppearance
        UITabBar.appearance().scrollEdgeAppearance = tabAppearance
        UITabBar.appearance().tintColor = chromeAccent

        // No bar fill: on iOS 26+ the system draws glass bar buttons and a soft
        // scroll-edge effect itself, and a painted background hides both.
        let navigationAppearance = UINavigationBarAppearance()
        navigationAppearance.configureWithTransparentBackground()
        navigationAppearance.titleTextAttributes = [
            .foregroundColor: UIColor.appOnSurface,
            .font: UIFontMetrics(forTextStyle: .headline).scaledFont(
                for: UIFont.appSerif(size: 17, weight: .semibold),
                compatibleWith: traitCollection
            )
        ]
        navigationAppearance.largeTitleTextAttributes = [
            .foregroundColor: UIColor.appOnSurface,
            .font: UIFontMetrics(forTextStyle: .largeTitle).scaledFont(
                for: UIFont.appSerif(size: 34, weight: .semibold),
                compatibleWith: traitCollection
            )
        ]
        UINavigationBar.appearance().standardAppearance = navigationAppearance
        UINavigationBar.appearance().scrollEdgeAppearance = navigationAppearance
        UINavigationBar.appearance().tintColor = chromeAccent
    }
}

private extension DynamicTypeSize {
    var uiContentSizeCategory: UIContentSizeCategory {
        switch self {
        case .xSmall: return .extraSmall
        case .small: return .small
        case .medium: return .medium
        case .large: return .large
        case .xLarge: return .extraLarge
        case .xxLarge: return .extraExtraLarge
        case .xxxLarge: return .extraExtraExtraLarge
        case .accessibility1: return .accessibilityMedium
        case .accessibility2: return .accessibilityLarge
        case .accessibility3: return .accessibilityExtraLarge
        case .accessibility4: return .accessibilityExtraExtraLarge
        case .accessibility5: return .accessibilityExtraExtraExtraLarge
        @unknown default: return .large
        }
    }
}

/// Condenses the floating root bar while the reader scrolls down and restores
/// it on scroll-up, at the top, or on tab change, like the system tab bar's
/// minimize-on-scroll. Only the Bool is observed, so per-frame offsets never
/// invalidate views.
@MainActor
@Observable
final class RootTabBarMinimizer {
    private(set) var isMinimized = false

    @ObservationIgnored private var lastOffsetBySource: [String: CGFloat] = [:]
    @ObservationIgnored private var travel: CGFloat = 0

    /// Offsets this close to the top always show the full bar.
    static let topSlack: CGFloat = 24
    static let minimizeDistance: CGFloat = 36
    static let expandDistance: CGFloat = 18

    /// `offset` is distance scrolled from the top of `source`'s content.
    func record(offset: CGFloat, source: String) {
        guard offset.isFinite else { return }
        defer { lastOffsetBySource[source] = offset }
        guard offset > Self.topSlack else {
            expand()
            return
        }
        guard let last = lastOffsetBySource[source] else { return }
        let delta = offset - last
        guard delta != 0 else { return }
        if (delta > 0) != (travel > 0) {
            travel = 0
        }
        travel += delta
        if travel >= Self.minimizeDistance {
            setMinimized(true)
        } else if travel <= -Self.expandDistance {
            setMinimized(false)
        }
    }

    func expand() {
        travel = 0
        setMinimized(false)
    }

    /// Distance scrolled from the top, clamped to the scrollable range so the
    /// rubber-band return after a fling into the end does not read as an
    /// upward scroll and restore the bar.
    static func scrolledDistance(
        contentOffsetY: CGFloat,
        insetTop: CGFloat,
        contentHeight: CGFloat,
        visibleHeight: CGFloat
    ) -> CGFloat {
        let maximum = max(contentHeight - visibleHeight, 0)
        return min(max(contentOffsetY + insetTop, 0), maximum)
    }

    static func scrolledDistance(in geometry: ScrollGeometry) -> CGFloat {
        scrolledDistance(
            contentOffsetY: geometry.contentOffset.y,
            insetTop: geometry.contentInsets.top,
            contentHeight: geometry.contentSize.height,
            visibleHeight: geometry.containerSize.height
        )
    }

    private func setMinimized(_ minimized: Bool) {
        guard isMinimized != minimized else { return }
        isMinimized = minimized
    }
}

/// Floating replacement for the system root tab bar.
struct CompactTabBar: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    struct Item: Identifiable {
        let tab: RootTab
        let label: String
        let icon: String
        let accessibilityIdentifier: String

        var id: RootTab { tab }
    }

    let items: [Item]
    let selection: RootTab
    var isMinimized = false
    let onSelect: (RootTab) -> Void
    @Namespace private var selectionNamespace
    /// Expanded item height, held while minimized so the bar condenses in
    /// width only and the root inset (and every screen's insets) stays put.
    @State private var expandedItemHeight: CGFloat?

    var body: some View {
        GlassEffectContainer(spacing: 6) {
            tabItems
                .padding(5)
                .glassEffect(.floatingChrome.interactive(), in: .capsule)
        }
        .frame(maxWidth: 240)
        .padding(.horizontal, Spacing.appHorizontalMargin)
        .padding(.top, 8)
        .padding(.bottom, 6)
        .frame(maxWidth: .infinity)
        .animation(
            AppMotion.respectingReduceMotion(reduceMotion, AppMotion.emphasized),
            value: selection
        )
        .animation(
            AppMotion.respectingReduceMotion(reduceMotion, AppMotion.emphasized),
            value: isMinimized
        )
        .sensoryFeedback(.impact(weight: .light), trigger: selection)
        .onChange(of: dynamicTypeSize) { _, _ in
            expandedItemHeight = nil
        }
    }

    private var tabItems: some View {
        HStack(spacing: 6) {
            ForEach(items) { item in
                itemButton(item)
            }
        }
    }

    private func itemButton(_ item: Item) -> some View {
        let isSelected = item.tab == selection
        return Button {
            onSelect(item.tab)
        } label: {
            VStack(spacing: 2) {
                Image(systemName: item.icon)
                    .font(.appSymbol(size: 17, weight: .semibold))
                if !isMinimized {
                    Text(item.label)
                        .font(.appCaption2.weight(.semibold))
                        .lineLimit(1)
                        .fixedSize()
                        .transition(.opacity.combined(with: .scale(scale: 0.8, anchor: .top)))
                }
            }
            .padding(.horizontal, isMinimized ? 12 : 16)
            .padding(.vertical, 6)
            .frame(minWidth: isMinimized ? 48 : 72, minHeight: 44)
            .frame(height: isMinimized ? expandedItemHeight : nil)
            .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { _, height in
                guard !isMinimized, height > 0 else { return }
                expandedItemHeight = height
            }
            .foregroundStyle(isSelected ? Color.onSurface : Color.onSurfaceSecondary)
            .background {
                // A lighter lens inside the clear bar, the way system tab bars
                // mark selection, rather than an opaque chip sitting on top.
                if isSelected {
                    Capsule()
                        .fill(Color.onSurface.opacity(0.14))
                        .matchedGeometryEffect(id: "tab.pill", in: selectionNamespace)
                }
            }
            .contentShape(Capsule())
        }
        .buttonStyle(CompactTabButtonStyle(reduceMotion: reduceMotion))
        .accessibilityLabel(item.label)
        .accessibilityAddTraits(isSelected ? .isSelected : [])
        .accessibilityIdentifier(item.accessibilityIdentifier)
    }
}

private struct CompactTabButtonStyle: ButtonStyle {
    let reduceMotion: Bool

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? 0.96 : 1)
            .animation(
                AppMotion.respectingReduceMotion(reduceMotion, AppMotion.press),
                value: configuration.isPressed
            )
    }
}
