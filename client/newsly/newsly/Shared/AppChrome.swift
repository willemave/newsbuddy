//
//  AppChrome.swift
//  newsly
//
//  Created by Assistant on 3/20/26.
//

import SwiftUI
import UIKit

enum AppChrome {
    static func configure() {
        let chromeAccent = UIColor.appChromeAccent
        let unselected = UIColor.tertiaryLabel
        let surface = UIColor.appSurfacePrimary

        let itemAppearance = UITabBarItemAppearance()
        itemAppearance.selected.iconColor = chromeAccent
        itemAppearance.selected.titleTextAttributes = [
            .foregroundColor: chromeAccent,
            .font: UIFont.appSans(size: 10, weight: .medium)
        ]
        itemAppearance.normal.iconColor = unselected
        itemAppearance.normal.titleTextAttributes = [
            .foregroundColor: unselected,
            .font: UIFont.appSans(size: 10, weight: .medium)
        ]

        let tabAppearance = UITabBarAppearance()
        tabAppearance.configureWithTransparentBackground()
        tabAppearance.backgroundColor = surface.withAlphaComponent(0.92)
        tabAppearance.shadowColor = UIColor.separator
        tabAppearance.stackedLayoutAppearance = itemAppearance
        tabAppearance.inlineLayoutAppearance = itemAppearance
        tabAppearance.compactInlineLayoutAppearance = itemAppearance
        UITabBar.appearance().standardAppearance = tabAppearance
        UITabBar.appearance().scrollEdgeAppearance = tabAppearance
        UITabBar.appearance().tintColor = chromeAccent

        let navigationAppearance = UINavigationBarAppearance()
        navigationAppearance.configureWithTransparentBackground()
        navigationAppearance.backgroundColor = surface.withAlphaComponent(0.92)
        navigationAppearance.shadowColor = UIColor.separator
        navigationAppearance.titleTextAttributes = [
            .foregroundColor: UIColor.appOnSurface,
            .font: UIFont.appSerif(size: 17, weight: .semibold)
        ]
        navigationAppearance.largeTitleTextAttributes = [
            .foregroundColor: UIColor.appOnSurface,
            .font: UIFont.appSerif(size: 34, weight: .semibold)
        ]
        UINavigationBar.appearance().standardAppearance = navigationAppearance
        UINavigationBar.appearance().scrollEdgeAppearance = navigationAppearance
        UINavigationBar.appearance().tintColor = chromeAccent
    }
}

/// Floating replacement for the system tab bar in the briefing experience:
/// horizontal icon-beside-label items keep the bar vertically compact.
struct CompactTabBar: View {
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

    var body: some View {
        HStack(spacing: 4) {
            ForEach(items) { item in
                itemButton(item)
            }
        }
        .padding(5)
        .background(Capsule().fill(.ultraThinMaterial))
        .overlay {
            Capsule().stroke(Color.outlineVariant.opacity(0.5), lineWidth: 1)
        }
        .frame(maxWidth: 320)
        .padding(.horizontal, Spacing.appHorizontalMargin)
        .padding(.top, 6)
        .padding(.bottom, 4)
        .frame(maxWidth: .infinity)
        .accessibilityIdentifier("tabbar.compact")
    }

    private func itemButton(_ item: Item) -> some View {
        let isSelected = item.tab == selection
        return Button {
            onSelect(item.tab)
        } label: {
            HStack(spacing: 6) {
                Image(systemName: item.icon)
                    .font(.appSymbol(size: 13, weight: .semibold))
                Text(item.label)
                    .font(.appCaption.weight(.semibold))
            }
            .padding(.vertical, 8)
            .frame(maxWidth: .infinity)
            .background {
                if isSelected {
                    Capsule().fill(Color.surfaceSecondary)
                }
            }
            .foregroundStyle(isSelected ? Color.appChromeAccent : Color.onSurfaceSecondary)
            .contentShape(Capsule())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(item.label)
        .accessibilityAddTraits(isSelected ? .isSelected : [])
        .accessibilityIdentifier(item.accessibilityIdentifier)
    }
}

@MainActor
enum RootDependencyFactory {
    static func makeTabCoordinator() -> TabCoordinatorViewModel {
        let shortFeedRepository = ContentRepository(includeAvailableDates: false)
        let longFeedRepository = ContentRepository(includeAvailableDates: false)
        let readRepository = ReadStatusRepository()
        let newsReadRepository = ReadStatusRepository(endpoint: .newsItems)
        let unreadService = UnreadCountService.shared

        let shortNewsViewModel = ShortNewsListViewModel(
            repository: shortFeedRepository,
            readRepository: newsReadRepository,
            unreadCountService: unreadService
        )
        let longContentViewModel = LongContentListViewModel(
            repository: longFeedRepository,
            readRepository: readRepository,
            unreadCountService: unreadService
        )
        let briefingViewModel = BriefingViewModel(
            service: LiveBriefingService()
        )

        return TabCoordinatorViewModel(
            shortNewsVM: shortNewsViewModel,
            longContentVM: longContentViewModel,
            briefingVM: briefingViewModel
        )
    }
}
