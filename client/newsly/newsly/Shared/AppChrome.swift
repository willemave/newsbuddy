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

/// Floating replacement for the system root tab bar.
struct CompactTabBar: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    struct Item: Identifiable {
        let tab: RootTab
        let label: String
        let icon: String
        let accessibilityIdentifier: String

        var id: RootTab { tab }
    }

    let items: [Item]
    let selection: RootTab
    let onSelect: (RootTab) -> Void
    @Namespace private var selectionNamespace

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
        .sensoryFeedback(.impact(weight: .light), trigger: selection)
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
                Text(item.label)
                    .font(.appCaption2.weight(.semibold))
                    .lineLimit(1)
                    .fixedSize()
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 6)
            .frame(minWidth: 72, minHeight: 44)
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
